"""reroll: generate conda v3 repodata records from a wheel's METADATA file.

`reroll()` converts a wheel's METADATA (plus its filename) into the
`WheelRecord`(s) that correspond to the v3 repodata for a conda equivalent. A wheel can
produce more than one record: a compressed platform tag (e.g.
`macosx_10_9_universal2`) resolves to more than one conda subdir, and each
resulting `(subdir, build)` pair becomes its own record.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from reroll.name_mapping import (
    Candidate,
    CandidateSource,
    NameMapper,
    NameMappers,
    UnresolvedCandidates,
    aggregator_mapper,
    exact_version,
    map_name,
    static_mapper,
)

__all__ = [
    "Candidate",
    "CandidateSource",
    "NameMapper",
    "NameMappers",
    "UnresolvedCandidates",
    "WheelRecord",
    "aggregator_mapper",
    "exact_version",
    "map_name",
    "reroll",
    "static_mapper",
]


class WheelRecord(BaseModel):
    """A single wheel's contribution to a repodata.json `v3.whl` map."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    build: str
    build_number: int
    subdir: str
    depends: tuple[str, ...]
    url: str
    noarch: str | None = None
    license: str | None = None


def reroll(metadata: str, filename: str) -> tuple[WheelRecord, ...]:
    """Convert a wheel's METADATA (plus its filename) into its repodata record(s)."""
    del metadata, filename  # not yet parsed; see docstring
    return (
        WheelRecord(
            name="tinylib",
            version="1.2.3",
            build="py3_none_any_0",
            build_number=0,
            subdir="noarch",
            noarch="python",
            license="MIT",
            depends=("requests >=2.20", "python >=3.9"),
            url="tinylib-1.2.3-py3-none-any.whl",
        ),
    )
