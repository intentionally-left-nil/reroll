"""Convert a PEP 508 requirement string into its conda MatchSpec equivalent.

See docs/matchspec.md.
"""

from __future__ import annotations

from markerpry import Node, parse_marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier, SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name
from packaging.version import InvalidVersion, Version
from rattler import MatchSpec
from rattler.exceptions import InvalidMatchSpecError

from reroll.dependencies.marker_conversion import UnconvertableMarkerError, marker_condition
from reroll.dependencies.version_format import format_version
from reroll.errors import InvalidRequirementError, UnconvertableRequirementError
from reroll.name_mapping import NameMappers, map_name

_MAX_EXTRA_LENGTH = 64
"""CEP-29's `extras` bracket key limits each extra name to 64 characters."""


def pep508_to_matchspec(
    entry: str,
    mappers: NameMappers,
    *,
    allow_pre: bool = False,
    abi3_upper_bound: str | None = None,
) -> str:
    """The conda MatchSpec for `entry`, a single PEP 508 requirement string
    (a `Requires-Dist` entry).

    `abi3_upper_bound` bounds `marker_condition`'s `python_version in
    "<literal>"` rewrite the same way it bounds `explode_abi3`'s tag
    explosion -- a minor-only version string like `"3.15"`; `None` (the
    default) defers to `latest_python_minor`, lazily, and only if `entry`'s
    marker actually has such a clause.

    Raises `UnconvertableRequirementError` for anything that can't be
    converted: a direct URL reference (`name @ url`); a local version label
    (`1.0+local`); a pre-release version, unless `allow_pre` is set; an
    extra name (`name[extra]`) longer than 64 characters once normalized; a
    marker referring to `extra` at all (that's a different mechanism --
    grouping a dependency into one of the *current* package's own extras --
    than an environment marker, and combining it with a real environment
    condition isn't implemented here); or an assembled MatchSpec string
    that fails py-rattler's own validation. Also raises
    `InvalidRequirementError` if `entry` itself does not parse as a PEP 508
    requirement, `reroll.errors.UnconvertableMarkerError` or
    `reroll.errors.UnconvertablePythonVersionEqualityError` for a marker
    using a construct that has no matchspec equivalent (`marker_condition`),
    and `reroll.errors.UnresolvedCondaNameError` for a PyPI name with no
    resolvable conda name.
    """
    try:
        requirement = Requirement(entry)
    except InvalidRequirement as exc:
        raise InvalidRequirementError(
            f"cannot parse {entry!r} as a PEP 508 requirement: {exc}"
        ) from exc
    marker_node = parse_marker(requirement.marker) if requirement.marker is not None else None
    if marker_node is not None and "extra" in marker_node:
        raise UnconvertableRequirementError(
            f"cannot convert {entry!r}: its marker refers to `extra`, which is a "
            "separate per-package-extra mechanism, not an environment condition"
        )
    if requirement.url is not None:
        raise UnconvertableRequirementError(
            f"cannot convert {entry!r}: it has a direct URL reference"
        )
    conda_name = map_name(requirement.name, mappers)
    version_parts = _convert_specifiers(requirement.specifier, entry, allow_pre=allow_pre)
    extras = {canonicalize_name(extra) for extra in requirement.extras}
    if extras:
        _reject_invalid_extras(extras, entry)

    brackets: list[str] = []
    if extras:
        brackets.append(_format_extras(extras))
    if marker_node is not None:
        condition = _marker_condition(marker_node, entry, abi3_upper_bound=abi3_upper_bound)
        brackets.append(f'when="{condition}"')

    name_and_version = (
        conda_name if not version_parts else f"{conda_name} {','.join(version_parts)}"
    )
    bracket_suffix = f"[{','.join(brackets)}]" if brackets else ""
    matchspec = f"{name_and_version}{bracket_suffix}"

    try:
        MatchSpec(matchspec)
    except InvalidMatchSpecError as exc:
        raise UnconvertableRequirementError(
            f"{matchspec!r}, converted from {entry!r}, is not a valid matchspec"
        ) from exc
    return matchspec


def _convert_specifiers(specifiers: SpecifierSet, entry: str, *, allow_pre: bool) -> list[str]:
    parts: list[str] = []
    for specifier in sorted(specifiers, key=str):
        parts.extend(_convert_specifier(specifier, entry, allow_pre=allow_pre))
    return parts


