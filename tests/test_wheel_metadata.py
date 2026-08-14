"""Unit tests for `reroll.wheel_metadata`."""

from __future__ import annotations

import logging

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from reroll.errors import (
    InvalidMetadataError,
    InvalidPythonRequirementRangeError,
    InvalidRequirementError,
    InvalidVersionSpecifierError,
)
from reroll.wheel_metadata import WheelMetadata, parse_metadata

_MINIMAL = "Metadata-Version: 2.1\nName: tinylib\nVersion: 1.2.3\n\n"


def _text(*lines: str) -> str:
    """A METADATA blob: `lines` as headers, followed by the blank
    line separating headers from the (unused) body.
    """
    return "\n".join(lines) + "\n\n"


# --------------------------------------------------------------------------
# `name`
# --------------------------------------------------------------------------


class TestName:
    def test_normalized(self) -> None:
        metadata = parse_metadata(
            _text("Metadata-Version: 2.1", "Name: Tiny_Lib.X", "Version: 1.0")
        )

        assert metadata.name == "tiny-lib-x"

    def test_missing_is_rejected(self) -> None:
        """A missing `Name` fails pydantic's own required-`str` check on
        `_NormalizedDistName` before `_normalize_dist_name` ever runs, so
        `_normalize_dist_name`'s `InvalidMetadataError` re-raise (covered by
        `test_pep_345_style_name_is_rejected` etc.) does not apply here.
        """
        with pytest.raises(InvalidMetadataError):
            parse_metadata(_text("Metadata-Version: 2.1", "Version: 1.0"))

    def test_duplicate_header_with_differing_values_is_rejected(self) -> None:
        """A repeated single-value header with genuinely different values
        is ambiguous -- which value is correct? -- so it's rejected.
        """
        with pytest.raises(InvalidMetadataError):
            parse_metadata(_text("Metadata-Version: 2.1", "Name: foo", "Name: bar", "Version: 1.0"))

    def test_duplicate_header_with_identical_value_is_accepted(self) -> None:
        """A repeated single-value header where every occurrence is
        byte-identical isn't actually ambiguous -- there's only one value
        being expressed twice. Real-world wheels (e.g. built by the OZI
        build backend) emit `Name` this way; both pip and uv accept them
        by taking the first occurrence, so rejecting them would be
        stricter than reroll's own "match uv" acceptance bar.
        """
        metadata = parse_metadata(
            _text("Metadata-Version: 2.1", "Name: tinylib", "Version: 1.0", "Name: tinylib")
        )

        assert metadata.name == "tinylib"

    def test_pep_345_style_name_is_rejected(self) -> None:
        """PEP 345's name grammar was more permissive than the modern
        (PEP 508) grammar `canonicalize_name(validate=True)` enforces --
        e.g. a bare `.` is legal under PEP 345 but not today.
        """
        with pytest.raises(InvalidMetadataError):
            parse_metadata(_text("Metadata-Version: 1.2", "Name: .", "Version: 1.0"))

    def test_constructing_the_model_directly_normalizes_too(self) -> None:
        """`WheelMetadata` validates its own fields, independent of
        `parse_metadata` -- e.g. when rehydrated from a database row.
        `model_validate` here, not the keyword constructor, matches that
        "raw row" shape: a `version` field is still an unvalidated string.
        """
        metadata = WheelMetadata.model_validate({"name": "Tiny_Lib", "version": "1.0"})

        assert metadata.name == "tiny-lib"


# --------------------------------------------------------------------------
# Boundary cases: ambiguous single-value headers
# --------------------------------------------------------------------------


