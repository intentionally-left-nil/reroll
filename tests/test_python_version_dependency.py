"""Unit tests for `reroll.dependencies.python_version_dependency`.

Covers docs/wheel_to_conda_dependencies.md#determining-the-python-version-dependency
exhaustively: the "Simplified Requires-Python" grammar (bare `>=`/`<`/`~=`,
`==major.minor[.*]`, `==major.*`, and the two-clause `<`+`>=` half-open
range), the generic comma-combining fallback for everything else, and the
`PythonRangeMismatchError`/`InvalidVersionSpecifierError` failure modes.
"""

from __future__ import annotations

import pytest
from packaging.version import Version
from pydantic import ValidationError

from reroll.dependencies.python_version_dependency import HalfOpenRange, python_version_dependency
from reroll.errors import InvalidVersionSpecifierError, PythonRangeMismatchError
from reroll.filename.python_requirement import PythonRequirement

# --------------------------------------------------------------------------
# No `Requires-Python` at all -- the filename's own range, untouched
# --------------------------------------------------------------------------


class TestNoRequiresPython:
    def test_floor_becomes_an_unbounded_range(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(8), None)

        assert result == HalfOpenRange(lower=Version("3.8"), upper=None)

    def test_exact_minor_becomes_a_bounded_range(self) -> None:
        result = python_version_dependency(PythonRequirement.pinned(13), None)

        assert result == HalfOpenRange(lower=Version("3.13"), upper=Version("3.14"))


# --------------------------------------------------------------------------
# `Requires-Python` that cannot be parsed as a PEP 440 specifier set at all,
# even after every lenient fixup (docs/wheel_metadata.md) has been tried.
# --------------------------------------------------------------------------


class TestUnparsableRequiresPython:
    @pytest.mark.parametrize(
        "requires_python",
        [
            "not a specifier",
            "abc",
            "3.11",  # a bare version -- no comparison operator at all
            ">=",  # an operator with no version to compare against
            "~=3",  # `~=` requires at least two release segments (PEP 440)
        ],
    )
    def test_raises_invalid_version_specifier_error(self, requires_python: str) -> None:
        with pytest.raises(InvalidVersionSpecifierError):
            python_version_dependency(PythonRequirement.floor(8), requires_python)


class TestLenientFixupRecovery:
    """A `Requires-Python` value that fails strict parsing but is rescued
    by one of `reroll.lenient_parser`'s fixups is treated exactly like the
    fixed-up value -- it is never a parse failure.
    """

    def test_missing_comma_is_inserted_before_categorizing(self) -> None:
        with_fixup = python_version_dependency(PythonRequirement.floor(8), ">=3.10<4")
        already_fixed = python_version_dependency(PythonRequirement.floor(8), ">=3.10,<4")

        assert (
            with_fixup == already_fixed == HalfOpenRange(lower=Version("3.10"), upper=Version("4"))
        )


# --------------------------------------------------------------------------
# Simplified Requires-Python: a bare `>=` or `<` operator
# --------------------------------------------------------------------------


class TestSimplifiedBareInequality:
    def test_floor_below_filename_floor_is_shadowed_by_it(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(8), ">=3.5")

        assert result == HalfOpenRange(lower=Version("3.8"), upper=None)

    def test_floor_above_filename_floor_tightens_it(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(8), ">=3.11")

        assert result == HalfOpenRange(lower=Version("3.11"), upper=None)

    def test_floor_keeps_its_own_granularity_untruncated(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(10), ">=3.11.5")

        assert result == HalfOpenRange(lower=Version("3.11.5"), upper=None)

    def test_floor_combines_with_an_exact_filename_minor(self) -> None:
        result = python_version_dependency(PythonRequirement.pinned(11), ">=3.11")

        assert result == HalfOpenRange(lower=Version("3.11"), upper=Version("3.12"))

    def test_bare_ceiling_combines_with_filename_floor(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(8), "<3.9")

        assert result == HalfOpenRange(lower=Version("3.8"), upper=Version("3.9"))

    def test_bare_ceiling_tightens_below_an_exact_filename_minor_s_own_ceiling(self) -> None:
        result = python_version_dependency(PythonRequirement.pinned(7), "<3.8")

        assert result == HalfOpenRange(lower=Version("3.7"), upper=Version("3.8"))


