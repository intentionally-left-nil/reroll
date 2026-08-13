"""Grayskull-backed PyPI -> conda name mapping.

Wires the community-curated table from `grayskull/strategy/config.yaml` into
a `NameMapper`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from grayskull.base.track_packages import ConfigPkg, _get_track_info_from_file
from grayskull.strategy.pypi import PYPI_CONFIG
from packaging.utils import NormalizedName, canonicalize_name
from ruamel.yaml import YAMLError

from reroll.errors import ConfigLoadError
from reroll.name_mapping import Candidate, CandidateSource, NameMapper

_MAPPER_NAME = "grayskull_config"


def grayskull_mapper(config_file: Path | str = PYPI_CONFIG) -> NameMapper:
    """Build a `NameMapper` from grayskull's `config.yaml` exception table.

    `config_file` defaults to the table bundled inside the installed
    `grayskull` package; pass an explicit path to use a different one.

    A hit contributes a `Candidate` (`probability=1.0`,
    `source=CandidateSource.GRAYSKULL`) without ending the chain. A miss
    returns `candidates` unchanged.

    Raises `ConfigLoadError` if `config_file` can't be read or parsed as
    YAML.
    """
    try:
        raw_table = _get_track_info_from_file(config_file)
    except (OSError, YAMLError) as exc:
        raise ConfigLoadError(f"failed to load grayskull config {config_file!r}: {exc}") from exc
    resolved: dict[NormalizedName, Candidate] = {
        canonicalize_name(name): Candidate(
            conda_name=ConfigPkg(name, **entry).conda_forge,
            probability=1.0,
            source=CandidateSource.GRAYSKULL,
            mapper=_MAPPER_NAME,
        )
        for name, entry in raw_table.items()
    }

    def _lookup(
        name: NormalizedName,
        candidates: Sequence[Candidate],
    ) -> Sequence[Candidate]:
        candidate = resolved.get(name)
        if candidate is None:
            return candidates
        return (*candidates, candidate)

    return _lookup