def _convert_specifier(specifier: Specifier, entry: str, *, allow_pre: bool) -> list[str]:
    """One PEP 440 specifier's contribution to a MatchSpec's version
    clause -- most contribute a single `<op><version>` clause, but `~=`
    (deprecated per CEP-29) expands into an explicit `>=`/`<` pair.

    Raises `UnconvertableRequirementError` if `entry`'s whole conversion
    must be rejected: a local version label, or a pre-release version with
    `allow_pre` unset.
    """
    if specifier.operator == "~=":
        return _expand_compatible_release(specifier.version, entry, allow_pre=allow_pre)
    if specifier.operator in ("==", "!=") and specifier.version.endswith(".*"):
        if specifier.operator == "!=":
            return [f"!={specifier.version}"]
        return [f"={specifier.version[:-2]}"]
    if specifier.operator in ("<", ">"):
        return _convert_exclusive_comparator(
            specifier.operator, specifier.version, entry, allow_pre=allow_pre
        )
    operator = "==" if specifier.operator == "===" else specifier.operator
    try:
        version = Version(specifier.version)
    except InvalidVersion:
        return [f"{operator}{specifier.version}"]
    _reject_unsupported_version(version, entry, allow_pre=allow_pre)
    return [f"{operator}{format_version(version)}"]


def _expand_compatible_release(raw_version: str, entry: str, *, allow_pre: bool) -> list[str]:
    """`~=X.Y.Z`'s expansion into `>=X.Y.Z,<X.(Y+1).0a0` (docs/matchspec.md's
    Operator conversion) -- the version literal sans its last release
    segment, bumped by one, anchored at `.0a0` so a pre-release of that
    boundary lands on the lower side of the range.
    """
    version = Version(raw_version)
    _reject_unsupported_version(version, entry, allow_pre=allow_pre)
    prefix = version.release[:-1]
    bumped = prefix[:-1] + (prefix[-1] + 1,)
    epoch_prefix = f"{version.epoch}!" if version.epoch else ""
    upper_release = ".".join(str(segment) for segment in bumped)
    return [f">={format_version(version)}", f"<{epoch_prefix}{upper_release}.0a0"]


def _convert_exclusive_comparator(
    operator: str, raw_version: str, entry: str, *, allow_pre: bool
) -> list[str]:
    """`<V`/`>V`'s PEP 440 carve-out (the "Version specifiers" spec's
    Exclusive ordered comparison): `<V` excludes every pre-release of `V`
    unless `V` is itself a pre-release, and `>V` excludes every
    post-release of `V` unless `V` is itself a post-release or dev-release.
    Conda's plain ordered comparison has no such family exception, so a
    passthrough `<V`/`>V` is only correct when `V` already carries the
    suffix (pre, dev, or post) that makes the carve-out a no-op; otherwise
    the boundary needs an explicit anchor (`<V` -> `<Va0`, an `a0`
    pre-release tag glued directly onto `V` with no separating dot, below
    every pre-release of `V`) or an extra exclusion clause (`>V` ->
    `>V,!=V.post*`, since post-releases of `V` have no fixed upper anchor).

    `raw_version` is always a valid PEP 440 version here: unlike `===`,
    `packaging.specifiers.Specifier` itself rejects a non-PEP-440 version
    for `<`/`>` before this function ever sees it.
    """
    version = Version(raw_version)
    _reject_unsupported_version(version, entry, allow_pre=allow_pre)
    formatted = format_version(version)
    if operator == "<":
        if version.is_prerelease:
            return [f"<{formatted}"]
        return [f"<{formatted}a0"]
    if version.dev is not None or version.post is not None:
        return [f">{formatted}"]
    return [f">{formatted}", f"!={formatted}.post*"]


def _reject_unsupported_version(version: Version, entry: str, *, allow_pre: bool) -> None:
    if version.local is not None:
        raise UnconvertableRequirementError(
            f"cannot convert {entry!r}: it has a local version label"
        )
    if version.is_prerelease and not allow_pre:
        raise UnconvertableRequirementError(
            f"cannot convert {entry!r}: it is a pre-release and allow_pre is unset"
        )


def _reject_invalid_extras(extras: set[NormalizedName], entry: str) -> None:
    """Raises `UnconvertableRequirementError` if any of `extras` -- already
    normalized via `canonicalize_name` -- exceeds CEP-29's 64-character
    limit.
    """
    for extra in extras:
        if len(extra) > _MAX_EXTRA_LENGTH:
            raise UnconvertableRequirementError(
                f"cannot convert {entry!r}: extra {extra!r} exceeds "
                f"{_MAX_EXTRA_LENGTH} characters once normalized"
            )


def _format_extras(extras: set[NormalizedName]) -> str:
    """The matchspec `extras=[...]` bracket for `extras`, already
    normalized via `canonicalize_name`.
    """
    return f"extras=[{','.join(sorted(extras))}]"


def _marker_condition(marker_node: Node, entry: str, *, abi3_upper_bound: str | None) -> str:
    """`marker_condition(marker_node, abi3_upper_bound=abi3_upper_bound)`,
    with `entry` folded into the message on failure.

    Reraises the same `UnconvertableMarkerError` instance rather than
    constructing a new one: that error already logged itself at
    construction, and a fresh instance would log the one failure twice.
    """
    try:
        return marker_condition(marker_node, abi3_upper_bound=abi3_upper_bound)
    except UnconvertableMarkerError as exc:
        exc.args = (f"cannot convert the marker in {entry!r} to a matchspec: {exc}",)
        raise
