"""Emit a wheel's conda dependencies for every target its filename implies:
one per `CondaSubdir` its platform tag maps to, or a single noarch result
(possibly split across all `CondaSubdir`s if a dependency marker forces it).
"""

from __future__ import annotations

from reroll.dependencies.calculate_dependencies import WheelDependencies, calculate_dependencies
from reroll.errors import NeedsArchSplitError
from reroll.filename import WheelConfig
from reroll.name_mapping import NameMappers
from reroll.subdir import CondaSubdir, subdirs_for_platform
from reroll.wheel_metadata import WheelMetadata

__all__ = ["wheel_dependencies"]


def wheel_dependencies(
    config: WheelConfig,
    metadata: WheelMetadata,
    mappers: NameMappers,
    *,
    allow_pre: bool = False,
    abi3_upper_bound: str | None = None,
) -> dict[CondaSubdir | None, WheelDependencies]:
    """`config`/`metadata`'s conda dependencies, one `WheelDependencies`
    per target the resulting repodata record(s) need.

    `reroll.subdir.subdirs_for_platform` decides the targets from
    `config.platform`: a platform-specific wheel gets one entry per
    `CondaSubdir` its platform tag maps to (two, for a macOS `universal2`
    tag). A noarch wheel gets `{None: ...}`, unless `calculate_dependencies`
    raises `NeedsArchSplitError` for that noarch attempt
    (docs/wheel_to_conda_dependencies.md's noarch decision), in which case
    it is retried once per `CondaSubdir` instead.

    `abi3_upper_bound` is passed straight through to `calculate_dependencies`.

    Raises `reroll.errors.UnsupportedPlatformError` if `config.platform`
    is not one reroll supports.

    Raises `reroll.errors.PythonRangeMismatchError`,
    `reroll.errors.UnconvertableMarkerError`, or
    `reroll.errors.UnconvertableRequirementError`, per
    `calculate_dependencies` -- the caller should not generate a repodata
    record for this wheel.
    """
    subdirs = subdirs_for_platform(config.platform)
    if not subdirs:
        try:
            noarch = calculate_dependencies(
                config,
                metadata,
                mappers,
                subdir=None,
                allow_pre=allow_pre,
                abi3_upper_bound=abi3_upper_bound,
            )
        except NeedsArchSplitError:
            return {
                subdir: calculate_dependencies(
                    config,
                    metadata,
                    mappers,
                    subdir=subdir,
                    allow_pre=allow_pre,
                    abi3_upper_bound=abi3_upper_bound,
                )
                for subdir in CondaSubdir
            }
        return {None: noarch}
    return {
        subdir: calculate_dependencies(
            config,
            metadata,
            mappers,
            subdir=subdir,
            allow_pre=allow_pre,
            abi3_upper_bound=abi3_upper_bound,
        )
        for subdir in subdirs
    }
