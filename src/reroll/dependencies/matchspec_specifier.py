"""Convert a PEP 440 specifier set into its conda MatchSpec version clause.

Implements docs/matchspec.md's Operator conversion table.
"""

from __future__ import annotations

from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from reroll.dependencies.version_format import format_version
from reroll.errors import UnconvertableRequirementError


def specifier_to_matchspec(specifiers: str | SpecifierSet, *, allow_pre: bool = False) -> str:
    """The conda MatchSpec version clause for `specifiers` -- e.g.
    `">=1.0,<2.0"` for two specifiers combined, or `""` if `specifiers` is
    empty. A string `specifiers` is parsed the same way a `SpecifierSet`
    already is (`SpecifierSet(specifiers)`), and propagates
    `packaging.specifiers.InvalidSpecifier` if it doesn't parse.

    Multiple clauses are joined in canonical order: lower bounds
    (`>=`/`>`) first, then upper bounds (`<=`/`<`), then pins
    (`==`/`~=`/`===`), then exclusions (`!=`) -- ties within the same
    operator sort alphabetically by the clause's own string spelling.

    Raises `UnconvertableRequirementError` if any specifier has a local
    version label, or is a pre-release version and `allow_pre` is unset.
    """
    if isinstance(specifiers, str):
        specifiers = SpecifierSet(specifiers)
    parts: list[str] = []
    for specifier in sorted(specifiers, key=_sort_key):
        parts.extend(_convert_specifier(specifier, allow_pre=allow_pre))
    return ",".join(parts)


_OPERATOR_RANK = {
    ">=": 0,
    ">": 0,
    "<=": 1,
    "<": 1,
    "==": 2,
    "~=": 2,
    "===": 2,
    "!=": 3,
}
"""Canonical clause order for `specifier_to_matchspec`: lower bounds, then
upper bounds, then pins, then exclusions.
"""


def _sort_key(specifier: Specifier) -> tuple[int, str]:
    return (_OPERATOR_RANK[specifier.operator], str(specifier))


def _convert_specifier(specifier: Specifier, *, allow_pre: bool) -> list[str]:
    """One PEP 440 specifier's contribution to a MatchSpec's version
    clause -- most contribute a single `<op><version>` clause, but `~=`
    (deprecated per CEP-29) expands into an explicit `>=`/`<` pair.

    Raises `UnconvertableRequirementError` if `specifier` has a local
    version label, or is a pre-release version with `allow_pre` unset.
    """
    if specifier.operator == "~=":
        return _expand_compatible_release(specifier, allow_pre=allow_pre)
    if specifier.operator in ("==", "!=") and specifier.version.endswith(".*"):
        if specifier.operator == "!=":
            return [f"!={specifier.version}"]
        return [f"={specifier.version[:-2]}"]
    if specifier.operator in ("<", ">"):
        return _convert_exclusive_comparator(specifier, allow_pre=allow_pre)
    operator = "==" if specifier.operator == "===" else specifier.operator
    try:
        version = Version(specifier.version)
    except InvalidVersion:
        return [f"{operator}{specifier.version}"]
    _reject_unsupported_version(version, specifier, allow_pre=allow_pre)
    return [f"{operator}{format_version(version)}"]


def _expand_compatible_release(specifier: Specifier, *, allow_pre: bool) -> list[str]:
    """`~=X.Y.Z`'s expansion into `>=X.Y.Z,<X.(Y+1).0a0` (docs/matchspec.md's
    Operator conversion) -- the version literal sans its last release
    segment, bumped by one, anchored at `.0a0` so a pre-release of that
    boundary lands on the lower side of the range.
    """
    version = Version(specifier.version)
    _reject_unsupported_version(version, specifier, allow_pre=allow_pre)
    prefix = version.release[:-1]
    bumped = prefix[:-1] + (prefix[-1] + 1,)
    epoch_prefix = f"{version.epoch}!" if version.epoch else ""
    upper_release = ".".join(str(segment) for segment in bumped)
    return [f">={format_version(version)}", f"<{epoch_prefix}{upper_release}.0a0"]


def _convert_exclusive_comparator(specifier: Specifier, *, allow_pre: bool) -> list[str]:
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

    `specifier.version` is always a valid PEP 440 version here: unlike
    `===`, `packaging.specifiers.Specifier` itself rejects a non-PEP-440
    version for `<`/`>` before this function ever sees it.
    """
    version = Version(specifier.version)
    _reject_unsupported_version(version, specifier, allow_pre=allow_pre)
    formatted = format_version(version)
    if specifier.operator == "<":
        if version.is_prerelease:
            return [f"<{formatted}"]
        return [f"<{formatted}a0"]
    if version.dev is not None or version.post is not None:
        return [f">{formatted}"]
    return [f">{formatted}", f"!={formatted}.post*"]


def _reject_unsupported_version(version: Version, specifier: Specifier, *, allow_pre: bool) -> None:
    if version.local is not None:
        raise UnconvertableRequirementError(
            f"cannot convert specifier {str(specifier)!r}: it has a local version label"
        )
    if version.is_prerelease and not allow_pre:
        raise UnconvertableRequirementError(
            f"cannot convert specifier {str(specifier)!r}: it is a pre-release and "
            "allow_pre is unset"
        )
