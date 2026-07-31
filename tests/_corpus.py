"""Shared helpers for discovering corpus-style test cases.

A "case" is a directory containing:
  - METADATA        the wheel METADATA file being converted
  - repodata.json   the v3 repodata reroll is expected to produce for it

See tests/corpus/README.md for the full case format and guidance on adding
new cases. Written to be reusable for a future tests/integration/ suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

REQUIRED_FILES = ("METADATA", "repodata.json")


def discover_cases(cases_dir: Path) -> list[Path]:
    """Return every case directory under `cases_dir` that has all required files."""
    if not cases_dir.is_dir():
        return []
    return sorted(
        case_dir
        for case_dir in cases_dir.iterdir()
        if case_dir.is_dir() and all((case_dir / name).exists() for name in REQUIRED_FILES)
    )


def parametrize_cases(cases_dir: Path, *, empty_reason: str) -> list[Any]:
    """Build pytest.param entries for each case, or a single skipped placeholder.

    Using a placeholder (rather than an empty parametrize list) gives a clear,
    named reason in test output instead of pytest's generic "empty parameter
    set" skip.
    """
    cases = discover_cases(cases_dir)
    if cases:
        return [pytest.param(case_dir, id=case_dir.name) for case_dir in cases]
    return [pytest.param(None, marks=pytest.mark.skip(reason=empty_reason))]
