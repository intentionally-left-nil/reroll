"""Unit tests for `reroll.dependencies.python_version_membership`."""

from __future__ import annotations

import pytest
from markerpry import FALSE, TRUE, Node, evaluate, parse
from packaging.markers import Marker

from reroll.dependencies.python_version_membership import (
    rewrite_python_version_in_modifier,
    rewrite_python_version_not_in_modifier,
)


def _explode(marker_str: str, max_minor: int) -> Node:
    return parse(marker_str).modify(leaf=rewrite_python_version_in_modifier(max_minor))


def _exclude(marker_str: str, max_minor: int) -> Node:
    return parse(marker_str).modify(leaf=rewrite_python_version_not_in_modifier(max_minor))


class TestSpaceAndCommaSeparatedLists:
    """`"3.2 3.3 3.4"` and `"3.2,3.3,3.4"` are the two list spellings the
    task calls out by name; both convert to the same `or`-chain because
    the conversion never parses the literal's separator at all.
    """

    def test_space_separated_list_becomes_an_or_chain_of_equalities(self) -> None:
        result = _explode('python_version in "3.2 3.3 3.4"', max_minor=6)

        assert str(result) == (
            'python_version == "3.2" or python_version == "3.3" or python_version == "3.4"'
        )

    def test_comma_separated_list_converts_the_same_as_space_separated(self) -> None:
        space_separated = _explode('python_version in "3.2 3.3 3.4"', max_minor=6)
        comma_separated = _explode('python_version in "3.2,3.3,3.4"', max_minor=6)

        assert str(space_separated) == str(comma_separated)

    def test_comma_space_separated_list_also_converts_the_same_way(self) -> None:
        result = _explode('python_version in "3.2, 3.3, 3.4"', max_minor=6)

        assert str(result) == (
            'python_version == "3.2" or python_version == "3.3" or python_version == "3.4"'
        )

    def test_output_order_follows_minor_order_not_the_literal_s_order(self) -> None:
        result = _explode('python_version in "3.4,3.2,3.3"', max_minor=6)

        assert str(result) == (
            'python_version == "3.2" or python_version == "3.3" or python_version == "3.4"'
        )


class TestMaxMinorBoundary:
    def test_minor_beyond_max_minor_is_not_emitted(self) -> None:
        result = _explode('python_version in "3.2 3.3 3.9"', max_minor=5)

        assert str(result) == 'python_version == "3.2" or python_version == "3.3"'

    def test_single_matching_minor_produces_a_bare_equality_not_a_chain(self) -> None:
        result = _explode('python_version in "3.2"', max_minor=6)

        assert str(result) == 'python_version == "3.2"'

    def test_no_minor_within_range_collapses_to_false(self) -> None:
        result = _explode('python_version in "not a version list"', max_minor=6)

        assert result == FALSE


class TestNonMatchingLeavesPassThroughUnchanged:
    """Anything besides `python_version in "<literal>"` (key on the left,
    not negated) is out of scope for this conversion and must come back
    identical -- checked via `is`, since every node type is frozen and
    `modify()` is documented to return the original object when nothing
    changed.
    """

    def test_not_in_passes_through_unchanged(self) -> None:
        node = parse('python_version not in "3.2 3.3"')

        assert node.modify(leaf=rewrite_python_version_in_modifier(6)) is node

    def test_literal_on_the_left_passes_through_unchanged(self) -> None:
        node = parse('"3.2" in python_version')

        assert node.modify(leaf=rewrite_python_version_in_modifier(6)) is node

    def test_other_key_passes_through_unchanged(self) -> None:
        node = parse('sys_platform in "linux darwin"')

        assert node.modify(leaf=rewrite_python_version_in_modifier(6)) is node

    def test_non_contains_leaf_passes_through_unchanged(self) -> None:
        node = parse('python_version == "3.9"')

        assert node.modify(leaf=rewrite_python_version_in_modifier(6)) is node


class TestEquivalenceWithPipEvaluation:
    """Confirms the exploded chain isn't just plausible-looking but
    actually decides the same way `packaging.markers.Marker` (pip/uv's
    own marker evaluator) does for every candidate `python_version` from
    `3.0` through `3.<max_minor>` -- including the substring-membership
    quirk where `"3.1"` is itself a substring of `"3.10"`.
    """

    @pytest.mark.parametrize(
        "literal",
        ["3.2 3.3 3.4", "3.2,3.3,3.4", "3.1 3.10", "3.10", "no versions here"],
    )
    def test_exploded_chain_agrees_with_pip_for_every_candidate_minor(self, literal: str) -> None:
        max_minor = 12
        marker_str = f'python_version in "{literal}"'
        exploded = _explode(marker_str, max_minor)

        for minor in range(max_minor + 1):
            candidate = f"3.{minor}"
            pip_result = Marker(marker_str).evaluate({"python_version": candidate})
            our_result = evaluate(exploded, {"python_version": [candidate]})
            assert bool(our_result) == pip_result, candidate


