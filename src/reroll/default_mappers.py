"""The default PyPI -> conda name-mapping chain."""

from __future__ import annotations

from reroll.conda_lock_mapper import conda_lock_mapper
from reroll.grayskull_mapper import grayskull_mapper
from reroll.name_mapping import NameMappers, aggregator_mapper
from reroll.parselmouth_mapper import parselmouth_mapper


def default_mappers() -> NameMappers:
    """Build the default chain: grayskull, then conda-lock, then
    parselmouth, with `aggregator_mapper` deciding last.

    Each call builds a fresh tuple of mappers -- there is no shared state
    for callers to accidentally mutate.
    """
    return (
        grayskull_mapper(),
        conda_lock_mapper(),
        parselmouth_mapper(),
        aggregator_mapper,
    )
