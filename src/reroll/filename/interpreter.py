"""Parse a wheel filename's interpreter tag (e.g. `cp313`, `py3`)."""

from __future__ import annotations

import re

_INTERPRETER_RE = re.compile(r"^(py|cp)(\d)(\d*)$")


def parse_interpreter(tag: str) -> tuple[str, int, int]:
    """Return `(prefix, major, minor)`. Raises `ValueError` if invalid.

    Grammar: `(py|cp)` followed by digits. The first digit is the major
    version; all remaining digits are the minor -- there is no way to encode
    a patch version in a wheel tag.

    Whether a given minor is actually *supported* depends on which ABI it's
    paired with (a `cp32-none` pin is unsupported but `cp32-abi3` isn't), so
    that check lives in `reroll.filename.abi.check_interpreter_abi`, not here.
    """
    match = _INTERPRETER_RE.match(tag)
    if match is None:
        raise ValueError(f"invalid interpreter tag: {tag!r}")
    prefix, major_str, minor_str = match.groups()
    major = int(major_str)
    if major != 3:
        raise ValueError(f"unsupported interpreter major version: {tag!r}")
    if prefix == "cp" and minor_str == "":
        raise ValueError(f"'cp' interpreter tag requires an exact minor version: {tag!r}")
    minor = int(minor_str) if minor_str else 0
    return prefix, major, minor
