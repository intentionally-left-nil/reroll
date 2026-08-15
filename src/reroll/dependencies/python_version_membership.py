"""Rewrite a `python_version in "..."` or `python_version not in "..."`
marker clause into an equivalent `==`/`!=`-chain, per
docs/matchspec.md#dealing-with-in-and-not-in's exhaustive-expansion note.
"""

from __future__ import annotations

from typing import TypeGuard

from markerpry import FALSE, TRUE, CompareNode, ContainsNode, LeafModifier, Node, OperatorNode


def is_python_version_in_literal(node: Node) -> TypeGuard[ContainsNode]:
    """Whether `node` is the shape `rewrite_python_version_in_modifier`
    rewrites: `python_version in "<literal>"`, key on the left, not
    negated. Exposed separately so a caller that needs to decide whether
    to bother resolving `max_minor` at all (e.g. its `latest_python_minor`
    network/cache lookup) can check this first.
    """
    return (
        isinstance(node, ContainsNode)
        and node.key == "python_version"
        and node.key_on_left
        and not node.negate
    )


def rewrite_python_version_in_modifier(max_minor: int) -> LeafModifier:
    """Builds a `markerpry` leaf modifier (pass to `Node.modify(leaf=...)`)
    that rewrites every `python_version in "<literal>"` clause into an
    `or`-chain of `python_version == "3.<minor>"` comparisons, one term
    for each `minor` from `0` through `max_minor` whose `"3.<minor>"`
    form appears as a substring of `<literal>`.

    This reproduces the clause's original substring-membership semantics
    exactly (`markerpry.constraint.StringConstraint`) without parsing
    `<literal>`'s separator style, so both space- and comma-separated
    lists (`"3.2 3.3 3.4"`, `"3.2,3.3,3.4"`) convert the same way.
    Collapses to `FALSE` if no candidate minor matches.

    Every other leaf -- `not in`, membership with the key on the right
    (`"<literal>" in python_version`), any other marker key, or a
    non-`ContainsNode` leaf -- passes through unchanged.
    """

    def leaf(node: Node) -> Node:
        if is_python_version_in_literal(node):
            return _exploded_chain(node.literal, max_minor)
        return node

    return leaf


def is_python_version_not_in_literal(node: Node) -> TypeGuard[ContainsNode]:
    """Whether `node` is the shape `rewrite_python_version_not_in_modifier`
    rewrites: `python_version not in "<literal>"`, key on the left,
    negated. Exposed separately for the same reason as
    `is_python_version_in_literal`.
    """
    return (
        isinstance(node, ContainsNode)
        and node.key == "python_version"
        and node.key_on_left
        and node.negate
    )


def rewrite_python_version_not_in_modifier(max_minor: int) -> LeafModifier:
    """Builds a `markerpry` leaf modifier (pass to `Node.modify(leaf=...)`)
    that rewrites every `python_version not in "<literal>"` clause into an
    `and`-chain of `python_version != "3.<minor>"` comparisons, one term
    for each `minor` from `0` through `max_minor` whose `"3.<minor>"`
    form appears as a substring of `<literal>`.

    `not in` holds for exactly the candidate minors `in` excludes, so this
    is `rewrite_python_version_in_modifier`'s complement: the same
    substring detection, but each matched minor becomes a `!=` term
    instead of a dropped one, and the terms join with `and` instead of
    `or`. Collapses to `TRUE` if no candidate minor matches -- `not in`
    then holds unconditionally, for every candidate minor.

    Every other leaf -- `in`, membership with the key on the right
    (`"<literal>" not in python_version`), any other marker key, or a
    non-`ContainsNode` leaf -- passes through unchanged.
    """

    def leaf(node: Node) -> Node:
        if is_python_version_not_in_literal(node):
            return _excluded_chain(node.literal, max_minor)
        return node

    return leaf


def _exploded_chain(literal: str, max_minor: int) -> Node:
    matches: list[Node] = [
        CompareNode("python_version", "==", f"3.{minor}")
        for minor in range(max_minor + 1)
        if f"3.{minor}" in literal
    ]
    if not matches:
        return FALSE
    result = matches[0]
    for match in matches[1:]:
        result = OperatorNode.combine("or", result, match)
    return result


def _excluded_chain(literal: str, max_minor: int) -> Node:
    matches: list[Node] = [
        CompareNode("python_version", "!=", f"3.{minor}")
        for minor in range(max_minor + 1)
        if f"3.{minor}" in literal
    ]
    if not matches:
        return TRUE
    result = matches[0]
    for match in matches[1:]:
        result = OperatorNode.combine("and", result, match)
    return result
