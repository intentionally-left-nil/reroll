"""Emit a wheel's conda dependencies for every target its filename implies:
a single noarch result, or one per `CondaSubdir` if a dependency marker
forces an arch split.
"""

from __future__ import annotations

from reroll.dependencies.calculate_dependencies import WheelDependencies, calculate_dependencies
from reroll.errors import NeedsArchSplitError
from reroll.filename import PlatformFamily, WheelConfig
from reroll.name_mapping import NameMappers
from reroll.subdir import CondaSubdir
from reroll.wheel_metadata import WheelMetadata

__all__ = ["wheel_dependencies"]


def wheel_dependencies(
    config: WheelConfig,
    metadata: WheelMetadata,
    mappers: NameMappers,
    *,
    allow_pre: bool = False,
) -> dict[CondaSubdir | None, WheelDependencies]:
    """`config`/`metadata`'s conda dependencies, one `WheelDependencies`
    per target the resulting repodata record(s) need: `{None: ...}` for a
    noarch wheel whose dependencies need no arch split, or one entry per
    `CondaSubdir` if `calculate_dependencies` raises `NeedsArchSplitError`
    for the noarch attempt (docs/wheel_to_conda_dependencies.md's noarch
    decision).

    Raises `NotImplementedError` for a wheel whose filename is already
    platform-specific (`config.platform_family` is not `PlatformFamily.ANY`)
    -- mapping its platform tag to a `CondaSubdir` is not implemented yet.

    Raises `reroll.errors.PythonRangeMismatchError`,
    `reroll.errors.UnconvertableMarkerError`, or
    `reroll.errors.UnconvertableRequirementError`, per
    `calculate_dependencies` -- the caller should not generate a repodata
    record for this wheel.
    """
    if config.platform_family is not PlatformFamily.ANY:
        # TODO: map a platform-specific wheel's platform/arch tag to its
        # one CondaSubdir once that conversion exists.
        raise NotImplementedError(
            f"platform-specific wheel dependencies are not yet implemented: "
            f"{config.platform!r} has no CondaSubdir mapping"
        )
    try:
        noarch = calculate_dependencies(config, metadata, mappers, subdir=None, allow_pre=allow_pre)
    except NeedsArchSplitError:
        return {
            subdir: calculate_dependencies(
                config, metadata, mappers, subdir=subdir, allow_pre=allow_pre
            )
            for subdir in CondaSubdir
        }
    return {None: noarch}
