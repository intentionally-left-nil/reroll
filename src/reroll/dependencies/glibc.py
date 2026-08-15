"""Conda `__glibc` MatchSpec implied by a manylinux wheel's platform tag."""

from __future__ import annotations

from reroll.filename import PlatformFamily, WheelConfig


def glibc_dependency(config: WheelConfig) -> str | None:
    """The `__glibc` MatchSpec `config`'s manylinux glibc floor implies,
    e.g. `"__glibc >=2.17"` for `manylinux_2_17_x86_64` or the legacy
    `manylinux2014_x86_64` alias (`reroll.filename.platform`'s PEP 600 and
    legacy-alias version mapping). `None` for any other platform family --
    a manylinux wheel's glibc floor is a property of the wheel's own
    platform tag, not of any particular `CondaSubdir` a caller is
    generating dependencies for.
    """
    match config.platform_family, config.platform_version:
        case PlatformFamily.MANYLINUX, (major, minor):
            return f"__glibc >={major}.{minor}"
        case _:
            return None
