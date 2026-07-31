from __future__ import annotations

from pathlib import Path

import pytest

from ._corpus import parametrize_cases

CASES_DIR = Path(__file__).parent / "corpus" / "cases"


@pytest.mark.parametrize(
    "case_dir",
    parametrize_cases(CASES_DIR, empty_reason="no corpus cases yet - see tests/corpus/README.md"),
)
def test_corpus_case(case_dir: Path | None) -> None:
    """Convert METADATA and compare against the expected repodata.json.

    Define correct behavior here *before* writing the implementation: add a
    case under tests/corpus/cases/<name>/, then remove the xfail below once
    reroll can actually produce it.
    """
    assert case_dir is not None
    pytest.xfail(f"reroll conversion is not implemented yet ({case_dir.name})")
