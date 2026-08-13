"""Unit tests for `reroll.dependencies.version_format`."""

from __future__ import annotations

import pytest
from packaging.version import Version

from reroll.dependencies.version_format import format_version, format_version_literal


class TestFormatVersion:
    def test_plain_release_is_dot_joined(self) -> None:
        assert format_version(Version("1.0.0")) == "1.0.0"

    def test_single_segment_release(self) -> None:
        assert format_version(Version("1")) == "1"

    def test_epoch_is_prefixed_with_bang_no_dot(self) -> None:
        assert format_version(Version("1!1.0.0")) == "1!1.0.0"

    def test_pre_release_is_dotted(self) -> None:
        assert format_version(Version("1.0.0rc1")) == "1.0.0.rc1"

    @pytest.mark.parametrize(
        ("literal", "expected"),
        [
            ("1.0.0a1", "1.0.0.a1"),
            ("1.0.0b1", "1.0.0.b1"),
            ("1.0.0rc1", "1.0.0.rc1"),
        ],
    )
    def test_all_pre_release_letters_are_dotted(self, literal: str, expected: str) -> None:
        assert format_version(Version(literal)) == expected

    def test_post_release_is_dotted(self) -> None:
        assert format_version(Version("1.0.0.post1")) == "1.0.0.post1"

    def test_post_release_shorthand_is_normalized(self) -> None:
        """PEP 440's `X.Y-N` shorthand for `X.Y.postN` normalizes to the
        same dotted form as the explicit spelling.
        """
        assert format_version(Version("1.0-1")) == "1.0.post1"

    def test_dev_release_is_dotted(self) -> None:
        assert format_version(Version("1.0.0.dev1")) == "1.0.0.dev1"

    def test_pre_post_and_dev_combine_in_order(self) -> None:
        assert format_version(Version("1!2.0.0rc1.post2.dev3")) == "1!2.0.0.rc1.post2.dev3"

    def test_local_segment_is_dropped(self) -> None:
        """A local segment is never emitted -- callers that need to reject
        a local version outright must check `Version.local` themselves
        before calling this function.
        """
        assert format_version(Version("1.0.0+local")) == "1.0.0"


class TestFormatVersionLiteral:
    def test_valid_version_literal_is_formatted(self) -> None:
        assert format_version_literal("1.0.0rc1") == "1.0.0.rc1"

    def test_non_pep440_literal_passes_through_unchanged(self) -> None:
        assert format_version_literal("not-a-version") == "not-a-version"