# --------------------------------------------------------------------------
# Simplified Requires-Python: a bare `~=` operator
# --------------------------------------------------------------------------


class TestSimplifiedCompatibleRelease:
    def test_three_segment_form_is_bounded_at_the_next_minor(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(11), "~=3.11.2")

        assert result == HalfOpenRange(lower=Version("3.11.2"), upper=Version("3.12"))

    def test_three_segment_form_keeps_the_full_lower_bound_including_micro(self) -> None:
        """The `~=` lower bound is the literal value given, micro segment
        and all -- only the upper bound is derived by truncation.
        """
        result = python_version_dependency(PythonRequirement.floor(9), "~=3.11.2")

        assert isinstance(result, HalfOpenRange)
        assert result.lower == Version("3.11.2")

    def test_two_segment_form_is_bounded_at_the_next_major(self) -> None:
        """Per PEP 440, `~=3.11` (exactly two release segments) is
        equivalent to `>=3.11,==3.*` -- not a minor pin. Combined with an
        unconstrained filename floor, the next-major ceiling passes
        through untouched.
        """
        result = python_version_dependency(PythonRequirement.floor(0), "~=3.11")

        assert result == HalfOpenRange(lower=Version("3.11"), upper=Version("4"))

    def test_two_segment_form_is_tightened_back_down_by_an_exact_filename_minor(self) -> None:
        result = python_version_dependency(PythonRequirement.pinned(11), "~=3.11")

        assert result == HalfOpenRange(lower=Version("3.11"), upper=Version("3.12"))


# --------------------------------------------------------------------------
# Simplified Requires-Python: `==major.minor` and `==major.minor.*`, both
# treated identically -- as a direct minor pin, per the doc.
# --------------------------------------------------------------------------


class TestSimplifiedEqualityMajorMinor:
    @pytest.mark.parametrize("requires_python", ["==3.11", "==3.11.*"])
    def test_bare_and_wildcard_forms_pin_the_minor_on_their_own(self, requires_python: str) -> None:
        result = python_version_dependency(PythonRequirement.floor(0), requires_python)

        assert result == HalfOpenRange(lower=Version("3.11"), upper=Version("3.12"))

    @pytest.mark.parametrize("requires_python", ["==3.11", "==3.11.*"])
    def test_bare_and_wildcard_forms_produce_the_same_range(self, requires_python: str) -> None:
        result = python_version_dependency(PythonRequirement.floor(9), requires_python)

        assert result == HalfOpenRange(lower=Version("3.11"), upper=Version("3.12"))

    @pytest.mark.parametrize("requires_python", ["==3.11", "==3.11.*"])
    def test_tightened_by_an_exact_filename_minor(self, requires_python: str) -> None:
        result = python_version_dependency(PythonRequirement.pinned(11), requires_python)

        assert result == HalfOpenRange(lower=Version("3.11"), upper=Version("3.12"))

    def test_three_segment_exact_value_is_not_simplified(self) -> None:
        """`==3.11.2` is explicitly excluded from the `==major.minor`
        category (docs), so it falls through to the generic combining
        path instead of this algorithm.
        """
        result = python_version_dependency(PythonRequirement.floor(9), "==3.11.2")

        assert isinstance(result, str)


# --------------------------------------------------------------------------
# Simplified Requires-Python: `==major.*`
# --------------------------------------------------------------------------


class TestSimplifiedEqualityMajorWildcard:
    def test_produces_an_unbounded_floor_at_the_major_version(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(0), "==3.*")

        assert result == HalfOpenRange(lower=Version("3"), upper=None)

    def test_tightened_by_the_filename_s_own_floor(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(9), "==3.*")

        assert result == HalfOpenRange(lower=Version("3.9"), upper=None)

    def test_tightened_by_an_exact_filename_minor(self) -> None:
        result = python_version_dependency(PythonRequirement.pinned(11), "==3.*")

        assert result == HalfOpenRange(lower=Version("3.11"), upper=Version("3.12"))

    def test_a_higher_major_still_only_produces_a_floor_with_no_ceiling(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(9), "==4.*")

        assert result == HalfOpenRange(lower=Version("4"), upper=None)

    def test_an_unrelated_lower_major_is_shadowed_by_the_filename_floor(self) -> None:
        """`==2.*` alone means "any 2.x, and nothing says otherwise above
        that" once turned into an unbounded-above range -- so it never
        conflicts with a higher filename floor, however implausible the
        combination.
        """
        result = python_version_dependency(PythonRequirement.floor(9), "==2.*")

        assert result == HalfOpenRange(lower=Version("3.9"), upper=None)


