"""Convert a PEP 508 requirement string into its conda MatchSpec equivalent.

See docs/matchspec.md.
"""

from __future__ import annotations

from markerpry import Node, parse_marker
from packaging.requirements import Requirement
from packaging.specifiers import Specifier, SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name
from packaging.version import InvalidVersion, Version
from rattler import MatchSpec
from rattler.exceptions import InvalidMatchSpecError

from reroll.dependencies.marker_conversion import UnconvertableMarkerError, marker_condition
from reroll.dependencies.version_format import format_version
from reroll.errors import UnconvertableRequirementError
from reroll.name_mapping import NameMappers, map_name

_MAX_EXTRA_LENGTH = 64
"""CEP-29's `extras` bracket key limits each extra name to 64 characters."""


def pep508_to_matchspec(
    entry: str,
    mappers: NameMappers,
    *,
    allow_pre: bool = False,
) -> str:
    """The conda MatchSpec for `entry`, a single PEP 508 requirement string
    (a `Requires-Dist` entry).

    Raises `UnconvertableRequirementError` for anything that can't be
    converted: a direct URL reference (`name @ url`); a local version label
    (`1.0+local`); a pre-release version, unless `allow_pre` is set; an
    extra name (`name[extra]`) longer than 64 characters once normalized; a
    marker referring to `extra` at all (that's a different mechanism --
    grouping a dependency into one of the *current* package's own extras --
    than an environment marker, and combining it with a real environment
    condition isn't implemented here); or an assembled MatchSpec string
    that fails py-rattler's own validation. Also raises
    `reroll.errors.UnconvertableMarkerError` for a marker using a construct
    that has no matchspec equivalent, and
    `reroll.errors.UnresolvedCondaNameError` for a PyPI name with no
    resolvable conda name.
    """
    requirement = Requirement(entry)
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
        brackets.append(f'when="{_marker_condition(marker_node, entry)}"')

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


def _marker_condition(marker_node: Node, entry: str) -> str:
    """`marker_condition(marker_node)`, with `entry` folded into the
    message on failure.

    Reraises the same `UnconvertableMarkerError` instance rather than
    constructing a new one: that error already logged itself at
    construction, and a fresh instance would log the one failure twice.
    """
    try:
        return marker_condition(marker_node)
    except UnconvertableMarkerError as exc:
        exc.args = (f"cannot convert the marker in {entry!r} to a matchspec: {exc}",)
        raise
