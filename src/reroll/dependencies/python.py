"""Conda `python`/`python_abi` MatchSpecs implied by a wheel's Python tag,
tightened against its `Requires-Python` metadata.
"""

from __future__ import annotations

from packaging.specifiers import SpecifierSet

from reroll.errors import PythonRangeMismatchError
from reroll.filename import WheelConfig
from reroll.filename.python_requirement import minor_range
from reroll.wheel_metadata import WheelMetadata

_PYTHON_ABI_FLOOR = 10
"""Lowest minor for which `python_abi` is emitted. Both conda-forge and main
ship a `python_abi` package with an interpreter-specific `_cp3XX` build tag
down to this floor; pinning `python` alone is used instead for any minor
below it (docs/wheel_to_conda_dependencies.md).
"""


def python_range(config: WheelConfig, metadata: WheelMetadata) -> tuple[int, int | None]:
    """The `(floor, ceiling)` Python 3 minor range implied by `config`'s
    wheel tag, tightened against `metadata.requires_python` if present --
    the shape `reroll.filename.python_requirement.minor_range` returns,
    and the same shape `reroll.dependencies.conditional_dependency`'s
    `python_version` parameter expects.

    Raises `PythonRangeMismatchError` if the two ranges don't intersect at
    all -- the caller should not generate a repodata record for this wheel.
    """
    filename_floor, filename_ceiling = minor_range(config.python.specifier)
    floor, ceiling = filename_floor, filename_ceiling
    if metadata.requires_python is not None:
        meta_floor, meta_ceiling = minor_range(SpecifierSet(metadata.requires_python))
        floor = max(floor, meta_floor)
        ceiling = _tighter_ceiling(ceiling, meta_ceiling)
        if ceiling is not None and floor >= ceiling:
            raise PythonRangeMismatchError(
                f"{config.normalized_pypi_name}: filename-implied python range "
                f"{_python_matchspec(filename_floor, filename_ceiling)} does not intersect "
                f"Requires-Python {metadata.requires_python!r}; no depends generated"
            )
    return floor, ceiling


def python_dependencies(config: WheelConfig, metadata: WheelMetadata) -> tuple[str, ...]:
    """The `python` MatchSpec implied by `python_range(config, metadata)`,
    plus a `python_abi` MatchSpec for a compiled CPython wheel targeting
    Python 3.10 or later (derived from `config` alone -- `Requires-Python`
    never affects it).

    Raises `PythonRangeMismatchError`, per `python_range`.
    """
    requirement = config.python
    floor, ceiling = python_range(config, metadata)
    depends = [_python_matchspec(floor, ceiling)]
    if requirement.exact and requirement.minor >= _PYTHON_ABI_FLOOR:
        depends.append(_python_abi_matchspec(requirement.minor, free_threaded=config.free_threaded))
    return tuple(depends)


def _tighter_ceiling(a: int | None, b: int | None) -> int | None:
    """The tighter of two optional, exclusive ceilings -- `None` (unbounded)
    only when both are `None`.
    """
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _python_matchspec(floor: int, ceiling: int | None) -> str:
    """`"python >=3.8"` for an unbounded floor; `"python >=3.7,<3.8.0a0"`
    for a bounded range -- the `0a0` suffix excludes `ceiling`'s own
    alpha/dev/rc pre-releases too, per conda recipe convention.
    """
    if ceiling is None:
        return f"python >=3.{floor}"
    return f"python >=3.{floor},<3.{ceiling}.0a0"


def _python_abi_matchspec(minor: int, *, free_threaded: bool) -> str:
    """`"python_abi 3.10.* *_cp310"` (or `*_cp310t` when free-threaded)."""
    build = f"cp3{minor}t" if free_threaded else f"cp3{minor}"
    return f"python_abi 3.{minor}.* *_{build}"
