"""Unit tests for `reroll.dependencies.calculate_dependencies`."""

from __future__ import annotations

import pytest

from reroll.dependencies import WheelDependencies
from reroll.dependencies.calculate_dependencies import calculate_dependencies
from reroll.errors import (
    NeedsArchSplitError,
    PythonRangeMismatchError,
    UnconvertableMarkerError,
    UnconvertableRequirementError,
)
from reroll.filename import WheelConfig
from reroll.name_mapping import (
    CandidateSource,
    NameMappers,
    NameResolution,
    Winner,
    aggregator_mapper,
    passthrough_mapper,
    static_mapper,
)
from reroll.subdir import CondaSubdir
from reroll.wheel_metadata import WheelMetadata

_NAME_RESOLUTION = NameResolution(
    pypi_name="tinylib",
    winner=Winner(
        conda_name="tinylib",
        probability=0.0,
        source=CandidateSource.PASSTHROUGH,
        mapper="passthrough_mapper",
    ),
)


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
        name_resolution=_NAME_RESOLUTION,
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
    subdir: CondaSubdir | None = None,
    allow_pre: bool = False,
) -> WheelDependencies:
    return calculate_dependencies(config, metadata, mappers, subdir=subdir, allow_pre=allow_pre)


# --------------------------------------------------------------------------
# No `Requires-Dist` at all -- delegates straight to `python_dependencies`
# --------------------------------------------------------------------------


class TestNoRequiresDist:
    def test_empty_requires_dist_yields_only_python(self) -> None:
        config = _config(interpreter="cp313", abi="cp313")
        metadata = _metadata()

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("python >=3.13,<3.14a0", "python_abi 3.13.* *_cp313")
        assert result.extra_depends == {}

    def test_unsolvable_python_range_raises(self) -> None:
        config = _config(interpreter="cp39", abi="cp39")
        metadata = _metadata(requires_python=">=3.10")

        with pytest.raises(PythonRangeMismatchError):
            _dependencies(config, metadata, (passthrough_mapper,))


# --------------------------------------------------------------------------
# Marker-free entries: unconditional, so field order/name mapping/allow_pre
# behave exactly as the plain simple-dependency conversion path.
# --------------------------------------------------------------------------


