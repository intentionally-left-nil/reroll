"""Pluggable PyPI -> conda name mapping.

Background lives in `docs/pypi_conda_mapping.md`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum

from packaging.utils import NormalizedName, canonicalize_name
from pydantic import BaseModel, ConfigDict, Field

from reroll.errors import UnresolvedCondaNameError


class CandidateSource(StrEnum):
    """The data source for a candidate selection of a conda name mapping"""

    PARSELMOUTH = "parselmouth"
    GRAYSKULL = "grayskull"
    CONDA_LOCK = "conda-lock"
    OTHER = "other"


class Candidate(BaseModel):
    """One mapper's guess at the conda name for a PyPI package, along with
    enough provenance to let a later, smarter mapper decide what to do
    with it.
    """

    model_config = ConfigDict(frozen=True)

    conda_name: str
    probability: float = Field(ge=0.0, le=1.0)
    source: CandidateSource
    mapper: str


NameMapper = Callable[[NormalizedName, Sequence[Candidate]], str | Sequence[Candidate]]
"""A callable taking a canonicalized PyPI name and the candidates
accumulated by earlier mappers in the chain. Returns either the
final conda name as a `str` (ending the chain immediately, later mappers
are not consulted), or a `Sequence[Candidate]` for the next mapper to
consider -- typically the input `candidates` plus whatever new ones this
mapper contributed.
"""

NameMappers = tuple[NameMapper, *tuple[NameMapper, ...]]
"""A non-empty chain of `NameMapper` callables to parse a filename
"""


def map_name(name: str, mappers: NameMappers) -> str:
    """Resolve `name` to its conda equivalent by threading a growing
    sequence of `Candidate`s through each of `mappers` in order.

    `mappers` must be non-empty -- enforced by its type (`NameMappers`) and,
    at runtime, by a `ValueError`. A caller that wants "no mapper has an
    opinion, just use the normalized PyPI name" must request that policy
    explicitly by ending the chain with `aggregator_mapper`

    `name` is canonicalized before any mapper sees it

    If every mapper returns candidates, this raises `UnresolvedCondaNameError`
    carrying the final candidate sequence
    """
    if not mappers:
        raise ValueError("map_name requires at least one mapper")
    normalized = canonicalize_name(name)
    candidates: Sequence[Candidate] = ()
    for mapper in mappers:
        result = mapper(normalized, candidates)
        if isinstance(result, str):
            return result
        candidates = result
    raise UnresolvedCondaNameError(normalized, candidates)


def aggregator_mapper(
    name: NormalizedName,
    candidates: Sequence[Candidate],
) -> str | Sequence[Candidate]:
    """A `NameMapper` meant to be placed last in a chain, deciding a final
    conda name from the `Candidate`s earlier mappers contributed.

    Decision order: the first grayskull candidate; the first certain
    (probability 1.0) conda-lock candidate; a name proposed by two or more
    distinct mappers; a sole mapper's candidate scoring at least 0.9, or
    parselmouth's only candidate. Anything else defers by returning
    `candidates` unchanged; empty `candidates` falls back to the normalized
    PyPI `name`.
    """
    if not candidates:
        return name
    for candidate in candidates:
        if candidate.source is CandidateSource.GRAYSKULL:
            return candidate.conda_name
    for candidate in candidates:
        if candidate.source is CandidateSource.CONDA_LOCK and candidate.probability == 1.0:
            return candidate.conda_name
    winner = _vote_winner(candidates)
    if winner is not None:
        return winner
    if len({candidate.mapper for candidate in candidates}) == 1:
        if candidates[0].source is CandidateSource.PARSELMOUTH:
            if len(candidates) == 1:
                return candidates[0].conda_name
        else:
            best = max(candidates, key=lambda candidate: candidate.probability)
            if best.probability >= 0.9:
                return best.conda_name
    return candidates


def static_mapper(table: Mapping[str, str]) -> NameMapper:
    """Build a `NameMapper` from a literal table. Any hits are returned immediately"""
    normalized_table = {canonicalize_name(key): value for key, value in table.items()}

    def _lookup(
        name: NormalizedName,
        candidates: Sequence[Candidate],
    ) -> str | Sequence[Candidate]:
        result = normalized_table.get(name)
        return candidates if result is None else result

    return _lookup


def _vote_winner(candidates: Sequence[Candidate]) -> str | None:
    """The `conda_name` proposed by at least two distinct mappers, else `None`.

    Ties break on the highest distinct-mapper count, then the highest summed
    probability, then the lexicographically smallest name.
    """
    mappers_by_name: dict[str, set[str]] = {}
    probability_by_name: dict[str, float] = {}
    for candidate in candidates:
        mappers_by_name.setdefault(candidate.conda_name, set()).add(candidate.mapper)
        probability_by_name[candidate.conda_name] = (
            probability_by_name.get(candidate.conda_name, 0.0) + candidate.probability
        )
    contested = [name for name, mappers in mappers_by_name.items() if len(mappers) >= 2]
    if not contested:
        return None
    return min(
        contested,
        key=lambda name: (-len(mappers_by_name[name]), -probability_by_name[name], name),
    )
