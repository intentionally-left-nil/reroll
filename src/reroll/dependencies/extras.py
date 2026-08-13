"""Find every `extra` name a wheel's `Requires-Dist` entries reference."""

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
    deeply it's nested in a boolean expression. `Provides-Extra` is not
    consulted; this is the sole source of truth for which extras a
    package has.

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
