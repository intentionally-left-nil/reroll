"""Unit tests for `reroll.dependencies.matchspec_specifier`."""

from __future__ import annotations

import pytest
from packaging.specifiers import SpecifierSet

from reroll.dependencies.matchspec_specifier import specifier_to_matchspec
from reroll.errors import UnconvertableRequirementError


class TestBasics:
    def test_empty_specifier_set_is_the_empty_string(self) -> None:
        assert specifier_to_matchspec(SpecifierSet()) == ""

    def test_a_string_is_parsed_the_same_way_as_a_specifierset(self) -> None:
        assert specifier_to_matchspec(">=2.0.0") == specifier_to_matchspec(SpecifierSet(">=2.0.0"))

    @pytest.mark.parametrize("operator", [">=", "<=", "!=", "=="])
    def test_operator_is_passed_through_as_is(self, operator: str) -> None:
        assert specifier_to_matchspec(f"{operator}2.0.0") == f"{operator}2.0.0"

    def test_multiple_specifiers_are_joined_in_canonical_order(self) -> None:
        assert specifier_to_matchspec(">=1.0.0,<2.0.0") == "<2.0.0a0,>=1.0.0"

    def test_multiple_specifiers_with_the_same_operator_sort_lexicographically(self) -> None:
        assert specifier_to_matchspec("!=1.0.0,!=2.0.0") == "!=1.0.0,!=2.0.0"


class TestExclusiveComparators:
    def test_strict_less_than_gets_the_pre_release_carve_out_anchor(self) -> None:
        assert specifier_to_matchspec("<2.0.0") == "<2.0.0a0"

    def test_strict_less_than_of_a_pre_release_has_no_anchor(self) -> None:
        assert specifier_to_matchspec("<2.0.0rc1", allow_pre=True) == "<2.0.0.rc1"

    def test_strict_greater_than_gets_the_post_release_carve_out_exclusion(self) -> None:
        assert specifier_to_matchspec(">1.0.0") == ">1.0.0,!=1.0.0.post*"

    def test_strict_greater_than_of_a_post_release_has_no_exclusion(self) -> None:
        assert specifier_to_matchspec(">1.0.0.post1") == ">1.0.0.post1"


class TestGlobs:
    def test_equals_glob_is_rewritten_to_the_canonical_fuzzy_form(self) -> None:
        assert specifier_to_matchspec("==1.0.*") == "=1.0"

    def test_not_equals_glob_passes_through_unchanged(self) -> None:
        assert specifier_to_matchspec("!=1.0.*") == "!=1.0.*"


class TestCompatibleRelease:
    def test_compatible_release_expands_to_a_range(self) -> None:
        assert specifier_to_matchspec("~=1.4.2") == ">=1.4.2,<1.5.0a0"

    def test_compatible_release_with_two_segments_drops_the_major(self) -> None:
        assert specifier_to_matchspec("~=1.4") == ">=1.4,<2.0a0"


class TestArbitraryEquality:
    def test_arbitrary_equality_is_converted_to_double_equals(self) -> None:
        assert specifier_to_matchspec("===1.0.0") == "==1.0.0"

    def test_arbitrary_equality_against_a_non_pep440_string_passes_through(self) -> None:
        assert specifier_to_matchspec("===not-a-version") == "==not-a-version"


class TestRejections:
    def test_rejects_a_local_version_label(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="local version label"):
            specifier_to_matchspec("==1.0.0+local")

    def test_local_version_label_error_names_the_specifier(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match=r"==1\.0\.0\+local"):
            specifier_to_matchspec("==1.0.0+local")

    def test_rejects_a_pre_release_version_by_default(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="pre-release"):
            specifier_to_matchspec("==1.0.0rc1")

    def test_allow_pre_permits_a_pre_release_version(self) -> None:
        assert specifier_to_matchspec("==1.0.0rc1", allow_pre=True) == "==1.0.0.rc1"

    def test_allow_pre_still_rejects_a_local_version_label(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="local version label"):
            specifier_to_matchspec("==1.0.0+local", allow_pre=True)
