"""Unit tests for `reroll.dependencies`."""

from __future__ import annotations

import logging

import pytest

from reroll.dependencies import calculate_dependencies, wheel_dependencies
from reroll.dependencies.extras import find_extras
from reroll.dependencies.python import python_dependencies
from reroll.dependencies.requires_dist import strip_interpreter_requirements
from reroll.errors import PythonRangeMismatchError
from reroll.filename import Arch, WheelConfig
from reroll.name_mapping import aggregator_mapper
from reroll.subdir import CondaSubdir
from reroll.wheel_metadata import WheelMetadata


def _config(
    *,
    interpreter: str = "py3",
    abi: str = "none",
    platform: str = "any",
    arch: Arch | None = None,
) -> WheelConfig:
    """A valid `WheelConfig` (py3-none-any) with the interpreter/abi/platform/arch
    tags overridden for tests that only care about one axis at a time.
    """
    return WheelConfig(
        normalized_pypi_name="tinylib",
        conda_name="tinylib",
        version="1.2.3",
        build=(),
        interpreter=interpreter,
        abi=abi,
        platform=platform,
        arch=arch,
    )


def _metadata(
    *,
    requires_python: str | None = None,
    requires_dist: tuple[str, ...] = (),
) -> WheelMetadata:
    """A valid, minimal `WheelMetadata` with `requires_python`/`requires_dist`
    overridden.
    """
    return WheelMetadata(
        name="tinylib",
        version="1.2.3",
        requires_python=requires_python,
        requires_dist=requires_dist,
    )


# --------------------------------------------------------------------------
# `python_dependencies`: no `Requires-Python` -- filename range used as-is
# --------------------------------------------------------------------------


class TestNoRequiresPython:
    @pytest.mark.parametrize(
        ("interpreter", "expected_matchspec"),
        [
            ("py3", "python >=3.0"),
            ("py38", "python >=3.8"),
            ("py313", "python >=3.13"),
        ],
    )
    def test_pure_python_floor_matchspec(self, interpreter: str, expected_matchspec: str) -> None:
        config = _config(interpreter=interpreter, abi="none")

        assert python_dependencies(config, _metadata()) == (expected_matchspec,)

    @pytest.mark.parametrize(
        ("interpreter", "abi", "expected_matchspec"),
        [
            ("cp37", "none", "python >=3.7,<3.8.0a0"),
            ("cp37", "cp37", "python >=3.7,<3.8.0a0"),
            ("cp39", "cp39", "python >=3.9,<3.10.0a0"),
        ],
    )
    def test_pinned_python_only_below_310(
        self, interpreter: str, abi: str, expected_matchspec: str
    ) -> None:
        config = _config(interpreter=interpreter, abi=abi)

        assert python_dependencies(config, _metadata()) == (expected_matchspec,)

    @pytest.mark.parametrize(
        ("minor", "expected_python", "expected_python_abi"),
        [
            (10, "python >=3.10,<3.11.0a0", "python_abi 3.10.* *_cp310"),
            (11, "python >=3.11,<3.12.0a0", "python_abi 3.11.* *_cp311"),
            (12, "python >=3.12,<3.13.0a0", "python_abi 3.12.* *_cp312"),
            (13, "python >=3.13,<3.14.0a0", "python_abi 3.13.* *_cp313"),
        ],
    )
    def test_regular_gil_emits_python_abi_from_310(
        self, minor: int, expected_python: str, expected_python_abi: str
    ) -> None:
        config = _config(interpreter=f"cp3{minor}", abi=f"cp3{minor}")

        assert python_dependencies(config, _metadata()) == (
            expected_python,
            expected_python_abi,
        )

    def test_free_threaded_emits_t_suffixed_python_abi(self) -> None:
        config = _config(interpreter="cp313", abi="cp313t")

        assert python_dependencies(config, _metadata()) == (
            "python >=3.13,<3.14.0a0",
            "python_abi 3.13.* *_cp313t",
        )

    def test_none_abi_still_emits_python_abi(self) -> None:
        """A `cp`-prefixed interpreter pins its minor exactly even with the
        `none` ABI (docs/wheel_filename.md); since the wheel is still
        CPython-only, `python_abi` is emitted the same as for a `cp310`
        ABI tag.
        """
        config = _config(interpreter="cp310", abi="none")

        assert python_dependencies(config, _metadata()) == (
            "python >=3.10,<3.11.0a0",
            "python_abi 3.10.* *_cp310",
        )


