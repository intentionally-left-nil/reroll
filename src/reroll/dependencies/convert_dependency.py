"""Convert a single `Requires-Dist` entry into its conda MatchSpec
equivalent, or classify why it can't be converted (yet, or at all).
"""

from __future__ import annotations

import logging

from packaging.requirements import Requirement
from packaging.specifiers import Specifier
from packaging.version import InvalidVersion, Version

from reroll.name_mapping import NameMappers, UnresolvedCandidates, map_name

_logger = logging.getLogger(__name__)


class Unsupported:
    """`UNSUPPORTED`'s type -- callers can check `is UNSUPPORTED` for the
    exact sentinel, or `isinstance(result, Unsupported)` to narrow a
    `convert_dependency` result's type down to it.
    """


UNSUPPORTED = Unsupported()
"""Sentinel `convert_dependency` result for an entry with extras or a
marker: conversion for these is a future addition, not yet implemented, so
the entry is left out of `depends` entirely rather than converted or
rejected. Distinct from `None`, which means `entry` can never be
represented in conda at all and the whole repodata record must be rejected.
"""


def convert_dependency(
    entry: str,
    mappers: NameMappers,
    *,
    allow_pre: bool = False,
) -> str | None | Unsupported:
    """The conda MatchSpec for `entry`, a single `Requires-Dist` entry.

    `UNSUPPORTED` if `entry` carries extras or a marker: richer forms
    conversion is a future addition (a marker-conversion layer), not yet
    implemented here.

    `None` signals that `entry` cannot be represented in conda at all, and
    the whole repodata entry must be rejected rather than built with a
    partial `depends` list -- logged at `WARNING`. This happens for: a
    direct URL reference (`name @ url`); a local version label
    (`1.0+local`); a pre-release version, unless `allow_pre` is set; or a
    PyPI name with no resolvable conda name.
    """
    requirement = Requirement(entry)
    if requirement.extras or requirement.marker is not None:
        _logger.debug("skipping %r, extras/marker conversion is not yet implemented", entry)
        return UNSUPPORTED
    if requirement.url is not None:
        _logger.warning("rejecting dependency with a direct URL reference: %r", entry)
        return None
    try:
        conda_name = map_name(requirement.name, mappers)
    except UnresolvedCandidates as exc:
        _logger.warning("unresolved conda name for dependency %r: %s", entry, exc)
        return None
    parts: list[str] = []
    for specifier in sorted(requirement.specifier, key=str):
        part = _convert_specifier(specifier, entry, allow_pre=allow_pre)
        if part is None:
            return None
        parts.append(part)
    if not parts:
        return conda_name
    return f"{conda_name} {','.join(parts)}"


def _convert_specifier(specifier: Specifier, entry: str, *, allow_pre: bool) -> str | None:
    """One `,`-joined clause of a MatchSpec's version, e.g. `>=1.0.0` --
    epochs and post-releases are passed through as-is. `None` if `entry`'s
    whole conversion must be rejected: a local version label, or a
    pre-release version with `allow_pre` unset. An arbitrary (non-PEP440)
    `===` right-hand side is passed through unchecked, since it can't be
    inspected for either condition.
    """
    operator = "==" if specifier.operator == "===" else specifier.operator
    try:
        version = Version(specifier.version)
    except InvalidVersion:
        return f"{operator}{specifier.version}"
    if version.local is not None:
        _logger.warning("rejecting dependency with a local version label: %r", entry)
        return None
    if version.is_prerelease and not allow_pre:
        _logger.warning("rejecting pre-release dependency %r: allow_pre is not set", entry)
        return None
    return f"{operator}{specifier.version}"