class TestAmbiguousSingleValueHeaders:
    """A header that's supposed to appear at most once -- `Name`, `Version`,
    `License`, `License-Expression`, `Requires-Python` -- is routed by
    `packaging.metadata.parse_email` to its `unparsed` dict, rather than
    being decoded, if it's repeated or mojibake-encoded from non-UTF-8
    bytes. Per `docs/wheel_metadata.md`, a repeated header with genuinely
    differing values must fail metadata parsing the same as an invalid
    value would, not silently fall back to the field's default -- unlike
    `Name` (covered in `TestName`), these fields are all optional, so
    without this handling a duplicate/undecodable header would be
    indistinguishable from an absent one. A repeated header where every
    occurrence is byte-identical is not ambiguous, though, and is accepted.
    """

    def test_duplicate_license_expression_with_differing_values_is_rejected(self) -> None:
        with pytest.raises(InvalidMetadataError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.4",
                    "Name: tinylib",
                    "Version: 1.0",
                    "License-Expression: MIT",
                    "License-Expression: Apache-2.0",
                )
            )

    def test_duplicate_license_expression_with_identical_value_is_accepted(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.4",
                "Name: tinylib",
                "Version: 1.0",
                "License-Expression: MIT",
                "License-Expression: MIT",
            )
        )

        assert metadata.license_expression == "MIT"

    def test_duplicate_license_with_differing_values_is_rejected(self) -> None:
        with pytest.raises(InvalidMetadataError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "License: MIT",
                    "License: Apache-2.0",
                )
            )

    def test_duplicate_license_with_identical_value_is_accepted(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "License: MIT",
                "License: MIT",
            )
        )

        assert metadata.license == "MIT"

    def test_duplicate_version_with_differing_values_is_rejected(self) -> None:
        with pytest.raises(InvalidMetadataError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Version: 2.0",
                )
            )

    def test_duplicate_version_with_identical_value_is_accepted(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Version: 1.0",
            )
        )

        assert metadata.version == Version("1.0")

    def test_duplicate_requires_python_with_differing_values_is_rejected(self) -> None:
        with pytest.raises(InvalidMetadataError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Python: >=3.6",
                    "Requires-Python: >=3.8",
                )
            )

    def test_duplicate_requires_python_with_identical_value_is_accepted(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Python: >=3.6",
                "Requires-Python: >=3.6",
            )
        )

        assert metadata.requires_python == ">=3.6"

    def test_mojibake_encoded_license_is_rejected(self) -> None:
        """A `License` value containing a byte that isn't valid UTF-8 --
        e.g. from a METADATA file read with `errors="surrogateescape"` to
        tolerate an unknown encoding -- makes `parse_email` route it to
        `unparsed` the same way a duplicate header would, rather than
        decoding it. A single mojibake-encoded occurrence has nothing to
        compare against, so it's always ambiguous, never accepted.
        """
        metadata_text = (
            "Metadata-Version: 2.1\nName: tinylib\nVersion: 1.0\nLicense: Caf\udce9 License\n\n"
        )

        with pytest.raises(InvalidMetadataError):
            parse_metadata(metadata_text)


# --------------------------------------------------------------------------
# `version`
# --------------------------------------------------------------------------


class TestVersion:
    def test_parsed_to_a_version(self) -> None:
        metadata = parse_metadata(_MINIMAL)

        assert metadata.version == Version("1.2.3")

    def test_missing_is_rejected(self) -> None:
        with pytest.raises(InvalidMetadataError):
            parse_metadata(_text("Metadata-Version: 2.1", "Name: tinylib"))

    def test_invalid_version_is_rejected(self) -> None:
        with pytest.raises(InvalidMetadataError):
            parse_metadata(
                _text("Metadata-Version: 2.1", "Name: tinylib", "Version: not-a-version")
            )


# --------------------------------------------------------------------------
# `parse_metadata` <-> field-validation failures other than `name`'s
# --------------------------------------------------------------------------


class TestFieldValidationRaisesInvalidMetadataError:
    """`TestName.test_missing_is_rejected` and `TestVersion`'s two cases
    above cover this with synthetic METADATA text; this class adds a real
    corpus repro: `HolyGrail` / `HolyGrail-0.2.1.Perceval-py2-none-any.whl`
    (`Version: 0.2.1.Perceval`, not a valid PEP 440 version).
    """

    def test_invalid_version_raises_invalid_metadata_error(self) -> None:
        with pytest.raises(InvalidMetadataError):
            parse_metadata(
                _text("Metadata-Version: 2.1", "Name: HolyGrail", "Version: 0.2.1.Perceval")
            )


