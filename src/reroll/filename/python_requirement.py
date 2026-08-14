"""The Python version constraint a wheel tag implies."""

from __future__ import annotations

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict

from reroll.errors import InvalidPythonRequirementRangeError

_MINOR_SCAN_CEILING = 100
"""Highest Python 3 minor `minor_range` scans up to. Generous enough that no
real `Requires-Python` upper bound falls beyond it; a range that's still
open at this minor is treated as unbounded rather than genuinely capped
here.
"""


class PythonRequirement(BaseModel):
    """The Python constraint a wheel tag implies.

    Only two shapes exist -- a floor or a pinned minor -- and no filename can
    express a patch version, so `minor: int` + `exact: bool` covers the
    entire domain without being able to represent an impossible state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    minor: int
    exact: bool

    @property
    def version(self) -> str:
        """`"3.13"` -- the only place `"3."` appears."""
        return f"3.{self.minor}"

    @property
    def specifier(self) -> SpecifierSet:
        """`"==3.13.*"` if pinned, else `">=3.13,<4"` -- the only place
        `"<4"` appears. A plain `@property`, not `@computed_field`:
        `SpecifierSet` has no pydantic core schema, so a `computed_field`
        return type would fail to generate one.
        """
        if self.exact:
            return SpecifierSet(f"=={self.version}.*")
        return SpecifierSet(f">={self.version},<4")

    @classmethod
    def floor(cls, minor: int) -> PythonRequirement:
        return cls(minor=minor, exact=False)

    @classmethod
    def pinned(cls, minor: int) -> PythonRequirement:
        return cls(minor=minor, exact=True)


def minor_range(specifiers: SpecifierSet) -> tuple[int, int | None]:
    """The contiguous Python 3 `(floor, ceiling)` minor range `specifiers`
    implies, at minor granularity (a patch-level exclusion like `!=3.9.2`
    doesn't remove minor 9, since other 3.9.x releases still satisfy it).
    A minor counts as satisfied if *any* of its patch releases does, even
    if a bound (e.g. `>=3.9.16`) falls strictly inside it rather than at
    its `.0` release. `ceiling` is the first non-satisfying minor, or
    `None` for an open-ended upper bound -- so a
    `PythonRequirement.floor(8).specifier` maps to `(8, None)` and a
    `.pinned(13).specifier` maps to `(13, 14)`.

    Raises `InvalidPythonRequirementRangeError` if `specifiers` matches no
    Python 3 minor at all, or a non-contiguous set of them (e.g. `!=3.9.*`
    carving a hole out of an otherwise-open range) -- no real
    `Requires-Python` value takes either shape, and neither is
    representable as a single range.
    """
    satisfied = [m for m in range(_MINOR_SCAN_CEILING) if _minor_is_satisfied(specifiers, m)]
    if not satisfied or satisfied != list(range(satisfied[0], satisfied[-1] + 1)):
        raise InvalidPythonRequirementRangeError(
            f"not a contiguous Python 3.x minor range: {specifiers}"
        )
    floor, top = satisfied[0], satisfied[-1]
    ceiling = None if top == _MINOR_SCAN_CEILING - 1 else top + 1
    return floor, ceiling


def _minor_is_satisfied(specifiers: SpecifierSet, minor: int) -> bool:
    """Whether any `3.<minor>.x` release satisfies `specifiers` -- probing
    only `3.<minor>.0` would miss a minor whose only satisfying releases
    sit on the far side of a mid-minor bound (e.g. `>=3.9.16` doesn't
    satisfy at `3.9.0`, but `3.9.16` onward does).
    """
    return any(
        specifiers.contains(f"3.{minor}.{micro}") for micro in _probe_micros(specifiers, minor)
    )


def _probe_micros(specifiers: SpecifierSet, minor: int) -> set[int]:
    """Candidate micro numbers worth probing for `minor`: `0`, plus the
    micro of every clause anchored at this exact minor and its immediate
    neighbors -- enough to straddle either side of any inclusive or
    exclusive boundary a `>=`/`>`/`<=`/`<`/`==`/`!=`/`~=` clause can place
    inside a minor.
    """
    micros = {0}
    for clause in specifiers:
        anchor = _anchor_version(clause.version)
        if anchor is None or len(anchor.release) < 2 or anchor.release[:2] != (3, minor):
            continue
        micro = anchor.release[2] if len(anchor.release) > 2 else 0
        micros.update({micro - 1, micro, micro + 1})
    return {m for m in micros if m >= 0}


def _anchor_version(version: str) -> Version | None:
    """The concrete `Version` a specifier clause's `version` string is
    anchored at, or `None` if it doesn't parse as one (e.g. a legacy
    `===` literal) -- stripping a trailing wildcard (`"3.9.*"` ->
    `"3.9"`) first, since `Version` itself rejects the `.*` suffix.
    """
    try:
        return Version(version.removesuffix(".*"))
    except InvalidVersion:
        return None
