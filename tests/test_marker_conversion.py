"""Unit tests for `reroll.dependencies.marker_conversion`."""

from __future__ import annotations

import pytest
from markerpry import TRUE, parse
from packaging.markers import Marker
from packaging.version import Version as PypiVersion
from rattler import Version, VersionSpec

from reroll.dependencies.marker_conversion import marker_condition
from reroll.dependencies.version_format import format_version
from reroll.errors import UnconvertableMarkerError, UnconvertablePythonVersionEqualityError


def _marker_evaluates(marker: str, full_version: str) -> bool:
    """Whether `marker` (a `python_version` comparison) holds for a real
    (full) python version, per `packaging.markers.Marker.evaluate` --
    `python_version` is always major.minor, so `full_version` is
    truncated before evaluating, matching what a real interpreter's
    marker environment would report.
    """
    major, minor = full_version.split(".")[:2]
    return Marker(marker).evaluate({"python_version": f"{major}.{minor}"})


class TestPythonVersion:
    @pytest.mark.parametrize(
        ("marker", "expected"),
        [
            ('python_version == "3.9"', "python>=3.9.0a0,<3.10.0a0"),
            ('python_version != "3.9"', "python!=3.9.*"),
            ('python_version >= "3.9"', "python>=3.9.0a0"),
            ('python_version > "3.9"', "python>=3.10.0a0"),
            ('python_version <= "3.9"', "python<3.10.0a0"),
            ('python_version < "3.9"', "python<3.9.0a0"),
        ],
    )
    def test_comparators_convert_per_table(self, marker: str, expected: str) -> None:
        assert marker_condition(parse(marker)) == expected

    def test_double_digit_minor_increments_correctly(self) -> None:
        assert marker_condition(parse('python_version > "3.9"')) == "python>=3.10.0a0"
        assert marker_condition(parse('python_version < "3.10"')) == "python<3.10.0a0"

    @pytest.mark.parametrize("marker", ['python_version ~= "3.9"', 'python_version === "3.9"'])
    def test_unsupported_comparator_raises(self, marker: str) -> None:
        with pytest.raises(UnconvertableMarkerError, match="python_version"):
            marker_condition(parse(marker))

    def test_non_major_minor_literal_raises(self) -> None:
        with pytest.raises(UnconvertableMarkerError, match="python_version"):
            marker_condition(parse('python_version == "3.x"'))

    @pytest.mark.parametrize("comparator", ["==", "!="])
    def test_nonzero_micro_equality_literal_raises_its_own_error(self, comparator: str) -> None:
        """`python_version` can never carry a nonzero micro segment, so
        equality (or inequality) against a literal that has one is a
        constant, not a matchspec range -- see
        `TestPythonVersionNonzeroMicroLiteralEquality` for the underlying
        ground truth. This gets its own error class, rather than the
        generic `UnconvertableMarkerError`, so its real-world frequency
        can be measured separately.
        """
        with pytest.raises(UnconvertablePythonVersionEqualityError, match="python_version"):
            marker_condition(parse(f'python_version {comparator} "3.9.1"'))

    @pytest.mark.parametrize("literal", ["3.9.0rc1", "3.9.0.post1", "3.9.0.dev1", "1!3.9"])
    def test_non_plain_release_literal_raises(self, literal: str) -> None:
        """A literal with an epoch, pre-release, post-release, or
        dev-release component isn't a plain major[.minor[.micro]] release,
        so it's rejected the same way an unparseable literal is -- this
        codebase has no derivation for what such a literal should mean
        against `python_version`.
        """
        with pytest.raises(UnconvertableMarkerError, match="python_version"):
            marker_condition(parse(f'python_version == "{literal}"'))

    def test_le_boundary_is_anchored_so_a_patch_release_still_matches(self) -> None:
        """`python_version <= "3.9"` converts to `python<3.10.0a0`, not a
        literal `python<=3.9` -- the anchor at the *next* minor's `.0a0`
        is what lets a 3.9 patch release like 3.9.1 still satisfy the
        condition; `<=3.9` alone would incorrectly exclude it.
        """
        condition = marker_condition(parse('python_version <= "3.9"'))

        assert condition == "python<3.10.0a0"
        assert VersionSpec("<3.10.0a0").matches(Version("3.9.1"))
        assert not VersionSpec("<=3.9").matches(Version("3.9.1"))

    def test_gt_boundary_is_anchored_so_a_patch_release_is_still_excluded(self) -> None:
        """`python_version > "3.9"` converts to `python>=3.10.0a0`, not a
        literal `python>3.9` -- the anchor at the *next* minor's `.0a0` is
        what keeps a 3.9 patch release like 3.9.1 excluded; `>3.9` alone
        would incorrectly include it.
        """
        condition = marker_condition(parse('python_version > "3.9"'))

        assert condition == "python>=3.10.0a0"
        assert not VersionSpec(">=3.10.0a0").matches(Version("3.9.1"))
        assert VersionSpec(">3.9").matches(Version("3.9.1"))


