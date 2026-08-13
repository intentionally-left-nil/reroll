"""Convert a `markerpry` marker tree into a matchspec `when=` condition.

Implements docs/matchspec.md's marker-to-matchspec conversion table.
"""

from __future__ import annotations

import re

from markerpry import CompareNode, ContainsNode, Node, OperatorNode

from reroll.dependencies.version_format import format_version_literal

_SYS_PLATFORM = {"linux": "__linux", "darwin": "__osx", "win32": "__win"}
_PLATFORM_SYSTEM = {"Linux": "__linux", "Darwin": "__osx", "Windows": "__win"}
_OS_NAME = {"posix": "__unix", "nt": "__win"}
_VIRTUAL_PACKAGE_KEYS = {
    "sys_platform": _SYS_PLATFORM,
    "platform_system": _PLATFORM_SYSTEM,
    "os_name": _OS_NAME,
}
_FULL_VERSION_KEYS = frozenset({"python_full_version", "implementation_version"})
_ORDERED_COMPARATORS = frozenset({">=", ">", "<=", "<"})
_EQUALITY_COMPARATORS = frozenset({"==", "!="})
_MAJOR_MINOR = re.compile(r"(\d+)\.(\d+)")


def marker_condition(node: Node) -> str:
    """`node`'s matchspec `when=` value (unquoted), per
    docs/matchspec.md#marker-to-matchspec-conversion.

    Raises `ValueError` for anything that table can't represent: an
    `in`/`not in` test, `platform_machine` or any other marker key without
    a matchspec equivalent, `!=` (or any comparator besides `==`) against
    `sys_platform`/`platform_system`/`os_name`, or an unrecognized value or
    comparator for a key this function does handle. A marker containing an
    `extra` clause is not this function's concern -- callers should check
    for that (`"extra" in node`) before calling.
    """
    if isinstance(node, OperatorNode):
        return f"{_wrap(node._left)} {node.operator} {_wrap(node._right)}"
    if isinstance(node, ContainsNode):
        raise ValueError(
            f"'in'/'not in' markers are not supported for matchspec conversion: {node}"
        )
    if isinstance(node, CompareNode):
        return _compare_condition(node)
    raise AssertionError(f"unreachable: unexpected marker node type {type(node).__name__}")


def _wrap(node: Node) -> str:
    condition = marker_condition(node)
    return f"({condition})" if isinstance(node, OperatorNode) else condition


def _compare_condition(node: CompareNode) -> str:
    key, comparator, literal = node.key, node.comparator, node.literal
    if key == "python_version":
        return _python_version_condition(comparator, literal)
    if key in _FULL_VERSION_KEYS:
        return _full_version_condition(key, comparator, literal)
    if key in _VIRTUAL_PACKAGE_KEYS:
        return _virtual_package_condition(key, comparator, literal)
    raise ValueError(f"marker key {key!r} has no matchspec equivalent")


def _python_version_condition(comparator: str, literal: str) -> str:
    match = _MAJOR_MINOR.fullmatch(literal)
    if match is None:
        raise ValueError(f"python_version literal {literal!r} is not a major.minor version")
    major, minor = int(match.group(1)), int(match.group(2))
    this_minor = f"{major}.{minor}"
    next_minor = f"{major}.{minor + 1}"
    if comparator == "==":
        return f"python>={this_minor}.0a0,<{next_minor}.0a0"
    if comparator == "!=":
        return f"python!={this_minor}.*"
    if comparator == ">=":
        return f"python>={this_minor}.0a0"
    if comparator == ">":
        return f"python>={next_minor}.0a0"
    if comparator == "<=":
        return f"python<{next_minor}.0a0"
    if comparator == "<":
        return f"python<{this_minor}.0a0"
    raise ValueError(f"comparator {comparator!r} is not supported for python_version")


def _full_version_condition(key: str, comparator: str, literal: str) -> str:
    if comparator not in _EQUALITY_COMPARATORS | _ORDERED_COMPARATORS:
        raise ValueError(f"comparator {comparator!r} is not supported for {key}")
    return f"python{comparator}{format_version_literal(literal)}"


def _virtual_package_condition(key: str, comparator: str, literal: str) -> str:
    if comparator != "==":
        raise ValueError(
            f"comparator {comparator!r} is not supported for {key} "
            "(only '==' can map to a virtual package)"
        )
    mapping = _VIRTUAL_PACKAGE_KEYS[key]
    if literal not in mapping:
        raise ValueError(f"{key} value {literal!r} has no known virtual package mapping")
    return mapping[literal]
