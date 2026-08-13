"""Unit tests for `reroll.subdir`."""

from __future__ import annotations

import pytest

from reroll.errors import UnsupportedPlatformError
from reroll.subdir import CondaSubdir, subdirs_for_platform


class TestCondaSubdir:
    def test_value_round_trips_to_the_member(self) -> None:
        assert CondaSubdir("linux-64") is CondaSubdir.LINUX_64

    def test_has_exactly_the_six_supported_subdirs(self) -> None:
        assert {member.value for member in CondaSubdir} == {
            "linux-64",
            "linux-aarch64",
            "osx-64",
            "osx-arm64",
            "win-64",
            "win-arm64",
        }


class TestSubdirsForPlatform:
    def test_manylinux_x86_64(self) -> None:
        assert subdirs_for_platform("manylinux_2_17_x86_64") == (CondaSubdir.LINUX_64,)

    def test_manylinux_aarch64(self) -> None:
        assert subdirs_for_platform("manylinux_2_17_aarch64") == (CondaSubdir.LINUX_AARCH64,)

    def test_macos_x86_64(self) -> None:
        assert subdirs_for_platform("macosx_10_9_x86_64") == (CondaSubdir.OSX_64,)

    def test_macos_arm64(self) -> None:
        assert subdirs_for_platform("macosx_11_0_arm64") == (CondaSubdir.OSX_ARM64,)

    def test_macos_universal2_fans_out_to_both_osx_subdirs(self) -> None:
        assert subdirs_for_platform("macosx_10_9_universal2") == (
            CondaSubdir.OSX_64,
            CondaSubdir.OSX_ARM64,
        )

    def test_win_amd64(self) -> None:
        assert subdirs_for_platform("win_amd64") == (CondaSubdir.WIN_64,)

    def test_win_arm64(self) -> None:
        assert subdirs_for_platform("win_arm64") == (CondaSubdir.WIN_ARM64,)

    def test_pure_python_wheel_needs_no_subdir(self) -> None:
        """A noarch (`"any"`) tag is not split by architecture at all --
        it belongs in conda's `noarch` subdir, which isn't one of the
        six `CondaSubdir` members.
        """
        assert subdirs_for_platform("any") == ()

    def test_unsupported_platform_is_rejected(self) -> None:
        with pytest.raises(UnsupportedPlatformError):
            subdirs_for_platform("musllinux_1_2_x86_64")
