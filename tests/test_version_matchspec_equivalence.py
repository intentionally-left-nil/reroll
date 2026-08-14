"""Equivalence tests for the plain (no `when=`) matchspec version clause
`pep508_to_matchspec` produces for a dependency's own PEP 440 specifier,
using `tests.version_oracle` to check it against pip/uv's own specifier
evaluation instead of a hardcoded expected string -- particularly around
pre-release (rc/alpha/beta/dev) ordering and range-boundary cutoffs
(`~=` expansion, glob rewriting, epochs).

`TestExclusiveComparatorPrePostReleaseCarveOut` at the bottom documents a
real, confirmed disagreement the sweep above deliberately excludes -- see
that class's docstring.
"""

from __future__ import annotations

import pytest

from tests.version_oracle import assert_matchspec_agrees_with_pip

# A version sweep crossing every boundary these tests care about: below
# 1.0.0, every one of 1.0.0's own pre-release stages (dev, alpha, beta, rc),
# 1.0.0 itself, its post-release, the next patch (and *its* rc), the next
# minor (and its own alpha), and the next major (and its own alpha).
_VERSION_CANDIDATES = (
    "0.9.9",
    "1.0.0.dev0",
    "1.0.0.dev1",
    "1.0.0a0",
    "1.0.0a1",
    "1.0.0b1",
    "1.0.0rc1",
    "1.0.0rc2",
    "1.0.0",
    "1.0.0.post1",
    "1.0.1",
    "1.0.1rc1",
    "1.1.0",
    "1.1.0a0",
    "2.0.0",
    "2.0.0a0",
)

# `==`, `!=`, `>=`, `<=` (and, by construction, `~=` and the glob rewrites)
# are the operators confirmed equivalent below -- strict `<`/`>` are
# deliberately excluded; see `TestExclusiveComparatorPrePostReleaseCarveOut`.
_VERIFIED_EQUIVALENT_COMPARATORS = ("==", "!=", ">=", "<=")


class TestPlainOperatorEquivalence:
    @pytest.mark.parametrize("comparator", _VERIFIED_EQUIVALENT_COMPARATORS)
    def test_plain_release_literal_agrees_with_pip_across_every_candidate(
        self, comparator: str
    ) -> None:
        assert_matchspec_agrees_with_pip(f"{comparator}1.0.0", _VERSION_CANDIDATES)

    @pytest.mark.parametrize("comparator", _VERIFIED_EQUIVALENT_COMPARATORS)
    def test_rc_literal_agrees_with_pip_across_every_candidate(self, comparator: str) -> None:
        """The literal itself is a pre-release (`1.0.0rc1`) -- exercised
        separately from a plain-release literal since `format_version`'s
        `.rc1` spelling, not just the comparator, is what must order
        correctly against every candidate.
        """
        assert_matchspec_agrees_with_pip(
            f"{comparator}1.0.0rc1", _VERSION_CANDIDATES, allow_pre=True
        )

    @pytest.mark.parametrize("comparator", _VERIFIED_EQUIVALENT_COMPARATORS)
    def test_post_release_literal_agrees_with_pip_across_every_candidate(
        self, comparator: str
    ) -> None:
        assert_matchspec_agrees_with_pip(f"{comparator}1.0.0.post1", _VERSION_CANDIDATES)

    @pytest.mark.parametrize("comparator", _VERIFIED_EQUIVALENT_COMPARATORS)
    def test_dev_release_literal_agrees_with_pip_across_every_candidate(
        self, comparator: str
    ) -> None:
        """A dev-release literal (`1.0.0.dev1`) sorts *before* every other
        pre-release stage of the same base version per PEP 440 -- the
        boundary most likely to catch a PEP 440 vs. conda CEP-33 ordering
        mismatch, if one existed.
        """
        assert_matchspec_agrees_with_pip(
            f"{comparator}1.0.0.dev1", _VERSION_CANDIDATES, allow_pre=True
        )

    def test_inclusive_range_agrees_with_pip_across_every_candidate(self) -> None:
        assert_matchspec_agrees_with_pip(">=1.0.0,<=1.1.0", _VERSION_CANDIDATES)


