"""Unit tests for `reroll.subdir`."""

from __future__ import annotations

from reroll.subdir import CondaSubdir


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
