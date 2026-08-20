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
    PASSTHROUGH = "passthrough"
    CONSENSUS = "consensus"
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


class Winner(Candidate):
    """A `Candidate` promoted to the definitive, committed answer for one
    name lookup.

    Being a `Candidate` subclass -- rather than a `mapped: bool` field on
    `Candidate` itself -- means "this is the final answer" is a type
    distinction `map_name` can check with `isinstance`, not a flag a
    `Sequence[Candidate]` could end up with more than one of.
    """

    @classmethod
    def from_candidate(cls, candidate: Candidate) -> Winner:
        return cls(**candidate.model_dump())


class NameResolution(BaseModel):
    """A PyPI name paired with the `Winner` `map_name` resolved it to."""

    model_config = ConfigDict(frozen=True)

    pypi_name: NormalizedName
    winner: Winner


def is_passthrough(candidate: Candidate) -> bool:
    """Whether `candidate` (or `Winner`) came from `passthrough_mapper`'s
    "nobody had an opinion" fallback, rather than an actual name mapping.
    """
    return candidate.source is CandidateSource.PASSTHROUGH


NameMapper = Callable[[NormalizedName, Sequence[Candidate]], Winner | Sequence[Candidate]]
"""A callable taking a canonicalized PyPI name and the candidates
accumulated by earlier mappers in the chain. Returns either a `Winner`
(ending the chain immediately, later mappers are not consulted), or a
`Sequence[Candidate]` for the next mapper to consider -- typically the
input `candidates` plus whatever new ones this mapper contributed.
"""

NameMappers = tuple[NameMapper, *tuple[NameMapper, ...]]
"""A non-empty chain of `NameMapper` callables to parse a filename
"""


def map_name(name: str, mappers: NameMappers) -> Winner:
    """Resolve `name` to its conda equivalent by threading a growing
    sequence of `Candidate`s through each of `mappers` in order.

    `mappers` must be non-empty -- enforced by its type (`NameMappers`) and,
    at runtime, by a `ValueError`. A caller that wants "no mapper has an
    opinion, just use the normalized PyPI name" must request that policy
    explicitly by ending the chain with `passthrough_mapper`

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
        if isinstance(result, Winner):
            return result
        candidates = result
    raise UnresolvedCondaNameError(normalized, candidates)


def aggregator_mapper(
    name: NormalizedName,
    candidates: Sequence[Candidate],
) -> Winner | Sequence[Candidate]:
    """A `NameMapper` deciding a final conda name from the `Candidate`s
    earlier mappers contributed.

    Decision order: the first grayskull candidate; the first certain
    (probability 1.0) conda-lock candidate; a name proposed by two or more
    distinct mappers; parselmouth's only candidate; or a sole mapper's best
    candidate scoring at least 0.9. Anything else -- including empty
    `candidates` -- defers by returning `candidates` unchanged: this mapper
    has an opinion only about candidates it's given, never about an
    unopinionated PyPI name (`passthrough_mapper` covers that case).

    A `Winner` promoted from an existing candidate (every branch except the
    vote) carries that candidate's own `probability`/`source`/`mapper`
    unchanged -- this mapper only ever picks among opinions already on the
    table, it never fabricates provenance for one. The vote branch is the
    exception: "two or more mappers agree" is a fact `aggregator_mapper`
    itself observes, so its `Winner` is attributed to `aggregator_mapper`
    with `CandidateSource.CONSENSUS`, not to whichever candidate happened
    to be first.
    """
    if not candidates:
        return candidates
    for candidate in candidates:
        if candidate.source is CandidateSource.GRAYSKULL:
            return Winner.from_candidate(candidate)
    for candidate in candidates:
        if candidate.source is CandidateSource.CONDA_LOCK and candidate.probability == 1.0:
            return Winner.from_candidate(candidate)
    winner = _vote_winner(candidates)
    if winner is not None:
        return winner
    if len({candidate.mapper for candidate in candidates}) == 1:
        if len(candidates) == 1 and candidates[0].source is CandidateSource.PARSELMOUTH:
            return Winner.from_candidate(candidates[0])
        best = max(candidates, key=lambda candidate: candidate.probability)
        if best.probability >= 0.9:
            return Winner.from_candidate(best)
    return candidates


def passthrough_mapper(
    name: NormalizedName,
    candidates: Sequence[Candidate],
) -> Winner | Sequence[Candidate]:
    """A `NameMapper` committing to the normalized PyPI `name` itself, but
    only when nobody earlier in the chain has anything to say: empty
    `candidates` resolves to a `Winner` wrapping `name` with
    `CandidateSource.PASSTHROUGH`; non-empty `candidates` defer by
    returning them unchanged, since a name with candidates nobody committed
    to is ambiguous, not unopinionated.

    Meant to be placed last in a chain that wants "no mapper has an
    opinion, just use the normalized PyPI name" as an explicit, opt-in
    policy (`map_name`). `is_passthrough` lets a caller recognize this
    fallback happened, downstream of `map_name`.
    """
    if candidates:
        return candidates
    return Winner(
        conda_name=name,
        probability=0.0,
        source=CandidateSource.PASSTHROUGH,
        mapper="passthrough_mapper",
    )


def static_mapper(table: Mapping[str, str], *, mapper_name: str = "static_mapper") -> NameMapper:
    """Build a `NameMapper` from a literal table. Any hits are returned
    immediately, attributed to `mapper_name` -- override it when building
    more than one static-table mapper (e.g. `overrides_mapper`) so their
    `Winner`s stay distinguishable.
    """
    normalized_table = {canonicalize_name(key): value for key, value in table.items()}

    def _lookup(
        name: NormalizedName,
        candidates: Sequence[Candidate],
    ) -> Winner | Sequence[Candidate]:
        result = normalized_table.get(name)
        if result is None:
            return candidates
        return Winner(
            conda_name=result, probability=1.0, source=CandidateSource.OTHER, mapper=mapper_name
        )

    return _lookup


def _vote_winner(candidates: Sequence[Candidate]) -> Winner | None:
    """A `Winner` for the `conda_name` proposed by at least two distinct
    mappers, else `None`.

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
    winning_name = min(
        contested,
        key=lambda name: (-len(mappers_by_name[name]), -probability_by_name[name], name),
    )
    return Winner(
        conda_name=winning_name,
        probability=1.0,
        source=CandidateSource.CONSENSUS,
        mapper="aggregator_mapper",
    )