class TestNotInSpaceAndCommaSeparatedLists:
    """`rewrite_python_version_not_in_modifier`'s complement of
    `TestSpaceAndCommaSeparatedLists`: same substring detection, but each
    matched minor becomes a `!=` term, joined with `and` instead of `or`.
    """

    def test_space_separated_list_becomes_an_and_chain_of_inequalities(self) -> None:
        result = _exclude('python_version not in "3.2 3.3 3.4"', max_minor=6)

        assert str(result) == (
            'python_version != "3.2" and python_version != "3.3" and python_version != "3.4"'
        )

    def test_comma_separated_list_converts_the_same_as_space_separated(self) -> None:
        space_separated = _exclude('python_version not in "3.2 3.3 3.4"', max_minor=6)
        comma_separated = _exclude('python_version not in "3.2,3.3,3.4"', max_minor=6)

        assert str(space_separated) == str(comma_separated)

    def test_comma_space_separated_list_also_converts_the_same_way(self) -> None:
        result = _exclude('python_version not in "3.2, 3.3, 3.4"', max_minor=6)

        assert str(result) == (
            'python_version != "3.2" and python_version != "3.3" and python_version != "3.4"'
        )

    def test_output_order_follows_minor_order_not_the_literal_s_order(self) -> None:
        result = _exclude('python_version not in "3.4,3.2,3.3"', max_minor=6)

        assert str(result) == (
            'python_version != "3.2" and python_version != "3.3" and python_version != "3.4"'
        )


class TestNotInMaxMinorBoundary:
    def test_minor_beyond_max_minor_is_not_emitted(self) -> None:
        result = _exclude('python_version not in "3.2 3.3 3.9"', max_minor=5)

        assert str(result) == 'python_version != "3.2" and python_version != "3.3"'

    def test_single_matching_minor_produces_a_bare_inequality_not_a_chain(self) -> None:
        result = _exclude('python_version not in "3.2"', max_minor=6)

        assert str(result) == 'python_version != "3.2"'

    def test_no_minor_within_range_collapses_to_true(self) -> None:
        result = _exclude('python_version not in "not a version list"', max_minor=6)

        assert result == TRUE


class TestNotInLeavesPassThroughUnchanged:
    """Anything besides `python_version not in "<literal>"` (key on the
    left, negated) is out of scope for this conversion and must come back
    identical -- checked via `is`, same as
    `TestNonMatchingLeavesPassThroughUnchanged`.
    """

    def test_in_passes_through_unchanged(self) -> None:
        node = parse('python_version in "3.2 3.3"')

        assert node.modify(leaf=rewrite_python_version_not_in_modifier(6)) is node

    def test_literal_on_the_left_passes_through_unchanged(self) -> None:
        node = parse('"3.2" not in python_version')

        assert node.modify(leaf=rewrite_python_version_not_in_modifier(6)) is node

    def test_other_key_passes_through_unchanged(self) -> None:
        node = parse('sys_platform not in "linux darwin"')

        assert node.modify(leaf=rewrite_python_version_not_in_modifier(6)) is node

    def test_non_contains_leaf_passes_through_unchanged(self) -> None:
        node = parse('python_version == "3.9"')

        assert node.modify(leaf=rewrite_python_version_not_in_modifier(6)) is node


class TestNotInEquivalenceWithPipEvaluation:
    """`TestEquivalenceWithPipEvaluation`'s complement for `not in`."""

    @pytest.mark.parametrize(
        "literal",
        ["3.2 3.3 3.4", "3.2,3.3,3.4", "3.1 3.10", "3.10", "no versions here"],
    )
    def test_excluded_chain_agrees_with_pip_for_every_candidate_minor(self, literal: str) -> None:
        max_minor = 12
        marker_str = f'python_version not in "{literal}"'
        excluded = _exclude(marker_str, max_minor)

        for minor in range(max_minor + 1):
            candidate = f"3.{minor}"
            pip_result = Marker(marker_str).evaluate({"python_version": candidate})
            our_result = evaluate(excluded, {"python_version": [candidate]})
            assert bool(our_result) == pip_result, candidate
