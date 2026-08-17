"""Conda `python`/`python_abi` MatchSpecs implied by a wheel's Python tag,
tightened against its `Requires-Python` metadata.
"""

from __future__ import annotations

from reroll.dependencies.matchspec_specifier import specifier_to_matchspec
from reroll.dependencies.python_version_dependency import HalfOpenRange, python_version_dependency
from reroll.errors import PythonRangeMismatchError, UnconvertableRequirementError
from reroll.filename import WheelConfig
from reroll.wheel_metadata import WheelMetadata

_PYTHON_ABI_FLOOR = 10
"""Lowest minor for which `python_abi` is emitted. Both conda-forge and main
ship a `python_abi` package with an interpreter-specific `_cp3XX` build tag
down to this floor; pinning `python` alone is used instead for any minor
below it (docs/wheel_to_conda_dependencies.md).
"""


def python_dependencies(
    config: WheelConfig, metadata: WheelMetadata, *, allow_pre: bool = False
) -> tuple[str, ...]:
    """The `python` MatchSpec for `config`/`metadata`'s combined Python
    version range (`python_version_dependency`), plus a `python_abi`
    MatchSpec for a compiled CPython wheel targeting Python 3.10 or later
    (derived from `config` alone -- `Requires-Python` never affects it).

    `allow_pre` governs a pre-release bound in the combined range the same
    way it governs any other dependency's version -- see
    `reroll.dependencies.matchspec_specifier.specifier_to_matchspec`.

    Raises `PythonRangeMismatchError` if `config`'s filename-implied Python
    range and `metadata.requires_python` don't intersect at all.
    """
    requirement = config.python
    range_or_spec = _combined_range(config, metadata)
    depends = [_range_matchspec(range_or_spec, config.normalized_pypi_name, allow_pre=allow_pre)]
    if requirement.exact and requirement.minor >= _PYTHON_ABI_FLOOR:
        depends.append(_python_abi_matchspec(requirement.minor, free_threaded=config.free_threaded))
    return tuple(depends)


def exact_minor(config: WheelConfig, metadata: WheelMetadata) -> int | None:
    """The single Python 3 minor `config`/`metadata`'s combined Python
    version range (`python_version_dependency`) is guaranteed to fall
    within, or `None` if it isn't restricted to exactly one -- an
    unbounded range, a range spanning more than one minor, or a range that
    didn't go through `python_version_dependency`'s Simplified-Requires-Python
    algorithm at all (docs/wheel_to_conda_dependencies.md's `python_version`
    conditional-marker rule).

    Raises `PythonRangeMismatchError`, per `python_dependencies`.
    """
    range_or_spec = _combined_range(config, metadata)
    if isinstance(range_or_spec, HalfOpenRange):
        return _range_exact_minor(range_or_spec)
    return None


def _combined_range(config: WheelConfig, metadata: WheelMetadata) -> HalfOpenRange | str:
    """`python_version_dependency(config.python, metadata.requires_python)`,
    with `config.normalized_pypi_name` folded into any
    `PythonRangeMismatchError`.

    Reraises the same instance rather than constructing a new one: that
    error already logged itself at construction, and a fresh instance
    would log the one failure twice.
    """
    try:
        return python_version_dependency(config.python, metadata.requires_python)
    except PythonRangeMismatchError as exc:
        exc.args = (f"{config.normalized_pypi_name}: {exc}",)
        raise


def _range_exact_minor(range_: HalfOpenRange) -> int | None:
    """The Python 3 minor `range_` (a `[lower, upper)` range) is entirely
    within, or `None` if it isn't -- either `range_.upper` shares
    `range_.lower`'s minor (both bounds fall in the same minor, however far
    apart), or `range_.upper` lands exactly on the very next minor's `.0`
    release (`range_.lower`'s own minor is then the only one the whole
    range can ever produce).
    """
    lower, upper = range_.lower, range_.upper
    if lower is None:
        raise AssertionError("unreachable: a combined HalfOpenRange's lower bound is never None")
    if upper is None or lower.major != 3:
        return None
    if (upper.major, upper.minor) == (lower.major, lower.minor):
        return lower.minor
    if (
        (upper.major, upper.minor) == (lower.major, lower.minor + 1)
        and upper.micro == 0
        and not upper.is_prerelease
        and upper.post is None
    ):
        return lower.minor
    return None


def _range_matchspec(range_or_spec: HalfOpenRange | str, pypi_name: str, *, allow_pre: bool) -> str:
    """`range_or_spec`'s `python` MatchSpec -- `range_or_spec` itself is
    already a plain PEP 440 specifier (the generic combining fallback), or
    is first rendered as one (`_range_specifier`) if it's a `HalfOpenRange`;
    either way, the whole version clause's conversion to MatchSpec syntax
    is delegated to `specifier_to_matchspec`.
    """
    specifier = range_or_spec if isinstance(range_or_spec, str) else _range_specifier(range_or_spec)
    try:
        clause = specifier_to_matchspec(specifier, allow_pre=allow_pre)
    except UnconvertableRequirementError as exc:
        exc.args = (f"cannot convert {pypi_name}'s Python version range {specifier!r}: {exc}",)
        raise
    return f"python {clause}"


def _range_specifier(range_: HalfOpenRange) -> str:
    """`range_`'s plain PEP 440 specifier string, per
    docs/wheel_to_conda_dependencies.md's "just use the tightened range
    as-is" -- `range_.lower` is never `None` (the filename side of the
    combination always contributes a concrete lower bound).
    """
    lower = range_.lower
    if lower is None:
        raise AssertionError("unreachable: a combined HalfOpenRange's lower bound is never None")
    if range_.upper is None:
        return f">={lower}"
    return f">={lower},<{range_.upper}"


def _python_abi_matchspec(minor: int, *, free_threaded: bool) -> str:
    """`"python_abi 3.10.* *_cp310"` (or `*_cp310t` when free-threaded)."""
    build = f"cp3{minor}t" if free_threaded else f"cp3{minor}"
    return f"python_abi 3.{minor}.* *_{build}"