class TestUnconditionalEntries:
    def test_simple_entries_come_before_python(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=2.0.0",))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("requests >=2.0.0", "python >=3.0")

    def test_multiple_entries_keep_their_order(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=2.0.0", "click==8.*"))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("requests >=2.0.0", "click =8", "python >=3.0")

    def test_strips_bare_interpreter_requirements_before_conversion(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("python>=3.9", "requests>=2.0.0"))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("requests >=2.0.0", "python >=3.0")

    def test_entry_with_its_own_extras(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests[security]>=2.0.0",))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("requests >=2.0.0[extras=[security]]", "python >=3.0")

    def test_uses_mappers_to_convert_dependency_names(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=2.0.0",))
        mappers = (static_mapper({"requests": "python-requests"}), aggregator_mapper)

        result = _dependencies(config, metadata, mappers)

        assert result.depends == ("python-requests >=2.0.0", "python >=3.0")

    def test_allow_pre_is_passed_through(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests==1.0.0rc1",))

        result = _dependencies(config, metadata, (passthrough_mapper,), allow_pre=True)

        assert result.depends == ("requests ==1.0.0.rc1", "python >=3.0")

    def test_rejects_the_whole_record_for_an_unrepresentable_entry(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests==1.0.0+local",))

        with pytest.raises(UnconvertableRequirementError, match="local version label"):
            _dependencies(config, metadata, (passthrough_mapper,))

    def test_rejects_the_whole_record_for_a_direct_url_entry(self) -> None:
        """docs/wheel_to_conda_dependencies.md's "Simple dependency
        conversion" section: a direct URL reference has no matchspec
        equivalent, same as a local version label above. Expected to raise
        `UnconvertableRequirementError`, same as `pep508_to_matchspec`
        does when called directly with a URL entry
        (`test_pep508_to_matchspec.py`).

        Currently fails: `_bare_entry` rebuilds the entry from
        `requirement.name`/`.extras`/`.specifier` only, silently dropping
        `.url` before the reconstructed string ever reaches
        `pep508_to_matchspec`'s own URL check -- so a direct-URL entry is
        converted as if it were a bare name dependency instead of being
        rejected.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests @ https://example.com/requests.whl",))

        with pytest.raises(UnconvertableRequirementError, match="direct URL"):
            _dependencies(config, metadata, (passthrough_mapper,))

    def test_rejects_the_whole_record_for_a_prerelease_entry_without_allow_pre(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests==1.0.0rc1",))

        with pytest.raises(UnconvertableRequirementError, match="pre-release"):
            _dependencies(config, metadata, (passthrough_mapper,))


# --------------------------------------------------------------------------
# A leftover environment marker (not `extra`) converts to a `when=` clause.
# --------------------------------------------------------------------------


class TestResidualMarker:
    def test_unresolved_marker_becomes_a_when_clause(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; python_version >= "3.9"',))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == (
            'requests >=2.0.0[when="python>=3.9.0a0"]',
            "python >=3.0",
        )

    def test_marker_matching_the_pinned_minor_is_unconditional(self) -> None:
        """`python_version` fully resolves once the wheel's python range
        collapses to a single minor (`conditional_dependency`'s exact-minor
        reduction), leaving no `when=` clause at all.
        """
        config = _config(interpreter="cp313", abi="cp313")
        metadata = _metadata(requires_dist=('requests>=2.0.0; python_version == "3.13"',))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == (
            "requests >=2.0.0",
            "python >=3.13,<3.14a0",
            "python_abi 3.13.* *_cp313",
        )

    def test_marker_not_matching_the_pinned_minor_is_dropped(self) -> None:
        config = _config(interpreter="cp313", abi="cp313")
        metadata = _metadata(requires_dist=('requests>=2.0.0; python_version == "3.9"',))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == (
            "python >=3.13,<3.14a0",
            "python_abi 3.13.* *_cp313",
        )

    def test_non_simplified_requires_python_leaves_python_version_markers_unresolved(
        self,
    ) -> None:
        """`<=3.11` isn't a Simplified Requires-Python, so the combined
        Python version range is a plain PEP 440 specifier string, not a
        `HalfOpenRange` -- `exact_minor` has no structured range to check
        for a single-minor restriction, so it always returns `None` for
        it. `python_version`/`python_full_version`/`implementation_version`
        are therefore left out of every `Requires-Dist` entry's evaluation
        environment for this record, and a marker referencing any of them
        is handed straight through as a `when=` clause -- conservatively
        correct (never silently resolved to the wrong answer), just less
        tight than a Simplified range would allow.
        """
        config = _config(interpreter="py38", abi="none")
        metadata = _metadata(
            requires_python="<=3.11",
            requires_dist=('requests>=2.0.0; python_version < "3.10"',),
        )

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == (
            'requests >=2.0.0[when="python<3.10.0a0"]',
            "python >=3.8,<=3.11",
        )


# --------------------------------------------------------------------------
# Extras: `find_extras` discovers candidate extras, `conditional_dependency`
# decides per-extra applicability.
# --------------------------------------------------------------------------


class TestExtraOnlyDependency:
    def test_extra_only_dependency_is_grouped_by_extra_name(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('httpx>=0.23.0; extra == "standard"',))

        result = _dependencies(config, metadata, (passthrough_mapper,))

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

        result = _dependencies(config, metadata, (passthrough_mapper,))

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

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.extra_depends == {
            "standard": ("httpx >=0.23.0",),
            "all": ("orjson >=3.2.1",),
        }

    def test_extra_name_is_normalized(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('httpx>=0.23.0; extra == "Some_Extra.Name"',))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.extra_depends == {"some-extra-name": ("httpx >=0.23.0",)}

    def test_extra_dependency_uses_mappers_to_convert_its_name(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; extra == "standard"',))
        mappers = (static_mapper({"requests": "python-requests"}), aggregator_mapper)

        result = _dependencies(config, metadata, mappers)

        assert result.extra_depends == {"standard": ("python-requests >=2.0.0",)}

    def test_no_extra_entries_yields_an_empty_extra_depends(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=2.0.0",))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.extra_depends == {}

    def test_unrepresentable_extra_dependency_rejects_the_whole_record(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests==1.0.0+local; extra == "standard"',))

        with pytest.raises(UnconvertableRequirementError, match="local version label"):
            _dependencies(config, metadata, (passthrough_mapper,))

    def test_entry_gains_an_extras_bracket_alongside_its_own_extra_marker(self) -> None:
        """`fastapi-cli[standard] (>=0.0.5) ; extra == "standard"` -- the
        entry's own `[standard]` extras selector is unrelated to the
        `extra == "standard"` marker that routes it into fastapi's own
        `standard` extra, and both survive independently.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('fastapi-cli[standard]>=0.0.5; extra == "standard"',))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.extra_depends == {
            "standard": ("fastapi-cli >=0.0.5[extras=[standard]]",),
        }


class TestUnconditionalDependencyIsDedupedFromExtras:
    """docs/wheel_to_conda_dependencies.md's `packageB` example: a
    marker-free entry is unconditionally true for the base environment
    *and* every extra environment -- but the exact-string-match
    post-processing step (docs "Splitting base dependencies from extras")
    removes it from each extra's list once it's already in `depends`, so
    the final result carries it only once.
    """

    def test_marker_free_entry_ends_up_only_in_base_depends(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(
            requires_dist=(
                "requests>=2.0.0",
                'httpx>=0.23.0; extra == "standard"',
            )
        )

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("requests >=2.0.0", "python >=3.0")
        assert result.extra_depends == {"standard": ("httpx >=0.23.0",)}

    def test_dedup_only_removes_exact_string_matches(self) -> None:
        """`requests>=1.2` is marker-free, so it's an exact match removed
        from `major-bump`'s list too -- but `requests>=2.0` (only true for
        `major-bump`) has a different version constraint and is not an
        exact match against anything in `depends`, so it survives.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(
            requires_dist=(
                "requests>=1.2",
                'requests>=2.0; extra == "major-bump"',
            )
        )

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("requests >=1.2", "python >=3.0")
        assert result.extra_depends == {"major-bump": ("requests >=2.0",)}

    def test_dedup_is_naive_string_matching_not_semantic_equivalence(self) -> None:
        """docs/wheel_to_conda_dependencies.md's "Splitting base
        dependencies from extras" section: `>=1.0` and `>=1.0.0` are the
        same constraint (`packaging.version.Version` treats them equal),
        but `format_version` spells them differently (`"1.0"` vs
        `"1.0.0"`), so the post-processing dedup -- exact string matching
        only -- does not recognize them as the same MatchSpec. Both
        survive; this is accepted per the doc, not a bug.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(
            requires_dist=(
                "requests>=1.0",
                'requests>=1.0.0; extra == "standard"',
            )
        )

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("requests >=1.0", "python >=3.0")
        assert result.extra_depends == {"standard": ("requests >=1.0.0",)}

    def test_dedup_does_not_apply_across_two_different_extras(self) -> None:
        """A dependency shared by two extras (but not the base), e.g.
        FastAPI's `httpx` under both `standard` and `all`, is kept in
        both -- dedup only ever removes an extra's entry against `depends`,
        never against another extra.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(
            requires_dist=(
                'httpx>=0.23.0; extra == "standard"',
                'httpx>=0.23.0; extra == "all"',
            )
        )

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("python >=3.0",)
        assert result.extra_depends == {
            "standard": ("httpx >=0.23.0",),
            "all": ("httpx >=0.23.0",),
        }


class TestMixedMarkerAndExtraClause:
    """docs/wheel_to_conda_dependencies.md's "fun scenario":
    `packageA ; python_version < 3.9 and extra != cli` -- solved once per
    environment (uv's "solve twice, union" behavior), not by partitioning
    strictly into "belongs to base XOR belongs to cli".
    """

    def test_negated_extra_clause_included_in_base_but_not_the_named_extra(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('packagea; python_version < "3.9" and extra != "cli"',))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ('packagea[when="python<3.9.0a0"]', "python >=3.0")
        assert result.extra_depends == {"cli": ()}


# --------------------------------------------------------------------------
# Arch-split and unconvertable-marker propagation: whole-record rejection,
# not swallowed or retried inside this function.
# --------------------------------------------------------------------------


class TestWholeRecordRejection:
    def test_noarch_with_a_platform_marker_raises_needs_arch_split(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; platform_machine == "x86_64"',))

        with pytest.raises(NeedsArchSplitError):
            _dependencies(config, metadata, (passthrough_mapper,), subdir=None)

    def test_arch_specific_subdir_resolves_the_same_marker(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; platform_machine == "x86_64"',))

        result = _dependencies(config, metadata, (passthrough_mapper,), subdir=CondaSubdir.LINUX_64)

        assert result.depends == ("requests >=2.0.0", "python >=3.0")

    def test_unpermitted_marker_key_raises_unconvertable_marker(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; platform_release == "5.0"',))

        with pytest.raises(UnconvertableMarkerError):
            _dependencies(config, metadata, (passthrough_mapper,))

    def test_marker_combining_extra_clauses_still_raises(self) -> None:
        """A marker mixing more than one `extra ==` clause with something
        markerpry can't reduce to a plain boolean is still whatever
        `conditional_dependency`/`pep508_to_matchspec` would raise for it --
        this is not a new carve-out.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=('requests>=2.0.0; extra == "foo" or extra == "bar"',))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("python >=3.0",)
        assert result.extra_depends == {
            "foo": ("requests >=2.0.0",),
            "bar": ("requests >=2.0.0",),
        }


# --------------------------------------------------------------------------
# "Repeated dependency names" (docs/wheel_to_conda_dependencies.md): reroll
# never merges/intersects same-named entries itself -- both survive as
# separate MatchSpecs, left for the solver to intersect.
# --------------------------------------------------------------------------


class TestRepeatedDependencyNames:
    def test_same_pypi_name_repeated_with_different_specifiers_is_not_merged(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("requests>=1.2", "requests<3.0"))

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.depends == ("requests >=1.2", "requests <3.0a0", "python >=3.0")

    def test_two_different_pypi_names_mapping_to_the_same_conda_name_are_not_merged(
        self,
    ) -> None:
        """When two distinct PyPI names both map to one conda package (a
        many-to-one mapper), reroll still emits one MatchSpec per source
        entry rather than merging them -- exactly like the same-name case
        above, just with the collision happening after name mapping
        instead of before it.
        """
        config = _config(interpreter="py3", abi="none")
        metadata = _metadata(requires_dist=("foo>=1.0", "bar>=2.0"))
        mappers = (static_mapper({"foo": "shared-conda-name", "bar": "shared-conda-name"}),)

        result = _dependencies(config, metadata, mappers)

        assert result.depends == (
            "shared-conda-name >=1.0",
            "shared-conda-name >=2.0",
            "python >=3.0",
        )


# --------------------------------------------------------------------------
# `Provides-Extra` (docs/wheel_to_conda_dependencies.md's "Dealing with
# extras"): completely ignored -- extras come solely from `Requires-Dist`
# markers, regardless of what `Provides-Extra` says.
# --------------------------------------------------------------------------


class TestProvidesExtraIgnored:
    def test_provides_extra_with_no_matching_requires_dist_marker_yields_no_extra(
        self,
    ) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = WheelMetadata(
            name="tinylib",
            version="1.2.3",
            requires_dist=("requests>=2.0.0",),
            provides_extra=("standard",),
        )

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.extra_depends == {}

    def test_requires_dist_extra_marker_is_found_even_without_provides_extra(self) -> None:
        config = _config(interpreter="py3", abi="none")
        metadata = WheelMetadata(
            name="tinylib",
            version="1.2.3",
            requires_dist=('httpx>=0.23.0; extra == "standard"',),
            provides_extra=(),
        )

        result = _dependencies(config, metadata, (passthrough_mapper,))

        assert result.extra_depends == {"standard": ("httpx >=0.23.0",)}


# --------------------------------------------------------------------------
# `WheelDependencies` itself validates `depends`/`extra_depends` -- shared
# with `WheelRecord` (reroll/__init__.py) so both get the same guarantee.
# --------------------------------------------------------------------------


class TestWheelDependenciesValidation:
    def test_rejects_an_invalid_matchspec_in_depends(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            WheelDependencies(depends=("python >=1.0,<",), extra_depends={})

    def test_rejects_an_invalid_matchspec_in_extra_depends(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            WheelDependencies(depends=(), extra_depends={"standard": ("python >=1.0,<",)})

    def test_rejects_an_invalid_extra_name_key(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            WheelDependencies(depends=(), extra_depends={"Not Valid": ()})

    def test_accepts_valid_depends_and_extra_depends(self) -> None:
        result = WheelDependencies(
            depends=("python >=3.9",),
            extra_depends={"standard": ("httpx >=0.23.0",)},
        )

        assert result.depends == ("python >=3.9",)
        assert result.extra_depends == {"standard": ("httpx >=0.23.0",)}
