"""reroll: generate conda v3 repodata records from a wheel file.

`reroll()` reads a wheel's `.dist-info/METADATA`, then converts it (plus the
wheel's filename) into its `WheelRecord`(s). `reroll.stages` exposes each
step of that pipeline (`extract_metadata_file`, `parse_metadata`,
`get_wheel_records`) individually.
"""

from __future__ import annotations

from pathlib import Path

from reroll.default_mappers import default_mappers
from reroll.dependencies.pep508_to_matchspec import pep508_to_matchspec
from reroll.errors import (
    RerollError,
    RerollInvalidWheelError,
    RerollRuntimeError,
    RerollScopeError,
    RerollUnconvertableError,
    UnresolvedCondaNameError,
)
from reroll.name_mapping import (
    Candidate,
    CandidateSource,
    NameMapper,
    NameMappers,
    aggregator_mapper,
    map_name,
    static_mapper,
)
from reroll.stages import extract_metadata_file, get_wheel_records, parse_metadata
from reroll.wheel_metadata import WheelMetadata
from reroll.wheel_record import WheelRecord

__all__ = [
    "Candidate",
    "CandidateSource",
    "NameMapper",
    "NameMappers",
    "RerollError",
    "RerollInvalidWheelError",
    "RerollRuntimeError",
    "RerollScopeError",
    "RerollUnconvertableError",
    "UnresolvedCondaNameError",
    "WheelMetadata",
    "WheelRecord",
    "aggregator_mapper",
    "default_mappers",
    "map_name",
    "to_matchspec",
    "reroll",
    "static_mapper",
]


def reroll(
    path: str | Path,
    *,
    mappers: NameMappers | None = None,
    allow_pre: bool = False,
    abi3_upper_bound: str | None = None,
    sha256: str | None = None,
    size: int | None = None,
    url: str | None = None,
) -> tuple[WheelRecord, ...]:
    """Convert the wheel file at `path` into its repodata record(s): its
    filename's `WheelConfig`(s) combined with its `.dist-info/METADATA`
    (`reroll.stages.extract_metadata_file`, `reroll.stages.parse_metadata`,
    `reroll.stages.get_wheel_records`, in that order).

    `mappers` defaults to `default_mappers()` -- the chain of grayskull,
    conda-lock, the hand-maintained overrides table, and parselmouth
    mappers, aggregated by `aggregator_mapper` -- when not given explicitly.

    `sha256`, `size`, and `url` are never computed from `path` -- each is
    set on every returned record only if passed in here (docs/wheel_record.md).
    """
    metadata_text = extract_metadata_file(path)
    metadata = parse_metadata(metadata_text)
    return get_wheel_records(
        metadata,
        Path(path).name,
        mappers=mappers,
        allow_pre=allow_pre,
        abi3_upper_bound=abi3_upper_bound,
        sha256=sha256,
        size=size,
        url=url,
    )


def to_matchspec(
    entry: str,
    *,
    mappers: NameMappers | None = None,
    allow_pre: bool = False,
    abi3_upper_bound: str | None = None,
) -> str:
    """`entry`'s conda MatchSpec (`reroll.dependencies.pep508_to_matchspec`).

    `abi3_upper_bound` bounds a `python_version in "<literal>"` marker's
    conversion: a minor-only version string like `"3.15"`; `None` (the
    default) keeps this easy to call without one -- it defers to
    `latest_python_minor`, lazily, and only if `entry`'s marker actually
    has such a clause.
    """
    mappers = mappers or default_mappers()
    return pep508_to_matchspec(
        entry, mappers, allow_pre=allow_pre, abi3_upper_bound=abi3_upper_bound
    )
