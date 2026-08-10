"""The latest maintained CPython 3.x minor version, per endoflife.date's
Python product feed, cached locally to avoid a network round trip on every
`abi3` wheel.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_PYTHON_RELEASES_URL = "https://endoflife.date/api/v1/products/python/"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "reroll"
CACHE_FILENAME_PREFIX = "python_version_"

_MAX_CACHE_AGE = timedelta(days=1)
_RELEASE_MINOR_RE = re.compile(r"^3\.(\d+)$")


def latest_python_minor(
    cache_dir: Path | None = None, *, url: str = DEFAULT_PYTHON_RELEASES_URL
) -> int:
    """The highest CPython 3.x minor version endoflife.date currently
    reports, e.g. `14` for a feed whose newest release is `"3.14"`.

    Backed by a local cache (`cache_dir`, default `~/.cache/reroll`) of the
    raw feed response, refreshed only once a day: a directory scan finds the
    newest `python_version_<timestamp>.json` file, downloads a fresh one only
    if that file is missing or more than a day old, and then deletes every
    other cached file (a second scan, taken after the possible download, so
    a concurrent refresh from another process is not deleted out from under
    it).
    """
    directory = DEFAULT_CACHE_DIR if cache_dir is None else cache_dir
    path = _ensure_fresh_cache(directory, url)
    data = json.loads(path.read_text())
    return _latest_minor_from_releases(data)


def _ensure_fresh_cache(directory: Path, url: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    existing = _cached_files(directory)
    if existing and not _is_stale(existing[0]):
        return existing[0]

    _download(url, directory)

    refreshed = _cached_files(directory)
    for stale in refreshed[1:]:
        stale.unlink(missing_ok=True)
    return refreshed[0]


def _cached_files(directory: Path) -> list[Path]:
    """Cached `python_version_*.json` files in `directory`, newest first."""
    return sorted(
        directory.glob(f"{CACHE_FILENAME_PREFIX}*.json"), key=_cache_timestamp, reverse=True
    )


def _is_stale(path: Path) -> bool:
    return datetime.now(UTC) - _cache_timestamp(path) > _MAX_CACHE_AGE


def _cache_timestamp(path: Path) -> datetime:
    raw = path.stem.removeprefix(CACHE_FILENAME_PREFIX)
    return datetime.fromtimestamp(int(raw), tz=UTC)


def _download(url: str, directory: Path) -> Path:
    """Fetch `url` into a fresh `python_version_<timestamp>.json` file in
    `directory`, returning its path.

    Downloads to a scratch file in the same directory first, then
    atomically renames it into place, so a concurrent reader (this same
    cache is shared across processes) never observes a partially-written
    file under the final, glob-discoverable name -- only the complete
    response, or nothing at all.
    """
    timestamp = int(datetime.now(UTC).timestamp())
    dest = directory / f"{CACHE_FILENAME_PREFIX}{timestamp}.json"
    request = urllib.request.Request(url, headers={"User-Agent": "reroll-filename"})
    staged_fd, staged_name = tempfile.mkstemp(dir=directory, prefix=f".{dest.name}.")
    os.close(staged_fd)
    staged_path = Path(staged_name)
    try:
        with urllib.request.urlopen(request) as response:
            staged_path.write_bytes(response.read())
        staged_path.replace(dest)
    except BaseException:
        staged_path.unlink(missing_ok=True)
        raise
    return dest


def _latest_minor_from_releases(data: Any) -> int:
    """The highest minor among `data["result"]["releases"][*]["name"]`
    entries shaped like a bare `"3.<minor>"` (excluding patch-level names
    such as `"3.14.7"`, which only appear under `latest`, and non-3.x majors
    such as `"2.7"`).
    """
    releases = data["result"]["releases"]
    minors = [
        int(match.group(1))
        for release in releases
        if (match := _RELEASE_MINOR_RE.match(release["name"])) is not None
    ]
    if not minors:
        raise ValueError("endoflife.date response contains no 3.x Python release")
    return max(minors)