class TestPythonVersionBareMajorLiteral:
    """A bare-major literal (`python_version == "3"`, no minor segment) is
    PEP 440-equivalent to `"3.0"`: a version's trailing zero release
    segments are insignificant, so `packaging`'s own marker evaluation
    treats `"3"` and `"3.0"` identically. The matchspec produced for it
    must therefore reproduce `packaging.markers.Marker.evaluate`'s result
    for every real (full) python version whose *truncated* major.minor is
    fed into that evaluation -- not just at the `3.0`/`3.1` boundary
    itself, but also across a pre-release of that boundary and a
    higher major version.
    """

    _FULL_VERSIONS = ["3.0.0", "3.0.1", "3.0.0a0", "3.1.0", "3.9.5", "4.0.0"]

    @pytest.mark.parametrize(
        ("comparator", "expected"),
        [("==", "python>=3.0.0a0,<3.1.0a0"), (">=", "python>=3.0.0a0")],
    )
    def test_matchspec_matches_the_marker_for_every_full_version(
        self, comparator: str, expected: str
    ) -> None:
        condition = marker_condition(parse(f'python_version {comparator} "3"'))
        assert condition == expected

        spec = VersionSpec(condition.removeprefix("python"))
        for full_version in self._FULL_VERSIONS:
            marker_result = _marker_evaluates(f'python_version {comparator} "3"', full_version)
            assert spec.matches(Version(full_version)) == marker_result, full_version


class TestPythonVersionZeroPaddedMicroLiteral:
    """A micro-pinned literal whose patch segment is `0` (e.g. `"3.5.0"`)
    is PEP 440-equivalent to bare `"3.5"` for the same trailing-zero
    reason as `TestPythonVersionBareMajorLiteral` -- `packaging`'s marker
    evaluation treats them identically for every comparator, so the
    matchspec must be identical too (the *existing* major.minor table
    entry, unchanged).
    """

    _FULL_VERSIONS = ["3.4.9", "3.5.0", "3.5.1", "3.5.9", "3.6.0a0", "3.6.0"]

    @pytest.mark.parametrize(
        ("comparator", "expected"),
        [
            ("==", "python>=3.5.0a0,<3.6.0a0"),
            ("!=", "python!=3.5.*"),
            (">=", "python>=3.5.0a0"),
            (">", "python>=3.6.0a0"),
            ("<=", "python<3.6.0a0"),
            ("<", "python<3.5.0a0"),
        ],
    )
    def test_matchspec_matches_the_marker_for_every_full_version(
        self, comparator: str, expected: str
    ) -> None:
        condition = marker_condition(parse(f'python_version {comparator} "3.5.0"'))
        assert condition == expected

        spec = VersionSpec(condition.removeprefix("python"))
        for full_version in self._FULL_VERSIONS:
            marker_result = _marker_evaluates(f'python_version {comparator} "3.5.0"', full_version)
            assert spec.matches(Version(full_version)) == marker_result, full_version


