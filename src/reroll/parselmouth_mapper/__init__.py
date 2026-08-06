"""Parselmouth-backed PyPI -> conda name mapping.

`open_parselmouth_database` keeps a local sqlite evidence database current
with parselmouth's `relations-v1` table; `parselmouth_mapper` builds a
`NameMapper` that reads it. Background: `docs/pypi_conda_mapping.md`.
"""

from __future__ import annotations

from reroll.parselmouth_mapper.db import open_parselmouth_database, write_relations
from reroll.parselmouth_mapper.ingest import (
    DEFAULT_CHANNEL,
    DEFAULT_RELATIONS_URL,
    DownloadResult,
    download_relations,
    iter_relations,
)
from reroll.parselmouth_mapper.mapper import parselmouth_mapper
from reroll.parselmouth_mapper.names import (
    NameAxis,
    NameClass,
    classify_name_relation,
    name_axis,
    parse_conda_filename,
    variant_distance,
)
from reroll.parselmouth_mapper.scoring import BASE_PROBABILITY, CandidateEvidence, score_evidence
from reroll.parselmouth_mapper.types import RelationRow
from reroll.parselmouth_mapper.versions import (
    VersionClass,
    VersionState,
    classify_version,
    dominant_version_state,
    version_sort_key,
    version_state,
)

__all__ = [
    "BASE_PROBABILITY",
    "DEFAULT_CHANNEL",
    "DEFAULT_RELATIONS_URL",
    "CandidateEvidence",
    "DownloadResult",
    "NameAxis",
    "NameClass",
    "RelationRow",
    "VersionClass",
    "VersionState",
    "classify_name_relation",
    "classify_version",
    "dominant_version_state",
    "download_relations",
    "iter_relations",
    "name_axis",
    "open_parselmouth_database",
    "parse_conda_filename",
    "parselmouth_mapper",
    "score_evidence",
    "variant_distance",
    "version_sort_key",
    "version_state",
    "write_relations",
]
