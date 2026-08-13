"""Unit tests for `reroll.wheel_archive`."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from reroll.errors import InvalidWheelArchiveError
from reroll.wheel_archive import extract_metadata_file


def _make_wheel(
    path: Path,
    *,
    entries: dict[str, bytes] | None = None,
) -> Path:
    """A `.whl` (zip) file at `path` containing `entries` (defaulting to a
    single well-formed `tinylib-1.2.3.dist-info/METADATA` entry).
    """
    if entries is None:
        entries = {
            "tinylib-1.2.3.dist-info/METADATA": b"Metadata-Version: 2.1\nName: tinylib\n\n",
        }
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)
    return path


class TestExtractMetadataFile:
    def test_returns_the_decoded_metadata_contents(self, tmp_path: Path) -> None:
        wheel = _make_wheel(
            tmp_path / "tinylib-1.2.3-py3-none-any.whl",
            entries={
                "tinylib-1.2.3.dist-info/METADATA": b"Metadata-Version: 2.1\nName: tinylib\n\n",
                "tinylib-1.2.3.dist-info/WHEEL": b"Wheel-Version: 1.0\n\n",
                "tinylib/__init__.py": b"",
            },
        )

        assert extract_metadata_file(wheel) == "Metadata-Version: 2.1\nName: tinylib\n\n"

    def test_accepts_a_str_path(self, tmp_path: Path) -> None:
        wheel = _make_wheel(tmp_path / "tinylib-1.2.3-py3-none-any.whl")

        assert extract_metadata_file(str(wheel)) == "Metadata-Version: 2.1\nName: tinylib\n\n"

    def test_decodes_non_ascii_content_as_utf8(self, tmp_path: Path) -> None:
        wheel = _make_wheel(
            tmp_path / "tinylib-1.2.3-py3-none-any.whl",
            entries={
                "tinylib-1.2.3.dist-info/METADATA": (
                    "Metadata-Version: 2.1\nAuthor: José\n\n".encode()
                ),
            },
        )

        assert "José" in extract_metadata_file(wheel)

    def test_missing_metadata_entry_raises(self, tmp_path: Path) -> None:
        wheel = _make_wheel(
            tmp_path / "tinylib-1.2.3-py3-none-any.whl",
            entries={"tinylib-1.2.3.dist-info/WHEEL": b"Wheel-Version: 1.0\n\n"},
        )

        with pytest.raises(InvalidWheelArchiveError):
            extract_metadata_file(wheel)

    def test_multiple_metadata_entries_raises(self, tmp_path: Path) -> None:
        wheel = _make_wheel(
            tmp_path / "tinylib-1.2.3-py3-none-any.whl",
            entries={
                "tinylib-1.2.3.dist-info/METADATA": b"Name: tinylib\n\n",
                "other-9.9.dist-info/METADATA": b"Name: other\n\n",
            },
        )

        with pytest.raises(InvalidWheelArchiveError):
            extract_metadata_file(wheel)

    def test_nested_metadata_entry_does_not_count(self, tmp_path: Path) -> None:
        """`foo/bar.dist-info/METADATA` has an extra path component and is
        not a legal wheel `.dist-info` layout, so it's not matched -- and
        the wheel is treated as missing its `METADATA` entry entirely.
        """
        wheel = _make_wheel(
            tmp_path / "tinylib-1.2.3-py3-none-any.whl",
            entries={"foo/tinylib-1.2.3.dist-info/METADATA": b"Name: tinylib\n\n"},
        )

        with pytest.raises(InvalidWheelArchiveError):
            extract_metadata_file(wheel)

    def test_not_a_zip_file_raises(self, tmp_path: Path) -> None:
        not_a_wheel = tmp_path / "not-a-wheel.whl"
        not_a_wheel.write_bytes(b"this is not a zip archive")

        with pytest.raises(InvalidWheelArchiveError):
            extract_metadata_file(not_a_wheel)

    def test_undecodable_metadata_contents_raises(self, tmp_path: Path) -> None:
        wheel = _make_wheel(
            tmp_path / "tinylib-1.2.3-py3-none-any.whl",
            entries={"tinylib-1.2.3.dist-info/METADATA": b"\xff\xfe not utf-8"},
        )

        with pytest.raises(InvalidWheelArchiveError):
            extract_metadata_file(wheel)
