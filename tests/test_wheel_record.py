"""Unit tests for `reroll.wheel_record`."""

from __future__ import annotations

import re

import pytest

from reroll.dependencies import WheelDependencies
from reroll.errors import (
    MetadataFilenameMismatchError,
    UnconvertableRequirementError,
    UnsupportedPrereleaseError,
)
from reroll.name_mapping import NameMappers, aggregator_mapper, static_mapper
from reroll.subdir import CondaSubdir
from reroll.wheel_metadata import WheelMetadata
from reroll.wheel_record import WheelRecord, get_wheel_records

_MAPPERS: NameMappers = (aggregator_mapper,)


def _metadata(
    *,
    name: str = "tinylib",
    version: str = "1.2.3",
    requires_python: str | None = None,
    requires_dist: tuple[str, ...] = (),
    license_expression: str | None = None,
) -> WheelMetadata:
    return WheelMetadata(
        name=name,
        version=version,
        requires_python=requires_python,
        requires_dist=requires_dist,
        license_expression=license_expression,
    )


# --------------------------------------------------------------------------
# `WheelRecord`: the model itself
# --------------------------------------------------------------------------


def _record(
    *,
    depends: tuple[str, ...] = (),
    extra_depends: dict[str, tuple[str, ...]] | None = None,
    sha256: str | None = None,
    size: int | None = None,
    url: str | None = None,
) -> WheelRecord:
    return WheelRecord(
        name="tinylib",
        version="1.2.3",
        build="py3_none_any_0",
        build_number=0,
        subdir="noarch",
        fn="tinylib-1.2.3-py3-none-any.whl",
        depends=depends,
        extra_depends={} if extra_depends is None else extra_depends,
        sha256=sha256,
        size=size,
        url=url,
    )


