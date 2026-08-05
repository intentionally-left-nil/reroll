"""Conda package name validation, per CEP 26.
https://github.com/conda/ceps/blob/main/cep-0026.md
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

__all__ = ["CondaPackageName", "validate_package_name"]

_MAX_NAME_LENGTH = 64

# Transcribed verbatim from CEP 26 -- the regex, not the CEP's prose, is the
# normative artifact. Two consequences follow, both pinned by tests:
#
# - Trailing separators (`foo_`, `foo-`, `foo.`) match and are accepted,
#   even though the prose reads as if they should not be.
# - The match is case-sensitive, so `Requests` is rejected. CEP 26 describes
#   the regex as case-insensitive, but its prose requires lowercase names,
#   and every real conda name is lowercase.
#
# Length is not bounded by this regex, so it is checked separately below.
_DISTRIBUTABLE_NAME_RE = re.compile(r"^(([a-z0-9])|([a-z0-9_](?!_)))[._-]?([a-z0-9]+(\.|-|_|$))*$")


def validate_package_name(value: str) -> str:
    """Return `value` unchanged if it is a legal conda package name.

    Raises `ValueError` naming the offender if `value` exceeds
    `_MAX_NAME_LENGTH` or fails the CEP 26 name regex. Never mutates its
    input: lowercasing or otherwise repairing a bad value would hide the
    caller's bug.
    """
    if len(value) > _MAX_NAME_LENGTH:
        raise ValueError(
            f"conda package name {value!r} exceeds {_MAX_NAME_LENGTH} characters ({len(value)})"
        )
    if not _DISTRIBUTABLE_NAME_RE.match(value):
        raise ValueError(f"{value!r} is not a legal conda package name (CEP 26)")
    return value


CondaPackageName = Annotated[str, AfterValidator(validate_package_name)]
"""A `str` guaranteed to satisfy CEP 26's package-name rules."""
