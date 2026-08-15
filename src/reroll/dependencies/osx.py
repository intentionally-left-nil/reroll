"""Conda `__osx` MatchSpec implied by a macOS wheel's platform tag."""

from __future__ import annotations

from reroll.filename import PlatformFamily, WheelConfig


def osx_dependency(config: WheelConfig) -> str | None:
    """The `__osx` MatchSpec `config`'s macOS deployment-target floor
    implies, e.g. `"__osx >=10.9"` for `macosx_10_9_x86_64`, already
    including the arm64/11.0 clamp (`reroll.filename.wheel_config.
    WheelConfig.platform_version`). `None` for any other platform family --
    a macOS wheel's floor is a property of the wheel's own platform tag,
    not of any particular `CondaSubdir` a caller is generating dependencies
    for.
    """
    match config.platform_family, config.platform_version:
        case PlatformFamily.MACOS, (major, minor):
            return f"__osx >={major}.{minor}"
        case _:
            return None
