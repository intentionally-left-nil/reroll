"""Conda `python`/`python_abi` MatchSpecs implied by a wheel's Python tag,
tightened against its `Requires-Python` metadata.
"""

from __future__ import annotations

import logging

from packaging.specifiers import SpecifierSet

from reroll.filename import WheelConfig
from reroll.filename.python_requirement import minor_range
from reroll.wheel_metadata import WheelMetadata

_PYTHON_ABI_FLOOR = 13
"""Lowest minor for which `python_abi` is emitted. conda-forge and main both
ship `python_abi` below this, but main's earlier builds lack the
interpreter-specific `_cp3XX` build tag, so pinning `python` alone is used
instead for any minor below this floor (docs/wheel_to_conda_dependencies.md).
"""

_logger = logging.getLogger(__name__)


def python_dependencies(config: WheelConfig, metadata: WheelMetadata) -> tuple[str, ...] | None:
    """The `python` MatchSpec implied by `config`'s wheel tag, tightened
    against `metadata.requires_python` if present, plus a `python_abi`
    MatchSpec for a compiled CPython wheel targeting Python 3.13 or later
    (derived from `config` alone -- `Requires-Python` never affects it).

    `None` if the two ranges don't intersect at all -- the caller should not
    generate a repodata record for this wheel (a `WARNING` log explains why).
    """
    requirement = config.python
    filename_floor, filename_ceiling = minor_range(requirement.specifier)
    floor, ceiling = filename_floor, filename_ceiling
    if metadata.requires_python is not None:
        meta_floor, meta_ceiling = minor_range(SpecifierSet(metadata.requires_python))
        floor = max(floor, meta_floor)
        ceiling = _tighter_ceiling(ceiling, meta_ceiling)
        if ceiling is not None and floor >= ceiling:
            _logger.warning(
                "%s: filename-implied python range %s does not intersect "
                "Requires-Python %r; no depends generated",
                config.normalized_pypi_name,
                _python_matchspec(filename_floor, filename_ceiling),
                metadata.requires_python,
            )
            return None

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
    """`"python_abi 3.13.* *_cp313"` (or `*_cp313t` when free-threaded)."""
    build = f"cp3{minor}t" if free_threaded else f"cp3{minor}"
    return f"python_abi 3.{minor}.* *_{build}"
