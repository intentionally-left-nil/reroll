"""Parse a wheel filename's ABI tag and validate its pairing with an
interpreter tag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from reroll.filename.enums import AbiKind
from reroll.filename.interpreter import parse_interpreter

_ABI_CP_RE = re.compile(r"^cp(\d+)([dmut]*)$")


@dataclass(frozen=True)
class AbiInfo:
    kind: AbiKind
    minor: int | None
    free_threaded: bool


def parse_abi(tag: str) -> AbiInfo:
    """Return the parsed ABI shape. Raises `ValueError` if invalid.

    Accepts `none`; `abi3`/`abi3t`; `cp` + digits with an optional build
    suffix combining `d`/`m`/`u`/`t` in any order. Per
    docs/wheel_filename.md, only `d` (debug) is unsupported -- every CPython
    shipped on defaults/conda-forge from 3.4 onward is already a pymalloc,
    wide-unicode build, so `m`/`u` are harmless and `normalize_abi` strips
    them rather than rejecting the wheel. Non-CPython ABIs are rejected by
    simply not matching any of these forms.
    """
    if tag == "none":
        return AbiInfo(kind=AbiKind.NONE, minor=None, free_threaded=False)
    if tag in ("abi3", "abi3t"):
        return AbiInfo(kind=AbiKind.STABLE, minor=None, free_threaded=tag.endswith("t"))

    match = _ABI_CP_RE.match(tag)
    if match is None:
        raise ValueError(f"invalid abi tag: {tag!r}")
    digits, suffix = match.groups()
    major = int(digits[0])
    if major != 3:
        raise ValueError(f"unsupported abi major version: {tag!r}")
    if "d" in suffix:
        raise ValueError(f"debug abi suffix is not supported: {tag!r}")
    minor = int(digits[1:]) if len(digits) > 1 else 0
    return AbiInfo(kind=AbiKind.VERSIONED, minor=minor, free_threaded="t" in suffix)


def normalize_abi(tag: str) -> str:
    """Canonicalize a `cp`-style ABI tag by dropping the `m`/`u` build
    suffixes reroll silently ignores (docs/wheel_filename.md). `none`,
    `abi3`, and `abi3t` have no such suffixes and pass through unchanged.
    Must only be called after `parse_abi` has confirmed `tag` is valid
    (i.e. does not carry `d`), so the only suffix left to preserve is `t`.
    """
    match = _ABI_CP_RE.match(tag)
    if match is None:
        return tag
    digits, suffix = match.groups()
    return f"cp{digits}t" if "t" in suffix else f"cp{digits}"


def check_interpreter_abi(interpreter: str, abi: str) -> None:
    """Cross-field validation for the legal (interpreter, ABI) pairings.
    Raises `ValueError` for everything else.

    Only six pairing *shapes* are legal: any interpreter with the `none`
    ABI; a `cpXY` interpreter with a `cpXY`/`cpXYt` ABI whose minor matches;
    or a `cpXY` interpreter with `abi3` (minor >= 2) or `abi3t` (minor >=
    15). Beyond that shape check, a `cp` tag paired with `none` or a
    matching versioned ABI *pins* that minor exactly, and Python 3.0-3.3 was
    never shipped on defaults or conda-forge, so those two shapes also
    require minor >= 4. `abi3`/`abi3t` loosen the tag to a floor instead, so
    they get their own (already-checked) floors rather than the 3.4 one --
    `cp32-abi3` is fine even though python 3.2 itself doesn't exist on those
    channels, because the floor resolves up to whatever 3.4+ IS available.
    """
    prefix, _, interp_minor = parse_interpreter(interpreter)
    abi_info = parse_abi(abi)

    if abi_info.kind is AbiKind.NONE:
        # Any interpreter tag may pair with the `none` ABI -- but a `cp` tag
        # pins that minor exactly, so it still needs to be >= 3.4 to resolve.
        if prefix == "cp" and interp_minor < 4:
            raise ValueError(f"CPython < 3.4 is unsupported: {interpreter!r}")
        return

    if prefix == "py":
        # A generic interpreter tag never advertises an ABI requirement --
        # no real interpreter emits a `py*` tag paired with anything but
        # `none`.
        raise ValueError(
            f"generic interpreter tag {interpreter!r} requires the 'none' ABI, got {abi!r}"
        )

    if abi_info.kind is AbiKind.VERSIONED:
        # A versioned ABI's minor must match the interpreter's, and (like
        # `none`) pins that minor exactly.
        if abi_info.minor != interp_minor:
            raise ValueError(f"versioned abi {abi!r} minor must match interpreter {interpreter!r}")
        if interp_minor < 4:
            raise ValueError(f"CPython < 3.4 is unsupported: {interpreter!r}")
        return

    # STABLE (abi3 / abi3t). The floor comes from the interpreter tag's own
    # minor, not from the acceptance-gate versions below -- a `cp313-abi3`
    # wheel installs on 3.13+ but not on 3.12, even though `abi3` itself is
    # legal all the way back to 3.2.
    if abi_info.free_threaded:
        if interp_minor < 15:
            raise ValueError(f"'abi3t' requires CPython >= 3.15, got {interpreter!r}")
    elif interp_minor < 2:
        raise ValueError(f"'abi3' requires CPython >= 3.2, got {interpreter!r}")
