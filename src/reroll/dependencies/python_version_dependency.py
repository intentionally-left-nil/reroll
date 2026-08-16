"""Combine a wheel's filename-implied Python requirement with its
`Requires-Python` metadata into a single Python version dependency, per
docs/wheel_to_conda_dependencies.md#determining-the-python-version-dependency.
"""

from __future__ import annotations

from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict

from reroll.errors import PythonRangeMismatchError
from reroll.filename.py_version import PyVersion
from reroll.filename.python_requirement import PythonRequirement
from reroll.lenient_parser import parse_lenient_version_specifiers


class HalfOpenRange(BaseModel):
    """A `[lower, upper)` Python version range -- either bound may be
    `None` for unbounded, matching the `[3.11, None)` notation
    docs/wheel_to_conda_dependencies.md uses.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lower: PyVersion | None
    upper: PyVersion | None


def python_version_dependency(
    filename: PythonRequirement, requires_python: str | None
) -> str | HalfOpenRange:
    """The Python version dependency implied by combining `filename` (the
    floor or exact minor a wheel's tag requires) with `requires_python`
    (a wheel's raw `Requires-Python` metadata value, or `None` if absent).

    If `requires_python` is `None`, or is a "Simplified Requires-Python"
    specifier (docs/wheel_to_conda_dependencies.md's reduced grammar --
    a bare `>=`/`<`/`~=`, an `==` clause pinning or wildcarding a major or
    major.minor, or a two-clause `<`+`>=` half-open range), returns the
    combined range as a `HalfOpenRange`.

    Otherwise, returns the generic combination: `filename` converted to a
    bare specifier (`>=major.minor` for a floor, `~=major.minor` for an
    exact minor) followed by a comma and `requires_python` itself.

    Raises `InvalidVersionSpecifierError` if `requires_python` cannot be
    parsed as a PEP 440 specifier set even after every lenient fixup
    (`reroll.lenient_parser`) has been tried.

    Raises `PythonRangeMismatchError` if `requires_python` is a Simplified
    Requires-Python and its combined range with `filename`'s is disjoint
    (including a range whose lower bound equals its upper bound). A
    non-Simplified `requires_python` never raises this, however
    contradictory the resulting generic combination looks.
    """
    if requires_python is None:
        return _filename_range(filename)
    specifiers = parse_lenient_version_specifiers(requires_python)
    simplified = _simplified_range(specifiers)
    if simplified is None:
        return _generic_combine(filename, specifiers)
    combined = _combine(_filename_range(filename), simplified)
    if (
        combined.lower is not None
        and combined.upper is not None
        and combined.lower >= combined.upper
    ):
        raise PythonRangeMismatchError(
            f"filename-implied python range {_filename_generic_specifier(filename)!r} "
            f"does not intersect Requires-Python {requires_python!r}"
        )
    return combined


def _filename_range(filename: PythonRequirement) -> HalfOpenRange:
    """The `HalfOpenRange` `filename` implies on its own: `[3.13, 3.14)`
    for an exact minor, `[3.13, None)` for a floor.
    """
    lower = Version(filename.version)
    upper = Version(f"3.{filename.minor + 1}") if filename.exact else None
    return HalfOpenRange(lower=lower, upper=upper)


def _filename_generic_specifier(filename: PythonRequirement) -> str:
    """`filename` converted to a bare PEP 440 specifier for the generic
    combining path: `">=3.13"` for a floor, `"~=3.13"` for an exact minor.
    """
    return f"~={filename.version}" if filename.exact else f">={filename.version}"


def _generic_combine(filename: PythonRequirement, specifiers: SpecifierSet) -> str:
    """`filename`'s bare specifier, comma-joined with `specifiers` -- or
    just the bare specifier on its own if `specifiers` has no clauses at
    all (an empty `Requires-Python` constrains nothing).
    """
    filename_spec = _filename_generic_specifier(filename)
    requires_python = str(specifiers)
    if not requires_python:
        return filename_spec
    return f"{filename_spec},{requires_python}"


def _combine(a: HalfOpenRange, b: HalfOpenRange) -> HalfOpenRange:
    """The intersection of two `HalfOpenRange`s: the higher of their two
    lower bounds, and the lower of their two upper bounds -- `None` on
    either side only loses to a concrete bound, per
    docs/wheel_to_conda_dependencies.md's "where None always loses".
    """
    return HalfOpenRange(
        lower=_tighter(a.lower, b.lower, keep_higher=True),
        upper=_tighter(a.upper, b.upper, keep_higher=False),
    )


def _tighter(a: Version | None, b: Version | None, *, keep_higher: bool) -> Version | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b) if keep_higher else min(a, b)


def _simplified_range(specifiers: SpecifierSet) -> HalfOpenRange | None:
    """The `HalfOpenRange` `specifiers` implies if it is a Simplified
    Requires-Python (docs/wheel_to_conda_dependencies.md's reduced
    grammar), or `None` if it doesn't fit that grammar at all.
    """
    clauses = tuple(specifiers)
    if len(clauses) == 1:
        return _single_clause_range(clauses[0])
    if len(clauses) == 2:
        by_operator = {clause.operator: clause for clause in clauses}
        if set(by_operator) == {"<", ">="}:
            return HalfOpenRange(
                lower=Version(by_operator[">="].version),
                upper=Version(by_operator["<"].version),
            )
    return None


def _single_clause_range(clause: Specifier) -> HalfOpenRange | None:
    """The `HalfOpenRange` a single specifier clause implies, for exactly
    the operators docs/wheel_to_conda_dependencies.md's Simplified
    Requires-Python grammar allows as a bare, single-clause specifier --
    `None` for every other operator (including `==` with a value that
    isn't major/major.minor, wildcarded or not).
    """
    match clause.operator:
        case ">=":
            return HalfOpenRange(lower=Version(clause.version), upper=None)
        case "<":
            return HalfOpenRange(lower=None, upper=Version(clause.version))
        case "~=":
            return _compatible_release_range(Version(clause.version))
        case "==":
            return _equality_range(clause.version)
        case _:
            return None


def _equality_range(version: str) -> HalfOpenRange | None:
    """The `HalfOpenRange` an `==` clause's value implies: a major-only
    wildcard (`3.*` -> `[3, None)`), or a bare or wildcarded major.minor
    (`3.11`/`3.11.*`, both pinned directly to that minor: `[3.11, 3.12)`).

    `None` for anything else -- determined entirely from the parsed
    `Version`'s own release length and the absence of any pre/post/dev/
    local/epoch component, e.g. `3.11.2`, a bare major with no dot,
    `3.11rc1`, or a 3+ segment wildcard.
    """
    is_wildcard = version.endswith(".*")
    parsed = Version(version.removesuffix(".*") if is_wildcard else version)
    if (
        parsed.epoch
        or parsed.pre is not None
        or parsed.post is not None
        or parsed.dev is not None
        or parsed.local is not None
    ):
        return None
    release = parsed.release
    if is_wildcard and len(release) == 1:
        return HalfOpenRange(lower=parsed, upper=None)
    if len(release) == 2:
        return _minor_pin_range(parsed)
    return None


def _minor_pin_range(version: Version) -> HalfOpenRange:
    """The `HalfOpenRange` a bare major.minor `Version` pins: itself as
    the lower bound, and its minor release segment incremented by one as
    the upper bound -- `Version("3.11")` becomes `[3.11, 3.12)`.
    """
    upper_release = (*version.release[:-1], version.release[-1] + 1)
    return HalfOpenRange(lower=version, upper=Version(".".join(map(str, upper_release))))


def _compatible_release_range(version: Version) -> HalfOpenRange:
    """The `HalfOpenRange` a `~=` clause's value implies: the value
    itself as the lower bound, and its release segments with the last one
    dropped and the new last one incremented as the upper bound --
    `~=3.11.2` becomes `[3.11.2, 3.12)`; `~=3.11` becomes `[3.11, 4)`, per
    PEP 440's compatible-release clause always dropping exactly one
    release segment regardless of how many are given.
    """
    upper_release = list(version.release[:-1])
    upper_release[-1] += 1
    return HalfOpenRange(lower=version, upper=Version(".".join(map(str, upper_release))))
