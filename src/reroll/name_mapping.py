"""Pluggable PyPI -> conda name mapping.

Background lives in `docs/pypi_conda_mapping.md`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum

from packaging.utils import NormalizedName, canonicalize_name
from pydantic import BaseModel, ConfigDict, Field


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


class UnresolvedCandidates(Exception):
    """Raised by `map_name` when every mapper in the chain has run and
    none of them returned a final conda name
    """

    def __init__(
        self,
        name: str,
        candidates: Sequence[Candidate] = (),
    ) -> None:
        self.name = name
        self.candidates = tuple(candidates)
        super().__init__(
            f"no mapper resolved a conda name for {name!r}: candidates={self.candidates!r}"
        )


def map_name(name: str, mappers: NameMappers) -> str:
    """Resolve `name` to its conda equivalent by threading a growing
    sequence of `Candidate`s through each of `mappers` in order.

    `mappers` must be non-empty -- enforced by its type (`NameMappers`) and,
    at runtime, by a `ValueError`. A caller that wants "no mapper has an
    opinion, just use the normalized PyPI name" must request that policy
    explicitly by ending the chain with `aggregator_mapper`

    `name` is canonicalized before any mapper sees it

    If every mapper returns candidates, this raises `UnresolvedCandidates`
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
    raise UnresolvedCandidates(normalized, candidates)


def aggregator_mapper(
    name: NormalizedName,
    candidates: Sequence[Candidate],
) -> str | Sequence[Candidate]:
    """A `NameMapper` meant to be placed last in a chain, where a decision
    finally gets made from whatever `Candidate`s earlier mappers
    contributed -- weighing `probability` and `source` against each
    other.
    """
    return candidates if candidates else name


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
