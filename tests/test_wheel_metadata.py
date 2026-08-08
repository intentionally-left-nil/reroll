"""Unit tests for `reroll.wheel_metadata`."""

from __future__ import annotations

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import ValidationError

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
        with pytest.raises(ValidationError):
            parse_metadata(_text("Metadata-Version: 2.1", "Version: 1.0"))

    def test_duplicate_header_is_rejected(self) -> None:
        """A repeated single-value header is demoted to 'unparsed' by
        `packaging.metadata.parse_email`, which is indistinguishable from
        the header being absent -- and absent is rejected too.
        """
        with pytest.raises(ValidationError):
            parse_metadata(_text("Metadata-Version: 2.1", "Name: foo", "Name: bar", "Version: 1.0"))

    def test_pep_345_style_name_is_rejected(self) -> None:
        """PEP 345's name grammar was more permissive than the modern
        (PEP 508) grammar `canonicalize_name(validate=True)` enforces --
        e.g. a bare `.` is legal under PEP 345 but not today.
        """
        with pytest.raises(ValidationError):
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
    bytes. Per `docs/wheel_metadata.md`, this must fail metadata parsing
    the same as an invalid value would, not silently fall back to the
    field's default -- unlike `Name` (covered in `TestName`), these fields
    are all optional, so without this handling a duplicate/undecodable
    header would be indistinguishable from an absent one.
    """

    def test_duplicate_license_expression_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.4",
                    "Name: tinylib",
                    "Version: 1.0",
                    "License-Expression: MIT",
                    "License-Expression: Apache-2.0",
                )
            )

    def test_duplicate_license_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "License: MIT",
                    "License: Apache-2.0",
                )
            )

    def test_duplicate_version_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Version: 2.0",
                )
            )

    def test_duplicate_requires_python_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Python: >=3.6",
                    "Requires-Python: >=3.8",
                )
            )

    def test_mojibake_encoded_license_is_rejected(self) -> None:
        """A `License` value containing a byte that isn't valid UTF-8 --
        e.g. from a METADATA file read with `errors="surrogateescape"` to
        tolerate an unknown encoding -- makes `parse_email` route it to
        `unparsed` the same way a duplicate header would, rather than
        decoding it.
        """
        metadata_text = (
            "Metadata-Version: 2.1\nName: tinylib\nVersion: 1.0\nLicense: Caf\udce9 License\n\n"
        )

        with pytest.raises(ValidationError):
            parse_metadata(metadata_text)


# --------------------------------------------------------------------------
# `version`
# --------------------------------------------------------------------------


class TestVersion:
    def test_parsed_to_a_version(self) -> None:
        metadata = parse_metadata(_MINIMAL)

        assert metadata.version == Version("1.2.3")

    def test_missing_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(_text("Metadata-Version: 2.1", "Name: tinylib"))

    def test_invalid_version_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(
                _text("Metadata-Version: 2.1", "Name: tinylib", "Version: not-a-version")
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

    def test_invalid_expression_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.4",
                    "Name: tinylib",
                    "Version: 1.0",
                    "License-Expression: not a valid @@ expression",
                )
            )


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
        with pytest.raises(ValidationError):
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
        with pytest.raises(ValidationError):
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
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Python: >=1.0 and !!invalid!!",
                )
            )


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
        with pytest.raises(ValidationError):
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
        with pytest.raises(ValidationError):
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
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: foo (not-a-version)",
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

    def test_embedded_null_in_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
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
        with pytest.raises(ValidationError):
            parse_metadata("")