# --------------------------------------------------------------------------
# `license_expression`
# --------------------------------------------------------------------------


class TestLicenseExpression:
    def test_absent_is_none(self) -> None:
        metadata = parse_metadata(_MINIMAL)

        assert metadata.license_expression is None

    def test_valid_spdx_expression_is_canonicalized(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.4",
                "Name: tinylib",
                "Version: 1.0",
                "License-Expression: mit OR apache-2.0",
            )
        )

        assert metadata.license_expression == "MIT OR Apache-2.0"

    def test_non_spdx_expression_is_dropped_to_none(self) -> None:
        """An unparseable `License-Expression` (invalid syntax, or a
        syntactically-valid expression using an unknown license id) drops
        to `None` rather than failing the whole record -- unlike most
        fields, per `docs/wheel_metadata.md`. Publishing tools and PyPI
        are supposed to reject this at upload time (see PEP 639), but
        pip/uv never parse this field at all, so it has no bearing on
        whether the wheel actually installs.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.4",
                "Name: tinylib",
                "Version: 1.0",
                "License-Expression: not a valid @@ expression",
            )
        )

        assert metadata.license_expression is None
        assert metadata.name == "tinylib"

    def test_unknown_license_id_is_dropped_to_none(self) -> None:
        """Syntactically well-formed but referencing a license id that
        isn't in the SPDX list -- a different failure mode than invalid
        syntax, but `canonicalize_license_expression` raises the same
        `InvalidLicenseExpression` for both, so both are dropped to `None`.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.4",
                "Name: tinylib",
                "Version: 1.0",
                "License-Expression: Use-it-after-midnight",
            )
        )

        assert metadata.license_expression is None

    def test_dropping_an_invalid_expression_logs_a_debug_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="reroll.wheel_metadata"):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.4",
                    "Name: tinylib",
                    "Version: 1.0",
                    "License-Expression: not a valid @@ expression",
                )
            )

        (record,) = caplog.records
        assert record.levelno == logging.DEBUG
        assert "not a valid @@ expression" in record.message


# --------------------------------------------------------------------------
# `license` (free text)
# --------------------------------------------------------------------------


class TestLicense:
    def test_absent_is_none(self) -> None:
        metadata = parse_metadata(_MINIMAL)

        assert metadata.license is None

    def test_free_text_is_passed_through_unchanged(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "License: Copyright (c) Someone. All rights reserved.",
            )
        )

        assert metadata.license == "Copyright (c) Someone. All rights reserved."


# --------------------------------------------------------------------------
# `license_classifiers`
# --------------------------------------------------------------------------


class TestLicenseClassifiers:
    def test_absent_is_empty(self) -> None:
        metadata = parse_metadata(_MINIMAL)

        assert metadata.license_classifiers == ()

    def test_only_license_classifiers_are_kept(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Classifier: License :: OSI Approved :: MIT License",
                "Classifier: Programming Language :: Python :: 3",
            )
        )

        assert metadata.license_classifiers == ("License :: OSI Approved :: MIT License",)

    def test_multiple_license_classifiers_are_all_kept(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Classifier: License :: OSI Approved :: MIT License",
                "Classifier: License :: OSI Approved :: Apache Software License",
            )
        )

        assert metadata.license_classifiers == (
            "License :: OSI Approved :: MIT License",
            "License :: OSI Approved :: Apache Software License",
        )


# --------------------------------------------------------------------------
# `requires_python`
# --------------------------------------------------------------------------


