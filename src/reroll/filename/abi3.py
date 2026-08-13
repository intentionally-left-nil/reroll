"""Explode `abi3`/`abi3t` ABI tags into one concrete per-minor CPython tag
each, since neither is itself an installable target.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from packaging.tags import Tag

from reroll.errors import RerollError
from reroll.filename.abi import check_interpreter_abi
from reroll.filename.interpreter import parse_interpreter
from reroll.filename.python_latest_release import latest_python_minor

_STABLE_ABIS = ("abi3", "abi3t")
_MINOR_ONLY_RE = re.compile(r"^3\.(\d+)$")


def explode_abi3(tags: Iterable[Tag], *, abi3_upper_bound: str | None = None) -> frozenset[Tag]:
    """Replace every `cp3X-abi3-*`/`cp3X-abi3t-*` tag in `tags` with one
    `cp3Y-cp3Y-*`/`cp3Y-cp3Yt-*` tag per minor `Y` from `X` up through
    `abi3_upper_bound` (a minor-only version string like `"3.15"`;
    `abi3_upper_bound="3.15.3"` is rejected). A stable-ABI tag whose own
    `(interpreter, abi)` pairing is illegal -- a bad interpreter shape, or a
    floor below the ABI's own (`check_interpreter_abi`) -- or whose floor is
    already past `abi3_upper_bound` (so its per-minor range would be empty)
    is left unchanged rather than exploded, since `WheelConfig` never
    accepts a raw `abi3`/`abi3t` tag and will reject it there instead. All
    other tags pass through unchanged too. The result is deduplicated
    against both itself and any unrelated tags already present.

    `abi3_upper_bound=None` defers to `latest_python_minor`, but only once
    at least one stable-ABI tag is actually present -- a wheel with no
    `abi3`/`abi3t` tag never triggers that lookup (nor its network/cache
    cost).
    """
    tags = frozenset(tags)
    if not any(tag.abi in _STABLE_ABIS for tag in tags):
        return tags

    upper_minor = _resolve_upper_bound(abi3_upper_bound)
    exploded: set[Tag] = set()
    for tag in tags:
        if tag.abi in _STABLE_ABIS:
            exploded |= _explode_one(tag, upper_minor)
        else:
            exploded.add(tag)
    return frozenset(exploded)


def _explode_one(tag: Tag, upper_minor: int) -> frozenset[Tag]:
    if not _is_legal_pairing(tag.interpreter, tag.abi):
        return frozenset({tag})

    _, _, floor_minor = parse_interpreter(tag.interpreter)
    if floor_minor > upper_minor:
        # A legal pairing whose floor is already past `abi3_upper_bound`
        # explodes to an empty range -- leave it unchanged instead, so it
        # still reaches `WheelConfig`'s raw-`abi3`/`abi3t` rejection rather
        # than vanishing without a trace.
        return frozenset({tag})
    free_threaded = tag.abi == "abi3t"
    return frozenset(
        Tag(f"cp3{minor}", f"cp3{minor}t" if free_threaded else f"cp3{minor}", tag.platform)
        for minor in range(floor_minor, upper_minor + 1)
    )


def _is_legal_pairing(interpreter: str, abi: str) -> bool:
    try:
        check_interpreter_abi(interpreter, abi)
    except RerollError:
        return False
    return True


def _resolve_upper_bound(abi3_upper_bound: str | None) -> int:
    if abi3_upper_bound is None:
        return latest_python_minor()

    match = _MINOR_ONLY_RE.match(abi3_upper_bound)
    if match is None:
        raise ValueError(
            "abi3_upper_bound must be a minor version like '3.15' "
            f"(not a patch version): {abi3_upper_bound!r}"
        )
    return int(match.group(1))
