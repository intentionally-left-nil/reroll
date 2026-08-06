"""conda-lock-backed PyPI -> conda name mapping.

Reads the mapping conda-forge's autotick bot publishes. conda-lock owns the
loading of the mapping itself.
Two sibling tables are loaded here, because conda-lock does not know about
them, and they are what expose *how much to trust* each entry:
`import_name_priority_mapping.json` (did a graph-centrality tie-break decide
this?) and `name_mapping.json` (do two PyPI spellings collapse onto this key?).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import TypedDict

from conda_lock.lookup import DEFAULT_MAPPING_URL, _get_pypi_lookup
from conda_lock.lookup_cache import cached_download_file
from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name
from pydantic import TypeAdapter

from reroll.name_mapping import Candidate, CandidateSource, NameMapper

__all__ = [
    "AMBIGUOUS_PROBABILITY",
    "COLLIDING_PROBABILITY",
    "DEFAULT_MAPPING_URL",
    "DEFAULT_NAME_MAPPING_URL",
    "DEFAULT_PRIORITY_URL",
    "STATIC_PROBABILITY",
    "UNAMBIGUOUS_PROBABILITY",
    "build_candidates",
    "candidate_mapper",
    "conda_lock_mapper",
]

_MAPPINGS_BASE = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi"

DEFAULT_PRIORITY_URL = f"{_MAPPINGS_BASE}/import_name_priority_mapping.json"
"""Per import name, every conda package that provides it, worst-ranked first."""

DEFAULT_NAME_MAPPING_URL = f"{_MAPPINGS_BASE}/name_mapping.json"
"""Every extracted mapping row, before same-PyPI-name rows are collapsed."""

STATIC_PROBABILITY = 1.0
"""A human wrote this row into the bot's static override table."""

UNAMBIGUOUS_PROBABILITY = 0.9
"""Derived from a recipe's PyPI source URL, and exactly one conda package
provides the import name.
"""

AMBIGUOUS_PROBABILITY = 0.6
"""Several conda packages provide the import name, so a HITS hub/authority
score picked the winner -- centrality, not correctness. This is how a
non-Python package can outrank the Python one it shares a name with.
"""

COLLIDING_PROBABILITY = 0.4
"""Two different PyPI spellings (`-`/`_`/`.`) canonicalize onto this key while
naming different conda packages, so only one of the two aliases survived.
"""

_STATIC_MAPPING_SOURCE = "static"
_MAPPER_NAME = "conda_forge_bot_graph"
_CACHE_SUBDIR = "pypi-mapping"
"""conda-lock's own cache subdirectory, shared so the mapping is not stored
twice for callers that use both libraries.
"""


class MappingEntry(TypedDict):
    """One row of the bot's mapping tables."""

    pypi_name: str
    conda_name: str
    import_name: str
    mapping_source: str


class PriorityEntry(TypedDict):
    """One row of `import_name_priority_mapping.json`."""

    import_name: str
    ranked_conda_names: list[str]


def _ambiguous_import_names(import_priority: Sequence[PriorityEntry]) -> frozenset[str]:
    return frozenset(
        entry["import_name"] for entry in import_priority if len(entry["ranked_conda_names"]) > 1
    )


def _colliding_pypi_names(raw_mappings: Sequence[MappingEntry]) -> frozenset[NormalizedName]:
    conda_names_by_pypi_name: defaultdict[NormalizedName, set[str]] = defaultdict(set)
    for entry in raw_mappings:
        conda_names_by_pypi_name[canonicalize_name(entry["pypi_name"])].add(entry["conda_name"])
    return frozenset(
        pypi_name
        for pypi_name, conda_names in conda_names_by_pypi_name.items()
        if len(conda_names) > 1
    )


def build_candidates(
    mapping: Mapping[str, MappingEntry],
    import_priority: Sequence[PriorityEntry],
    raw_mappings: Sequence[MappingEntry],
) -> dict[NormalizedName, Candidate]:
    """Score every row of `mapping` into a `Candidate`, keyed by canonicalized
    PyPI name.

    Every row is kept, including the handful whose `pypi_name` upstream filled
    in with a content digest: a lookup key comes from a real wheel filename, so
    those rows are unreachable rather than harmful. `conda_name` is not
    validated.
    """
    ambiguous = _ambiguous_import_names(import_priority)
    colliding = _colliding_pypi_names(raw_mappings)

    candidates: dict[NormalizedName, Candidate] = {}
    for raw_name, entry in mapping.items():
        pypi_name = canonicalize_name(raw_name)
        if entry["mapping_source"] == _STATIC_MAPPING_SOURCE:
            probability = STATIC_PROBABILITY
        elif pypi_name in colliding:
            probability = COLLIDING_PROBABILITY
        elif entry["import_name"] in ambiguous:
            probability = AMBIGUOUS_PROBABILITY
        else:
            probability = UNAMBIGUOUS_PROBABILITY
        candidates[pypi_name] = Candidate(
            conda_name=entry["conda_name"],
            probability=probability,
            source=CandidateSource.CONDA_LOCK,
            mapper=_MAPPER_NAME,
        )
    return candidates


def candidate_mapper(candidates: Mapping[NormalizedName, Candidate]) -> NameMapper:
    """Build a `NameMapper` from a prebuilt candidate table.

    A hit appends its candidate without ending the chain; a miss returns
    `candidates` unchanged.
    """

    def _lookup(
        name: NormalizedName,
        specifier: SpecifierSet,
        accumulated: Sequence[Candidate],
    ) -> Sequence[Candidate]:
        del specifier
        candidate = candidates.get(name)
        if candidate is None:
            return accumulated
        return (*accumulated, candidate)

    return _lookup


def _read_sibling(source: Path | str) -> bytes:
    """Fetch one of the two sibling tables.

    Mirrors conda-lock's own source dispatch because the equivalent branch in
    `_get_pypi_lookup` is inlined and cannot be called for another table.
    HTTP goes through conda-lock's shared ETag-conditional cache.
    """
    text = str(source)
    if text.startswith(("http://", "https://")):
        return cached_download_file(text, cache_subdir_name=_CACHE_SUBDIR)
    if text.startswith("file://"):
        text = text[len("file://") :]
    return Path(text).read_bytes()


_MAPPING_ADAPTER = TypeAdapter(dict[str, MappingEntry])
_PRIORITY_ADAPTER = TypeAdapter(list[PriorityEntry])
_RAW_MAPPINGS_ADAPTER = TypeAdapter(list[MappingEntry])


@cache
def _candidate_table(
    mapping_url: str,
    priority_url: str,
    name_mapping_url: str,
) -> dict[NormalizedName, Candidate]:
    """Load, validate and score all three tables, memoized on their sources.

    Callers must treat the result as read-only: it is shared by every mapper
    built from the same three sources.
    """
    return build_candidates(
        mapping=_MAPPING_ADAPTER.validate_python(_get_pypi_lookup(mapping_url)),
        import_priority=_PRIORITY_ADAPTER.validate_json(_read_sibling(priority_url)),
        raw_mappings=_RAW_MAPPINGS_ADAPTER.validate_json(_read_sibling(name_mapping_url)),
    )


def conda_lock_mapper(
    mapping_url: Path | str = DEFAULT_MAPPING_URL,
    priority_url: Path | str = DEFAULT_PRIORITY_URL,
    name_mapping_url: Path | str = DEFAULT_NAME_MAPPING_URL,
) -> NameMapper:
    return candidate_mapper(
        _candidate_table(str(mapping_url), str(priority_url), str(name_mapping_url))
    )