# --------------------------------------------------------------------------
# Simplified Requires-Python: the two-clause `<` + `>=` half-open range
# --------------------------------------------------------------------------


class TestSimplifiedHalfOpenCompound:
    def test_basic_compound_range(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(9), ">=3.10,<3.14.2")

        assert result == HalfOpenRange(lower=Version("3.10"), upper=Version("3.14.2"))

    def test_clause_order_does_not_matter(self) -> None:
        forward = python_version_dependency(PythonRequirement.floor(9), ">=3.10,<3.14.2")
        reversed_ = python_version_dependency(PythonRequirement.floor(9), "<3.14.2,>=3.10")

        assert forward == reversed_

    def test_filename_floor_tightens_the_lower_bound(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(11), ">=3.9,<4")

        assert result == HalfOpenRange(lower=Version("3.11"), upper=Version("4"))

    def test_exact_filename_minor_tightens_the_upper_bound(self) -> None:
        result = python_version_dependency(PythonRequirement.pinned(10), ">=3.9,<3.15")

        assert result == HalfOpenRange(lower=Version("3.10"), upper=Version("3.11"))


# --------------------------------------------------------------------------
# `PythonRangeMismatchError`: only ever raised for a Simplified
# Requires-Python whose combined range is disjoint from the filename's.
# --------------------------------------------------------------------------


class TestPythonRangeMismatch:
    def test_bare_floor_above_an_exact_filename_minor_s_ceiling(self) -> None:
        with pytest.raises(PythonRangeMismatchError):
            python_version_dependency(PythonRequirement.pinned(13), ">=3.14")

    def test_bare_ceiling_at_or_below_the_filename_floor(self) -> None:
        with pytest.raises(PythonRangeMismatchError):
            python_version_dependency(PythonRequirement.floor(8), "<3.8")

    def test_compatible_release_below_an_exact_filename_minor(self) -> None:
        with pytest.raises(PythonRangeMismatchError):
            python_version_dependency(PythonRequirement.pinned(10), "~=3.11.2")

    def test_equality_major_minor_pin_below_an_exact_filename_minor(self) -> None:
        with pytest.raises(PythonRangeMismatchError):
            python_version_dependency(PythonRequirement.pinned(10), "==3.13")

    def test_equality_major_minor_pin_does_not_reach_a_higher_exact_filename_minor(self) -> None:
        """`==3.9` is a tight pin at `[3.9, 3.10)` -- it does not, unlike
        a bare `~=3.9` would, extend all the way to the next major
        version, so it conflicts with an exact-3.11 filename wheel.
        """
        with pytest.raises(PythonRangeMismatchError):
            python_version_dependency(PythonRequirement.pinned(11), "==3.9")

    def test_half_open_compound_that_is_disjoint_on_its_own(self) -> None:
        """docs/wheel_to_conda_dependencies.md's own callout:
        `Requires-Python: >=3.10,<3.7` is a Simplified half-open range
        (one comma, one `<` and one `>=`) that proceeds through the whole
        algorithm and is rejected for being disjoint, regardless of the
        filename's own range.
        """
        with pytest.raises(PythonRangeMismatchError):
            python_version_dependency(PythonRequirement.floor(0), ">=3.10,<3.7")

    def test_half_open_compound_disjoint_only_once_combined_with_the_filename(self) -> None:
        with pytest.raises(PythonRangeMismatchError):
            python_version_dependency(PythonRequirement.pinned(9), ">=3.10,<4")

    def test_touching_boundary_is_still_disjoint(self) -> None:
        """A half-open range's lower bound equal to its upper bound has no
        members -- `lower == upper` counts as disjoint, not a 0-width
        match.
        """
        with pytest.raises(PythonRangeMismatchError):
            python_version_dependency(PythonRequirement.pinned(10), ">=3.10,<3.10")

    def test_touching_boundary_that_is_not_equal_does_not_raise(self) -> None:
        result = python_version_dependency(PythonRequirement.pinned(10), ">=3.9,<3.11")

        assert result == HalfOpenRange(lower=Version("3.10"), upper=Version("3.11"))


