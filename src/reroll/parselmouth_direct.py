"""Parselmouth-backed PyPI -> conda name mapping (one-off HTTP mode).

Implements Parselmouth name mapping by making an HTTP request to conda-mapping.prefix.dev
for every request. Only to be used for a small number of packages
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping

from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName
from packaging.version import InvalidVersion, Version

from reroll.name_mapping import AmbiguousCondaName, NameMapper

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_CHANNEL",
    "Fetcher",
    "default_fetch",
    "parselmouth_direct_mapper",
]

DEFAULT_BASE_URL = "https://conda-mapping.prefix.dev/pypi-to-conda-v1"

DEFAULT_CHANNEL = "conda-forge"

Fetcher = Callable[[str], bytes | None]


def default_fetch(url: str, *, timeout: float = 10.0) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "reroll"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def parselmouth_direct_mapper(
    channel: str = DEFAULT_CHANNEL,
    *,
    base_url: str = DEFAULT_BASE_URL,
    fetch: Fetcher = default_fetch,
) -> NameMapper:

    def _lookup(name: NormalizedName, specifier: SpecifierSet) -> str | None:
        url = f"{base_url}/{_quote(channel)}/{_quote(name)}.json"
        payload = fetch(url)
        if payload is None:
            return None
        conda_versions: Mapping[str, str] = json.loads(payload)["conda_versions"]
        candidates = _matching_conda_names(conda_versions, specifier)
        if not candidates:
            return None
        if len(candidates) > 1:
            raise AmbiguousCondaName(name, specifier, candidates=sorted(candidates))
        return next(iter(candidates))

    return _lookup


def _quote(segment: str) -> str:
    return urllib.parse.quote(segment, safe="")


def _matching_conda_names(conda_versions: Mapping[str, str], specifier: SpecifierSet) -> set[str]:
    """The distinct conda names among `conda_versions`' entries whose
    (parsed) version is contained in `specifier`.

    A version string that fails to parse as PEP 440 is skipped, not
    raised on: Parselmouth's version strings are not guaranteed to be
    valid PyPI versions.
    """
    matches: set[str] = set()
    for raw_version, conda_name in conda_versions.items():
        try:
            version = Version(raw_version)
        except InvalidVersion:
            continue
        if specifier.contains(version):
            matches.add(conda_name)
    return matches