# --------------------------------------------------------------------------
# `python_dependencies`: `Requires-Python` tightens the filename range
# --------------------------------------------------------------------------


class TestRequiresPythonTightening:
    def test_looser_requires_python_leaves_floor_unchanged(self) -> None:
        config = _config(interpreter="py38", abi="none")

        assert python_dependencies(config, _metadata(requires_python=">=3.5")) == ("python >=3.8",)

    def test_stricter_requires_python_raises_the_floor(self) -> None:
        config = _config(interpreter="py38", abi="none")

        assert python_dependencies(config, _metadata(requires_python=">=3.10")) == (
            "python >=3.10",
        )

    def test_requires_python_adds_a_ceiling_to_a_floor(self) -> None:
        """Per docs/wheel_to_conda_dependencies.md, this "funky tightening"
        case (a pure-python wheel whose combined range is neither a plain
        floor nor a single-minor pin) is rendered as-is, using the same
        `>=,<X.0a0` shape as every other bounded range.
        """
        config = _config(interpreter="py38", abi="none")

        assert python_dependencies(config, _metadata(requires_python=">=3.9,<3.12")) == (
            "python >=3.9,<3.12.0a0",
        )

    def test_requires_python_tightens_a_floor_to_an_exact_minor(self) -> None:
        """The "exact minor version" case docs/wheel_to_conda_dependencies.md
        calls out separately from generic "funky tightening": `Requires-Python`
        pinning to a single minor collapses a pure-python floor to that
        minor's exact range, rendered the same as any other bounded range.
        """
        config = _config(interpreter="py38", abi="none")

        assert python_dependencies(config, _metadata(requires_python="==3.9.*")) == (
            "python >=3.9,<3.10.0a0",
        )

    def test_compatible_requires_python_leaves_a_pin_unchanged(self) -> None:
        config = _config(interpreter="cp39", abi="cp39")

        assert python_dependencies(config, _metadata(requires_python=">=3.5")) == (
            "python >=3.9,<3.10.0a0",
        )

    def test_incompatible_requires_python_is_unsolvable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        config = _config(interpreter="cp39", abi="cp39")

        with (
            caplog.at_level(logging.WARNING, logger="reroll.unconvertable"),
            pytest.raises(PythonRangeMismatchError) as exc_info,
        ):
            python_dependencies(config, _metadata(requires_python=">=3.10"))

        assert "tinylib" in str(exc_info.value)
        assert ">=3.10" in str(exc_info.value)
        assert caplog.records

    def test_python_abi_is_unaffected_by_requires_python(self) -> None:
        """`python_abi` is derived from the wheel's own filename tag, not
        from the tightened range -- narrowing `Requires-Python` still
        leaves it exactly as it would be without `Requires-Python` at all.
        """
        config = _config(interpreter="cp313", abi="cp313")

        assert python_dependencies(config, _metadata(requires_python=">=3.13,<3.14")) == (
            "python >=3.13,<3.14.0a0",
            "python_abi 3.13.* *_cp313",
        )


# --------------------------------------------------------------------------
# `strip_interpreter_requirements`: `Requires-Dist` interpreter stripping
# --------------------------------------------------------------------------


