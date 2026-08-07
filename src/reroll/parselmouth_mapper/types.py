"""Shared data types for parselmouth-backed PyPI -> conda name mapping."""

from __future__ import annotations

from typing import TypedDict


class RelationRow(TypedDict):
    """One row of parselmouth's `relations-v1/{channel}/relations.jsonl.gz`,
    narrowed to the fields `ingest` and `db` consume.
    """

    conda_name: str
    conda_filename: str
    pypi_name: str
    pypi_version: str
