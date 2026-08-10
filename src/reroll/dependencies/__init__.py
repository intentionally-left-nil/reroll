"""Compute a wheel's conda `depends` MatchSpecs from its parsed configuration."""

from __future__ import annotations

from reroll.dependencies.convert_dependency import Unsupported, convert_dependency
from reroll.dependencies.python import python_dependencies
from reroll.dependencies.requires_dist import strip_interpreter_requirements
from reroll.filename import WheelConfig
from reroll.name_mapping import NameMappers
from reroll.wheel_metadata import WheelMetadata

__all__ = ["wheel_dependencies"]


def wheel_dependencies(
    config: WheelConfig,
    metadata: WheelMetadata,
    mappers: NameMappers,
    *,
    allow_pre: bool = False,
) -> tuple[str, ...] | None:
    """The conda `depends` MatchSpecs implied by `config` and `metadata`:
    `metadata.requires_dist` (after stripping bare interpreter requirements,
    `reroll.dependencies.requires_dist.strip_interpreter_requirements`),
    followed by the `python`/`python_abi` requirements
    (`reroll.dependencies.python.python_dependencies`) -- matching the
    dependencies-then-`python` field order real conda-pypi output uses.

    `None` if `config`'s filename-implied Python range and
    `metadata.requires_python` don't intersect, or if a `Requires-Dist`
    entry this function does convert turns out to be unrepresentable in
    conda (`reroll.dependencies.convert_dependency.convert_dependency`) --
    either way, the caller should not generate a repodata record for this
    wheel.

    A `Requires-Dist` entry with extras or a marker is left out of
    `depends` entirely for now rather than converted or rejected: marker
    and extras conversion is a future addition, not yet implemented.
    """
    python_deps = python_dependencies(config, metadata)
    if python_deps is None:
        return None
    converted: list[str] = []
    for entry in strip_interpreter_requirements(metadata.requires_dist):
        result = convert_dependency(entry, mappers, allow_pre=allow_pre)
        if isinstance(result, Unsupported):
            continue
        if result is None:
            return None
        converted.append(result)
    return tuple(converted) + python_deps
