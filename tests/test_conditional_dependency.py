"""Unit tests for `reroll.dependencies.conditional_dependency`."""

from __future__ import annotations

import pytest
from markerpry import parse

from reroll.dependencies.conditional_dependency import conditional_dependency
from reroll.errors import NeedsArchSplitError, UnconvertableMarkerError
from reroll.subdir import CondaSubdir


def _result(
    marker_str: str,
    *,
    extra: str = "",
    python_minor: int | None = None,
    subdir: CondaSubdir | None = None,
) -> str | None:
    return conditional_dependency(
        parse(marker_str), extra=extra, python_minor=python_minor, subdir=subdir
    )


class TestFullEvaluation:
    """Steps 3-5: a marker that fully resolves short-circuits to `""`/`None`
    without ever reaching the leftover-marker string.
    """

    def test_marker_matching_the_pinned_minor_is_unconditional(self) -> None:
        assert _result('python_version == "3.13"', python_minor=13) == ""

    def test_marker_not_matching_the_pinned_minor_is_skipped(self) -> None:
        assert _result('python_version == "3.9"', python_minor=13) is None


class TestExtraSelection:
    """Step 2: `extra` is added to the environment before evaluation, so a
    per-extra marker's truth depends on which extra (if any) is targeted.
    """

    def test_extra_matching_the_targeted_extra_is_unconditional(self) -> None:
        assert _result('extra == "cli"', extra="cli") == ""

    def test_extra_not_matching_the_targeted_extra_is_skipped(self) -> None:
        assert _result('extra == "cli"', extra="") is None

    def test_extra_short_circuits_the_rest_of_an_and_chain(self) -> None:
        """`extra == "cli" and python_version < "3.9"` should skip outright
        for the base dependency (`extra=""`) without needing `python_version`
        to be resolvable at all.
        """
        assert _result('extra == "cli" and python_version < "3.9"', extra="") is None

    def test_extra_matching_leaves_the_rest_of_an_and_chain_as_a_marker(self) -> None:
        result = _result(
            'extra == "cli" and python_version < "3.9"',
            extra="cli",
            python_minor=None,
        )

        assert result == 'python_version < "3.9"'


class TestArchSplit:
    """Step 6: a noarch record can't represent a leftover platform-specific
    key -- the caller must retry per-subdir instead.
    """

    @pytest.mark.parametrize(
        "marker_str",
        [
            'sys_platform == "linux"',
            'platform_system == "Linux"',
            'os_name == "posix"',
            'platform_machine == "x86_64"',
        ],
    )
    def test_noarch_with_a_platform_marker_raises(self, marker_str: str) -> None:
        with pytest.raises(NeedsArchSplitError):
            _result(marker_str, subdir=None)

    def test_arch_specific_resolves_a_matching_platform_marker_instead(self) -> None:
        assert _result('sys_platform == "linux"', subdir=CondaSubdir.LINUX_64) == ""

    def test_arch_specific_resolves_a_non_matching_platform_marker_instead(self) -> None:
        assert _result('sys_platform == "linux"', subdir=CondaSubdir.OSX_64) is None

    def test_arch_key_and_unpermitted_key_together_prefers_arch_split(self) -> None:
        """Step 6 runs before step 7: a noarch marker combining a
        platform-specific key with an otherwise-unpermitted key raises
        `NeedsArchSplitError`, not `UnconvertableMarkerError` -- the caller
        should retry per-subdir first; only an arch-specific retry that
        *still* can't resolve the unpermitted key would reach step 7.
        """
        with pytest.raises(NeedsArchSplitError):
            _result('platform_machine == "x86_64" and platform_release == "10"', subdir=None)


class TestUnpermittedKeys:
    """Step 7: any key besides `python_version`/`python_full_version`/
    `implementation_version` left over after evaluation (and after the
    noarch arch-split check) means the dependency has no conda
    representation at all.
    """

    def test_unmapped_key_raises_regardless_of_noarch_or_arch(self) -> None:
        with pytest.raises(UnconvertableMarkerError, match="platform_release"):
            _result('platform_release == "10"', subdir=None)

        with pytest.raises(UnconvertableMarkerError, match="platform_release"):
            _result('platform_release == "10"', subdir=CondaSubdir.LINUX_64)


class TestLeftoverMarkerString:
    """Step 8 stops at handing back a plain PEP 508 marker string --
    matchspec `when=` conversion is a later stage's job, not this
    function's.
    """

    def test_unpinned_python_version_is_returned_as_is(self) -> None:
        assert _result('python_version >= "3.9"', python_minor=None) == ('python_version >= "3.9"')