class TestRequiresPython:
    def test_absent_is_none(self) -> None:
        metadata = parse_metadata(_MINIMAL)

        assert metadata.requires_python is None

    def test_valid_specifier_is_kept(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Python: >=3.9",
            )
        )

        assert metadata.requires_python == ">=3.9"

    def test_invalid_specifier_is_rejected(self) -> None:
        with pytest.raises(InvalidVersionSpecifierError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Python: not a specifier",
                )
            )

    def test_missing_separator_is_repaired_by_lenient_fixup(self) -> None:
        """`>=3.6<4.0` is missing a comma between two clauses -- a digit
        immediately followed by an operator, which is exactly the shape
        uv's `LenientRequirement` fixups (via `reroll.lenient_parser`)
        repair. See `docs/wheel_metadata.md`'s lenient-parsing decisions.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Python: >=3.6<4.0",
            )
        )

        assert metadata.requires_python is not None
        specifiers = SpecifierSet(metadata.requires_python)
        assert Version("3.5") not in specifiers
        assert Version("3.7") in specifiers
        assert Version("4.0") not in specifiers

    def test_missing_separator_not_covered_by_lenient_fixups_is_rejected(self) -> None:
        """`!=3.4.*!=3.5.*` is missing a comma between two clauses too, but
        the gap is between a wildcard (`*`) and the next clause's operator,
        not between a digit and an operator -- a shape none of uv's
        `LenientRequirement` fixups repair. Reroll only accepts what uv's
        logic accepts (see `docs/wheel_metadata.md`), so this is rejected
        rather than repaired by a reroll-specific rule.
        """
        with pytest.raises(InvalidVersionSpecifierError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Python: >=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*!=3.5.*",
                )
            )

    def test_unrepairable_invalid_specifier_is_still_rejected(self) -> None:
        """The lenient fixups must not silently accept garbage that merely
        happens to contain digits and operators.
        """
        with pytest.raises(InvalidVersionSpecifierError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Python: >=1.0 and !!invalid!!",
                )
            )

    def test_non_ascii_entry_is_rejected(self) -> None:
        """PEP 440's version specifier grammar is ASCII-only, like PEP
        508's requirement grammar (see `TestRequiresDist`'s equivalent
        test) -- `SpecifierSet()` rejects non-ASCII on its own, with no
        lenient fixup for it.
        """
        with pytest.raises(InvalidVersionSpecifierError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Python: >=café",
                )
            )

    def test_non_contiguous_minor_range_is_rejected(self) -> None:
        """`!=3.9.*` carves a hole out of an otherwise-open range -- a shape
        no real `Requires-Python` value uses in practice, and one that
        `reroll.dependencies` has no way to intersect with a wheel's
        filename-implied range. Rejected outright (same as any other
        `Requires-Python` shape reroll doesn't support) rather than
        silently dropped.
        """
        with pytest.raises(InvalidPythonRequirementRangeError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Python: >=3.8,!=3.9.*,<3.12",
                )
            )

    def test_unsatisfiable_range_is_rejected(self) -> None:
        with pytest.raises(InvalidPythonRequirementRangeError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Python: <3.0",
                )
            )

    def test_micro_level_floor_within_a_single_minor_is_accepted(self) -> None:
        """`>=3.9.16` is a genuinely contiguous minor-9-onward range --
        `3.9.0` itself just isn't one of the satisfying releases. This
        must not be rejected as non-contiguous just because the floor
        falls strictly inside minor 9 rather than at its start.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Python: >=3.9.16",
            )
        )

        assert metadata.requires_python == ">=3.9.16"


# --------------------------------------------------------------------------
# `requires_dist`
# --------------------------------------------------------------------------


