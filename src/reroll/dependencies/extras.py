"""Recognize a `Requires-Dist` entry whose marker is a plain per-extra
clause, and strip that marker so the entry can be converted like any
other dependency.
"""

from __future__ import annotations

from markerpry import CompareNode, parse_marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


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
