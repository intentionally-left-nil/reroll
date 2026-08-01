"""Unit tests for `reroll.reroll`.

`reroll` doesn't parse METADATA or a wheel's filename yet -- it always
returns the same hardcoded `WheelRecord` (see `src/reroll/__init__.py`).
These tests only pin down that placeholder behavior; replace them with
tests of real sub-behavior (e.g. a single field mapping) as parsing is
implemented.
"""

from __future__ import annotations

from reroll import WheelRecord, reroll


def test_reroll_returns_a_single_record() -> None:
    (record,) = reroll(metadata="", filename="tinylib-1.2.3-py3-none-any.whl")

    assert isinstance(record, WheelRecord)


def test_reroll_record_matches_hardcoded_placeholder() -> None:
    (record,) = reroll(metadata="", filename="tinylib-1.2.3-py3-none-any.whl")

    assert record.name == "tinylib"
    assert record.version == "1.2.3"
    assert record.build == "py3_none_any_0"
    assert record.build_number == 0
    assert record.subdir == "noarch"
    assert record.noarch == "python"
    assert record.license == "MIT"
    assert record.depends == ("requests >=2.20", "python >=3.9")
    assert record.url == "tinylib-1.2.3-py3-none-any.whl"


def test_reroll_ignores_metadata_and_filename_arguments() -> None:
    """Neither argument is parsed yet, so any input yields the same record."""
    default = reroll(metadata="", filename="tinylib-1.2.3-py3-none-any.whl")
    other = reroll(metadata="Name: something-else\n", filename="other-9.9.9-py2-none-any.whl")

    assert default == other
