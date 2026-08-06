"""Classifying how one PyPI version corroborates one conda version."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from functools import lru_cache, total_ordering

from packaging.version import InvalidVersion, Version


class VersionClass(StrEnum):
    """The kind of difference between a conda and a PyPI version string."""

    EXACT = "exact"
    PEP440_EQUAL = "pep440_equal"
    POST_ONLY = "post_only"
    LOCAL_ONLY = "local_only"
    DEV_ONLY = "dev_only"
    PRE_ONLY = "pre_only"
    EPOCH_ONLY = "epoch_only"
    SUFFIX_MULTI = "suffix_multi"
    RELEASE_PREFIX = "release_prefix"
    DIFFER = "differ"
    UNPARSEABLE = "unparseable"


class VersionState(StrEnum):
    """How strongly a relation's PyPI version corroborates its conda one."""

    AGREES = "agrees"
    NO_SIGNAL = "no_signal"
    DISAGREES = "disagrees"


def classify_version(conda_version: str, pypi_version: str) -> VersionClass:
    """Name the *kind* of difference between `conda_version` and `pypi_version`."""
    if conda_version == pypi_version:
        return VersionClass.EXACT
    conda_parsed, pypi_parsed = _parse_version(conda_version), _parse_version(pypi_version)
    if conda_parsed is None or pypi_parsed is None:
        return VersionClass.UNPARSEABLE
    if conda_parsed == pypi_parsed:
        return VersionClass.PEP440_EQUAL
    conda_key = (
        conda_parsed.epoch,
        conda_parsed.release,
        conda_parsed.pre,
        conda_parsed.post,
        conda_parsed.dev,
        conda_parsed.local,
    )
    pypi_key = (
        pypi_parsed.epoch,
        pypi_parsed.release,
        pypi_parsed.pre,
        pypi_parsed.post,
        pypi_parsed.dev,
        pypi_parsed.local,
    )
    diffs = [axis for axis, c, p in zip(_VERSION_AXES, conda_key, pypi_key, strict=True) if c != p]
    if diffs == ["post"]:
        return VersionClass.POST_ONLY
    if diffs == ["local"]:
        return VersionClass.LOCAL_ONLY
    if diffs == ["dev"]:
        return VersionClass.DEV_ONLY
    if diffs == ["pre"]:
        return VersionClass.PRE_ONLY
    if diffs == ["epoch"]:
        return VersionClass.EPOCH_ONLY
    if "release" not in diffs:
        return VersionClass.SUFFIX_MULTI
    conda_release = _strip_trailing_zeros(conda_parsed.release)
    pypi_release = _strip_trailing_zeros(pypi_parsed.release)
    if (
        conda_release[: len(pypi_release)] == pypi_release
        or pypi_release[: len(conda_release)] == conda_release
    ):
        return VersionClass.RELEASE_PREFIX
    return VersionClass.DIFFER


def version_state(conda_version: str, pypi_version: str) -> VersionState:
    """Classify how strongly `pypi_version` corroborates `conda_version`.

    `AGREES` for a suffix-only-different `classify_version` class. `DISAGREES`
    for a real, different release. Otherwise `NO_SIGNAL`: the PyPI side alone
    is uninformative -- an all-zero setuptools-scm placeholder (`0`, `0.0.0`),
    a `+unknown` local segment, or simply not PEP 440-parseable.
    """
    if classify_version(conda_version, pypi_version) in _AGREEING_CLASSES:
        return VersionState.AGREES
    if (
        _ALL_ZERO_RE.match(pypi_version)
        or _UNKNOWN_LOCAL_RE.search(pypi_version)
        or _parse_version(pypi_version) is None
    ):
        return VersionState.NO_SIGNAL
    return VersionState.DISAGREES


def dominant_version_state(counts: Mapping[VersionState, int]) -> VersionState:
    """The version state one version's worth of relations reduces to: one
    agreeing relation (`counts[AGREES]`) is enough to call it `AGREES`;
    failing that, any real disagreement outweighs placeholder noise. A
    state absent from `counts` counts as zero.
    """
    if counts.get(VersionState.AGREES, 0) > 0:
        return VersionState.AGREES
    if counts.get(VersionState.DISAGREES, 0) > 0:
        return VersionState.DISAGREES
    return VersionState.NO_SIGNAL


def version_sort_key(version: str) -> VersionSortKey:
    """Order version strings by PEP 440 precedence (epoch first, then
    release, pre, post, dev, local -- the full ordering, not just release).
    Every unparseable version sorts before all parseable ones, grouped
    together and ordered by their raw string, since they carry no
    reliable precedence of their own.
    """
    return VersionSortKey(version)


@total_ordering
class VersionSortKey:
    """A comparable key as returned by `version_sort_key`."""

    def __init__(self, version: str) -> None:
        self._raw = version
        self._parsed = _parse_version(version)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VersionSortKey):
            return NotImplemented
        if self._parsed is None or other._parsed is None:
            return self._parsed is None and other._parsed is None and self._raw == other._raw
        return self._parsed == other._parsed

    def __lt__(self, other: VersionSortKey) -> bool:
        if self._parsed is None and other._parsed is None:
            return self._raw < other._raw
        if self._parsed is None or other._parsed is None:
            return self._parsed is None
        return self._parsed < other._parsed


_AGREEING_CLASSES = frozenset(
    {
        VersionClass.EXACT,
        VersionClass.PEP440_EQUAL,
        VersionClass.POST_ONLY,
        VersionClass.LOCAL_ONLY,
        VersionClass.DEV_ONLY,
        VersionClass.EPOCH_ONLY,
        VersionClass.SUFFIX_MULTI,
    }
)
_VERSION_AXES = ("epoch", "release", "pre", "post", "dev", "local")
_ALL_ZERO_RE = re.compile(r"^0(\.0)*([.+-]?dev\d*)?$")
_UNKNOWN_LOCAL_RE = re.compile(r"(?i)(^|[+._-])unknown")


@lru_cache(maxsize=4096)
def _parse_version(value: str) -> Version | None:
    """PEP 440-parse `value`, retrying with `_` swapped for `-` (conda
    version strings may not contain `-`, e.g. `1.0.0_rc1`).
    """
    for candidate in (value, value.replace("_", "-")):
        try:
            return Version(candidate)
        except InvalidVersion:
            continue
    return None


def _strip_trailing_zeros(release: tuple[int, ...]) -> tuple[int, ...]:
    trimmed = list(release)
    while len(trimmed) > 1 and trimmed[-1] == 0:
        trimmed.pop()
    return tuple(trimmed)
