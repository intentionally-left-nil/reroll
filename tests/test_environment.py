"""Unit tests for `reroll.dependencies.environment`."""

from __future__ import annotations

import pytest
from markerpry import evaluate, parse
from markerpry.constraint import RangeConstraint
from packaging.version import Version

from reroll.dependencies.environment import arch_specific_environment, noarch_environment
from reroll.subdir import CondaSubdir


class TestNoarchEnvironment:
    def test_fixes_the_interpreter_keys_regardless_of_minor(self) -> None:
        assert noarch_environment(None) == {
            "platform_python_implementation": ["CPython"],
            "implementation_name": ["cpython"],
        }

    def test_omits_python_version_keys_when_minor_is_unknown(self) -> None:
        environment = noarch_environment(None)

        assert "python_version" not in environment
        assert "python_full_version" not in environment
        assert "implementation_version" not in environment

    def test_sets_python_version_to_the_pinned_minor(self) -> None:
        environment = noarch_environment(13)

        assert environment["python_version"] == [Version("3.13")]

    def test_full_version_and_implementation_version_share_the_pinned_minors_range(self) -> None:
        environment = noarch_environment(13)
        expected_range = [RangeConstraint(Version("3.13.0"), Version("3.13.100"))]

        assert environment["python_full_version"] == expected_range
        assert environment["implementation_version"] == expected_range

    def test_still_fixes_the_interpreter_keys_when_minor_is_pinned(self) -> None:
        environment = noarch_environment(13)

        assert environment["platform_python_implementation"] == ["CPython"]
        assert environment["implementation_name"] == ["cpython"]


class TestArchSpecificEnvironment:
    @pytest.mark.parametrize(
        ("subdir", "platform_system", "platform_machine", "sys_platform", "os_name"),
        [
            (CondaSubdir.LINUX_64, "Linux", "x86_64", "linux", "posix"),
            (CondaSubdir.LINUX_AARCH64, "Linux", "aarch64", "linux", "posix"),
            (CondaSubdir.OSX_64, "Darwin", "x86_64", "darwin", "posix"),
            (CondaSubdir.OSX_ARM64, "Darwin", "arm64", "darwin", "posix"),
            (CondaSubdir.WIN_64, "Windows", "AMD64", "win32", "nt"),
            (CondaSubdir.WIN_ARM64, "Windows", "ARM64", "win32", "nt"),
        ],
    )
    def test_maps_each_supported_subdir(
        self,
        subdir: CondaSubdir,
        platform_system: str,
        platform_machine: str,
        sys_platform: str,
        os_name: str,
    ) -> None:
        environment = arch_specific_environment(None, subdir)

        assert environment["platform_system"] == [platform_system]
        assert environment["platform_machine"] == [platform_machine]
        assert environment["sys_platform"] == [sys_platform]
        assert environment["os_name"] == [os_name]

    def test_includes_the_noarch_environment_too(self) -> None:
        environment = arch_specific_environment(13, CondaSubdir.LINUX_64)

        assert environment["python_version"] == [Version("3.13")]
        assert environment["platform_python_implementation"] == ["CPython"]
        assert environment["implementation_name"] == ["cpython"]


class TestFullVersionReductionBehavior:
    """`noarch_environment`'s `python_full_version`/`implementation_version`
    entries lean on `markerpry.RangeConstraint` to reproduce
    docs/matchspec.md's reduction algorithm -- these tests demonstrate that
    behavior end-to-end via `markerpry.evaluate` rather than just inspecting
    the environment's shape.
    """

    def test_a_comparison_true_across_the_whole_pinned_minor_resolves_to_true(self) -> None:
        node = parse('python_full_version < "3.14"')

        assert evaluate(node, noarch_environment(13)) == True  # noqa: E712

    def test_a_comparison_false_across_the_whole_pinned_minor_resolves_to_false(self) -> None:
        node = parse('python_full_version < "3.9"')

        assert evaluate(node, noarch_environment(13)) == False  # noqa: E712

    def test_a_comparison_that_splits_within_the_pinned_minor_is_left_unresolved(self) -> None:
        node = parse('python_full_version >= "3.13.5"')

        assert evaluate(node, noarch_environment(13)) == node

    def test_equality_against_full_version_is_never_resolved_true(self) -> None:
        node = parse('python_full_version == "3.13.2"')

        assert evaluate(node, noarch_environment(13)) == node

    def test_implementation_version_behaves_the_same_as_full_version(self) -> None:
        node = parse('implementation_version < "3.9"')

        assert evaluate(node, noarch_environment(13)) == False  # noqa: E712
