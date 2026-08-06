"""Pluggable PyPI -> conda name mapping.

Background research (why mapping is hard, what the ecosystem tools do, how
Parselmouth's data is shaped) lives in `docs/pypi_conda_mapping.md`. This
module implements the resolution machinery only -- CEP 26 validation of the
*output* lives in `reroll.conda_package_name`, and Parselmouth support of
any kind is out of scope here (see `specs/name_mapping.md`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum

from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from reroll.conda_package_name import CondaPackageName

__all__ = [
    "Candidate",
    "CandidateSource",
    "NameMapper",
    "NameMappers",
    "UnresolvedCandidates",
    "aggregator_mapper",
    "exact_version",
    "map_name",
    "static_mapper",
]


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

    conda_name: CondaPackageName
    probability: float = Field(ge=0.0, le=1.0)
    source: CandidateSource
    mapper: str


NameMapper = Callable[
    [NormalizedName, SpecifierSet, Sequence[Candidate]], str | Sequence[Candidate]
]
"""A callable taking a canonicalized PyPI name, a version specifier, and the
candidates accumulated by earlier mappers in the chain. Returns either the
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
        specifier: SpecifierSet,
        candidates: Sequence[Candidate] = (),
    ) -> None:
        self.name = name
        self.specifier = specifier
        self.candidates = tuple(candidates)
        super().__init__(
            f"no mapper resolved a conda name for {name!r} (specifier={specifier!s}): "
            f"candidates={self.candidates!r}"
        )


def exact_version(version: Version) -> SpecifierSet:
    """`SpecifierSet(f"=={version}")` -- the only place that construction
    appears, so the edge cases (epochs, local versions, dev/post/rc
    segments) are pinned once.
    """
    return SpecifierSet(f"=={version}")


def map_name(name: str, specifier: SpecifierSet, mappers: NameMappers) -> str:
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
        result = mapper(normalized, specifier, candidates)
        if isinstance(result, str):
            return result
        candidates = result
    raise UnresolvedCandidates(normalized, specifier, candidates)


def aggregator_mapper(
    name: NormalizedName,
    specifier: SpecifierSet,
    candidates: Sequence[Candidate],
) -> str | Sequence[Candidate]:
    """A `NameMapper` meant to be placed last in a chain, where a decision
    finally gets made from whatever `Candidate`s earlier mappers
    contributed -- weighing `probability` and `source` against each
    other.
    """
    del specifier
    return candidates if candidates else name


def static_mapper(table: Mapping[str, str]) -> NameMapper:
    """Build a `NameMapper` from a literal table -- the grayskull-style
    exception list that is the first thing most users will need.

    The table is validated at construction time (every entry, not just the
    first bad one, is reported in one `ValidationError`). The specifier is
    ignored: a static table is version-independent by construction. A hit
    returns the conda name directly as a `str` -- a hand-maintained
    override is authoritative, not merely one more data point to weigh --
    and a miss returns `candidates` unchanged.
    """
    validated = _StaticTable.model_validate(table).root

    def _lookup(
        name: NormalizedName,
        specifier: SpecifierSet,
        candidates: Sequence[Candidate],
    ) -> str | Sequence[Candidate]:
        del specifier
        result = validated.get(name)
        return candidates if result is None else result

    return _lookup


class _StaticTable(RootModel[dict[NormalizedName, CondaPackageName]]):
    """Validates a `static_mapper` table at construction: keys are PEP 503
    canonicalized and values must satisfy CEP 26. A `RootModel` (rather
    than a loop calling `validate_package_name`) reports every bad entry at
    once with its key in `loc`, instead of stopping at the first.

    Key canonicalization runs as a plain `field_validator` on `root`,
    `mode="before"`, rather than through an `Annotated` key type: the only
    exported name callers need for "a canonicalized PyPI name" is
    `packaging.utils.NormalizedName` itself, so this module does not mint a
    second, confusingly similar type just to carry that one validator.
    """

    @field_validator("root", mode="before")
    @classmethod
    def _canonicalize_keys(cls, value: Mapping[str, str]) -> dict[str, str]:
        return {canonicalize_name(key): entry for key, entry in value.items()}
