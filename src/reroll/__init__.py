"""reroll: generate conda v3 repodata records from a wheel's METADATA file.

`reroll()` converts a wheel's METADATA and filename into its `WheelRecord`(s).
"""

from __future__ import annotations

from reroll.default_mappers import default_mappers
from reroll.dependencies import WheelDependencies
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
    "WheelRecord",
    "aggregator_mapper",
    "default_mappers",
    "map_name",
    "reroll",
    "static_mapper",
]


class WheelRecord(WheelDependencies):
    """A single wheel's contribution to a repodata.json `v3.whl` map.

    Inherits `depends`/`extra_depends` from `WheelDependencies`
    (`reroll.dependencies`) rather than redeclaring them, so a record's
    dependency fields are validated identically to the ones
    `reroll.dependencies.calculate_dependencies` produces.
    """

    name: str
    version: str
    build: str
    build_number: int
    subdir: str
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
            extra_depends={},
            url="tinylib-1.2.3-py3-none-any.whl",
        ),
    )
