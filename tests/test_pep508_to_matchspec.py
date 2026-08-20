"""Unit tests for `reroll.dependencies.pep508_to_matchspec`."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest
from packaging.utils import NormalizedName

from reroll.dependencies.pep508_to_matchspec import pep508_to_matchspec
from reroll.errors import (
    InvalidRequirementError,
    UnconvertableMarkerError,
    UnconvertablePythonVersionEqualityError,
    UnconvertableRequirementError,
    UnresolvedCondaNameError,
)
from reroll.name_mapping import (
    Candidate,
    aggregator_mapper,
    passthrough_mapper,
    static_mapper,
)


def _unresolved_mapper(
    name: NormalizedName, candidates: Sequence[Candidate]
) -> str | Sequence[Candidate]:
    """A `NameMapper` that never resolves anything, so `map_name` always
    ends the chain with `UnresolvedCondaNameError`.
    """
    del name
    return candidates


# --------------------------------------------------------------------------
# Name
# --------------------------------------------------------------------------


class TestName:
    def test_bare_name_maps_through_the_chain(self) -> None:
        mappers = (static_mapper({"requests": "python-requests"}), aggregator_mapper)

        assert pep508_to_matchspec("requests", mappers) == "python-requests"

    def test_bare_name_with_no_mapper_opinion_falls_back_to_normalized_name(self) -> None:
        assert pep508_to_matchspec("Requests", (passthrough_mapper,)) == "requests"

    def test_bare_name_with_no_mapper_opinion_normalizes_separators_too(self) -> None:
        assert pep508_to_matchspec("Foo_Bar.BAZ", (passthrough_mapper,)) == "foo-bar-baz"

    def test_versioned_dependency_maps_the_name_too(self) -> None:
        mappers = (static_mapper({"requests": "python-requests"}), aggregator_mapper)

        assert pep508_to_matchspec("requests>=2.0.0", mappers) == "python-requests >=2.0.0"

    def test_name_is_normalized_before_reaching_the_mapper_chain(self) -> None:
        """A mapper table keyed by the canonical name still hits even when
        the requirement string spells the name with different case and
        separators.
        """
        mappers = (static_mapper({"foo-bar-baz": "conda-foo-bar-baz"}), aggregator_mapper)

        assert pep508_to_matchspec("Foo_Bar.BAZ", mappers) == "conda-foo-bar-baz"

    def test_unresolved_name_raises(self) -> None:
        with pytest.raises(UnresolvedCondaNameError, match="requests"):
            pep508_to_matchspec("requests", (_unresolved_mapper,))


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------


class TestOperators:
    @pytest.mark.parametrize("operator", [">=", "<=", "!="])
    def test_operator_is_passed_through_as_is(self, operator: str) -> None:
        assert (
            pep508_to_matchspec(f"requests{operator}2.0.0", (passthrough_mapper,))
            == f"requests {operator}2.0.0"
        )

    def test_strict_less_than_gets_the_pre_release_carve_out_anchor(self) -> None:
        """`<V` for a non-pre-release `V` anchors at `<Va0` -- an `a0`
        pre-release tag glued directly onto `V`'s own conda-spelled
        version, no separating dot -- below every pre-release of `V`. See
        `TestExclusiveComparatorPrePostReleaseCarveOut` in
        `test_version_matchspec_equivalence.py` for why the dot matters.
        """
        assert pep508_to_matchspec("requests<2.0.0", (passthrough_mapper,)) == "requests <2.0.0a0"

    def test_strict_less_than_carve_out_anchor_with_a_missing_patch_segment(self) -> None:
        """The anchor glues `a0` onto `V` exactly as written, without
        inserting a synthetic patch segment first -- `<2.0.a0` (not
        `<2.0.0a0`) is what excludes every pre-release of the two-segment
        boundary `2.0`, `2.0.0.dev0` included. See
        `TestExclusiveComparatorPrePostReleaseCarveOut`'s
        `test_strict_less_than_with_a_missing_patch_segment_still_excludes_a_same_shape_dev_release`
        for why the synthetic-zero spelling is wrong.
        """
        assert pep508_to_matchspec("requests<2.0", (passthrough_mapper,)) == "requests <2.0a0"

    def test_strict_greater_than_gets_the_post_release_carve_out_exclusion(self) -> None:
        assert (
            pep508_to_matchspec("requests>2.0.0", (passthrough_mapper,))
            == "requests >2.0.0,!=2.0.0.post*"
        )

    @pytest.mark.parametrize("operator", ["<", ">"])
    def test_strict_less_or_greater_than_rejects_a_pre_release_by_default(
        self, operator: str
    ) -> None:
        with pytest.raises(UnconvertableRequirementError, match="pre-release"):
            pep508_to_matchspec(f"requests{operator}2.0.0rc1", (passthrough_mapper,))

    def test_strict_less_than_allow_pre_permits_a_pre_release_boundary(self) -> None:
        """A pre-release boundary needs no carve-out anchor -- see
        `TestExclusiveComparatorPrePostReleaseCarveOut` in
        `test_version_matchspec_equivalence.py`.
        """
        assert (
            pep508_to_matchspec("requests<2.0.0rc1", (passthrough_mapper,), allow_pre=True)
            == "requests <2.0.0.rc1"
        )

    def test_strict_greater_than_allow_pre_permits_a_pre_release_boundary(self) -> None:
        assert (
            pep508_to_matchspec("requests>2.0.0rc1", (passthrough_mapper,), allow_pre=True)
            == "requests >2.0.0.rc1,!=2.0.0.rc1.post*"
        )

    def test_arbitrary_equality_is_converted_to_double_equals(self) -> None:
        assert pep508_to_matchspec("requests===2.0.0", (passthrough_mapper,)) == "requests ==2.0.0"

    def test_arbitrary_equality_against_a_non_pep440_string_passes_through(self) -> None:
        assert (
            pep508_to_matchspec("requests===some-weird-string", (passthrough_mapper,))
            == "requests ==some-weird-string"
        )

    def test_multiple_specifiers_are_joined_in_canonical_order(self) -> None:
        assert (
            pep508_to_matchspec("requests<=2.0.0,!=1.0.1,>=0.9", (passthrough_mapper,))
            == "requests >=0.9,<=2.0.0,!=1.0.1"
        )

    def test_multiple_specifiers_with_the_same_operator_sort_lexicographically(self) -> None:
        """Canonical order sorts by the specifier's string form, so two
        `>=` bounds of different digit lengths land in string, not
        numeric, order.
        """
        assert (
            pep508_to_matchspec("requests>=9.0,>=10.0", (passthrough_mapper,))
            == "requests >=10.0,>=9.0"
        )

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ("click==8.*", "click =8"),
            ("click==1.0.*", "click =1.0"),
        ],
    )
    def test_equals_glob_is_rewritten_to_the_canonical_fuzzy_form(
        self, entry: str, expected: str
    ) -> None:
        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == expected

    def test_equals_glob_with_an_epoch_is_rewritten_to_the_canonical_fuzzy_form(self) -> None:
        assert pep508_to_matchspec("requests==1!2.0.*", (passthrough_mapper,)) == "requests =1!2.0"

    @pytest.mark.parametrize(
        "entry",
        ["click!=8.*", "click!=1.0.*"],
    )
    def test_not_equals_glob_passes_through_unchanged(self, entry: str) -> None:
        name, _, version = entry.partition("!=")

        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == f"{name} !={version}"

    def test_not_equals_glob_with_an_epoch_passes_through_unchanged(self) -> None:
        assert (
            pep508_to_matchspec("requests!=1!2.0.*", (passthrough_mapper,)) == "requests !=1!2.0.*"
        )

    def test_compatible_release_expands_to_a_range(self) -> None:
        assert (
            pep508_to_matchspec("requests~=3.13.2", (passthrough_mapper,))
            == "requests >=3.13.2,<3.14.0a0"
        )

    def test_compatible_release_with_two_segments_drops_the_major(self) -> None:
        assert (
            pep508_to_matchspec("requests~=3.13", (passthrough_mapper,)) == "requests >=3.13,<4.0a0"
        )

    def test_compatible_release_with_four_segments_only_bumps_the_last(self) -> None:
        assert (
            pep508_to_matchspec("requests~=1.2.3.4", (passthrough_mapper,))
            == "requests >=1.2.3.4,<1.2.4.0a0"
        )

    def test_compatible_release_preserves_the_epoch_in_both_bounds(self) -> None:
        assert (
            pep508_to_matchspec("requests~=1!3.13.2", (passthrough_mapper,))
            == "requests >=1!3.13.2,<1!3.14.0a0"
        )

    def test_compatible_release_combines_with_another_specifier_in_canonical_order(self) -> None:
        """`~=`'s pin category sorts before `!=`'s exclusion category, so
        the `~=` expansion's `>=`/`<` pair comes first even though `!=`
        sorts earlier alphabetically.
        """
        assert (
            pep508_to_matchspec("requests~=3.13.2,!=3.13.5", (passthrough_mapper,))
            == "requests >=3.13.2,<3.14.0a0,!=3.13.5"
        )

    def test_compatible_release_rejects_a_pre_release_by_default(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="pre-release"):
            pep508_to_matchspec("requests~=3.13.2rc1", (passthrough_mapper,))

    def test_compatible_release_allow_pre_permits_a_pre_release(self) -> None:
        assert (
            pep508_to_matchspec("requests~=3.13.2rc1", (passthrough_mapper,), allow_pre=True)
            == "requests >=3.13.2.rc1,<3.14.0a0"
        )

    def test_version_with_a_v_prefix_is_normalized_away(self) -> None:
        assert pep508_to_matchspec("requests>=v1.0", (passthrough_mapper,)) == "requests >=1.0"

    def test_arbitrary_equality_against_a_glob_like_string_is_not_rewritten(self) -> None:
        """`===`'s value never goes through `Version`'s wildcard-aware
        specifier parsing -- `"1.0.*"` isn't a valid bare `Version`, so it
        falls through to the literal passthrough, unlike a real `==`.
        """
        assert pep508_to_matchspec("requests===1.0.*", (passthrough_mapper,)) == "requests ==1.0.*"

    def test_arbitrary_equality_rejects_a_pre_release_version(self) -> None:
        """When `===`'s value happens to parse as a real PEP 440 version,
        it is still subject to the same pre-release check as `==`.
        """
        with pytest.raises(UnconvertableRequirementError, match="pre-release"):
            pep508_to_matchspec("requests===1.0.0rc1", (passthrough_mapper,))

    def test_arbitrary_equality_allow_pre_permits_a_pre_release_version(self) -> None:
        assert (
            pep508_to_matchspec("requests===1.0.0rc1", (passthrough_mapper,), allow_pre=True)
            == "requests ==1.0.0.rc1"
        )


# --------------------------------------------------------------------------
# Version conda-style formatting
# --------------------------------------------------------------------------


class TestVersionFormatting:
    def test_epoch_is_preserved(self) -> None:
        assert (
            pep508_to_matchspec("requests>=1!1.0.0", (passthrough_mapper,)) == "requests >=1!1.0.0"
        )

    def test_post_release_is_accepted(self) -> None:
        assert (
            pep508_to_matchspec("requests>=1.0.0.post1", (passthrough_mapper,))
            == "requests >=1.0.0.post1"
        )

    def test_post_release_shorthand_is_normalized(self) -> None:
        assert (
            pep508_to_matchspec("requests>=1.0-1", (passthrough_mapper,)) == "requests >=1.0.post1"
        )

    def test_pre_release_is_dotted_when_allowed(self) -> None:
        assert (
            pep508_to_matchspec("requests==1.0.0rc1", (passthrough_mapper,), allow_pre=True)
            == "requests ==1.0.0.rc1"
        )

    def test_many_release_segments_are_all_preserved(self) -> None:
        assert (
            pep508_to_matchspec("requests>=1.2.3.4", (passthrough_mapper,)) == "requests >=1.2.3.4"
        )

    def test_strict_less_than_carve_out_anchor_preserves_every_release_segment(self) -> None:
        assert (
            pep508_to_matchspec("requests<1.2.3.4", (passthrough_mapper,)) == "requests <1.2.3.4a0"
        )

    def test_strict_greater_than_carve_out_exclusion_preserves_every_release_segment(
        self,
    ) -> None:
        assert (
            pep508_to_matchspec("requests>1.2.3.4", (passthrough_mapper,))
            == "requests >1.2.3.4,!=1.2.3.4.post*"
        )

    def test_pre_post_and_dev_releases_all_combine_in_order(self) -> None:
        assert (
            pep508_to_matchspec(
                "requests==1.0.0a1.post2.dev3", (passthrough_mapper,), allow_pre=True
            )
            == "requests ==1.0.0.a1.post2.dev3"
        )


class TestRejections:
    @pytest.mark.parametrize(
        "entry",
        [
            "requests==1.0.0+local",
            "requests!=1.0.0+local",
            "requests===1.0.0+local",
        ],
    )
    def test_rejects_a_local_version_label(self, entry: str) -> None:
        with pytest.raises(UnconvertableRequirementError, match="local version label"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_local_version_label_error_names_the_entry(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="requests==1.0.0\\+local"):
            pep508_to_matchspec("requests==1.0.0+local", (passthrough_mapper,))

    @pytest.mark.parametrize(
        "entry",
        ["requests<1.0.0+local", "requests>1.0.0+local"],
    )
    def test_local_version_label_with_strict_less_or_greater_than_is_rejected_by_packaging(
        self, entry: str
    ) -> None:
        """Unlike `==`/`!=`/`===`, a local label combined with `<`/`>`
        never reaches reroll's own local-version-label check
        (`_reject_unsupported_version`, exercised by
        `test_rejects_a_local_version_label` above): `packaging`'s PEP 508
        parser rejects the combination itself ("Local version label can
        only be used with `==` or `!=` operators"), so `Requirement(entry)`
        already raises before `_convert_exclusive_comparator` runs --
        surfacing as `InvalidRequirementError`, not
        `UnconvertableRequirementError`.
        """
        with pytest.raises(InvalidRequirementError):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_rejects_a_direct_url_reference(self) -> None:
        entry = "requests @ https://example.com/requests-1.0.0.whl"

        with pytest.raises(UnconvertableRequirementError, match="direct URL"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    @pytest.mark.parametrize(
        "entry",
        [
            "requests==1.0.0dev1",
            "requests==1.0.0a1",
            "requests==1.0.0b1",
            "requests==1.0.0rc1",
        ],
    )
    def test_rejects_a_pre_release_version_by_default(self, entry: str) -> None:
        with pytest.raises(UnconvertableRequirementError, match="pre-release"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_pre_release_version_error_names_the_entry(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="requests==1.0.0rc1"):
            pep508_to_matchspec("requests==1.0.0rc1", (passthrough_mapper,))

    def test_dev_release_combined_with_a_post_release_is_still_a_pre_release(self) -> None:
        """A dev component makes a version a pre-release even alongside a
        post-release component, which alone would not be.
        """
        with pytest.raises(UnconvertableRequirementError, match="pre-release"):
            pep508_to_matchspec("requests==1.0.0.post1.dev1", (passthrough_mapper,))

    def test_allow_pre_still_rejects_a_local_version_label(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="local version label"):
            pep508_to_matchspec("requests==1.0.0+local", (passthrough_mapper,), allow_pre=True)

    def test_local_version_label_is_rejected_before_pre_release_even_with_allow_pre(self) -> None:
        """A local label is checked unconditionally, ahead of the
        pre-release check that `allow_pre` would otherwise satisfy.
        """
        with pytest.raises(UnconvertableRequirementError, match="local version label"):
            pep508_to_matchspec("requests==1.0.0rc1+local", (passthrough_mapper,), allow_pre=True)

    def test_direct_url_rejection_takes_precedence_over_an_unresolved_name(self) -> None:
        entry = "unmapped-pkg @ https://example.com/pkg.whl"

        with pytest.raises(UnconvertableRequirementError, match="direct URL"):
            pep508_to_matchspec(entry, (_unresolved_mapper,))

    def test_extra_marker_rejection_takes_precedence_over_a_direct_url(self) -> None:
        entry = 'requests @ https://example.com/pkg.whl ; extra == "foo"'

        with pytest.raises(UnconvertableRequirementError, match="extra"):
            pep508_to_matchspec(entry, (passthrough_mapper,))


# --------------------------------------------------------------------------
# Malformed `entry`: `Requirement(entry)` itself fails to parse
# --------------------------------------------------------------------------


class TestMalformedEntryLeaksInvalidRequirement:
    """A caller-assembled `entry` that fails PEP 508 parsing raises
    `InvalidRequirementError`, not a bare `packaging.requirements.InvalidRequirement`.

    One concrete way `calculate_dependencies` assembles such an `entry`:
    `markerpry`'s `CompareNode.__str__` (`markerpry/node.py:99`)
    unconditionally wraps a marker literal in a fresh, unescaped pair of
    double quotes, corrupting round-tripping for any literal that already
    contains a `"` -- reachable via a pre-PEP-508 marker abusing `extra` to
    smuggle a `python_version` literal, which PEP 685's `canonicalize_name`
    then re-quotes on parse (`bcdoc-0.15.0`'s real
    `extra == ':python_version=="2.6"'` becomes the literal
    `':python-version=="2-6"'`, then `str()`-renders as the doubly-quoted,
    unparseable `extra == ":python-version=="2-6""`). `entry` below is
    exactly that rendered string, standing in for whatever upstream
    assembly produced it -- this test is about `pep508_to_matchspec`'s own
    error handling, independent of whether `markerpry`'s quoting is ever
    fixed.
    """

    def test_unparseable_entry_raises_invalid_requirement_error(self) -> None:
        entry = 'ordereddict==1.1; extra == ":python-version=="2-6""'

        with pytest.raises(InvalidRequirementError):
            pep508_to_matchspec(entry, (passthrough_mapper,))


# --------------------------------------------------------------------------
# Extras (the dependency's own extras, `name[extra1,extra2]`)
# --------------------------------------------------------------------------


class TestExtras:
    def test_bare_extra_becomes_an_extras_bracket(self) -> None:
        assert pep508_to_matchspec("fastapi[all]", (passthrough_mapper,)) == "fastapi[extras=[all]]"

    def test_extras_come_after_the_version(self) -> None:
        assert (
            pep508_to_matchspec("fastapi[all]>=1.0", (passthrough_mapper,))
            == "fastapi >=1.0[extras=[all]]"
        )

    def test_multiple_extras_are_normalized_and_sorted(self) -> None:
        assert (
            pep508_to_matchspec("fastapi[Standard,ALL]", (passthrough_mapper,))
            == "fastapi[extras=[all,standard]]"
        )

    def test_extra_name_is_normalized(self) -> None:
        assert (
            pep508_to_matchspec("fastapi[Some_Extra.Name]", (passthrough_mapper,))
            == "fastapi[extras=[some-extra-name]]"
        )

    def test_extra_name_over_64_characters_is_rejected(self) -> None:
        entry = f"fastapi[{'a' * 65}]"

        with pytest.raises(UnconvertableRequirementError, match="64 characters"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_extra_name_over_64_characters_error_names_the_entry(self) -> None:
        entry = f"fastapi[{'a' * 65}]"

        with pytest.raises(UnconvertableRequirementError, match="fastapi"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_extra_name_at_exactly_64_characters_is_accepted(self) -> None:
        entry = f"fastapi[{'a' * 64}]"

        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == f"fastapi[extras=[{'a' * 64}]]"

    def test_empty_extras_brackets_produce_no_extras_clause(self) -> None:
        assert pep508_to_matchspec("fastapi[]", (passthrough_mapper,)) == "fastapi"

    def test_duplicate_extras_after_normalization_are_deduplicated(self) -> None:
        """`Foo-Bar` and `foo_bar` are distinct PEP 508 extras but the same
        conda extra once normalized, and a matchspec should list a given
        extra once.
        """
        assert (
            pep508_to_matchspec("fastapi[Foo-Bar,foo_bar]", (passthrough_mapper,))
            == "fastapi[extras=[foo-bar]]"
        )

    def test_any_invalid_extra_length_raises_even_when_others_are_valid(self) -> None:
        entry = f"fastapi[valid,{'a' * 65}]"

        with pytest.raises(UnconvertableRequirementError, match="64 characters"):
            pep508_to_matchspec(entry, (passthrough_mapper,))


# --------------------------------------------------------------------------
# Conditional markers
# --------------------------------------------------------------------------


class TestMarkers:
    def test_virtual_package_marker_becomes_a_when_clause(self) -> None:
        entry = 'requests>=2.0.0; sys_platform == "win32"'

        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == 'requests >=2.0.0[when="__win"]'

    def test_bare_name_with_marker_has_no_version_outside_the_brackets(self) -> None:
        entry = 'requests; sys_platform == "win32"'

        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == 'requests[when="__win"]'

    def test_python_version_marker_converts_per_the_table(self) -> None:
        entry = 'requests; python_version >= "3.9"'

        assert (
            pep508_to_matchspec(entry, (passthrough_mapper,)) == 'requests[when="python>=3.9.0a0"]'
        )

    def test_python_version_equality_marker_produces_a_rattler_valid_when_clause(self) -> None:
        """`python_version == "X.Y"`'s anchored-range conversion is
        validated end-to-end against py-rattler, not just checked as a
        string -- the assembled matchspec must actually parse.
        """
        entry = 'requests; python_version == "3.9"'

        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == (
            'requests[when="python>=3.9.0a0,<3.10.0a0"]'
        )

    def test_python_version_inequality_marker_produces_a_rattler_valid_when_clause(self) -> None:
        """`python_version != "X.Y"` converts to a glob-with-`!=` form
        (`python!=X.Y.*`) that is otherwise unusual for a plain version
        specifier -- confirm py-rattler still accepts it inside a `when=`
        clause end-to-end.
        """
        entry = 'requests; python_version != "3.9"'

        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == 'requests[when="python!=3.9.*"]'

    def test_combined_marker_preserves_and_or_structure(self) -> None:
        entry = 'requests; sys_platform == "win32" and python_version >= "3.9"'

        assert (
            pep508_to_matchspec(entry, (passthrough_mapper,))
            == 'requests[when="__win and python>=3.9.0a0"]'
        )

    def test_three_term_and_chain_is_fully_parenthesized(self) -> None:
        """The marker tree for a same-operator chain of 3+ terms nests
        left-associatively, and each nested `OperatorNode` is wrapped in
        parens regardless of whether its operator matches its parent's.
        """
        entry = (
            'requests; sys_platform == "win32" and os_name == "posix" and python_version >= "3.9"'
        )

        assert (
            pep508_to_matchspec(entry, (passthrough_mapper,))
            == 'requests[when="(__win and __unix) and python>=3.9.0a0"]'
        )

    def test_mixed_or_and_precedence_parenthesizes_the_tighter_and_group(self) -> None:
        """`and` binds tighter than `or` in PEP 508, and that grouping is
        preserved with explicit parens in the emitted condition.
        """
        entry = (
            'requests; sys_platform == "win32" or os_name == "posix" and python_version >= "3.9"'
        )

        assert (
            pep508_to_matchspec(entry, (passthrough_mapper,))
            == 'requests[when="__win or (__unix and python>=3.9.0a0)"]'
        )

    def test_explicit_parens_around_an_or_group_are_preserved(self) -> None:
        entry = (
            'requests; (sys_platform == "win32" or os_name == "posix") and python_version >= "3.9"'
        )

        assert (
            pep508_to_matchspec(entry, (passthrough_mapper,))
            == 'requests[when="(__win or __unix) and python>=3.9.0a0"]'
        )

    def test_reversed_comparison_operand_order_still_converts(self) -> None:
        """PEP 508 allows the literal on the left (`"3.9" <= python_version`);
        the marker parser flips it back to the key-on-the-left form before
        this function ever sees it.
        """
        entry = 'requests; "3.9" <= python_version'

        assert (
            pep508_to_matchspec(entry, (passthrough_mapper,)) == 'requests[when="python>=3.9.0a0"]'
        )

    def test_reversed_virtual_package_operand_order_still_converts(self) -> None:
        entry = 'requests; "win32" == sys_platform'

        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == 'requests[when="__win"]'

    def test_prerelease_literal_in_a_full_version_marker_is_allowed_without_allow_pre(
        self,
    ) -> None:
        """`allow_pre` governs package version specifiers, not marker
        literals -- a marker comparing against a pre-release-looking
        Python version is a statement about the interpreter, not the
        package being described, and converts unconditionally.
        """
        entry = 'requests; python_full_version >= "3.9.0rc1"'

        assert (
            pep508_to_matchspec(entry, (passthrough_mapper,))
            == 'requests[when="python>=3.9.0.rc1"]'
        )

    def test_full_version_glob_marker_produces_a_rattler_valid_when_clause(self) -> None:
        """`python_full_version == "X.Y.*"` isn't in docs/matchspec.md's
        marker table, and the current passthrough emits `python==X.Y.*`,
        which py-rattler's `MatchSpec` rejects as invalid (`==` combined
        with a glob). Real-world corpus data shows this exact shape --
        real dependencies (`numpy`, `zarr`, `importlib-metadata`,
        `pytest`, ...) pinned to one Python minor -- is the majority
        (1,815 of 2,232) of reroll's "not a valid matchspec" failures.
        """
        entry = 'numpy<1.25.0,>=1.24.0; python_full_version == "3.8.*"'

        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == (
            'numpy >=1.24.0,<1.25.0a0[when="python=3.8"]'
        )

    def test_extras_and_marker_combine_in_one_bracket(self) -> None:
        entry = 'fastapi[all]>=1.0; sys_platform == "win32"'

        assert (
            pep508_to_matchspec(entry, (passthrough_mapper,))
            == 'fastapi >=1.0[extras=[all],when="__win"]'
        )

    def test_marker_with_only_an_extra_clause_raises(self) -> None:
        entry = 'requests>=2.0.0; extra == "foo"'

        with pytest.raises(UnconvertableRequirementError, match="extra"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_marker_combining_extra_clauses_raises(self) -> None:
        entry = 'requests>=2.0.0; extra == "foo" or extra == "bar"'

        with pytest.raises(UnconvertableRequirementError, match="extra"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_marker_mixing_extra_and_an_environment_condition_raises(self) -> None:
        entry = 'requests>=2.0.0; extra == "foo" and python_version >= "3.8"'

        with pytest.raises(UnconvertableRequirementError, match="extra"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_reversed_extra_clause_still_raises(self) -> None:
        entry = 'requests>=2.0.0; "foo" == extra'

        with pytest.raises(UnconvertableRequirementError, match="extra"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_extra_clause_using_in_still_raises_the_extra_error(self) -> None:
        """The `extra` check runs before marker conversion, so an
        unconvertible `in` test against `extra` is reported as an `extra`
        problem, not an `'in'/'not in'` problem.
        """
        entry = 'requests>=2.0.0; "foo" in extra'

        with pytest.raises(UnconvertableRequirementError, match="extra"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_platform_machine_marker_raises(self) -> None:
        entry = 'requests>=2.0.0; platform_machine == "x86_64"'

        with pytest.raises(UnconvertableMarkerError, match="platform_machine"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    @pytest.mark.parametrize(
        "key",
        [
            "platform_release",
            "platform_version",
            "implementation_name",
            "platform_python_implementation",
        ],
    )
    def test_other_unmapped_marker_keys_raise(self, key: str) -> None:
        entry = f'requests>=2.0.0; {key} == "whatever"'

        with pytest.raises(UnconvertableMarkerError, match=key):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_virtual_package_inequality_marker_raises(self) -> None:
        entry = 'requests>=2.0.0; os_name != "nt"'

        with pytest.raises(UnconvertableMarkerError, match="os_name"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_virtual_package_marker_rejects_an_ordered_comparator(self) -> None:
        entry = 'requests>=2.0.0; sys_platform >= "linux"'

        with pytest.raises(UnconvertableMarkerError, match="sys_platform"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_virtual_package_marker_rejects_an_unmapped_value(self) -> None:
        entry = 'requests>=2.0.0; sys_platform == "cygwin"'

        with pytest.raises(UnconvertableMarkerError, match="cygwin"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_python_version_literal_with_a_patch_segment_raises(self) -> None:
        """`python_version` markers are documented as major.minor only; a
        literal with a nonzero patch segment (a common real-world mistake)
        raises its own dedicated error, `UnconvertablePythonVersionEqualityError`,
        rather than the generic `UnconvertableMarkerError`.
        """
        entry = 'requests>=2.0.0; python_version == "3.9.1"'

        with pytest.raises(UnconvertablePythonVersionEqualityError, match="major.minor"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_python_version_literal_with_only_a_major_segment_converts(self) -> None:
        """A bare-major literal (`"3"`) is PEP 440-equivalent to `"3.0"`
        (trailing zero release segments are insignificant), so it converts
        exactly as `python_version == "3.0"` would.
        """
        entry = 'requests>=2.0.0; python_version == "3"'

        assert pep508_to_matchspec(entry, (passthrough_mapper,)) == (
            'requests >=2.0.0[when="python>=3.0.0a0,<3.1.0a0"]'
        )

    def test_python_version_rejects_the_compatible_release_comparator(self) -> None:
        entry = 'requests>=2.0.0; python_version ~= "3.9"'

        with pytest.raises(UnconvertableMarkerError, match="python_version"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_full_version_rejects_the_compatible_release_comparator(self) -> None:
        entry = 'requests>=2.0.0; python_full_version ~= "3.9.0"'

        with pytest.raises(UnconvertableMarkerError, match="python_full_version"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_in_marker_converts_via_the_membership_rewrite(self) -> None:
        """Unlike every other `in`/`not in` shape, `python_version in
        "<literal>"` (key on the left, not negated) has a defined
        rewrite (docs/matchspec.md's "python_version workaround") instead
        of raising `UnconvertableMarkerError`.
        """
        entry = 'requests>=2.0.0; python_version in "3.9"'

        assert pep508_to_matchspec(entry, (passthrough_mapper,), abi3_upper_bound="3.9") == (
            'requests >=2.0.0[when="python>=3.9.0a0,<3.10.0a0"]'
        )

    def test_in_marker_with_no_matching_minor_raises_its_own_error(self) -> None:
        entry = 'requests>=2.0.0; python_version in "not a version list"'

        with pytest.raises(UnconvertablePythonVersionEqualityError):
            pep508_to_matchspec(entry, (passthrough_mapper,), abi3_upper_bound="3.9")

    def test_not_in_marker_converts_via_the_membership_rewrite(self) -> None:
        """Unlike every other `in`/`not in` shape, `python_version not in
        "<literal>"` (key on the left, negated) has a defined rewrite
        (docs/matchspec.md's "python_version workaround", mirrored for
        `not in`) instead of raising `UnconvertableMarkerError`.
        """
        entry = 'requests>=2.0.0; python_version not in "3.9"'

        assert pep508_to_matchspec(entry, (passthrough_mapper,), abi3_upper_bound="3.9") == (
            'requests >=2.0.0[when="python!=3.9.*"]'
        )

    def test_not_in_marker_with_no_matching_minor_raises_its_own_error(self) -> None:
        entry = 'requests>=2.0.0; python_version not in "no versions here"'

        with pytest.raises(UnconvertablePythonVersionEqualityError):
            pep508_to_matchspec(entry, (passthrough_mapper,), abi3_upper_bound="3.9")

    def test_reversed_not_in_marker_raises(self) -> None:
        entry = 'requests>=2.0.0; "3.9" not in python_version'

        with pytest.raises(UnconvertableMarkerError, match="'in'/'not in'"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_reversed_in_marker_raises(self) -> None:
        entry = 'requests>=2.0.0; "3.9" not in python_version'

        with pytest.raises(UnconvertableMarkerError, match="'in'/'not in'"):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_unsupported_marker_error_names_the_entry(self) -> None:
        entry = 'requests>=2.0.0; platform_machine == "x86_64"'

        with pytest.raises(UnconvertableMarkerError, match=repr(entry)):
            pep508_to_matchspec(entry, (passthrough_mapper,))

    def test_unconvertable_marker_error_logs_only_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A marker conversion failure is one logical error -- re-raising it
        with `entry` added for context must not construct a second
        `UnconvertableMarkerError` on top of the one `marker_condition`
        already raised (and thus already logged).
        """
        entry = 'requests>=2.0.0; platform_machine == "x86_64"'

        with (
            caplog.at_level(logging.WARNING, logger="reroll.unconvertable"),
            pytest.raises(UnconvertableMarkerError),
        ):
            pep508_to_matchspec(entry, (passthrough_mapper,))

        assert len(caplog.records) == 1


# --------------------------------------------------------------------------
# py-rattler validation of the assembled matchspec
# --------------------------------------------------------------------------


class TestRattlerValidation:
    def test_an_invalid_assembled_matchspec_raises_a_value_error(self) -> None:
        """A conda name a mapper produces could itself be invalid matchspec
        syntax (e.g. containing `[`) -- this is caught by validating the
        assembled string against py-rattler, not by reproducing rattler's
        own package-name grammar here.
        """
        mappers = (static_mapper({"badpkg": "bad[name"}), aggregator_mapper)

        with pytest.raises(UnconvertableRequirementError, match="badpkg"):
            pep508_to_matchspec("badpkg", mappers)

    def test_a_mapper_returning_an_empty_conda_name_raises_a_value_error(self) -> None:
        mappers = (static_mapper({"badpkg": ""}), aggregator_mapper)

        with pytest.raises(UnconvertableRequirementError, match="badpkg"):
            pep508_to_matchspec("badpkg", mappers)


# --------------------------------------------------------------------------
# Everything together
# --------------------------------------------------------------------------


class TestIntegration:
    def test_name_version_extras_marker_and_allow_pre_all_combine(self) -> None:
        """Every feature this function handles -- name mapping,
        normalization-sensitive extras dedup/sort, a `~=` expansion with a
        pre-release boundary, and a multi-clause `when=` condition -- in
        one requirement string.
        """
        mappers = (static_mapper({"foo-bar-baz": "conda-foo-bar-baz"}), aggregator_mapper)
        entry = (
            'Foo_Bar.BAZ[Extra1,extra_2]~=1.2.3rc1; sys_platform == "win32" '
            'and python_version >= "3.9"'
        )

        assert pep508_to_matchspec(entry, mappers, allow_pre=True) == (
            "conda-foo-bar-baz >=1.2.3.rc1,<1.3.0a0"
            '[extras=[extra-2,extra1],when="__win and python>=3.9.0a0"]'
        )