class TestRequiresDist:
    def test_absent_is_empty(self) -> None:
        metadata = parse_metadata(_MINIMAL)

        assert metadata.requires_dist == ()

    def test_multiple_entries_are_all_kept_in_order(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Dist: requests>=2.20",
                "Requires-Dist: click",
            )
        )

        assert metadata.requires_dist == ("requests>=2.20", "click")

    def test_old_pep_345_style_marker_is_upgraded(self) -> None:
        """`pywin32 (>1.0); sys.platform == 'win32'` is PEP 345 syntax:
        parenthesized version, and an old-style marker variable. `packaging`
        upgrades both when parsing.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 1.2",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Dist: pywin32 (>1.0); sys.platform == 'win32'",
            )
        )

        assert metadata.requires_dist == ('pywin32>1.0; sys_platform == "win32"',)

    def test_invalid_entry_is_rejected(self) -> None:
        with pytest.raises(InvalidRequirementError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: not a valid requirement !!!",
                )
            )

    def test_non_ascii_entry_is_rejected(self) -> None:
        """No field this module cares about legitimately needs non-ASCII --
        PEP 508's requirement grammar is ASCII-only, so `Requirement()`
        rejects it on its own.
        """
        with pytest.raises(InvalidRequirementError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: café",
                )
            )

    def test_lenient_fixup_repairs_missing_comma(self) -> None:
        """`elasticsearch-dsl (>=7.2.0<8.0.0)` is missing a comma between
        two clauses inside the (PEP 345 style) parenthesized version --
        exactly the shape uv's `LenientRequirement` fixups (via
        `reroll.lenient_parser`) repair. See `docs/wheel_metadata.md`'s
        lenient-parsing decisions.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 1.2",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Dist: elasticsearch-dsl (>=7.2.0<8.0.0)",
            )
        )

        assert metadata.requires_dist == ("elasticsearch-dsl<8.0.0,>=7.2.0",)

    def test_lenient_fixup_repairs_stray_quotes(self) -> None:
        """Some old wheel builders quote the version inside a PEP 345
        parenthesized specifier -- e.g. `python-version (>='3.10')`, seen
        in real, published wheel metadata -- which PEP 440 doesn't allow:
        version specifiers never contain quote characters. uv's
        `LenientRequirement` fixups strip stray quotes like this.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 1.2",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Dist: python-version (>='3.10')",
            )
        )

        assert metadata.requires_dist == ("python-version>=3.10",)

    def test_lenient_fixup_preserves_marker_quotes(self) -> None:
        """The stray-quote fixup only strips quotes before a trailing `;`
        marker -- the marker's own quoted string literals (required by PEP
        508 grammar) survive untouched.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 1.2",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Dist: foo (>='1.0'); python_version >= '3.10'",
            )
        )

        assert metadata.requires_dist == ('foo>=1.0; python_version >= "3.10"',)

    def test_unrepairable_entry_is_still_rejected(self) -> None:
        """The lenient fixups are attempted, but must not fabricate a valid
        requirement out of an entry that was never fixable in the first
        place.
        """
        with pytest.raises(InvalidRequirementError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: foo (not-a-version)",
                )
            )

    def test_pip_relaxed_parsing_missing_comma_quirk_is_rejected(self) -> None:
        """<https://pypi.org/project/ADLSstream/0.1.1/>: a missing comma
        between `INSTALL_REQUIRES` entries produced the METADATA value
        `tensorflow-addons (>=0.11.0keras-tcn)`. pip<=24.1 installed this
        by parsing `0.11.0keras-tcn` as a (nonsense) version; uv rejects
        it, and none of uv's `LenientRequirement` fixups repair a missing
        comma *inside* a version token (only between a digit and a
        comparison operator) -- see docs/wheel_metadata.md's "Other
        requires-dist quirks".
        """
        with pytest.raises(InvalidRequirementError):
            parse_metadata(
                _text(
                    "Metadata-Version: 1.2",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: tensorflow-addons (>=0.11.0keras-tcn)",
                )
            )


# --------------------------------------------------------------------------
# `provides_extra`
# --------------------------------------------------------------------------


