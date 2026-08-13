"""Unit tests for `reroll.wheel_record`."""

from __future__ import annotations

import pytest

from reroll.dependencies import WheelDependencies
from reroll.errors import UnconvertableRequirementError, UnsupportedPrereleaseError
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
        METADATA `Version` header, not the filename's own version --
        deliberately using different values here to pin that down.
        """
        metadata = _metadata(version="9.9.9")

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert record.version == "9.9.9"

    def test_version_is_cep33_formatted(self) -> None:
        metadata = _metadata(version="1.2.3rc1")

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=_MAPPERS)

        assert record.version == "1.2.3.rc1"

    def test_name_is_conda_mapped(self) -> None:
        metadata = _metadata()
        mappers: NameMappers = (static_mapper({"tinylib": "tiny-lib"}),)

        (record,) = get_wheel_records(metadata, "tinylib-1.2.3-py3-none-any.whl", mappers=mappers)

        assert record.name == "tiny-lib"


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
        assert record.depends == ("python >=3.13,<3.14.0a0", "python_abi 3.13.* *_cp313")


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
