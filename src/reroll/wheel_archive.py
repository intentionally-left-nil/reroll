"""Extract a wheel's `METADATA` file from its zip archive."""

from __future__ import annotations

import zipfile
from pathlib import Path

from reroll.errors import InvalidWheelArchiveError

_METADATA_SUFFIX = "METADATA"


def extract_metadata_file(path: str | Path) -> str:
    """The decoded text of the `.dist-info/METADATA` entry inside the wheel
    (zip archive) at `path`.

    Raises `InvalidWheelArchiveError` if `path` is not a valid zip archive,
    if its entries contain zero or more than one `*.dist-info/METADATA`
    entry, or if the entry's contents are not valid UTF-8.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if _is_metadata_entry(name)]
            if len(names) != 1:
                raise InvalidWheelArchiveError(
                    f"expected exactly one *.dist-info/METADATA entry in {path!r}, found {names!r}"
                )
            contents = archive.read(names[0])
    except zipfile.BadZipFile as exc:
        raise InvalidWheelArchiveError(
            f"{path!r} is not a valid wheel (zip) archive: {exc}"
        ) from exc
    try:
        return contents.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidWheelArchiveError(
            f"{names[0]!r} in {path!r} is not valid UTF-8: {exc}"
        ) from exc


def _is_metadata_entry(name: str) -> bool:
    """`name` is exactly `{dist_info_dir}/METADATA` -- one path separator,
    with the directory component ending in `.dist-info`. A nested match
    (e.g. `foo/bar.dist-info/METADATA`) does not count.
    """
    parts = name.split("/")
    return len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] == _METADATA_SUFFIX