class TestCompatibleReleaseEquivalence:
    """`~=` expands to a range anchored at `<X.(Y+1).0a0` -- the anchor's
    whole purpose is to land a pre-release of that boundary on the lower
    side, so the candidate sweep here concentrates on the boundary itself.
    `0a0` with no dot before the letter is conda's own sentinel for "the
    very start of this release, below even its own dev-releases" (verified
    directly against `rattler.Version`'s ordering, not assumed) -- unlike
    plain `<`/`>` (`TestExclusiveComparatorPrePostReleaseCarveOut`), this
    anchor already reproduces PEP 440's `~=` boundary (`packaging`'s own
    `_next_prefix_dev0`) exactly, which is why every case below agrees.
    """

    _BOUNDARY_CANDIDATES = (
        "3.12.9",
        "3.13.1",
        "3.13.2",
        "3.13.2.post1",
        "3.13.99",
        "3.14.0.dev0",
        "3.14.0a0",
        "3.14.0a1",
        "3.14.0b1",
        "3.14.0rc1",
        "3.14.0",
        "3.15.0",
    )

    def test_three_segment_base_agrees_with_pip_across_the_boundary(self) -> None:
        assert_matchspec_agrees_with_pip("~=3.13.2", self._BOUNDARY_CANDIDATES)

    def test_two_segment_base_agrees_with_pip_across_the_boundary(self) -> None:
        """`~=3.13` bumps the *major* segment (there's no minor left to
        bump), so the boundary moves to `4.0.0a0` instead.
        """
        candidates = (
            "3.0.0",
            "3.13.0",
            "3.99.0",
            "4.0.0.dev0",
            "4.0.0a0",
            "4.0.0a1",
            "4.0.0",
            "5.0.0",
        )
        assert_matchspec_agrees_with_pip("~=3.13", candidates)

    def test_four_segment_base_agrees_with_pip_across_the_boundary(self) -> None:
        """`~=1.2.3.4` only bumps the last segment (`<1.2.4.0a0`), not the
        third -- the boundary sits one segment deeper than the three-segment
        case.
        """
        candidates = (
            "1.2.3.3",
            "1.2.3.4",
            "1.2.3.99",
            "1.2.4.0.dev0",
            "1.2.4.0a0",
            "1.2.4.0a1",
            "1.2.4.0",
            "1.2.5.0",
        )
        assert_matchspec_agrees_with_pip("~=1.2.3.4", candidates)

    def test_epoch_is_preserved_on_both_bounds(self) -> None:
        candidates = (
            "1!3.12.9",
            "1!3.13.2",
            "1!3.13.99",
            "1!3.14.0a0",
            "1!3.14.0",
            "3.13.2",  # no epoch -- epoch 0, below every `1!...` candidate
            "2!3.13.2",  # a higher epoch, above every `1!...` candidate
        )
        assert_matchspec_agrees_with_pip("~=1!3.13.2", candidates)

    def test_pre_release_base_with_allow_pre_agrees_with_pip_across_the_boundary(self) -> None:
        candidates = (
            "3.13.1",
            "3.13.2.dev0",
            "3.13.2a0",
            "3.13.2a1",
            "3.13.2b1",
            "3.13.2rc1",
            "3.13.2",
            "3.14.0a0",
        )
        assert_matchspec_agrees_with_pip("~=3.13.2rc1", candidates, allow_pre=True)


class TestGlobEquivalence:
    """`==X.Y.*` rewrites to the fuzzy `=X.Y` form and `!=X.Y.*` passes
    through unchanged (docs/matchspec.md's Operator conversion) --
    equivalence matters most exactly at the minor-version boundary a glob
    straddles.
    """

    _BOUNDARY_CANDIDATES = (
        "0.9.9",
        "1.0.0.dev0",
        "1.0.0a0",
        "1.0.0",
        "1.0.0.post1",
        "1.0.5",
        "1.0.99",
        "1.1.0.dev0",
        "1.1.0a0",
        "1.1.0",
        "1.10.0",  # shares the "1.1" string prefix but is a different minor
    )

    def test_equality_glob_agrees_with_pip_across_the_boundary(self) -> None:
        assert_matchspec_agrees_with_pip("==1.0.*", self._BOUNDARY_CANDIDATES)

    def test_inequality_glob_agrees_with_pip_across_the_boundary(self) -> None:
        assert_matchspec_agrees_with_pip("!=1.0.*", self._BOUNDARY_CANDIDATES)

    def test_single_segment_glob_agrees_with_pip_across_the_boundary(self) -> None:
        candidates = (
            "0.9.9",
            "1.0.0.dev0",
            "1.0.0a0",
            "1.0.0",
            "1.99.0",
            "2.0.0.dev0",
            "2.0.0a0",
            "2.0.0",
        )
        assert_matchspec_agrees_with_pip("==1.*", candidates)

    def test_glob_with_an_epoch_agrees_with_pip_across_the_boundary(self) -> None:
        candidates = ("1.0.5", "1!0.9.9", "1!1.0.0", "1!1.0.5", "1!1.1.0", "2!1.0.0")
        assert_matchspec_agrees_with_pip("==1!1.0.*", candidates)