# --------------------------------------------------------------------------
# The generic combining fallback: any Requires-Python that does not match
# the Simplified grammar. Per the doc, this never raises
# `PythonRangeMismatchError`, however contradictory the combination looks.
# --------------------------------------------------------------------------


class TestGenericCombiningFallback:
    @pytest.mark.parametrize(
        "requires_python",
        [
            "<=3.11",  # inclusive ordered comparison -- not in {>=, <, ~=}
            ">3.11",  # exclusive ordered comparison -- not in {>=, <, ~=}
            "!=3.11.2",  # exclusion -- not in {>=, <, ~=}
            "===3.11",  # arbitrary equality
            "==3.11.2",  # `==` with a value that isn't major.minor
            "==3",  # `==` with a bare major, no dot at all
            "==3.11rc1",  # major.minor release, but with a pre-release suffix
            "==3.11.post1",  # major.minor release, but with a post-release suffix
            "==3.11.dev1",  # major.minor release, but with a dev-release suffix
            "==1!3.11",  # major.minor release, but with a non-zero epoch
            "==3.11+local1",  # major.minor release, but with a local version label
        ],
    )
    def test_single_clause_outside_the_simplified_operator_set_is_generic(
        self, requires_python: str
    ) -> None:
        result = python_version_dependency(PythonRequirement.floor(9), requires_python)

        assert isinstance(result, str)

    def test_more_than_two_clauses_is_generic_even_with_a_less_than_and_a_ge(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(11), "!=3.11.2,>=3.10,<4")

        assert isinstance(result, str)

    def test_two_clauses_without_one_of_each_required_operator_is_generic(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(9), ">=3.10,>=3.11")

        assert isinstance(result, str)

    def test_two_clauses_with_equals_and_less_than_is_generic(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(9), "==3.11,<4")

        assert isinstance(result, str)

    def test_never_raises_even_when_numerically_contradictory(self) -> None:
        """`<=3.10` (single clause, disallowed operator) combined with an
        exact 3.15 filename wheel looks like a wheel that could never
        install -- but since `<=3.10` isn't Simplified, the generic path
        just emits the (contradictory) combined string, per the doc.
        """
        result = python_version_dependency(PythonRequirement.pinned(15), "<=3.10")

        assert isinstance(result, str)

    def test_empty_requires_python_reduces_to_the_filename_specifier_alone(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(8), "")

        assert result == ">=3.8"


class TestGenericCombiningFilenameForm:
    """The filename half of the generic combination is always `>=
    major.minor` for a floor, or `~= major.minor` for an exact minor --
    verified here with single-clause `Requires-Python` values, where the
    combined string's exact spelling is fully deterministic.
    """

    def test_floor_uses_a_bare_inequality(self) -> None:
        result = python_version_dependency(PythonRequirement.floor(8), "<=3.11")

        assert result == ">=3.8,<=3.11"

    def test_exact_minor_uses_a_compatible_release_clause(self) -> None:
        result = python_version_dependency(PythonRequirement.pinned(9), "<=3.11")

        assert result == "~=3.9,<=3.11"


# --------------------------------------------------------------------------
# `HalfOpenRange` itself
# --------------------------------------------------------------------------


class TestHalfOpenRange:
    def test_equal_bounds_compare_equal(self) -> None:
        assert HalfOpenRange(lower=Version("3.8"), upper=None) == HalfOpenRange(
            lower=Version("3.8"), upper=None
        )

    def test_differing_bounds_compare_unequal(self) -> None:
        assert HalfOpenRange(lower=Version("3.8"), upper=None) != HalfOpenRange(
            lower=Version("3.9"), upper=None
        )

    def test_is_frozen(self) -> None:
        result = HalfOpenRange(lower=Version("3.8"), upper=None)

        attr = "lower"
        with pytest.raises(ValidationError):
            setattr(result, attr, Version("3.9"))
