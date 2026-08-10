"""Compute a wheel's conda `depends` MatchSpecs from its parsed configuration."""

from __future__ import annotations

from reroll.dependencies.python import python_dependencies
from reroll.filename import WheelConfig
from reroll.wheel_metadata import WheelMetadata

__all__ = ["wheel_dependencies"]


def wheel_dependencies(config: WheelConfig, metadata: WheelMetadata) -> tuple[str, ...] | None:
    """The conda `depends` MatchSpecs implied by `config` and `metadata`.

    `None` if `config`'s filename-implied Python range and `metadata`'s
    `Requires-Python` don't intersect -- the caller should not generate a
    repodata record for this wheel.

    Currently covers only the `python`/`python_abi` requirements
    (docs/wheel_to_conda_dependencies.md). `Requires-Dist` entries are not
    yet converted into `depends`; `reroll.dependencies.requires_dist`
    provides `strip_interpreter_requirements`, the one piece of that
    conversion already decided, for a future caller to compose in once the
    rest lands.
    """
    return python_dependencies(config, metadata)