class TestEpochEquivalence:
    _CANDIDATES = (
        "0.5.0",
        "1.0.0",
        "1!0.5.0",
        "1!1.0.0",
        "1!2.0.0",
        "2!0.1.0",
    )

    @pytest.mark.parametrize("comparator", ["==", "!=", ">=", ">", "<=", "<"])
    def test_agrees_with_pip_across_every_candidate(self, comparator: str) -> None:
        """Every comparator, including strict `<`/`>`, is safe here: none
        of `_CANDIDATES` shares the literal's epoch *and* trimmed release,
        which is the only situation `TestExclusiveComparatorPrePostReleaseCarveOut`
        shows disagreement in.
        """
        assert_matchspec_agrees_with_pip(f"{comparator}1!1.0.0", self._CANDIDATES)


class TestExclusiveComparatorPrePostReleaseCarveOut:
    """PEP 440 gives strict `<`/`>` a carve-out that docs/matchspec.md's
    Operator conversion section doesn't mention, and that reroll's plain
    passthrough conversion (`_convert_specifier`) doesn't implement:

    * `<V` excludes *every* pre-release of `V` itself (dev, alpha, beta,
      rc) -- not just versions mathematically below `V` -- unless `V` is
      itself a pre-release.
    * `>V` excludes *every* post-release of `V` itself -- unless `V` is
      itself a post-release.

    (`packaging`'s `_ranges.standard_ranges` implements exactly this; see
    the `>V`/`<V` branches.) Reroll converts `<V`/`>V` to a matchspec
    verbatim, which has no such carve-out -- conda's plain version
    ordering compares mathematically, with no notion that a pre-release of
    `V` should be treated as "not less than `V`" for this one comparator.

    Each case below is a confirmed, reproducible disagreement between
    pip's own evaluation and reroll's current conversion -- `xfail`ed
    rather than silently fixed or dropped, so it stays visible pending a
    decision on docs/matchspec.md's "operators pass through as-is" claim,
    which this contradicts. (`~=` and the `==X.Y.*` glob rewrite are
    *not* affected -- see `TestCompatibleReleaseEquivalence`'s docstring
    for why the `.0a0` anchor they use sidesteps this exact issue.)
    """

    @pytest.mark.xfail(
        strict=True,
        reason="PEP 440 <V excludes every pre-release of V; matchspec's plain <V does not",
    )
    def test_strict_less_than_wrongly_includes_a_dev_release_of_the_boundary(self) -> None:
        assert_matchspec_agrees_with_pip("<1.0.0", ["1.0.0.dev0"])

    @pytest.mark.xfail(
        strict=True,
        reason="PEP 440 <V excludes every pre-release of V; matchspec's plain <V does not",
    )
    def test_strict_less_than_wrongly_includes_an_rc_release_of_the_boundary(self) -> None:
        assert_matchspec_agrees_with_pip("<1.0.0", ["1.0.0rc1"])

    @pytest.mark.xfail(
        strict=True,
        reason="PEP 440 >V excludes every post-release of V; matchspec's plain >V does not",
    )
    def test_strict_greater_than_wrongly_includes_a_post_release_of_the_boundary(self) -> None:
        assert_matchspec_agrees_with_pip(">1.0.0", ["1.0.0.post1"])

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "classic PEP 440 gotcha: '>=X.rc1,<X' matches nothing in pip, since <X "
            "excludes X's whole pre-release family regardless of the lower bound"
        ),
    )
    def test_rc_lower_bound_with_a_plain_upper_bound_is_an_empty_range_in_pip(self) -> None:
        assert_matchspec_agrees_with_pip(">=1.0.0rc1,<1.0.0", ["1.0.0rc1"], allow_pre=True)
