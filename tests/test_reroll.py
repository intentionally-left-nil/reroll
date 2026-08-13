"""Unit tests for `reroll.reroll`.

`reroll` doesn't parse METADATA or a wheel's filename yet -- it always
returns the same hardcoded `WheelRecord` (see `src/reroll/__init__.py`).
These tests only pin down that placeholder behavior; replace them with
tests of real sub-behavior (e.g. a single field mapping) as parsing is
implemented.
"""

from __future__ import annotations

import pytest

from reroll import WheelRecord, reroll
from reroll.dependencies import WheelDependencies
from reroll.errors import UnconvertableRequirementError


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
    assert record.extra_depends == {}
    assert record.url == "tinylib-1.2.3-py3-none-any.whl"


class TestWheelRecordSharesWheelDependencies:
    """`WheelRecord` inherits `depends`/`extra_depends` from
    `WheelDependencies` (`reroll.dependencies`), rather than redeclaring
    its own copies -- so both are validated the same way.
    """

    def test_wheel_record_is_a_wheel_dependencies(self) -> None:
        (record,) = reroll(metadata="", filename="tinylib-1.2.3-py3-none-any.whl")

        assert isinstance(record, WheelDependencies)

    def test_rejects_an_invalid_matchspec_in_depends(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            WheelRecord(
                name="tinylib",
                version="1.2.3",
                build="py3_none_any_0",
                build_number=0,
                subdir="noarch",
                depends=("python >=1.0,<",),
                extra_depends={},
                url="tinylib-1.2.3-py3-none-any.whl",
            )

    def test_rejects_an_invalid_extra_name_key(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            WheelRecord(
                name="tinylib",
                version="1.2.3",
                build="py3_none_any_0",
                build_number=0,
                subdir="noarch",
                depends=(),
                extra_depends={"Not Valid": ()},
                url="tinylib-1.2.3-py3-none-any.whl",
            )


def test_reroll_ignores_metadata_and_filename_arguments() -> None:
    """Neither argument is parsed yet, so any input yields the same record."""
    default = reroll(metadata="", filename="tinylib-1.2.3-py3-none-any.whl")
    other = reroll(metadata="Name: something-else\n", filename="other-9.9.9-py2-none-any.whl")

    assert default == other
