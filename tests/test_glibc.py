"""Unit tests for `reroll.dependencies.glibc`."""

from __future__ import annotations

import pytest

from reroll.dependencies.glibc import glibc_dependency
from reroll.filename import Arch, WheelConfig
from reroll.name_mapping import CandidateSource, NameResolution, Winner

_NAME_RESOLUTION = NameResolution(
    pypi_name="tinylib",
    winner=Winner(
        conda_name="tinylib",
        probability=0.0,
        source=CandidateSource.PASSTHROUGH,
        mapper="passthrough_mapper",
    ),
)


def _config(*, platform: str, arch: Arch | None) -> WheelConfig:
    """A valid `WheelConfig` (cp313-cp313) with the platform/arch tags
    overridden for tests that only care about the glibc axis.
    """
    return WheelConfig(
        normalized_pypi_name="tinylib",
        conda_name="tinylib",
        version="1.2.3",
        build=(),
        interpreter="cp313",
        abi="cp313",
        platform=platform,
        arch=arch,
        name_resolution=_NAME_RESOLUTION,
    )


class TestManylinux:
    @pytest.mark.parametrize(
        ("platform", "expected"),
        [
            ("manylinux_2_17_x86_64", "__glibc >=2.17"),
            ("manylinux_2_28_x86_64", "__glibc >=2.28"),
            ("manylinux_2_17_aarch64", "__glibc >=2.17"),
        ],
    )
    def test_pep600_tag_uses_its_own_version(self, platform: str, expected: str) -> None:
        arch = Arch.ARM64 if platform.endswith("aarch64") else Arch.X86_64
        config = _config(platform=platform, arch=arch)

        assert glibc_dependency(config) == expected

    @pytest.mark.parametrize(
        ("alias", "expected"),
        [
            ("manylinux1_x86_64", "__glibc >=2.5"),
            ("manylinux1_aarch64", "__glibc >=2.5"),
            ("manylinux2010_x86_64", "__glibc >=2.12"),
            ("manylinux2010_aarch64", "__glibc >=2.12"),
            ("manylinux2014_x86_64", "__glibc >=2.17"),
            ("manylinux2014_aarch64", "__glibc >=2.17"),
        ],
    )
    def test_legacy_alias_uses_its_mapped_version(self, alias: str, expected: str) -> None:
        arch = Arch.ARM64 if alias.endswith("aarch64") else Arch.X86_64
        config = _config(platform=alias, arch=arch)

        assert glibc_dependency(config) == expected


class TestNonManylinux:
    def test_pure_python_wheel_has_no_glibc_dependency(self) -> None:
        config = _config(platform="any", arch=None)

        assert glibc_dependency(config) is None

    def test_macos_wheel_has_no_glibc_dependency(self) -> None:
        config = _config(platform="macosx_10_9_x86_64", arch=Arch.X86_64)

        assert glibc_dependency(config) is None

    def test_windows_wheel_has_no_glibc_dependency(self) -> None:
        config = _config(platform="win_amd64", arch=Arch.X86_64)

        assert glibc_dependency(config) is None