class TestFullVersionReduction:
    """Step 1: `python_full_version`/`implementation_version` used with
    `==`, `!=`, `in`, or `not in` anywhere in the marker disqualifies that
    key from the environment entirely, for the whole marker -- unlike an
    ordered comparator, which still benefits from the pinned minor's range
    check (docs/matchspec.md's reduction algorithm).
    """

    def test_ordered_comparator_still_uses_the_pinned_minors_range(self) -> None:
        """`python_full_version < "3.14"` is true across all of 3.13.*, so
        it resolves fully -- unaffected by the reduction rule, which only
        concerns `==`/`!=`/`in`/`not in`.
        """
        assert _result('python_full_version < "3.14"', python_minor=13) == ""

    def test_equality_outside_the_pinned_minor_is_not_resolved_false(self) -> None:
        """Without dropping `python_full_version` from the environment, a
        `RangeConstraint` spanning 3.13.0-3.13.100 would resolve `== "2.7.5"`
        to definitively false (2.7.5 can't be in that range) -- but the
        reduction algorithm says not to use `python_full_version` for
        equality at all, so this must be handed back as-is instead of
        being skipped.
        """
        assert _result('python_full_version == "2.7.5"', python_minor=13) == (
            'python_full_version == "2.7.5"'
        )

    def test_inequality_outside_the_pinned_minor_is_not_resolved_true(self) -> None:
        """The mirror image: a naive range check would resolve
        `!= "2.7.5"` to definitively true, but the reduction algorithm
        says equality/inequality against `python_full_version` is never
        reducible, so this must be handed back as-is instead of being
        added unconditionally.
        """
        assert _result('python_full_version != "2.7.5"', python_minor=13) == (
            'python_full_version != "2.7.5"'
        )

    def test_implementation_version_is_reduced_the_same_way(self) -> None:
        assert _result('implementation_version == "2.7.5"', python_minor=13) == (
            'implementation_version == "2.7.5"'
        )

    def test_in_test_against_full_version_is_dropped_from_the_environment_too(self) -> None:
        """`in`/`not in` is already undecidable for a `RangeConstraint`
        (it returns `None` unconditionally), so dropping the key changes
        nothing observable here -- this only confirms the drop covers
        `ContainsNode`, not just `CompareNode`.
        """
        assert _result('"3.13.2" in python_full_version', python_minor=13) == (
            '"3.13.2" in python_full_version'
        )

    def test_not_in_test_against_full_version_is_dropped_from_the_environment_too(self) -> None:
        """`not in`, same as `in` above, is one of the disqualifying
        operators (docs/matchspec.md's reduction algorithm groups `in`
        and `not in` together), and is covered by the same `ContainsNode`
        check regardless of its `negate` flag.
        """
        assert _result('"3.13.2" not in python_full_version', python_minor=13) == (
            '"3.13.2" not in python_full_version'
        )

    def test_ordered_comparator_between_the_pinned_minors_probe_points_is_not_reduced(
        self,
    ) -> None:
        """`python_full_version >= "3.13.50"` sits strictly between the
        pinned minor's two probe points (3.13.0 and 3.13.100): 3.13.0
        doesn't satisfy `>=3.13.50` but 3.13.100 does, so the two probes
        disagree and the reduction algorithm must leave this unresolved
        rather than reducing it to an always-true/always-false answer.
        """
        assert _result('python_full_version >= "3.13.50"', python_minor=13) == (
            'python_full_version >= "3.13.50"'
        )

    def test_disqualifying_python_full_version_does_not_disqualify_implementation_version(
        self,
    ) -> None:
        """`python_full_version` and `implementation_version` are reduced
        independently -- a disqualifying `==`/`!=`/`in`/`not in` use of one
        does not drop the other from the environment too.
        """
        result = _result(
            'python_full_version == "2.7.5" or implementation_version < "3.9"',
            python_minor=13,
        )

        assert result == 'python_full_version == "2.7.5"'

    def test_disqualifying_use_disqualifies_every_occurrence_in_the_marker(self) -> None:
        """The reduction rule drops the key for the *whole* marker once
        disqualified anywhere in it -- so a second, otherwise-resolvable
        clause on the same key is also left unresolved and preserved in
        the returned marker, rather than being resolved away and dropped.
        """
        result = _result(
            'python_full_version == "3.13.2" or python_full_version < "3.9"',
            python_minor=13,
        )

        assert result == 'python_full_version == "3.13.2" or python_full_version < "3.9"'


class TestExactMinor:
    """`python_minor` only fixes `python_version`/`python_full_version`/
    `implementation_version` in the environment when it's not `None` --
    otherwise a marker referencing them is handed back unresolved.
    """

    def test_no_pinned_minor_does_not_resolve_python_version(self) -> None:
        assert _result('python_version == "3.13"', python_minor=None) == (
            'python_version == "3.13"'
        )

    def test_pinned_minor_resolves_python_version(self) -> None:
        assert _result('python_version == "3.13"', python_minor=13) == ""