class TestStripInterpreterRequirements:
    @pytest.mark.parametrize(
        "entry",
        [
            "python",
            "python>=3.9",
            "python==3.*",
            "python[extra]",
            "cpython",
            "pypy>=7.0",
            "graalpy",
            "Python>=3.9",
        ],
    )
    def test_strips_unconditional_interpreter_requirement(self, entry: str) -> None:
        assert strip_interpreter_requirements((entry,)) == ()

    def test_leaves_unrelated_requirements_untouched(self) -> None:
        requires_dist = ("requests>=2.0", "pydantic-core==2.27.2")

        assert strip_interpreter_requirements(requires_dist) == requires_dist

    def test_leaves_a_marker_qualified_interpreter_requirement_untouched(self) -> None:
        """Per docs/wheel_to_conda_dependencies.md, the stripping rule only
        covers a *direct* dependency on the interpreter, not a marker --
        a marker-qualified reference is left for marker conversion instead.
        """
        requires_dist = ('python; extra == "dev"',)

        assert strip_interpreter_requirements(requires_dist) == requires_dist

    def test_strips_only_matching_entries_from_a_mixed_list(self) -> None:
        requires_dist = ("requests>=2.0", "python>=3.9", "click==8.*")

        assert strip_interpreter_requirements(requires_dist) == ("requests>=2.0", "click==8.*")

    def test_empty_requires_dist_is_unchanged(self) -> None:
        assert strip_interpreter_requirements(()) == ()


# --------------------------------------------------------------------------
# `find_extras`: collecting every extra a package declares
# --------------------------------------------------------------------------


class TestFindExtras:
    def test_no_requires_dist_yields_an_empty_set(self) -> None:
        assert find_extras(()) == set()

    def test_marker_free_entry_contributes_nothing(self) -> None:
        assert find_extras(("requests>=2.0.0",)) == set()

    def test_unrelated_marker_contributes_nothing(self) -> None:
        requires_dist = ('requests>=2.0.0; sys_platform == "win32"',)

        assert find_extras(requires_dist) == set()

    def test_simple_extra_marker_is_found(self) -> None:
        requires_dist = ('httpx>=0.23.0; extra == "standard"',)

        assert find_extras(requires_dist) == {"standard"}

    def test_reversed_operand_order_is_found(self) -> None:
        requires_dist = ('httpx>=0.23.0; "standard" == extra',)

        assert find_extras(requires_dist) == {"standard"}

    def test_extra_inequality_is_found(self) -> None:
        """Unlike `extra_marker_entry` (which only recognizes a bare `==`
        clause it can strip), `find_extras` just wants every extra name a
        marker references, regardless of comparator.
        """
        requires_dist = ('httpx>=0.23.0; extra != "standard"',)

        assert find_extras(requires_dist) == {"standard"}

    def test_extra_clause_anded_with_another_condition_is_found(self) -> None:
        requires_dist = ('packageA; python_version < "3.9" and extra == "cli"',)

        assert find_extras(requires_dist) == {"cli"}

    def test_multiple_extra_clauses_ored_together_are_both_found(self) -> None:
        requires_dist = ('requests>=2.0.0; extra == "foo" or extra == "bar"',)

        assert find_extras(requires_dist) == {"foo", "bar"}

    def test_nested_extra_clauses_are_found(self) -> None:
        requires_dist = ('packageA; (extra == "foo" or extra == "bar") and python_version < "3.9"',)

        assert find_extras(requires_dist) == {"foo", "bar"}

    def test_extras_are_accumulated_across_entries(self) -> None:
        requires_dist = (
            'httpx>=0.23.0; extra == "standard"',
            'orjson>=3.2.1; extra == "all"',
        )

        assert find_extras(requires_dist) == {"standard", "all"}

    def test_repeated_extra_across_entries_is_deduplicated(self) -> None:
        requires_dist = (
            'httpx>=0.23.0; extra == "standard"',
            'jinja2>=2.11.2; extra == "standard"',
        )

        assert find_extras(requires_dist) == {"standard"}

    def test_extra_name_is_normalized(self) -> None:
        requires_dist = ('httpx>=0.23.0; extra == "Some_Extra.Name"',)

        assert find_extras(requires_dist) == {"some-extra-name"}

    def test_extra_name_over_64_characters_passes_through_unrejected(self) -> None:
        """PEP 503 places no length limit on a name -- conda's 64-character
        `extras` bracket limit (docs/matchspec.md#extras-name-normalization)
        is a later, conda-specific concern, not this function's.
        """
        long_name = "a" * 65
        requires_dist = (f'httpx>=0.23.0; extra == "{long_name}"',)

        assert find_extras(requires_dist) == {long_name}


