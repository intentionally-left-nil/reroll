"""The conda subdirs reroll ever emits a wheel record for, and the mapping
from a wheel's platform tag to the subdir(s) it needs.
"""

from __future__ import annotations

from enum import Enum

from reroll.errors import UnsupportedPlatformError
from reroll.filename.enums import Arch, PlatformFamily
from reroll.filename.platform import classify_platform


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


def subdirs_for_platform(platform: str) -> tuple[CondaSubdir, ...]:
    """The `CondaSubdir`(s) that need a repodata record for a wheel's
    platform tag: two for a macOS `universal2` tag (`osx-64` and
    `osx-arm64`), one for any other architecture-specific tag, and none for
    a noarch (`"any"`) tag -- a pure Python wheel belongs in conda's
    `noarch` subdir, not one of these six.

    Raises `reroll.errors.UnsupportedPlatformError` for a platform tag
    reroll does not support.
    """
    info = classify_platform(platform)
    if info is None:
        raise UnsupportedPlatformError(f"unsupported platform tag: {platform!r}")
    if info.family is PlatformFamily.ANY:
        return ()
    return tuple(_SUBDIR_BY_FAMILY_ARCH[(info.family, arch)] for arch in info.archs)


_SUBDIR_BY_FAMILY_ARCH: dict[tuple[PlatformFamily, Arch], CondaSubdir] = {
    (PlatformFamily.MANYLINUX, Arch.X86_64): CondaSubdir.LINUX_64,
    (PlatformFamily.MANYLINUX, Arch.ARM64): CondaSubdir.LINUX_AARCH64,
    (PlatformFamily.MACOS, Arch.X86_64): CondaSubdir.OSX_64,
    (PlatformFamily.MACOS, Arch.ARM64): CondaSubdir.OSX_ARM64,
    (PlatformFamily.WINDOWS, Arch.X86_64): CondaSubdir.WIN_64,
    (PlatformFamily.WINDOWS, Arch.ARM64): CondaSubdir.WIN_ARM64,
}