class TestPythonVersionNonzeroMicroLiteralOrderedComparators:
    """A micro-pinned literal with a *nonzero* patch segment (e.g.
    `"3.5.2"`) can never equal `python_version`, which is always exactly
    major.minor -- so an ordered comparator's result no longer depends on
    the literal's patch digit at all, only on which side of the literal's
    major.minor its own major.minor falls. That collapses each comparator
    pair onto the *other* comparator's existing major.minor table entry:
    `>=` and `>` both behave like plain `>` against `"3.5"`, and `<=` and
    `<` both behave like plain `<=` against `"3.5"` (verified against
    `packaging`'s marker evaluation below). `==`/`!=` don't collapse onto
    an existing entry at all -- see
    `TestPythonVersionNonzeroMicroLiteralEquality`.
    """

    _FULL_VERSIONS = ["3.4.9", "3.5.0", "3.5.1", "3.5.9", "3.6.0a0", "3.6.0"]

    @pytest.mark.parametrize(
        ("comparator", "expected"),
        [
            (">=", "python>=3.6.0a0"),
            (">", "python>=3.6.0a0"),
            ("<=", "python<3.6.0a0"),
            ("<", "python<3.6.0a0"),
        ],
    )
    def test_matchspec_matches_the_marker_for_every_full_version(
        self, comparator: str, expected: str
    ) -> None:
        condition = marker_condition(parse(f'python_version {comparator} "3.5.2"'))
        assert condition == expected

        spec = VersionSpec(condition.removeprefix("python"))
        for full_version in self._FULL_VERSIONS:
            marker_result = _marker_evaluates(f'python_version {comparator} "3.5.2"', full_version)
            assert spec.matches(Version(full_version)) == marker_result, full_version


class TestPythonVersionNonzeroMicroLiteralEquality:
    """`python_version == "3.5.2"` is unconditionally `False`, and
    `python_version != "3.5.2"` is unconditionally `True`, for *every*
    python version -- `python_version` can never carry a nonzero third
    release segment, so equality against a literal that has one can never
    hold. Unlike the ordered comparators
    (`TestPythonVersionNonzeroMicroLiteralOrderedComparators`), a constant
    True/False can't be written as a `python<op>version` matchspec
    fragment, so this is a ground-truth fact about the marker itself
    (checked directly against `packaging.markers.Marker`) rather than a
    test of a specific conversion -- `marker_condition` raises
    `UnconvertablePythonVersionEqualityError` for this case instead of
    representing it (see
    `TestPythonVersion.test_nonzero_micro_equality_literal_raises_its_own_error`),
    so its real-world frequency can be measured before deciding whether a
    constant-folding representation is worth building.
    """

    _FULL_VERSIONS = ["3.4.9", "3.5.0", "3.5.1", "3.5.9", "3.6.0a0", "3.6.0"]

    def test_equality_is_unconditionally_false(self) -> None:
        for full_version in self._FULL_VERSIONS:
            assert _marker_evaluates('python_version == "3.5.2"', full_version) is False

    def test_inequality_is_unconditionally_true(self) -> None:
        for full_version in self._FULL_VERSIONS:
            assert _marker_evaluates('python_version != "3.5.2"', full_version) is True


class TestPythonVersionMajorGlobLiteral:
    """`python_version == "3.*"` (a bare-major PEP 440 prefix glob) is a
    well-formed marker meaning "any Python 3.y" -- `packaging`'s own
    marker evaluation resolves the glob via `Specifier`, exactly as it
    would for any other `python_version` comparison. The matchspec
    produced for it must reproduce that evaluation for every real (full)
    python version, the same way `TestPythonVersionBareMajorLiteral`
    checks the plain bare-major case.
    """

    _FULL_VERSIONS = ["2.7.18", "3.0.0", "3.5.9", "3.9.5", "3.10.0", "4.0.0"]

    @pytest.mark.parametrize(
        ("comparator", "expected"),
        [("==", "python=3"), ("!=", "python!=3.*")],
    )
    def test_matchspec_matches_the_marker_for_every_full_version(
        self, comparator: str, expected: str
    ) -> None:
        condition = marker_condition(parse(f'python_version {comparator} "3.*"'))
        assert condition == expected

        spec = VersionSpec(condition.removeprefix("python"))
        for full_version in self._FULL_VERSIONS:
            marker_result = _marker_evaluates(f'python_version {comparator} "3.*"', full_version)
            assert spec.matches(Version(full_version)) == marker_result, full_version

    @pytest.mark.parametrize("literal", ["3.x.*", "3.9.*"])
    def test_non_bare_major_glob_raises(self, literal: str) -> None:
        """A glob whose prefix isn't parseable at all (`"3.x.*"`), or is
        parseable but has more than one release segment (`"3.9.*"`), is
        not the bare-major shape this class handles -- unlike a bare
        major glob, matchspec's fuzzy match can't represent a
        minor-or-deeper glob the same way a plain comparison would (see
        `_python_version_major_glob`), so it isn't attempted here.
        """
        with pytest.raises(UnconvertableMarkerError, match="python_version"):
            marker_condition(parse(f'python_version == "{literal}"'))