# --------------------------------------------------------------------------
# `wheel_dependencies`: package entrypoint -- noarch vs. arch-split retry
# --------------------------------------------------------------------------


class TestWheelDependenciesNoarch:
    def test_noarch_wheel_returns_a_single_none_keyed_result(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=2.0.0",))

        result = wheel_dependencies(config, metadata, (aggregator_mapper,))

        assert result == {
            None: calculate_dependencies(config, metadata, (aggregator_mapper,), subdir=None)
        }

    def test_allow_pre_is_passed_through(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests==1.0.0rc1",))

        result = wheel_dependencies(config, metadata, (aggregator_mapper,), allow_pre=True)

        assert result[None].depends == ("requests ==1.0.0.rc1", "python >=3.0")

    def test_unsolvable_python_range_raises(self) -> None:
        config = _config(interpreter="cp39", abi="cp39")
        metadata = _metadata(requires_python=">=3.10")

        with pytest.raises(PythonRangeMismatchError):
            wheel_dependencies(config, metadata, (aggregator_mapper,))


class TestWheelDependenciesArchSplit:
    def test_arch_specific_marker_emits_one_result_per_subdir(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; platform_machine == "x86_64"',))

        result = wheel_dependencies(config, metadata, (aggregator_mapper,))

        assert set(result) == set(CondaSubdir)
        for subdir in CondaSubdir:
            assert result[subdir] == calculate_dependencies(
                config, metadata, (aggregator_mapper,), subdir=subdir
            )

    def test_linux_64_resolves_the_matching_arch_marker(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; platform_machine == "x86_64"',))

        result = wheel_dependencies(config, metadata, (aggregator_mapper,))

        assert result[CondaSubdir.LINUX_64].depends == ("requests >=2.0.0", "python >=3.0")
        assert result[CondaSubdir.LINUX_AARCH64].depends == ("python >=3.0",)


class TestWheelDependenciesPlatformSpecific:
    def test_platform_specific_wheel_resolves_its_one_subdir(self) -> None:
        config = _config(
            interpreter="cp313", abi="cp313", platform="manylinux_2_17_x86_64", arch=Arch.X86_64
        )
        metadata = _metadata(requires_dist=("requests>=2.0.0",))

        result = wheel_dependencies(config, metadata, (aggregator_mapper,))

        assert result == {
            CondaSubdir.LINUX_64: calculate_dependencies(
                config, metadata, (aggregator_mapper,), subdir=CondaSubdir.LINUX_64
            )
        }

    def test_universal2_wheel_resolves_both_macos_subdirs(self) -> None:
        config = _config(
            interpreter="cp313", abi="cp313", platform="macosx_10_9_universal2", arch=Arch.X86_64
        )
        metadata = _metadata(requires_dist=("requests>=2.0.0",))

        result = wheel_dependencies(config, metadata, (aggregator_mapper,))

        assert set(result) == {CondaSubdir.OSX_64, CondaSubdir.OSX_ARM64}
        for subdir in result:
            assert result[subdir] == calculate_dependencies(
                config, metadata, (aggregator_mapper,), subdir=subdir
            )

    def test_allow_pre_is_passed_through(self) -> None:
        config = _config(
            interpreter="cp313", abi="cp313", platform="manylinux_2_17_x86_64", arch=Arch.X86_64
        )
        metadata = _metadata(requires_dist=("requests==1.0.0rc1",))

        result = wheel_dependencies(config, metadata, (aggregator_mapper,), allow_pre=True)

        assert result[CondaSubdir.LINUX_64].depends == (
            "requests ==1.0.0.rc1",
            "python >=3.13,<3.14.0a0",
            "python_abi 3.13.* *_cp313",
        )
