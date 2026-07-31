from __future__ import annotations

from pathlib import Path

import pytest

from reroll import reroll

from ._corpus import load_case, parametrize_cases

CASES_DIR = Path(__file__).parent / "corpus" / "cases"


@pytest.mark.parametrize(
    "case_dir",
    parametrize_cases(CASES_DIR, empty_reason="no corpus cases yet - see tests/corpus/README.md"),
)
def test_corpus_case(case_dir: Path | None) -> None:
    """Convert a wheel's METADATA and compare the record(s) `reroll` produces.

    Define correct behavior here *before* writing the implementation: add a
    case under tests/corpus/cases/<name>/ with an `XFAIL` file, confirm it
    fails for the right reason, then delete the `XFAIL` file once `reroll`
    actually produces it.
    """
    assert case_dir is not None
    filename, metadata, expected = load_case(case_dir)

    actual = [
        record.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
        for record in reroll(metadata, filename)
    ]
    assert actual == expected
