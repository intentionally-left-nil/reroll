"""Unit tests for `reroll.dependencies.osx`."""

from __future__ import annotations

from reroll.dependencies.osx import osx_dependency
from reroll.filename import Arch, WheelConfig


def _config(*, platform: str, arch: Arch | None) -> WheelConfig:
    """A valid `WheelConfig` (cp313-cp313) with the platform/arch tags
    overridden for tests that only care about the macOS axis.
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
    )


class TestMacos:
    def test_x86_64_tag_uses_its_own_version(self) -> None:
        config = _config(platform="macosx_10_9_x86_64", arch=Arch.X86_64)

        assert osx_dependency(config) == "__osx >=10.9"

    def test_arm64_tag_uses_its_own_version(self) -> None:
        config = _config(platform="macosx_11_0_arm64", arch=Arch.ARM64)

        assert osx_dependency(config) == "__osx >=11.0"

    def test_arm64_tag_above_11_0_uses_its_own_version(self) -> None:
        config = _config(platform="macosx_12_0_arm64", arch=Arch.ARM64)

        assert osx_dependency(config) == "__osx >=12.0"

    def test_universal2_x86_64_half_uses_the_tags_own_version(self) -> None:
        config = _config(platform="macosx_10_9_universal2", arch=Arch.X86_64)

        assert osx_dependency(config) == "__osx >=10.9"

    def test_universal2_arm64_half_is_clamped_to_11_0(self) -> None:
        config = _config(platform="macosx_10_9_universal2", arch=Arch.ARM64)

        assert osx_dependency(config) == "__osx >=11.0"


class TestNonMacos:
    def test_pure_python_wheel_has_no_osx_dependency(self) -> None:
        config = _config(platform="any", arch=None)

        assert osx_dependency(config) is None

    def test_manylinux_wheel_has_no_osx_dependency(self) -> None:
        config = _config(platform="manylinux_2_17_x86_64", arch=Arch.X86_64)

        assert osx_dependency(config) is None

    def test_windows_wheel_has_no_osx_dependency(self) -> None:
        config = _config(platform="win_amd64", arch=Arch.X86_64)

        assert osx_dependency(config) is None