class TestWheelRecordModel:
    def test_sha256_size_url_default_to_none(self) -> None:
        record = _record()

        assert record.sha256 is None
        assert record.size is None
        assert record.url is None

    def test_sha256_size_url_accept_explicit_values(self) -> None:
        record = _record(sha256="abc123", size=42, url="https://example.org/tinylib.whl")

        assert record.sha256 == "abc123"
        assert record.size == 42
        assert record.url == "https://example.org/tinylib.whl"

    def test_noarch_and_license_default_to_none(self) -> None:
        record = _record()

        assert record.noarch is None
        assert record.license is None

    def test_is_a_wheel_dependencies(self) -> None:
        assert isinstance(_record(), WheelDependencies)

    def test_rejects_an_invalid_matchspec_in_depends(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            _record(depends=("python >=1.0,<",))

    def test_rejects_an_invalid_extra_name_key(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            _record(extra_depends={"Not Valid": ()})


# --------------------------------------------------------------------------
# `get_wheel_records`: a pure-python (noarch) wheel
# --------------------------------------------------------------------------


class TestNoarchRecord:
    def test_produces_a_single_noarch_record(self) -> None:
        metadata = _metadata(requires_dist=("requests>=2.20", "click>=8.0"))

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert record.name == "tinylib"
        assert record.version == "1.2.3"
        assert record.build == "py3_none_any_0"
        assert record.build_number == 0
        assert record.subdir == "noarch"
        assert record.noarch == "python"
        assert record.fn == "tinylib-1.2.3-py3-none-any.whl"
        assert record.depends == ("requests >=2.20", "click >=8.0", "python >=3.0")
        assert record.extra_depends == {}

    def test_license_is_derived_from_metadata(self) -> None:
        metadata = _metadata(license_expression="MIT")

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert record.license == "MIT"

    def test_no_license_information_yields_none(self) -> None:
        metadata = _metadata()

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert record.license is None

    def test_sha256_size_url_default_to_none(self) -> None:
        metadata = _metadata()

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert record.sha256 is None
        assert record.size is None
        assert record.url is None

    def test_sha256_size_url_are_passed_through_when_given(self) -> None:
        metadata = _metadata()

        (record,) = get_wheel_records(
            metadata,
            "tinylib-1.2.3-py3-none-any.whl",
            mappers=_MAPPERS,
            sha256="abc123",
            size=42,
            url="https://example.org/tinylib-1.2.3-py3-none-any.whl",
        )

        assert record.sha256 == "abc123"
        assert record.size == 42
        assert record.url == "https://example.org/tinylib-1.2.3-py3-none-any.whl"

    def test_version_comes_from_metadata_not_filename(self) -> None:
        """Per docs/wheel_record.md, `version` is sourced from the
        METADATA `Version` header, not the filename's own version string
        verbatim -- using a value that's PEP 440-equal to the filename's
        version but spelled differently (an explicit trailing-zero release
        segment) pins this down without tripping the filename/METADATA
        agreement check (docs/wheel_metadata.md), which the two *are*
        allowed to disagree with in spelling as long as they're equal.
        """
        metadata = _metadata(version="1.2.3.0")

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert record.version == "1.2.3.0"

    def test_version_is_cep33_formatted(self) -> None:
        metadata = _metadata(version="1.2.3rc1")

        (record,) = get_wheel_records(
            metadata, "tinylib-1.2.3rc1-py3-none-any.whl", mappers=_MAPPERS, allow_pre=True
        )

        assert record.version == "1.2.3.rc1"

    def test_name_is_conda_mapped(self) -> None:
        metadata = _metadata()
        mappers: NameMappers = (static_mapper({"tinylib": "tiny-lib"}),)

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=mappers)

        assert record.name == "tiny-lib"


# --------------------------------------------------------------------------
# `get_wheel_records`: METADATA must agree with the filename
# --------------------------------------------------------------------------


class TestMetadataFilenameAgreement:
    """docs/wheel_metadata.md: "The Name and version must match the
    filename". `get_wheel_records` must reject a wheel where the METADATA
    `Name`/`Version` disagree with the filename's, rather than silently
    trusting METADATA (which is what every other field does).
    """

    def test_name_mismatch_raises(self) -> None:
        metadata = _metadata(name="otherlib")

        with pytest.raises(MetadataFilenameMismatchError):
            get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

    def test_version_mismatch_raises(self) -> None:
        metadata = _metadata(version="9.9.9")

        with pytest.raises(MetadataFilenameMismatchError):
            get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

    def test_name_mismatch_is_checked_after_pep_503_normalization(self) -> None:
        """The filename's name segment and METADATA's `Name` header are
        each normalized independently (PEP 503) before comparison, so
        differing-but-equivalent spellings (case, `-`/`_`/`.` runs) must
        not raise.
        """
        metadata = _metadata(name="Tiny_Lib")

        (record,) = get_wheel_records(metadata, "tiny_lib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert record.name == "tiny-lib"

    def test_version_mismatch_is_checked_by_pep_440_equality_not_string_equality(self) -> None:
        """A version spelled differently but PEP 440-equal to the
        filename's (a redundant leading zero) must not raise -- see
        `test_version_comes_from_metadata_not_filename` for the
        trailing-zero-release-segment case.
        """
        metadata = _metadata(version="01.2.3")

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert record.version == "1.2.3"

    def test_mismatch_is_checked_before_dependency_conversion(self) -> None:
        """An unrelated `Requires-Dist` conversion failure must not mask a
        name/version mismatch -- the filename/METADATA agreement check
        runs first. The direct-URL entry is valid PEP 508 (so it survives
        `WheelMetadata` construction), but `calculate_dependencies` would
        reject it with `UnconvertableRequirementError` if this record's
        dependencies were ever converted.
        """
        metadata = _metadata(
            name="otherlib", requires_dist=("requests @ https://example.com/requests.whl",)
        )

        with pytest.raises(MetadataFilenameMismatchError):
            get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)


# --------------------------------------------------------------------------
# `get_wheel_records`: an arch-specific wheel
# --------------------------------------------------------------------------


class TestArchSpecificRecord:
    def test_sets_the_matching_subdir_and_omits_noarch(self) -> None:
        metadata = _metadata()

        (record,) = get_wheel_records(
            metadata,
            "tinylib-1.2.3-cp313-cp313-manylinux_2_17_x86_64.whl",
            mappers=_MAPPERS,
        )

        assert record.subdir == "linux-64"
        assert record.noarch is None
        assert record.build == "cp313_cp313_manylinux_2_17_x86_64_0"
        assert record.depends == (
            "python >=3.13,<3.14a0",
            "python_abi 3.13.* *_cp313",
            "__glibc >=2.17",
        )


# --------------------------------------------------------------------------
# `get_wheel_records`: a macOS `universal2` wheel -- one platform tag, two
# `WheelConfig`s (one per `Arch`), deduplicated to one record per subdir
# --------------------------------------------------------------------------


class TestUniversal2Record:
    def test_produces_exactly_two_records_one_per_macos_subdir(self) -> None:
        metadata = _metadata()

        records = get_wheel_records(
            metadata,
            "tinylib-1.2.3-cp313-cp313-macosx_10_9_universal2.whl",
            mappers=_MAPPERS,
        )

        assert len(records) == 2
        assert {record.subdir for record in records} == {"osx-64", "osx-arm64"}
        assert all(record.noarch is None for record in records)
        assert all(record.build == "cp313_cp313_macosx_10_9_universal2_0" for record in records)


# --------------------------------------------------------------------------
# `get_wheel_records`: a noarch wheel with an arch-specific dependency
# marker -- one record per `CondaSubdir` instead of a single noarch one
# --------------------------------------------------------------------------


class TestArchSplitRecords:
    def test_arch_specific_marker_emits_one_record_per_subdir(self) -> None:
        metadata = _metadata(requires_dist=('requests>=2.0.0; platform_machine == "x86_64"',))

        records = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert len(records) == len(CondaSubdir)
        assert {record.subdir for record in records} == {subdir.value for subdir in CondaSubdir}
        assert all(record.noarch is None for record in records)


# --------------------------------------------------------------------------
# `get_wheel_records`: compressed python tags expand into multiple,
# distinct `WheelConfig`s -- not deduplicated, since they're not the same
# `(interpreter, abi, platform)`
# --------------------------------------------------------------------------


class TestCompressedTags:
    def test_distinct_interpreter_tags_each_produce_a_record(self) -> None:
        metadata = _metadata()

        records = get_wheel_records(
            metadata, "tinylib-1.2.3-py38.py39-none-any.whl", mappers=_MAPPERS
        )

        assert {record.build for record in records} == {
            "py38_none_any_0",
            "py39_none_any_0",
        }


# --------------------------------------------------------------------------
# `get_wheel_records`: argument threading through to `parse_filename`
# --------------------------------------------------------------------------


class TestArgumentThreading:
    def test_allow_pre_defaults_to_rejecting_a_prerelease_filename_version(self) -> None:
        metadata = _metadata()

        with pytest.raises(UnsupportedPrereleaseError):
            get_wheel_records(metadata, "tinylib-1.2.3rc1-py3-none-any.whl", mappers=_MAPPERS)

    def test_allow_pre_true_permits_a_prerelease_filename_version(self) -> None:
        metadata = _metadata(version="1.2.3rc1")

        (record,) = get_wheel_records(
            metadata, "tinylib-1.2.3rc1-py3-none-any.whl", mappers=_MAPPERS, allow_pre=True
        )

        assert record.version == "1.2.3.rc1"

    def test_abi3_upper_bound_caps_the_abi3_explosion(self) -> None:
        metadata = _metadata()

        records = get_wheel_records(
            metadata,
            "tinylib-1.2.3-cp39-abi3-manylinux_2_17_x86_64.whl",
            mappers=_MAPPERS,
            abi3_upper_bound="3.10",
        )

        assert {record.build for record in records} == {
            "cp39_cp39_manylinux_2_17_x86_64_0",
            "cp310_cp310_manylinux_2_17_x86_64_0",
        }


# --------------------------------------------------------------------------
# `get_wheel_records`: `build`/`build_number` formatting
# (docs/wheel_record.md's `build`/`build_number` sections)
# --------------------------------------------------------------------------

_BUILD_STRING_RE = re.compile(r"^([a-z0-9_.]+_)?[0-9]+$")


class TestBuildString:
    @pytest.mark.parametrize(
        "filename",
        [
            "tinylib-1.2.3-py3-none-any.whl",
            "tinylib-1.2.3-cp313-cp313-manylinux_2_17_x86_64.whl",
            "tinylib-1.2.3-cp313-cp313-macosx_10_9_universal2.whl",
        ],
    )
    def test_build_matches_the_repodata_schema_pattern(self, filename: str) -> None:
        metadata = _metadata()

        records = get_wheel_records(metadata, filename, mappers=_MAPPERS)

        assert all(_BUILD_STRING_RE.match(record.build) for record in records)

    def test_wheel_build_tag_does_not_leak_into_build_number_or_build_string(self) -> None:
        """A wheel filename MAY carry its own PEP 427 build tag (e.g. the
        `1mybuild` segment here). Per docs/wheel_record.md's `build_number`
        section, reroll does not yet drive `build_number` from it -- it
        stays `0`, and the tag does not appear in the `build` string
        either.
        """
        metadata = _metadata()

        (record,) = get_wheel_records(
            metadata, "tinylib-1.2.3-1mybuild-py3-none-any.whl", mappers=_MAPPERS
        )

        assert record.build_number == 0
        assert record.build == "py3_none_any_0"
        assert "mybuild" not in record.build
