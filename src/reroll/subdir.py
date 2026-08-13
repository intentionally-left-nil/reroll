"""The conda subdirs reroll ever emits a wheel record for."""

from __future__ import annotations

from enum import Enum


class CondaSubdir(Enum):
    """The six subdirs an arch-specific wheel record can target
    (docs/wheel_to_conda_dependencies.md's noarch decision).
    """

    LINUX_64 = "linux-64"
    LINUX_AARCH64 = "linux-aarch64"
    OSX_64 = "osx-64"
    OSX_ARM64 = "osx-arm64"
    WIN_64 = "win-64"
    WIN_ARM64 = "win-arm64"
