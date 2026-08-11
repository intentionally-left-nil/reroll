"""Unit tests for `reroll.dependencies`."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest
from packaging.utils import NormalizedName

from reroll.dependencies import WheelDependencies, wheel_dependencies
from reroll.dependencies.convert_dependency import UNSUPPORTED, convert_dependency
from reroll.dependencies.extras import extra_marker_entry
from reroll.dependencies.python import python_dependencies
from reroll.dependencies.requires_dist import strip_interpreter_requirements
from reroll.filename import WheelConfig
from reroll.name_mapping import Candidate, NameMappers, aggregator_mapper, static_mapper
from reroll.wheel_metadata import WheelMetadata


def _config(*, interpreter: str = "py3", abi: str = "none") -> WheelConfig:
    """A valid `WheelConfig` (py3-none-any) with the interpreter/abi tag
    overridden for tests that only care about the python/python_abi axis.
    """
    return WheelConfig(
        normalized_pypi_name="tinylib",
        conda_name="tinylib",
        version="1.2.3",
        build=(),
        interpreter=interpreter,
        abi=abi,
        platform="any",
        arch=None,
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


def _dependencies(
    config: WheelConfig,
    metadata: WheelMetadata,
    mappers: NameMappers,
    *,
    allow_pre: bool = False,
) -> WheelDependencies:
    """`wheel_dependencies`'s result, asserted non-`None` -- for tests that
    only exercise the solvable case and want to access `.depends`/
    `.extra_depends` without a separate `is not None` narrowing assertion
    of their own.
    """
    result = wheel_dependencies(config, metadata, mappers, allow_pre=allow_pre)
    assert result is not None
    return result


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

        with caplog.at_level(logging.WARNING, logger="reroll.dependencies"):
            result = python_dependencies(config, _metadata(requires_python=">=3.10"))

        assert result is None
        assert "tinylib" in caplog.text
        assert ">=3.10" in caplog.text

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
# `convert_dependency`: `name`, or `name<op>version[,<op>version...]`
# --------------------------------------------------------------------------


def _unresolved_mapper(
    name: NormalizedName, candidates: Sequence[Candidate]
) -> str | Sequence[Candidate]:
    """A `NameMapper` that never resolves anything, so `map_name` always
    ends the chain with `UnresolvedCandidates`.
    """
    del name
    return candidates


class TestConvertDependencyName:
    def test_bare_name_maps_through_the_chain(self) -> None:
        mappers = (static_mapper({"requests": "python-requests"}), aggregator_mapper)

        assert convert_dependency("requests", mappers) == "python-requests"

    def test_bare_name_with_no_mapper_opinion_falls_back_to_normalized_name(self) -> None:
        assert convert_dependency("Requests", (aggregator_mapper,)) == "requests"

    def test_versioned_dependency_maps_the_name_too(self) -> None:
        mappers = (static_mapper({"requests": "python-requests"}), aggregator_mapper)

        assert convert_dependency("requests>=2.0.0", mappers) == "python-requests >=2.0.0"

    def test_unresolved_name_returns_none(self) -> None:
        assert convert_dependency("requests", (_unresolved_mapper,)) is None

    def test_unresolved_name_logs_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="reroll.dependencies"):
            result = convert_dependency("requests", (_unresolved_mapper,))

        assert result is None
        assert "requests" in caplog.text


class TestConvertDependencyOperators:
    @pytest.mark.parametrize("operator", [">=", "<=", ">", "<", "!=", "==", "~="])
    def test_operator_is_passed_through_as_is(self, operator: str) -> None:
        assert (
            convert_dependency(f"requests{operator}2.0.0", (aggregator_mapper,))
            == f"requests {operator}2.0.0"
        )

    def test_arbitrary_equality_is_converted_to_double_equals(self) -> None:
        assert convert_dependency("requests===2.0.0", (aggregator_mapper,)) == "requests ==2.0.0"

    def test_arbitrary_equality_against_a_non_pep440_string_passes_through(self) -> None:
        """`===` is an arbitrary *string* equality match (PEP 440), so its
        right-hand side need not parse as a PEP 440 version at all; such a
        value can't be checked for a local segment or a pre-release, so it
        is passed through unchanged apart from the `===` -> `==` rewrite.
        """
        assert (
            convert_dependency("requests===some-weird-string", (aggregator_mapper,))
            == "requests ==some-weird-string"
        )

    def test_multiple_specifiers_are_joined_in_canonical_order(self) -> None:
        assert (
            convert_dependency("requests<=2.0.0,!=1.0.1,>=0.9", (aggregator_mapper,))
            == "requests !=1.0.1,<=2.0.0,>=0.9"
        )


class TestConvertDependencyVersion:
    def test_epoch_is_preserved(self) -> None:
        assert convert_dependency("requests>=1!1.0.0", (aggregator_mapper,)) == "requests >=1!1.0.0"

    def test_post_release_is_accepted(self) -> None:
        assert (
            convert_dependency("requests>=1.0.0.post1", (aggregator_mapper,))
            == "requests >=1.0.0.post1"
        )

    @pytest.mark.parametrize(
        "entry",
        [
            "requests==1.0.0+local",
            "requests!=1.0.0+local",
            "requests===1.0.0+local",
        ],
    )
    def test_rejects_a_local_version_label(self, entry: str) -> None:
        assert convert_dependency(entry, (aggregator_mapper,)) is None

    def test_local_version_label_logs_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="reroll.dependencies"):
            result = convert_dependency("requests==1.0.0+local", (aggregator_mapper,))

        assert result is None
        assert "requests==1.0.0+local" in caplog.text

    def test_rejects_a_direct_url_reference(self) -> None:
        entry = "requests @ https://example.com/requests-1.0.0.whl"

        assert convert_dependency(entry, (aggregator_mapper,)) is None

    def test_direct_url_reference_logs_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        entry = "requests @ https://example.com/requests-1.0.0.whl"

        with caplog.at_level(logging.WARNING, logger="reroll.dependencies"):
            result = convert_dependency(entry, (aggregator_mapper,))

        assert result is None
        assert "requests" in caplog.text

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
        assert convert_dependency(entry, (aggregator_mapper,)) is None

    def test_pre_release_version_logs_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="reroll.dependencies"):
            result = convert_dependency("requests==1.0.0rc1", (aggregator_mapper,))

        assert result is None
        assert "requests==1.0.0rc1" in caplog.text

    def test_allow_pre_permits_a_pre_release_version(self) -> None:
        assert (
            convert_dependency("requests==1.0.0rc1", (aggregator_mapper,), allow_pre=True)
            == "requests ==1.0.0rc1"
        )

    def test_allow_pre_still_rejects_a_local_version_label(self) -> None:
        assert (
            convert_dependency("requests==1.0.0+local", (aggregator_mapper,), allow_pre=True)
            is None
        )


class TestConvertDependencyClassification:
    """An entry with extras or a marker is classified by `convert_dependency`
    itself, distinctly from `None` (unrepresentable, reject the whole
    record): extras/marker conversion is a future addition, not yet
    implemented, so such an entry is left out of `depends` without
    affecting the rest of the record.
    """

    def test_extras_return_the_unsupported_sentinel(self) -> None:
        assert convert_dependency("requests[security]>=2.0.0", (aggregator_mapper,)) is UNSUPPORTED

    def test_marker_returns_the_unsupported_sentinel(self) -> None:
        entry = 'requests>=2.0.0; sys_platform == "win32"'

        assert convert_dependency(entry, (aggregator_mapper,)) is UNSUPPORTED

    def test_extras_and_a_marker_together_return_the_unsupported_sentinel(self) -> None:
        entry = 'requests[security]>=2.0.0; sys_platform == "win32"'

        assert convert_dependency(entry, (aggregator_mapper,)) is UNSUPPORTED

    def test_unsupported_entry_is_not_confused_with_an_unresolved_name(self) -> None:
        """The sentinel is distinct from `None` even when the name could
        never resolve anyway -- classification happens before name mapping.
        """
        assert convert_dependency("requests[security]", (_unresolved_mapper,)) is UNSUPPORTED

    def test_unsupported_entry_logs_at_debug_not_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="reroll.dependencies"):
            result = convert_dependency("requests[security]>=2.0.0", (aggregator_mapper,))

        assert result is UNSUPPORTED
        assert "requests[security]>=2.0.0" in caplog.text
        assert not any(record.levelno >= logging.WARNING for record in caplog.records)


# --------------------------------------------------------------------------
# `extra_marker_entry`: recognizing a bare per-extra marker
# --------------------------------------------------------------------------


class TestExtraMarkerEntry:
    def test_marker_free_entry_is_unchanged(self) -> None:
        assert extra_marker_entry("requests>=2.0.0") == (None, "requests>=2.0.0")

    def test_unrelated_marker_is_unchanged(self) -> None:
        entry = 'requests>=2.0.0; sys_platform == "win32"'

        assert extra_marker_entry(entry) == (None, entry)

    def test_simple_extra_marker_extracts_name_and_strips_marker(self) -> None:
        entry = 'httpx>=0.23.0; extra == "standard"'

        assert extra_marker_entry(entry) == ("standard", "httpx>=0.23.0")

    def test_reversed_operand_order_is_recognized(self) -> None:
        entry = 'httpx>=0.23.0; "standard" == extra'

        assert extra_marker_entry(entry) == ("standard", "httpx>=0.23.0")

    def test_bare_name_with_extra_marker_strips_to_bare_name(self) -> None:
        entry = 'jinja2; extra == "standard"'

        assert extra_marker_entry(entry) == ("standard", "jinja2")

    def test_extra_name_is_normalized(self) -> None:
        entry = 'httpx>=0.23.0; extra == "Some_Extra.Name"'

        assert extra_marker_entry(entry) == ("some-extra-name", "httpx>=0.23.0")

    def test_extra_inequality_is_not_a_bare_extra_marker(self) -> None:
        entry = 'httpx>=0.23.0; extra != "standard"'

        assert extra_marker_entry(entry) == (None, entry)

    def test_conditional_extra_marker_is_not_a_bare_extra_marker(self) -> None:
        """Per docs/wheel_to_conda_dependencies.md, a marker combining more
        than one `extra ==` clause is out of scope for this diff.
        """
        entry = 'requests>=2.0.0; extra == "foo" or extra == "bar"'

        assert extra_marker_entry(entry) == (None, entry)

    def test_extra_marker_anded_with_another_condition_is_not_a_bare_extra_marker(self) -> None:
        entry = 'requests>=2.0.0; extra == "foo" and python_version >= "3.8"'

        assert extra_marker_entry(entry) == (None, entry)

    def test_entry_with_its_own_extras_is_not_a_bare_extra_marker(self) -> None:
        """A `Requires-Dist` entry can carry both a per-extra marker *and*
        its own extras selector, e.g. FastAPI's
        `fastapi-cli[standard] (>=0.0.5) ; extra == "standard"` -- since
        extras-on-a-dependency conversion isn't implemented yet, this stays
        unrecognized here too, same as `convert_dependency` classifies it.
        """
        entry = 'fastapi-cli[standard]>=0.0.5; extra == "standard"'

        assert extra_marker_entry(entry) == (None, entry)


# --------------------------------------------------------------------------
# `wheel_dependencies`: package entrypoint
# --------------------------------------------------------------------------


class TestWheelDependencies:
    def test_delegates_to_python_dependencies_when_requires_dist_is_empty(self) -> None:
        config = _config(interpreter="cp313", abi="cp313")
        metadata = _metadata()

        result = _dependencies(config, metadata, (aggregator_mapper,))

        assert result.depends == python_dependencies(config, metadata)
        assert result.extra_depends == {}

    def test_delegates_unsolvable_result(self) -> None:
        config = _config(interpreter="cp39", abi="cp39")
        metadata = _metadata(requires_python=">=3.10")

        assert wheel_dependencies(config, metadata, (aggregator_mapper,)) is None

    def test_simple_requires_dist_entries_come_before_python(self) -> None:
        """Matches the field order real conda-pypi output uses (§3.5 of
        `specs/wheel_dependency_conversion.md`): dependency entries first,
        `python` last.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=2.0.0",))

        assert _dependencies(config, metadata, (aggregator_mapper,)).depends == (
            "requests >=2.0.0",
            "python >=3.0",
        )

    def test_multiple_requires_dist_entries_keep_their_order(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=2.0.0", "click==8.*"))

        assert _dependencies(config, metadata, (aggregator_mapper,)).depends == (
            "requests >=2.0.0",
            "click ==8.*",
            "python >=3.0",
        )

    def test_strips_bare_interpreter_requirements_before_conversion(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("python>=3.9", "requests>=2.0.0"))

        assert _dependencies(config, metadata, (aggregator_mapper,)).depends == (
            "requests >=2.0.0",
            "python >=3.0",
        )

    def test_skips_an_entry_with_extras(self) -> None:
        """Extras-on-a-dependency conversion is not yet implemented (a
        future followup); such an entry is left out of `depends` for now
        rather than rejecting the whole record.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests[security]>=2.0.0",))

        assert _dependencies(config, metadata, (aggregator_mapper,)).depends == ("python >=3.0",)

    def test_skips_an_entry_with_an_unrelated_marker(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; sys_platform == "win32"',))

        assert _dependencies(config, metadata, (aggregator_mapper,)).depends == ("python >=3.0",)

    def test_skips_an_entry_with_a_conditional_extra_marker(self) -> None:
        """Per docs/wheel_to_conda_dependencies.md, a marker combining more
        than one `extra ==` clause is out of scope for this diff -- such an
        entry is left out of both `depends` and `extra_depends`.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; extra == "foo" or extra == "bar"',))

        result = _dependencies(config, metadata, (aggregator_mapper,))

        assert result.depends == ("python >=3.0",)
        assert result.extra_depends == {}

    def test_rejects_the_whole_record_for_an_unrepresentable_entry(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests==1.0.0+local",))

        assert wheel_dependencies(config, metadata, (aggregator_mapper,)) is None

    def test_uses_mappers_to_convert_dependency_names(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=2.0.0",))
        mappers = (static_mapper({"requests": "python-requests"}), aggregator_mapper)

        assert _dependencies(config, metadata, mappers).depends == (
            "python-requests >=2.0.0",
            "python >=3.0",
        )

    def test_allow_pre_is_passed_through_to_requires_dist_conversion(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests==1.0.0rc1",))

        result = _dependencies(config, metadata, (aggregator_mapper,), allow_pre=True)

        assert result.depends == (
            "requests ==1.0.0rc1",
            "python >=3.0",
        )


# --------------------------------------------------------------------------
# `wheel_dependencies`: grouping per-extra `Requires-Dist` entries
# --------------------------------------------------------------------------


class TestWheelDependenciesExtras:
    def test_extra_only_dependency_is_grouped_by_extra_name(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('httpx>=0.23.0; extra == "standard"',))

        result = _dependencies(config, metadata, (aggregator_mapper,))

        assert result.depends == ("python >=3.0",)
        assert result.extra_depends == {"standard": ("httpx >=0.23.0",)}

    def test_multiple_entries_for_the_same_extra_are_grouped_together(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(
            requires_dist=(
                'httpx>=0.23.0; extra == "standard"',
                'jinja2>=2.11.2; extra == "standard"',
            )
        )

        result = _dependencies(config, metadata, (aggregator_mapper,))

        assert result.extra_depends == {
            "standard": ("httpx >=0.23.0", "jinja2 >=2.11.2"),
        }

    def test_multiple_extras_produce_separate_keys(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(
            requires_dist=(
                'httpx>=0.23.0; extra == "standard"',
                'orjson>=3.2.1; extra == "all"',
            )
        )

        result = _dependencies(config, metadata, (aggregator_mapper,))

        assert result.extra_depends == {
            "standard": ("httpx >=0.23.0",),
            "all": ("orjson >=3.2.1",),
        }

    def test_extra_name_is_normalized(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('httpx>=0.23.0; extra == "Some_Extra.Name"',))

        result = _dependencies(config, metadata, (aggregator_mapper,))

        assert result.extra_depends == {"some-extra-name": ("httpx >=0.23.0",)}

    def test_extra_dependencies_are_not_duplicated_into_the_base_depends(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(
            requires_dist=("requests>=2.0.0", 'httpx>=0.23.0; extra == "standard"')
        )

        result = _dependencies(config, metadata, (aggregator_mapper,))

        assert result.depends == ("requests >=2.0.0", "python >=3.0")
        assert result.extra_depends == {"standard": ("httpx >=0.23.0",)}

    def test_no_extra_entries_yields_an_empty_extra_depends(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=2.0.0",))

        assert _dependencies(config, metadata, (aggregator_mapper,)).extra_depends == {}

    def test_extra_dependency_uses_mappers_to_convert_its_name(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; extra == "standard"',))
        mappers = (static_mapper({"requests": "python-requests"}), aggregator_mapper)

        result = _dependencies(config, metadata, mappers)

        assert result.extra_depends == {"standard": ("python-requests >=2.0.0",)}

    def test_unrepresentable_extra_dependency_rejects_the_whole_record(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests==1.0.0+local; extra == "standard"',))

        assert wheel_dependencies(config, metadata, (aggregator_mapper,)) is None
