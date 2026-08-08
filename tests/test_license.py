"""Unit tests for `reroll.license`."""

from __future__ import annotations

from reroll.license import convert_license
from reroll.wheel_metadata import WheelMetadata


def _metadata(
    license_expression: str | None = None,
    license: str | None = None,
    license_classifiers: tuple[str, ...] = (),
) -> WheelMetadata:
    return WheelMetadata(
        name="tinylib",
        version="1.0",
        license_expression=license_expression,
        license=license,
        license_classifiers=license_classifiers,
    )


class TestLicenseExpression:
    def test_returned_verbatim_even_with_other_fields_present(self) -> None:
        metadata = _metadata(
            license_expression="MIT",
            license="Apache-2.0",
            license_classifiers=(
                "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
            ),
        )

        assert convert_license(metadata) == "MIT"


class TestLicenseClassifiers:
    def test_single_mappable_classifier(self) -> None:
        metadata = _metadata(license_classifiers=("License :: OSI Approved :: MIT License",))

        assert convert_license(metadata) == "MIT"

    def test_two_classifiers_with_distinct_spdx_ids_is_ambiguous(self) -> None:
        metadata = _metadata(
            license_classifiers=(
                "License :: OSI Approved :: MIT License",
                "License :: OSI Approved :: Apache Software License",
            )
        )

        assert convert_license(metadata) is None

    def test_two_classifiers_with_same_spdx_id_are_deduped(self) -> None:
        metadata = _metadata(
            license_classifiers=(
                "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
                "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
            )
        )

        assert convert_license(metadata) == "GPL-2.0-only"

    def test_unmapped_classifier_is_skipped_not_counted(self) -> None:
        metadata = _metadata(
            license_classifiers=(
                "License :: OSI Approved :: MIT License",
                "License :: Public Domain",
            )
        )

        assert convert_license(metadata) == "MIT"

    def test_only_unmapped_classifiers_is_none(self) -> None:
        metadata = _metadata(license_classifiers=("License :: Public Domain",))

        assert convert_license(metadata) is None


class TestLegacyLicenseText:
    def test_valid_spdx_id_is_canonicalized(self) -> None:
        metadata = _metadata(license="mit")

        assert convert_license(metadata) == "MIT"

    def test_valid_spdx_expression_is_canonicalized(self) -> None:
        metadata = _metadata(license="Apache-2.0 OR MIT")

        assert convert_license(metadata) == "Apache-2.0 OR MIT"

    def test_non_spdx_free_text_is_none(self) -> None:
        metadata = _metadata(license="MIT License")

        assert convert_license(metadata) is None

    def test_license_text_blob_is_none(self) -> None:
        metadata = _metadata(
            license=(
                "Copyright (c) Someone.\n\n"
                "Redistribution and use in source and binary forms, with or "
                "without modification, are permitted provided that the "
                "following conditions are met..."
            )
        )

        assert convert_license(metadata) is None


class TestNoLicenseInformation:
    def test_all_fields_empty_is_none(self) -> None:
        metadata = _metadata()

        assert convert_license(metadata) is None


class TestPrecedence:
    def test_mappable_classifier_wins_over_valid_spdx_legacy_license(self) -> None:
        metadata = _metadata(
            license="Apache-2.0",
            license_classifiers=("License :: OSI Approved :: MIT License",),
        )

        assert convert_license(metadata) == "MIT"
