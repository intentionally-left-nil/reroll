"""Downloading and streaming parselmouth's upstream relations table."""

from __future__ import annotations

import gzip
import json
import shutil
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from reroll.parselmouth_mapper.types import RelationRow

DEFAULT_CHANNEL = "conda-forge"
DEFAULT_RELATIONS_URL = (
    f"https://conda-mapping.prefix.dev/relations-v1/{DEFAULT_CHANNEL}/relations.jsonl.gz"
)


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """The outcome of one conditional fetch of a relations table."""

    path: Path
    changed: bool
    etag: str | None


def iter_relations(path: Path) -> Iterator[RelationRow]:
    """Stream `RelationRow`s out of a downloaded `relations.jsonl.gz`,
    ignoring every upstream field this module does not consume.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            raw = json.loads(line)
            yield {
                "conda_name": raw["conda_name"],
                "conda_filename": raw["conda_filename"],
                "pypi_name": raw["pypi_name"],
                "pypi_version": raw["pypi_version"],
            }


def download_relations(
    url: str = DEFAULT_RELATIONS_URL, *, dest: Path, etag: str | None = None
) -> DownloadResult:
    """Conditionally download `url` to `dest`.

    If `etag` is given, it is sent as `If-None-Match`. A `304` response means
    upstream has not changed: `dest` is left untouched and `changed` is
    `False` -- callers must not read `dest` in that case. Any other response
    downloads the body to `dest`, reports `changed=True`, and carries the
    response's own `ETag` (`None` if the server sent none).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "reroll-parselmouth-mapper"}
    if etag is not None:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            with dest.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            return DownloadResult(path=dest, changed=True, etag=response.headers.get("ETag"))
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return DownloadResult(path=dest, changed=False, etag=etag)
        raise
