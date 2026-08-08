"""Unit tests for `reroll.wheel_metadata`."""

from __future__ import annotations

import pytest
from packaging.requirements import InvalidRequirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import ValidationError

from reroll.wheel_metadata import WheelMetadata, _parse_requirement, parse_metadata

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

    def test_missing_separator_between_exclusion_clauses_is_repaired(self) -> None:
        """Real, published wheel metadata has been seen with no separator
        between adjacent exclusion clauses -- `!=3.4.*!=3.5.*` instead of
        `!=3.4.*, !=3.5.*`.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 2.1",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Python: >=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*!=3.5.*",
            )
        )

        assert metadata.requires_python is not None
        specifiers = SpecifierSet(metadata.requires_python)
        assert Version("2.6") not in specifiers
        assert Version("3.0.1") not in specifiers
        assert Version("3.4.1") not in specifiers
        assert Version("3.5.1") not in specifiers
        assert Version("3.6.0") in specifiers

    def test_missing_separator_between_other_clauses_is_repaired(self) -> None:
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

    def test_unrepairable_invalid_specifier_is_still_rejected(self) -> None:
        """The missing-separator repair must not silently accept garbage
        that merely happens to contain digits and operators.
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

    def test_quoted_legacy_parenthesized_version_is_repaired(self) -> None:
        """Some old wheel builders quote the version inside a PEP 345
        parenthesized specifier -- e.g. `python-version (>='3.10')`, seen in
        real, published wheel metadata -- which PEP 440 doesn't allow:
        version specifiers never contain quote characters.
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

    def test_quoted_legacy_parenthesized_version_with_extras_is_repaired(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 1.2",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Dist: foo[bar] (>='1.0')",
            )
        )

        assert metadata.requires_dist == ("foo[bar]>=1.0",)

    def test_quoted_legacy_parenthesized_version_keeps_marker_quotes(self) -> None:
        """The repair only strips quotes from the parenthesized version --
        a trailing environment marker's own quoted string literals (required
        by PEP 508 grammar) must survive untouched.
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

    def test_quoted_legacy_parenthesized_version_with_double_quotes_is_repaired(self) -> None:
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 1.2",
                "Name: tinylib",
                "Version: 1.0",
                'Requires-Dist: foo (>="1.0")',
            )
        )

        assert metadata.requires_dist == ("foo>=1.0",)

    def test_marker_with_its_own_grouping_parens_survives_the_repair(self) -> None:
        """A repaired entry's marker may itself use parens for boolean
        grouping (`(a or b) and c`) -- unrelated to the PEP 345
        parenthesized-version shape being repaired, and syntactically legal
        on its own. Depth-tracking (not "match to the first `)`") is what
        keeps these two parenthesized regions from being confused.
        """
        metadata = parse_metadata(
            _text(
                "Metadata-Version: 1.2",
                "Name: tinylib",
                "Version: 1.0",
                "Requires-Dist: foo (>='1.0'); (extra == 'a' or extra == 'b')",
            )
        )

        assert metadata.requires_dist == ('foo>=1.0; extra == "a" or extra == "b"',)

    def test_unrepairable_parenthesized_entry_is_still_rejected(self) -> None:
        """The quote-stripping repair is attempted (the entry matches the
        parenthesized shape) but must not fabricate a valid specifier out of
        a version that was never a quoting problem in the first place.
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

    # ----------------------------------------------------------------------
    # Malformed parens: the repair's paren-depth tracking must never
    # mismatch a `)` to the wrong `(`, and must never turn genuinely
    # unbalanced or doubled parens into a false-positive valid parse.
    # ----------------------------------------------------------------------

    def test_missing_closing_paren_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: foo (>='1.0'",
                )
            )

    def test_stray_extra_closing_paren_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: foo (>='1.0'))",
                )
            )

    def test_doubled_parens_without_quotes_is_still_rejected(self) -> None:
        """No quotes to strip means the repair's reconstruction is identical
        to the original -- it must not loop into a different, accidentally
        valid interpretation.
        """
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: foo ((>=1.0))",
                )
            )

    def test_nested_parens_inside_quoted_version_are_still_rejected(self) -> None:
        """A version specifier can never legitimately contain parens
        (nested or otherwise) -- depth-tracking must find the *matching*
        outer `)` rather than the first one, but the result is still not a
        valid specifier.
        """
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: foo (>='1.0(2.0)')",
                )
            )

    def test_sibling_parenthesized_groups_is_rejected(self) -> None:
        """PEP 345 allows at most one parenthesized version group right
        after the name -- a second one is not a marker (no `;`) and is not
        repaired.
        """
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: foo (>='1.0') (<='2.0')",
                )
            )

    def test_closing_paren_inside_extras_is_rejected(self) -> None:
        """A `)` inside the extras brackets (`foo[hello)]`) is swallowed by
        the extras group's own `[^\\]]*` -- it never reaches the version
        group's paren-depth counter at all. But `hello)` is not a valid
        extra name either way, so the repaired reconstruction is rejected
        same as the original, just for a different, unrelated reason.
        """
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: foo[hello)] (>='1.0')",
                )
            )

    def test_entry_with_no_name_at_all_is_rejected(self) -> None:
        """An entry that doesn't even start with a name has no PEP 345
        parenthesized-version shape to repair.
        """
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Requires-Dist: (orphan)",
                )
            )


# --------------------------------------------------------------------------
# `requires_dist` -- exhaustive `[]` / `()` combinations
# --------------------------------------------------------------------------
#
# `_parse_requirement` is exercised directly (rather than through
# `parse_metadata`) since these are unit-level checks of the paren-depth
# repair itself, not of the METADATA-parsing pipeline around it.


class TestParseRequirementBracketParenCombinationsRepaired:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("foo (>='1.0')", "foo>=1.0"),
            ('foo (>="1.0")', "foo>=1.0"),
            ("foo[bar] (>='1.0')", "foo[bar]>=1.0"),
            ('foo[bar] (>="1.0")', "foo[bar]>=1.0"),
            ("foo[bar,baz] (>='1.0')", "foo[bar,baz]>=1.0"),
            ('foo[bar,baz] (>="1.0")', "foo[bar,baz]>=1.0"),
            ("foo[bar](>='1.0')", "foo[bar]>=1.0"),
            ("foo [bar] (>='1.0')", "foo[bar]>=1.0"),
        ],
        ids=[
            "no_extras_single_quotes",
            "no_extras_double_quotes",
            "one_extra_single_quotes",
            "one_extra_double_quotes",
            "multiple_extras_single_quotes",
            "multiple_extras_double_quotes",
            "no_space_before_paren",
            "space_before_bracket",
        ],
    )
    def test_repaired(self, value: str, expected: str) -> None:
        assert str(_parse_requirement(value)) == expected

    def test_repaired_marker_with_extras_and_grouping_parens(self) -> None:
        """Extras, a quoted legacy version, and a marker with its own
        grouping parens, all in one entry.
        """
        value = "foo[bar] (>='1.0'); (extra == 'a' or extra == 'b')"

        assert str(_parse_requirement(value)) == 'foo[bar]>=1.0; extra == "a" or extra == "b"'


class TestParseRequirementBracketParenCombinationsRejected:
    @pytest.mark.parametrize(
        "value",
        [
            "foo[bar (>='1.0')",
            "foo[[bar]] (>='1.0')",
            "foo[bar][baz] (>='1.0')",
            "foo bar] (>='1.0')",
            "foo[hello)] (>='1.0')",
            "foo[hel(lo] (>='1.0')",
            "foo[(bar)] (>='1.0')",
            "foo['bar'] (>='1.0')",
            "foo[bar] (>='1.0'",
            "foo[bar] (>='1.0'))",
            "foo[bar] ((>=1.0))",
            "foo[bar] (>='1.0(2.0)')",
            "foo[bar] (>='1.0') (<='2.0')",
            "foo[bar] (not-a-version)",
        ],
        ids=[
            "extras_missing_closing_bracket",
            "doubled_nested_brackets",
            "two_separate_extras_groups",
            "stray_closing_bracket_no_opening",
            "closing_paren_inside_extras",
            "opening_paren_inside_extras",
            "balanced_parens_inside_extras",
            "quotes_inside_extras",
            "extras_missing_closing_paren",
            "extras_stray_extra_closing_paren",
            "extras_doubled_parens_no_quotes",
            "extras_nested_parens_in_quoted_version",
            "extras_sibling_paren_groups",
            "extras_unrepairable_non_quote_issue",
        ],
    )
    def test_rejected(self, value: str) -> None:
        with pytest.raises(InvalidRequirement):
            _parse_requirement(value)


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

    def test_invalid_entry_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_metadata(
                _text(
                    "Metadata-Version: 2.1",
                    "Name: tinylib",
                    "Version: 1.0",
                    "Provides-Extra: not valid!",
                )
            )


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
