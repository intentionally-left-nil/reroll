"""Unit tests for `reroll.dependencies.marker_conversion`."""

from __future__ import annotations

import pytest
from markerpry import TRUE, parse

from reroll.dependencies.marker_conversion import marker_condition
from reroll.errors import UnconvertableMarkerError


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

    @pytest.mark.parametrize("literal", ["3", "3.9.1", "3.x"])
    def test_non_major_minor_literal_raises(self, literal: str) -> None:
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
