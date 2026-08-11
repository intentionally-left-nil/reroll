"""Compute a wheel's conda `depends`/`extra_depends` MatchSpecs from its
parsed configuration.
"""

from __future__ import annotations

from typing import NamedTuple

from reroll.dependencies.convert_dependency import Unsupported, convert_dependency
from reroll.dependencies.extras import extra_marker_entry
from reroll.dependencies.python import python_dependencies
from reroll.dependencies.requires_dist import strip_interpreter_requirements
from reroll.filename import WheelConfig
from reroll.name_mapping import NameMappers
from reroll.wheel_metadata import WheelMetadata

__all__ = ["WheelDependencies", "wheel_dependencies"]


class WheelDependencies(NamedTuple):
    """A wheel's converted conda dependencies: `depends`, the MatchSpecs
    every install of the package needs, and `extra_depends`, the
    additional MatchSpecs needed per extra (keyed by normalized extra
    name, `reroll.dependencies.extras.extra_marker_entry`) only when that
    extra is requested.
    """

    depends: tuple[str, ...]
    extra_depends: dict[str, tuple[str, ...]]


def wheel_dependencies(
    config: WheelConfig,
    metadata: WheelMetadata,
    mappers: NameMappers,
    *,
    allow_pre: bool = False,
) -> WheelDependencies | None:
    """The conda dependencies implied by `config` and `metadata`.

    `depends` is `metadata.requires_dist` (after stripping bare
    interpreter requirements,
    `reroll.dependencies.requires_dist.strip_interpreter_requirements`,
    and pulling out per-extra entries into `extra_depends`), followed by
    the `python`/`python_abi` requirements
    (`reroll.dependencies.python.python_dependencies`) -- matching the
    dependencies-then-`python` field order real conda-pypi output uses.
    `extra_depends` maps each extra name to the MatchSpecs a
    `Requires-Dist` entry marked with a bare `extra == "name"` clause
    contributes to it, in declaration order.

    `None` if `config`'s filename-implied Python range and
    `metadata.requires_python` don't intersect, or if a `Requires-Dist`
    entry this function does convert turns out to be unrepresentable in
    conda (`reroll.dependencies.convert_dependency.convert_dependency`) --
    either way, the caller should not generate a repodata record for this
    wheel.

    A `Requires-Dist` entry with extras of its own, or a marker other than
    a bare `extra == "name"` clause, is left out of both `depends` and
    `extra_depends` entirely for now rather than converted or rejected:
    conditional dependencies and extras-on-a-dependency conversion are a
    future addition, not yet implemented.
    """
    python_deps = python_dependencies(config, metadata)
    if python_deps is None:
        return None
    depends: list[str] = []
    extra_depends: dict[str, list[str]] = {}
    for entry in strip_interpreter_requirements(metadata.requires_dist):
        extra_name, converted_entry = extra_marker_entry(entry)
        result = convert_dependency(converted_entry, mappers, allow_pre=allow_pre)
        if isinstance(result, Unsupported):
            continue
        if result is None:
            return None
        if extra_name is None:
            depends.append(result)
        else:
            extra_depends.setdefault(extra_name, []).append(result)
    return WheelDependencies(
        depends=tuple(depends) + python_deps,
        extra_depends={name: tuple(deps) for name, deps in extra_depends.items()},
    )
