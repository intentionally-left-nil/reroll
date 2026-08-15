"""Equivalence tests for `python_version`, `python_full_version`, and
`implementation_version` markers, using `tests.marker_oracle` to check
reroll's matchspec conversion against pip/uv's own marker evaluation
instead of a hardcoded expected string.
"""

from __future__ import annotations

import pytest

from reroll.errors import UnconvertableMarkerError, UnconvertablePythonVersionEqualityError
from tests.marker_oracle import assert_matchspec_agrees_with_pip, assert_pip_is_constant

# A resolved CPython `python_full_version` sweep crossing every boundary these
# tests care about: pre-3.9, each of 3.9's own pre-release stages, 3.9.0
# itself, later 3.9 patches, the 3.10 boundary (including its own
# pre-release), a double-digit minor, and a major-version bump.
_PYTHON_VERSION_CANDIDATES = (
    "2.7.18",
    "3.7.9",
    "3.8.0",
    "3.8.16",
    "3.9.0a0",
    "3.9.0b1",
    "3.9.0rc1",
    "3.9.0",
    "3.9.1",
    "3.9.20",
    "3.10.0a0",
    "3.10.0",
    "3.10.5",
    "3.13.0",
    "3.13.2",
    "4.0.0",
    "4.0.0a0",
)


class TestPythonVersionEquivalence:
    @pytest.mark.parametrize(
        ("comparator", "literal"),
        [
            ("==", "3.9"),
            ("!=", "3.9"),
            (">=", "3.9"),
            (">", "3.9"),
            ("<=", "3.9"),
            ("<", "3.9"),
            ("==", "3"),
            ("!=", "3"),
            (">=", "3"),
            ("<", "4"),
            ("==", "3.9.0"),
            ("!=", "3.9.0"),
            (">=", "3.10"),
            ("<", "3.13"),
            (">=", "3.13"),
        ],
    )
    def test_agrees_with_pip_across_every_candidate(self, comparator: str, literal: str) -> None:
        assert_matchspec_agrees_with_pip(
            f'python_version {comparator} "{literal}"', _PYTHON_VERSION_CANDIDATES
        )

    @pytest.mark.parametrize(("comparator", "literal"), [("==", "3.*"), ("!=", "3.*")])
    def test_major_glob_agrees_with_pip_across_every_candidate(
        self, comparator: str, literal: str
    ) -> None:
        assert_matchspec_agrees_with_pip(
            f'python_version {comparator} "{literal}"', _PYTHON_VERSION_CANDIDATES
        )

    @pytest.mark.parametrize("comparator", [">=", ">", "<=", "<"])
    def test_nonzero_micro_literal_ordered_comparators_agree_with_pip(
        self, comparator: str
    ) -> None:
        """`python_version` is always major.minor, so an ordered comparator
        against a nonzero-micro literal (`"3.9.2"`) collapses onto the
        comparator's plain major.minor behavior -- confirmed here against
        pip's own evaluation, not just the collapsed matchspec string.
        """
        assert_matchspec_agrees_with_pip(
            f'python_version {comparator} "3.9.2"', _PYTHON_VERSION_CANDIDATES
        )

    @pytest.mark.parametrize("comparator", ["==", "!="])
    def test_nonzero_micro_literal_equality_is_a_pip_side_constant(self, comparator: str) -> None:
        """`marker_condition` refuses to convert this shape at all
        (`UnconvertablePythonVersionEqualityError`) because there is no
        matchspec fragment for a constant -- this confirms, independently
        via pip's own evaluation, that the refusal is justified: the
        marker really is constant for every candidate, not just the ones
        this codebase happened to consider.
        """
        marker = f'python_version {comparator} "3.9.2"'
        with pytest.raises(UnconvertablePythonVersionEqualityError):
            assert_matchspec_agrees_with_pip(marker, _PYTHON_VERSION_CANDIDATES)

        assert_pip_is_constant(marker, comparator == "!=", _PYTHON_VERSION_CANDIDATES)

    def test_and_combination_agrees_with_pip_across_every_candidate(self) -> None:
        marker = 'python_version >= "3.9" and python_version < "3.11"'
        assert_matchspec_agrees_with_pip(marker, _PYTHON_VERSION_CANDIDATES)

    def test_or_combination_agrees_with_pip_across_every_candidate(self) -> None:
        marker = 'python_version < "3.9" or python_version >= "3.13"'
        assert_matchspec_agrees_with_pip(marker, _PYTHON_VERSION_CANDIDATES)


# A resolved CPython `python_full_version` sweep exercising ordered and
# equality comparators against literals that themselves carry a
# pre-release, post-release, or dev-release segment -- `python_full_version`
# and `implementation_version` pass their comparator straight through to
# matchspec (docs/matchspec.md), so this is where a PEP 440 vs. conda
# CEP-33 ordering mismatch, if any existed, would show up.
_FULL_VERSION_CANDIDATES = (
    "3.8.16",
    "3.9.0.dev0",
    "3.9.0a0",
    "3.9.0a1",
    "3.9.0b1",
    "3.9.0rc1",
    "3.9.0",
    "3.9.0.post1",
    "3.9.1",
    "3.9.20",
    "3.10.0",
    "3.13.0",
    "3.13.2",
    "4.0.0",
)


