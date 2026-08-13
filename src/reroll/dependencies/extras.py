"""Recognize a `Requires-Dist` entry whose marker is a plain per-extra
clause, and strip that marker so the entry can be converted like any
other dependency.
"""

from __future__ import annotations

from collections.abc import Iterator

from markerpry import CompareNode, Node, parse_marker
from packaging.requirements import Requirement
from packaging.utils import NormalizedName, canonicalize_name


def find_extras(requires_dist: tuple[str, ...]) -> set[NormalizedName]:
    """Every extra name `requires_dist` references in a marker, PEP
    503-normalized with `canonicalize_name` and deduplicated
    (docs/wheel_to_conda_dependencies.md#calculating-extras).

    Searches each entry's full marker tree for every comparison against
    the `extra` key, regardless of comparator (`==`, `!=`, ...) or how
    deeply it's nested in a boolean expression -- unlike `extra_marker_entry`,
    this doesn't require the marker to be a single bare `extra == "name"`
    clause. `Provides-Extra` is not consulted; this is the sole source of
    truth for which extras a package has.

    This is still pypi-side normalization only -- an extra name over
    CEP-29's 64-character conda limit is returned as-is, not rejected here;
    that's the job of whatever later converts the name to conda syntax
    (docs/matchspec.md#extras-name-normalization).
    """
    extras: set[NormalizedName] = set()
    for entry in requires_dist:
        requirement = Requirement(entry)
        if requirement.marker is None:
            continue
        for literal in _extra_literals(parse_marker(requirement.marker)):
            extras.add(canonicalize_name(literal))
    return extras


def extra_marker_entry(entry: str) -> tuple[str | None, str]:
    """`(extra_name, entry_without_its_marker)` if `entry`'s marker is
    exactly a single `extra == "value"` (or `"value" == extra`) comparison
    and `entry` carries no extras of its own (`name[extra]`).
    `extra_name` is normalized the same way a dependency name is
    (`canonicalize_name`).

    `(None, entry)` unchanged for a marker-free entry, an entry with its
    own extras (a separate, not-yet-supported conversion), or any marker
    more complex than that single clause -- including a conditional
    combination like `extra == "foo" or extra == "bar"`
    (docs/wheel_to_conda_dependencies.md). Parsed via `markerpry` so the
    same `Node` tree can grow to recognize richer, multi-clause extra
    markers later without a rewrite.
    """
    requirement = Requirement(entry)
    if requirement.marker is None or requirement.extras:
        return None, entry
    node = parse_marker(requirement.marker)
    if not isinstance(node, CompareNode) or node.key != "extra" or node.comparator != "==":
        return None, entry
    return canonicalize_name(node.literal), _strip_marker(requirement)


def _strip_marker(requirement: Requirement) -> str:
    parts = [requirement.name]
    if requirement.specifier:
        parts.append(str(requirement.specifier))
    return "".join(parts)


def _extra_literals(node: Node) -> Iterator[str]:
    """Every literal compared against the `extra` key anywhere in `node`,
    including both sides of an `and`/`or` chain.
    """
    if isinstance(node, CompareNode) and node.key == "extra":
        yield node.literal
    if node.left is not None:
        yield from _extra_literals(node.left)
    if node.right is not None:
        yield from _extra_literals(node.right)
