"""Format a `packaging.version.Version` as a conda (CEP-33) version string."""

from __future__ import annotations

from packaging.version import InvalidVersion, Version


def format_version(version: Version) -> str:
    """`version`'s conda-style spelling: epoch (if any) prefixed with `!`,
    then release segments dot-joined, then `.{letter}{num}` for a
    pre-release, `.post{num}` for a post-release, and `.dev{num}` for a dev
    release -- e.g. `1.0.0rc1` becomes `1.0.0.rc1`, and PEP 440's `1.0-1`
    shorthand becomes `1.0.post1`.

    A local segment is never emitted; reject it beforehand via
    `version.local` if the caller's context requires that.
    """
    parts: list[str] = []
    if version.epoch:
        parts.append(f"{version.epoch}!")
    parts.append(".".join(str(segment) for segment in version.release))
    if version.pre is not None:
        letter, number = version.pre
        parts.append(f".{letter}{number}")
    if version.post is not None:
        parts.append(f".post{version.post}")
    if version.dev is not None:
        parts.append(f".dev{version.dev}")
    return "".join(parts)


def format_version_literal(literal: str) -> str:
    """`format_version` of `literal` if it parses as PEP 440, else `literal`
    unchanged -- for a marker's version literal, which need not be a valid
    version at all (PEP 508 compares it as a plain string).
    """
    try:
        return format_version(Version(literal))
    except InvalidVersion:
        return literal