class TestPythonFullVersion:
    @pytest.mark.parametrize(
        ("comparator", "matchspec_comparator"),
        [("==", "=="), ("!=", "!="), (">=", ">="), (">", ">"), ("<=", "<="), ("<", "<")],
    )
    def test_comparator_passes_through_with_renamed_key(
        self, comparator: str, matchspec_comparator: str
    ) -> None:
        marker = f'python_full_version {comparator} "3.13.0"'

        assert marker_condition(parse(marker)) == f"python{matchspec_comparator}3.13.0"

    def test_implementation_version_is_treated_the_same_as_python_full_version(self) -> None:
        assert marker_condition(parse('implementation_version >= "3.13.0"')) == "python>=3.13.0"

    def test_pre_release_literal_is_converted_to_conda_style(self) -> None:
        assert marker_condition(parse('python_full_version == "3.13.0rc1"')) == "python==3.13.0.rc1"

    @pytest.mark.parametrize(
        "marker",
        ['python_full_version ~= "3.13.0"', 'python_full_version === "3.13.0"'],
    )
    def test_unsupported_comparator_raises(self, marker: str) -> None:
        with pytest.raises(UnconvertableMarkerError, match="python_full_version"):
            marker_condition(parse(marker))


class TestFullVersionGlobLiteral:
    """`python_full_version == "X.Y.*"` shows up in real wheels (e.g.
    cibuildwheel-style per-minor pins), but isn't in docs/matchspec.md's
    marker table, which only covers a plain `"X.Y.Z"` literal. The current
    passthrough (`format_version_literal` can't parse `"3.8.*"` as a
    `Version`, so it falls through unchanged) produces `python==3.8.*`,
    which py-rattler's `MatchSpec` rejects -- confirmed against a real
    corpus of published wheels, where this exact shape (`numpy`, `zarr`,
    `importlib-metadata`, `pytest`, ... constrained to one Python minor)
    accounts for 1,815 of reroll's "not a valid matchspec" failures.
    """

    def test_equality_glob_literal_is_rewritten_to_the_fuzzy_form(self) -> None:
        """Mirrors docs/matchspec.md's Operator conversion rule for a
        plain specifier (`==X.Y.*` becomes `=X.Y`) -- matchspec disallows
        `==` combined with a glob regardless of where the glob came from.
        """
        assert marker_condition(parse('python_full_version == "3.8.*"')) == "python=3.8"

    def test_inequality_glob_literal_passes_through_unchanged(self) -> None:
        """Per docs/matchspec.md, prefix *exclusion* needs no rewrite."""
        assert marker_condition(parse('python_full_version != "3.8.*"')) == "python!=3.8.*"

    def test_glob_literal_whose_prefix_is_not_a_version_passes_through_unchanged(self) -> None:
        """A `.*`-suffixed literal only counts as a prefix-match glob if
        the part before `.*` itself parses as a version -- otherwise it's
        treated like any other unparseable literal (passed through
        unchanged), the same as before this class's rewrite existed.
        """
        assert marker_condition(parse('python_full_version == "3.x.*"')) == "python==3.x.*"

    @pytest.mark.parametrize(
        "full_version",
        [
            "3.8.0",
            "3.8.1",
            "3.8.10",
            "3.8.99",
            "3.7.9",
            "3.9.0",
            "3.10.0",
            "3.80.0",
            "3.8.0rc1",
            "3.8.0b1",
            "3.8.0.dev1",
            "3.8",
            "3.8.0.post1",
        ],
    )
    def test_fuzzy_matchspec_agrees_with_packagings_own_glob_evaluation(
        self, full_version: str
    ) -> None:
        """`python=3.8` is only the right translation of
        `python_full_version == "3.8.*"` if it agrees, for every concrete
        interpreter version the marker could actually be evaluated
        against, with how `packaging` itself (not `markerpry`, which never
        evaluates a marker, only converts its syntax) resolves that glob
        via `Specifier("==3.8.*").contains(...)`.

        `3.80.0` is the case that would catch a naive string-prefix
        implementation on either side: it starts with the characters
        `"3.8"` but is a different minor version, so both `packaging` and
        rattler's fuzzy match must reject it by comparing release
        segments, not string prefixes.
        """
        marker = Marker('python_full_version == "3.8.*"')
        packaging_says = marker.evaluate({"python_full_version": full_version})

        conda_version = format_version(PypiVersion(full_version))
        matchspec_says = VersionSpec("=3.8").matches(Version(conda_version))

        assert packaging_says == matchspec_says


