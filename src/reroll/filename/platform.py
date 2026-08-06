"""Parse a wheel filename's platform tag into family, version floor, and
supported architectures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from reroll.filename.enums import Arch, PlatformFamily


@dataclass(frozen=True)
class PlatformInfo:
    family: PlatformFamily
    version: tuple[int, int] | None  # raw tag version; None for ANY/WINDOWS
    archs: tuple[Arch, ...]  # architectures this platform tag supports


def classify_platform(platform: str) -> PlatformInfo | None:
    """Parse a platform tag into its family, raw version, and supported
    architectures. Returns `None` for anything reroll does not support.
    """
    if platform == "any":
        return PlatformInfo(PlatformFamily.ANY, None, ())

    match = _MANYLINUX_PEP600_RE.match(platform)
    if match is not None:
        gmaj, gmin, arch = match.groups()
        return PlatformInfo(PlatformFamily.MANYLINUX, (int(gmaj), int(gmin)), (_ARCH_BY_TAG[arch],))

    match = _MANYLINUX_LEGACY_RE.match(platform)
    if match is not None:
        alias, arch = match.groups()
        return PlatformInfo(
            PlatformFamily.MANYLINUX, _LEGACY_MANYLINUX_VERSIONS[alias], (_ARCH_BY_TAG[arch],)
        )

    match = _MACOS_RE.match(platform)
    if match is not None:
        maj, min_, arch = match.groups()
        version = (int(maj), int(min_))
        archs = (Arch.X86_64, Arch.ARM64) if arch == "universal2" else (_ARCH_BY_TAG[arch],)
        return PlatformInfo(PlatformFamily.MACOS, version, archs)

    match = _WINDOWS_RE.match(platform)
    if match is not None:
        arch = match.group(1)
        win_arch = Arch.X86_64 if arch == "amd64" else Arch.ARM64
        return PlatformInfo(PlatformFamily.WINDOWS, None, (win_arch,))

    return None


def supported_archs(platform: str) -> tuple[Arch | None, ...]:
    """The architectures a platform tag fans out to.

    `universal2` is the only multi-valued case: it is a fat binary covering
    both x86_64 and arm64, so it fans out to two configs that differ only in
    architecture -- the reason `arch` is a stored field on `WheelConfig`
    rather than a derived one. An unsupported platform yields `()`.
    """
    info = classify_platform(platform)
    if info is None:
        return ()
    if info.family is PlatformFamily.ANY:
        return (None,)
    return info.archs


_ARCH_BY_TAG = {"x86_64": Arch.X86_64, "aarch64": Arch.ARM64, "arm64": Arch.ARM64}

_LEGACY_MANYLINUX_VERSIONS = {
    "1": (2, 5),
    "2010": (2, 12),
    "2014": (2, 17),
}

_MANYLINUX_PEP600_RE = re.compile(r"^manylinux_(\d+)_(\d+)_(x86_64|aarch64)$")
_MANYLINUX_LEGACY_RE = re.compile(r"^manylinux(1|2010|2014)_(x86_64|aarch64)$")
_MACOS_RE = re.compile(r"^macosx_(\d+)_(\d+)_(x86_64|arm64|universal2)$")
_WINDOWS_RE = re.compile(r"^win_(amd64|arm64)$")
