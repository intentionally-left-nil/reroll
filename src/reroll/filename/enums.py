"""Wheel-tag vocabulary: ABI kind, platform family, and architecture."""

from __future__ import annotations

from enum import Enum


class AbiKind(Enum):
    NONE = "none"
    STABLE = "stable"
    VERSIONED = "versioned"


class PlatformFamily(Enum):
    ANY = "any"
    MANYLINUX = "manylinux"
    MACOS = "macos"
    WINDOWS = "windows"


class Arch(Enum):
    X86_64 = "x86_64"
    ARM64 = "arm64"
