"""Build `markerpry` `Environment`s for evaluating a `Requires-Dist` marker
against a wheel's known Python pinning and (for an arch-specific record)
target subdir.

See docs/wheel_to_conda_dependencies.md's noarch/arch-specific base
environment tables.
"""

from __future__ import annotations

from markerpry import ConstraintLike, Environment, RangeConstraint
from packaging.version import Version

from reroll.subdir import CondaSubdir

_SUBDIR_PLATFORM: dict[CondaSubdir, tuple[str, str, str, str]] = {
    CondaSubdir.LINUX_64: ("Linux", "x86_64", "linux", "posix"),
    CondaSubdir.LINUX_AARCH64: ("Linux", "aarch64", "linux", "posix"),
    CondaSubdir.OSX_64: ("Darwin", "x86_64", "darwin", "posix"),
    CondaSubdir.OSX_ARM64: ("Darwin", "arm64", "darwin", "posix"),
    CondaSubdir.WIN_64: ("Windows", "AMD64", "win32", "nt"),
    CondaSubdir.WIN_ARM64: ("Windows", "ARM64", "win32", "nt"),
}
"""`platform_system`/`platform_machine`/`sys_platform`/`os_name` for each
`CondaSubdir`."""


def noarch_environment(minor: int | None) -> Environment:
    """The marker environment for a noarch record: `platform_python_implementation`/
    `implementation_name` are always fixed to CPython (reroll supports no
    other interpreter). `python_version`/`python_full_version`/
    `implementation_version` are added only when `minor` is known -- the
    wheel's tightened Python range collapses to that exact minor (e.g. `13`
    for 3.13) -- and otherwise left out so a marker referencing them is
    left for direct matchspec conversion instead
    (`reroll.dependencies.marker_conversion`).

    `python_full_version`/`implementation_version` map to a `RangeConstraint`
    spanning `minor.0` to `minor.100` rather than a single value: `evaluate()`
    only resolves a comparison against them when both ends of that range
    agree, which is docs/matchspec.md's probe-at-`X.0`-and-`X.100` reduction
    algorithm, implemented already by `RangeConstraint` itself.
    """
    environment: dict[str, list[ConstraintLike]] = {
        "platform_python_implementation": ["CPython"],
        "implementation_name": ["cpython"],
    }
    if minor is not None:
        environment["python_version"] = [Version(f"3.{minor}")]
        full_version_range = RangeConstraint(Version(f"3.{minor}.0"), Version(f"3.{minor}.100"))
        environment["python_full_version"] = [full_version_range]
        environment["implementation_version"] = [full_version_range]
    return environment


def arch_specific_environment(minor: int | None, subdir: CondaSubdir) -> Environment:
    """`noarch_environment(minor)`'s environment, plus `platform_system`/
    `platform_machine`/`sys_platform`/`os_name` fixed by `subdir`.
    """
    return {**noarch_environment(minor), **_platform_environment(subdir)}


def _platform_environment(subdir: CondaSubdir) -> Environment:
    platform_system, platform_machine, sys_platform, os_name = _SUBDIR_PLATFORM[subdir]
    return {
        "platform_system": [platform_system],
        "platform_machine": [platform_machine],
        "sys_platform": [sys_platform],
        "os_name": [os_name],
    }