class TestProvidesExtra:
    def test_absent_is_empty(self) -> None:
        metadata = parse_metadata(_MINIMAL)

        assert metadata.provides_extra == ()

    def test_multiple_entries_are_normalized(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Provides-Extra: CLI_Tools",
                "Provides-Extra: test",
            )
        )

        assert metadata.provides_extra == ("cli-tools", "test")

    def test_malformed_entry_is_normalized_not_rejected(self) -> None:
        """`Provides-Extra` uses `canonicalize_name` without `validate=True`
        -- unlike `name` -- so metadata parsing never fails for this field;
        a malformed entry is merely normalized (runs of `-`/`_`/`.`
        collapsed to a single `-`, lowercased), not rejected.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Provides-Extra: not valid!",
            )
        )

        assert metadata.provides_extra == ("not valid!",)


# --------------------------------------------------------------------------
# Fields reroll does not care about
# --------------------------------------------------------------------------


class TestIgnoredFields:
    def test_obsoletes_dist_is_ignored_even_when_malformed(self) -> None:
        """`Obsoletes-Dist` isn't implemented by any packaging tool (see
        `docs/wheel_metadata.md`), so `WheelMetadata` has no field for it at
        all, and a malformed entry has no effect.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Obsoletes-Dist: not a valid requirement !!!",
            )
        )

        assert metadata.name == "tinylib"

    def test_unrelated_malformed_field_has_no_effect(self) -> None:
        """A field `WheelMetadata` never references (e.g.
        `Description-Content-Type`) is never validated, so a malformed value
        doesn't surface.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Description-Content-Type: not/a/valid/type",
            )
        )

        assert metadata.name == "tinylib"

    def test_unrecognized_metadata_version_has_no_effect(self) -> None:
        """Per `docs/wheel_metadata.md`'s "Metadata-Version" section,
        reroll never validates other fields against `Metadata-Version` --
        an unrecognized (here, futuristic) value doesn't block parsing of
        an otherwise-ordinary METADATA file.
        """
        metadata = parse_metadata(_text("Metadata-Version: 99.9", "Name: tinylib", "Version: 1.0"))

        assert metadata.name == "tinylib"
        assert metadata.version == Version("1.0")

    def test_missing_metadata_version_has_no_effect(self) -> None:
        """`Metadata-Version` is never required by reroll, unlike the spec
        (`docs/wheel_metadata.md`) -- a METADATA file omitting it entirely
        still parses.
        """
        metadata = parse_metadata(_text("Name: tinylib", "Version: 1.0"))

        assert metadata.name == "tinylib"
        assert metadata.version == Version("1.0")


# --------------------------------------------------------------------------
# Encoding / line-ending handling
# --------------------------------------------------------------------------


class TestEncodingChallenges:
    def test_windows_line_endings(self) -> None:
        metadata = parse_metadata("Metadata-Version: 2.1\r\nName: tinylib\r\nVersion: 1.0\r\n\r\n")

        assert metadata.name == "tinylib"
        assert metadata.version == Version("1.0")

    def test_mixed_line_endings(self) -> None:
        metadata = parse_metadata("Metadata-Version: 2.1\r\nName: tinylib\nVersion: 1.0\r\n\n")

        assert metadata.name == "tinylib"
        assert metadata.version == Version("1.0")

    def test_mac_classic_line_endings(self) -> None:
        """Classic Mac OS (pre-OS X) used a bare `\\r` as its line ending --
        `docs/wheel_metadata.md` calls this out by name alongside Windows'
        and Unix's, distinct from the CRLF (`\\r\\n`) case above.
        """
        metadata = parse_metadata("Metadata-Version: 2.1\rName: tinylib\rVersion: 1.0\r\r")

        assert metadata.name == "tinylib"
        assert metadata.version == Version("1.0")

    def test_embedded_null_in_name_is_rejected(self) -> None:
        with pytest.raises(InvalidMetadataError):
            parse_metadata(_text("Metadata-Version: 2.1", "Name: tiny\x00lib", "Version: 1.0"))


# --------------------------------------------------------------------------
# Truncated / empty METADATA
# --------------------------------------------------------------------------


class TestEmptyMetadata:
    def test_empty_content_is_rejected(self) -> None:
        """A zero-byte `METADATA` file -- e.g. from a corrupted download --
        has no headers at all, so both `name` and `version` come back empty.
        Both are already rejected individually; this locks in that the
        combination is too.
        """
        with pytest.raises(InvalidMetadataError):
            parse_metadata("")
