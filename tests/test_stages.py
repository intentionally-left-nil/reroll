"""Unit tests for `reroll.stages`.

`reroll.stages` is a pure re-export namespace -- each function is fully
tested in its own module (`reroll.wheel_archive`, `reroll.wheel_metadata`,
`reroll.wheel_record`). Here we only pin down that the namespace exposes
the exact same objects, so a caller reaching into `reroll.stages` to run
one stage in isolation gets the real implementation.
"""

from __future__ import annotations

from reroll import stages
from reroll.wheel_archive import extract_metadata_file
from reroll.wheel_metadata import parse_metadata
from reroll.wheel_record import get_wheel_records


class TestStagesNamespace:
    def test_extract_metadata_file_is_the_real_implementation(self) -> None:
        assert stages.extract_metadata_file is extract_metadata_file

    def test_parse_metadata_is_the_real_implementation(self) -> None:
        assert stages.parse_metadata is parse_metadata

    def test_get_wheel_records_is_the_real_implementation(self) -> None:
        assert stages.get_wheel_records is get_wheel_records
