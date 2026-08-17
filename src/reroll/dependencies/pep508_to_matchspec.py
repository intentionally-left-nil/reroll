"""Convert a PEP 508 requirement string into its conda MatchSpec equivalent.

See docs/matchspec.md.
"""

from __future__ import annotations

from markerpry import Node, parse_marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name
from rattler import MatchSpec
from rattler.exceptions import InvalidMatchSpecError

from reroll.dependencies.marker_conversion import UnconvertableMarkerError, marker_condition
from reroll.dependencies.matchspec_specifier import specifier_to_matchspec
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
    version_clause = _specifier_to_matchspec(requirement.specifier, entry, allow_pre=allow_pre)
    extras = {canonicalize_name(extra) for extra in requirement.extras}
    if extras:
        _reject_invalid_extras(extras, entry)

    brackets: list[str] = []
    if extras:
        brackets.append(_format_extras(extras))
    if marker_node is not None:
        condition = _marker_condition(marker_node, entry, abi3_upper_bound=abi3_upper_bound)
        brackets.append(f'when="{condition}"')

    name_and_version = conda_name if not version_clause else f"{conda_name} {version_clause}"
    bracket_suffix = f"[{','.join(brackets)}]" if brackets else ""
    matchspec = f"{name_and_version}{bracket_suffix}"

    try:
        MatchSpec(matchspec)
    except InvalidMatchSpecError as exc:
        raise UnconvertableRequirementError(
            f"{matchspec!r}, converted from {entry!r}, is not a valid matchspec"
        ) from exc
    return matchspec


def _specifier_to_matchspec(specifiers: SpecifierSet, entry: str, *, allow_pre: bool) -> str:
    """`specifier_to_matchspec(specifiers, allow_pre=allow_pre)`, with
    `entry` folded into the message on failure.

    Reraises the same `UnconvertableRequirementError` instance rather than
    constructing a new one: that error already logged itself at
    construction, and a fresh instance would log the one failure twice.
    """
    try:
        return specifier_to_matchspec(specifiers, allow_pre=allow_pre)
    except UnconvertableRequirementError as exc:
        exc.args = (f"cannot convert {entry!r}: {exc}",)
        raise


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