class TestPythonFullVersionEquivalence:
    @pytest.mark.parametrize(
        ("comparator", "literal"),
        [
            ("==", "3.9.0"),
            ("!=", "3.9.0"),
            (">=", "3.9.1"),
            (">", "3.9.1"),
            ("<=", "3.9.1"),
            ("<", "3.9.1"),
            ("==", "3.9.0rc1"),
            (">=", "3.9.0rc1"),
            ("<", "3.9.0b1"),
            (">=", "3.9.0a1"),
            (">=", "3.9.0.dev0"),
            ("<", "3.13.0"),
            (">=", "3.13.0"),
        ],
    )
    def test_agrees_with_pip_across_every_candidate(self, comparator: str, literal: str) -> None:
        assert_matchspec_agrees_with_pip(
            f'python_full_version {comparator} "{literal}"', _FULL_VERSION_CANDIDATES
        )

    @pytest.mark.parametrize(("comparator", "literal"), [("==", "3.9.*"), ("!=", "3.9.*")])
    def test_glob_literal_agrees_with_pip_across_every_candidate(
        self, comparator: str, literal: str
    ) -> None:
        assert_matchspec_agrees_with_pip(
            f'python_full_version {comparator} "{literal}"', _FULL_VERSION_CANDIDATES
        )

    def test_and_combination_agrees_with_pip_across_every_candidate(self) -> None:
        marker = 'python_full_version >= "3.9.0" and python_full_version < "3.10.0"'
        assert_matchspec_agrees_with_pip(marker, _FULL_VERSION_CANDIDATES)


class TestImplementationVersionEquivalence:
    """docs/matchspec.md says `implementation_version` is, for CPython,
    always equal to `python_full_version` and thus converts identically --
    this exercises the same comparator/literal matrix through the
    `implementation_version` key instead, against the same candidates.
    """

    @pytest.mark.parametrize(
        ("comparator", "literal"),
        [
            ("==", "3.9.0"),
            ("!=", "3.9.0"),
            (">=", "3.9.1"),
            (">", "3.9.1"),
            ("<=", "3.9.1"),
            ("<", "3.9.1"),
            ("==", "3.9.0rc1"),
            (">=", "3.9.0a1"),
            (">=", "3.9.0.dev0"),
        ],
    )
    def test_agrees_with_pip_across_every_candidate(self, comparator: str, literal: str) -> None:
        assert_matchspec_agrees_with_pip(
            f'implementation_version {comparator} "{literal}"', _FULL_VERSION_CANDIDATES
        )

    def test_glob_literal_agrees_with_pip_across_every_candidate(self) -> None:
        assert_matchspec_agrees_with_pip(
            'implementation_version == "3.9.*"', _FULL_VERSION_CANDIDATES
        )


class TestPythonVersionMembershipEquivalence:
    """docs/matchspec.md's "python_version workaround": `python_version
    in "<literal>"` rewrites to an `or`-chain of equalities before
    conversion (unlike every other `in`/`not in` shape,
    `TestOracleItselfRejectsUnconvertableMarkers`) -- this confirms that
    rewrite's result still agrees with pip's own (substring-based) marker
    evaluation.
    """

    @pytest.mark.parametrize("literal", ["3.8 3.9 3.13", "3.8,3.9,3.13", "3.8, 3.9, 3.13"])
    def test_multi_value_list_agrees_with_pip_across_every_candidate(self, literal: str) -> None:
        assert_matchspec_agrees_with_pip(
            f'python_version in "{literal}"', _PYTHON_VERSION_CANDIDATES, abi3_upper_bound="3.13"
        )

    def test_and_combination_agrees_with_pip_across_every_candidate(self) -> None:
        marker = 'python_version in "3.9 3.13" and python_version < "3.13"'
        assert_matchspec_agrees_with_pip(
            marker, _PYTHON_VERSION_CANDIDATES, abi3_upper_bound="3.13"
        )


class TestOracleItselfRejectsUnconvertableMarkers:
    """The oracle's `matchspec_condition` is a thin wrapper over
    `marker_condition` -- a marker that function can't convert at all must
    still raise, not silently produce a condition to (mis)compare.
    """

    def test_not_in_still_raises(self) -> None:
        with pytest.raises(UnconvertableMarkerError):
            assert_matchspec_agrees_with_pip(
                'python_version not in "3.11"', _PYTHON_VERSION_CANDIDATES, abi3_upper_bound="3.13"
            )

    def test_key_on_the_right_still_raises(self) -> None:
        with pytest.raises(UnconvertableMarkerError):
            assert_matchspec_agrees_with_pip(
                '"3.11" in python_version', _PYTHON_VERSION_CANDIDATES, abi3_upper_bound="3.13"
            )
