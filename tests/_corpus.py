"""Shared helpers for discovering corpus-style test cases.

A "case" is a directory containing:
  - wheel/<wheel-filename>.whl/METADATA   the one wheel being converted
  - records.json                         the record(s) `reroll` must produce for it

A case may optionally contain an `XFAIL` file. If present, its contents (or
a default message, if empty) are used as the reason for an `xfail` marker on
that case -- use this while a case's conversion isn't implemented yet.
Delete the file once `reroll` produces the case's expected output.

See tests/corpus/README.md for the full case format and guidance on adding
new cases. Written to be reusable for a future tests/integration/ suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

DEFAULT_XFAIL_REASON = "reroll conversion is not implemented yet for this case"


def _wheel_dir(case_dir: Path) -> Path | None:
    """Return the case's single `wheel/<filename>.whl/` directory, if valid.

    Exactly one subdirectory of `wheel/` must exist and contain `METADATA` --
    anything else (missing `wheel/`, no subdirectory, more than one) means
    the case isn't well-formed and is excluded from discovery.
    """
    wheel_root = case_dir / "wheel"
    if not wheel_root.is_dir():
        return None
    candidates = [
        entry for entry in wheel_root.iterdir() if entry.is_dir() and (entry / "METADATA").exists()
    ]
    return candidates[0] if len(candidates) == 1 else None


def _records_file(case_dir: Path) -> Path | None:
    records_path = case_dir / "records.json"
    return records_path if records_path.exists() else None


def discover_cases(cases_dir: Path) -> list[Path]:
    """Return every case directory under `cases_dir` with a wheel and expected records."""
    if not cases_dir.is_dir():
        return []
    return sorted(
        case_dir
        for case_dir in cases_dir.iterdir()
        if case_dir.is_dir()
        and _wheel_dir(case_dir) is not None
        and _records_file(case_dir) is not None
    )


def parametrize_cases(cases_dir: Path, *, empty_reason: str) -> list[Any]:
    """Build pytest.param entries for each case, or a single skipped placeholder.

    Using a placeholder (rather than an empty parametrize list) gives a clear,
    named reason in test output instead of pytest's generic "empty parameter
    set" skip. Cases with an `XFAIL` file are marked `xfail` individually.
    """
    cases = discover_cases(cases_dir)
    if not cases:
        return [pytest.param(None, marks=pytest.mark.skip(reason=empty_reason))]

    params = []
    for case_dir in cases:
        xfail_marker = case_dir / "XFAIL"
        marks = []
        if xfail_marker.exists():
            reason = xfail_marker.read_text(encoding="utf-8").strip() or DEFAULT_XFAIL_REASON
            marks.append(pytest.mark.xfail(reason=reason, strict=True))
        params.append(pytest.param(case_dir, id=case_dir.name, marks=marks))
    return params


def load_case(case_dir: Path) -> tuple[str, str, list[dict[str, Any]]]:
    """Load a case's `(filename, METADATA text, expected records)`."""
    wheel_dir = _wheel_dir(case_dir)
    records_path = _records_file(case_dir)
    assert wheel_dir is not None
    assert records_path is not None
    filename = wheel_dir.name
    metadata = (wheel_dir / "METADATA").read_text(encoding="utf-8")
    expected = json.loads(records_path.read_text(encoding="utf-8"))
    return filename, metadata, expected