class TestVirtualPackages:
    @pytest.mark.parametrize(
        ("marker", "expected"),
        [
            ('sys_platform == "linux"', "__linux"),
            ('sys_platform == "darwin"', "__osx"),
            ('sys_platform == "win32"', "__win"),
            ('platform_system == "Linux"', "__linux"),
            ('platform_system == "Darwin"', "__osx"),
            ('platform_system == "Windows"', "__win"),
            ('os_name == "posix"', "__unix"),
            ('os_name == "nt"', "__win"),
        ],
    )
    def test_known_equality_maps_to_a_virtual_package(self, marker: str, expected: str) -> None:
        assert marker_condition(parse(marker)) == expected

    @pytest.mark.parametrize(
        "marker",
        [
            'sys_platform != "win32"',
            'platform_system != "Windows"',
            'os_name != "nt"',
        ],
    )
    def test_inequality_is_unsupported(self, marker: str) -> None:
        with pytest.raises(UnconvertableMarkerError, match="!="):
            marker_condition(parse(marker))

    @pytest.mark.parametrize(
        "marker",
        [
            'sys_platform == "cygwin"',
            'platform_system == "Java"',
            'os_name == "java"',
        ],
    )
    def test_unrecognized_value_is_unsupported(self, marker: str) -> None:
        with pytest.raises(UnconvertableMarkerError, match="no known virtual package mapping"):
            marker_condition(parse(marker))

    def test_unsupported_comparator_is_rejected(self) -> None:
        with pytest.raises(UnconvertableMarkerError, match="not supported for sys_platform"):
            marker_condition(parse('sys_platform >= "win32"'))


class TestUnsupportedMarkerKeys:
    def test_platform_machine_is_unsupported(self) -> None:
        with pytest.raises(UnconvertableMarkerError, match="platform_machine"):
            marker_condition(parse('platform_machine == "x86_64"'))

    @pytest.mark.parametrize("marker", ['platform_release == "10"', 'platform_version == "1"'])
    def test_other_unmapped_keys_are_unsupported(self, marker: str) -> None:
        with pytest.raises(UnconvertableMarkerError, match="no matchspec equivalent"):
            marker_condition(parse(marker))


class TestContainsNode:
    def test_in_is_unsupported(self) -> None:
        with pytest.raises(UnconvertableMarkerError, match="in"):
            marker_condition(parse('python_version in "3.11"'))

    def test_not_in_is_unsupported(self) -> None:
        with pytest.raises(UnconvertableMarkerError, match="in"):
            marker_condition(parse('python_version not in "3.11"'))


class TestCombining:
    def test_and_combines_both_sides(self) -> None:
        marker = 'sys_platform == "win32" and python_version >= "3.9"'

        assert marker_condition(parse(marker)) == "__win and python>=3.9.0a0"

    def test_or_combines_both_sides(self) -> None:
        marker = 'sys_platform == "win32" or sys_platform == "darwin"'

        assert marker_condition(parse(marker)) == "__win or __osx"

    def test_mixed_nesting_parenthesizes_the_nested_operator_chain(self) -> None:
        marker = '(sys_platform == "win32" or sys_platform == "darwin") and python_version >= "3.9"'

        assert marker_condition(parse(marker)) == "(__win or __osx) and python>=3.9.0a0"

    def test_unsupported_leaf_propagates_through_a_combination(self) -> None:
        marker = 'platform_machine == "x86_64" and sys_platform == "win32"'

        with pytest.raises(UnconvertableMarkerError, match="platform_machine"):
            marker_condition(parse(marker))


class TestUnreachable:
    def test_a_resolved_boolean_leaf_raises_an_assertion_error(self) -> None:
        """`marker_condition` is only ever handed a `Node` parsed straight
        from a PEP 508 marker string, which never contains a resolved
        boolean literal -- this guards against misuse, e.g. passing an
        already-`markerpry.evaluate`d tree instead.
        """
        with pytest.raises(AssertionError):
            marker_condition(TRUE)
