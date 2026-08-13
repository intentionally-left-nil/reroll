"""Compute a wheel's conda `depends`/`extra_depends` MatchSpecs from its
parsed configuration.
"""

from __future__ import annotations

from reroll.dependencies.calculate_dependencies import WheelDependencies, calculate_dependencies

__all__ = ["WheelDependencies", "calculate_dependencies"]
