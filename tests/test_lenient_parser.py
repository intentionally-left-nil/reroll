"""Unit tests for `reroll.lenient_parser`.

Ported from uv's `crates/uv-pypi-types/src/lenient_requirement.rs` test
module (dual MIT/Apache-2.0 licensed), plus additional tests covering
paths uv's Rust tests don't exercise: the already-valid and
unfixable-even-with-fixups branches, and rust-to-python regex porting
sharp edges (see `reroll.lenient_parser`'s module docstring).
"""

from __future__ import annotations

import pytest
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from reroll.lenient_parser import (
    _MISSING_COMMA_RE,
    _TRAILING_COMMA_RE,
    parse_lenient_requirement,
    parse_lenient_version_specifiers,
)

# --------------------------------------------------------------------------
# `parse_lenient_requirement`
# --------------------------------------------------------------------------


class TestParseLenientRequirement:
    def test_already_valid_is_returned_unchanged(self) -> None:
        actual = parse_lenient_requirement("numpy>=1.19")

        assert actual == Requirement("numpy>=1.19")

    def test_unfixable_raises(self) -> None:
        with pytest.raises(InvalidRequirement):
            parse_lenient_requirement("this is not a requirement @@@")

    def test_missing_comma(self) -> None:
        actual = parse_lenient_requirement("elasticsearch-dsl (>=7.2.0<8.0.0)")

        assert actual == Requirement("elasticsearch-dsl (>=7.2.0,<8.0.0)")

    def test_not_equal_tilde(self) -> None:
        actual = parse_lenient_requirement("jupyter-core (!=~5.0,>=4.12)")
        assert actual == Requirement("jupyter-core (!=5.0.*,>=4.12)")

        actual = parse_lenient_requirement("jupyter-core (!=~5,>=4.12)")
        assert actual == Requirement("jupyter-core (!=5.*,>=4.12)")

    def test_greater_than_star(self) -> None:
        actual = parse_lenient_requirement("torch (>=1.9.*)")

        assert actual == Requirement("torch (>=1.9)")

    def test_greater_than_star_with_space(self) -> None:
        actual = parse_lenient_requirement("torch (>= 1.9.*)")

        assert actual == Requirement("torch (>= 1.9)")

    def test_missing_dot(self) -> None:
        actual = parse_lenient_requirement("pyzmq (>=2.7,!=3.0*,!=3.1*,!=3.2*)")

        assert actual == Requirement("pyzmq (>=2.7,!=3.0.*,!=3.1.*,!=3.2.*)")

    def test_trailing_comma(self) -> None:
        actual = parse_lenient_requirement("pyzmq >=3.6,")

        assert actual == Requirement("pyzmq >=3.6")

    def test_trailing_comma_after_quote(self) -> None:
        """<https://files.pythonhosted.org/packages/74/49/7349527cea7f708e7d3253ab6b32c9b5bdf84a57dde8fc265a33e6a4e662/boto3-1.2.0-py2.py3-none-any.whl>

        Needs two fixups to compound: the quote blocks a first-pass parse
        even after the trailing comma alone is stripped, and vice versa.
        """
        actual = parse_lenient_requirement("botocore>=1.3.0,<1.4.0',")

        assert actual == Requirement("botocore>=1.3.0,<1.4.0")

    def test_stray_quote_preserve_marker(self) -> None:
        """<https://github.com/astral-sh/uv/issues/2551>"""
        actual = parse_lenient_requirement('numpy >=1.19; python_version >= "3.7"')
        assert actual == Requirement('numpy >=1.19; python_version >= "3.7"')

        actual = parse_lenient_requirement('numpy ">=1.19"; python_version >= "3.7"')
        assert actual == Requirement('numpy >=1.19; python_version >= "3.7"')

        actual = parse_lenient_requirement("""'numpy' >=1.19"; python_version >= "3.7\"""")
        assert actual == Requirement('numpy >=1.19; python_version >= "3.7"')


# --------------------------------------------------------------------------
# `parse_lenient_version_specifiers`
# --------------------------------------------------------------------------


class TestParseLenientVersionSpecifiers:
    def test_already_valid_is_returned_unchanged(self) -> None:
        actual = parse_lenient_version_specifiers(">=1.9")

        assert actual == SpecifierSet(">=1.9")

    def test_unfixable_raises(self) -> None:
        with pytest.raises(InvalidSpecifier):
            parse_lenient_version_specifiers("@@@ not a specifier @@@")

    def test_missing_comma(self) -> None:
        actual = parse_lenient_version_specifiers(">=7.2.0<8.0.0")

        assert actual == SpecifierSet(">=7.2.0,<8.0.0")

    def test_not_equal_tilde(self) -> None:
        actual = parse_lenient_version_specifiers("!=~5.0,>=4.12")
        assert actual == SpecifierSet("!=5.0.*,>=4.12")

        actual = parse_lenient_version_specifiers("!=~5,>=4.12")
        assert actual == SpecifierSet("!=5.*,>=4.12")

    def test_greater_than_star(self) -> None:
        actual = parse_lenient_version_specifiers(">=1.9.*")
        assert actual == SpecifierSet(">=1.9")

    def test_greater_than_star_with_space(self) -> None:
        actual = parse_lenient_version_specifiers(">= 1.9.*")
        assert actual == SpecifierSet(">= 1.9")

        actual = parse_lenient_version_specifiers(">=1.*")
        assert actual == SpecifierSet(">=1")

    def test_missing_dot(self) -> None:
        actual = parse_lenient_version_specifiers(">=2.7,!=3.0*,!=3.1*,!=3.2*")

        assert actual == SpecifierSet(">=2.7,!=3.0.*,!=3.1.*,!=3.2.*")

    def test_trailing_comma(self) -> None:
        actual = parse_lenient_version_specifiers(">=3.6,")

        assert actual == SpecifierSet(">=3.6")

    def test_trailing_comma_trailing_space(self) -> None:
        actual = parse_lenient_version_specifiers(">=3.6, ")

        assert actual == SpecifierSet(">=3.6")

    def test_invalid_single_quotes(self) -> None:
        """<https://pypi.org/simple/shellingham/?format=application/vnd.pypi.simple.v1+json>"""
        actual = parse_lenient_version_specifiers(">= '2.7'")

        assert actual == SpecifierSet(">= 2.7")

    def test_invalid_double_quotes(self) -> None:
        """<https://pypi.org/simple/tensorflowonspark/?format=application/vnd.pypi.simple.v1+json>"""
        actual = parse_lenient_version_specifiers('>="3.6"')

        assert actual == SpecifierSet(">=3.6")

    def test_multi_fix(self) -> None:
        """<https://pypi.org/simple/celery/?format=application/vnd.pypi.simple.v1+json>"""
        actual = parse_lenient_version_specifiers(
            ">=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*,"
        )

        assert actual == SpecifierSet(">=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*")

    def test_smaller_than_star(self) -> None:
        """<https://pypi.org/simple/wincertstore/?format=application/vnd.pypi.simple.v1+json>"""
        actual = parse_lenient_version_specifiers(">=2.7,!=3.0.*,!=3.1.*,<3.4.*")

        assert actual == SpecifierSet(">=2.7,!=3.0.*,!=3.1.*,<3.4")

    def test_stray_quote(self) -> None:
        """<https://pypi.org/simple/algoliasearch/?format=application/vnd.pypi.simple.v1+json>
        <https://pypi.org/simple/okta/?format=application/vnd.pypi.simple.v1+json>
        """
        actual = parse_lenient_version_specifiers(">=2.7, !=3.0.*, !=3.1.*', !=3.2.*, !=3.3.*'")
        assert actual == SpecifierSet(">=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*")

        actual = parse_lenient_version_specifiers(">=3.6'")
        assert actual == SpecifierSet(">=3.6")

    def test_greater_than_dev(self) -> None:
        """<https://github.com/celery/celery/blob/6215f34d2675441ef2177bd850bf5f4b442e944c/requirements/default.txt#L1>"""
        actual = parse_lenient_version_specifiers(">dev")

        assert actual == SpecifierSet(">0.0.0dev")

    def test_trailing_alpha_zero(self) -> None:
        """<https://github.com/astral-sh/uv/issues/1798>"""
        actual = parse_lenient_version_specifiers(">=9.0.0a1.0")
        assert actual == SpecifierSet(">=9.0.0a1")

        actual = parse_lenient_version_specifiers(">=9.0a1.0")
        assert actual == SpecifierSet(">=9.0a1")

        actual = parse_lenient_version_specifiers(">=9a1.0")
        assert actual == SpecifierSet(">=9a1")


# --------------------------------------------------------------------------
# Rust-to-Python regex porting sharp edges
# --------------------------------------------------------------------------


class TestRustToPythonRegexPortSharpEdges:
    def test_trailing_comma_pattern_has_no_capture_group(self) -> None:
        """Regression guard for the sharpest edge in this port: uv's Rust
        replacement for this fixup is `${1}`, referencing a group that
        doesn't exist in `,\\s*$` -- which Rust's `regex` crate silently
        expands to `""`, but Python's `re` raises `error: invalid group
        reference` for the equivalent `\\1`. If a future edit adds a
        capture group to this pattern, the fixup's replacement string
        (currently a literal `""`) must be revisited too.
        """
        assert _TRAILING_COMMA_RE.groups == 0

    def test_trailing_comma_with_folded_newline_is_still_removed(self) -> None:
        """Python's (non-`MULTILINE`) `$` matches just before a trailing
        `\\n` as well as at the true end of the string; Rust's `regex`
        crate `$` only matches the true end. This module's fixup uses
        `\\Z` to sidestep the difference -- but even if it used `$`, `\\s*`
        already consumes a trailing newline (e.g. from METADATA header
        line-folding) before the anchor is reached, so the two are
        equivalent here.
        """
        actual = parse_lenient_version_specifiers(">=3.6,\n")

        assert actual == SpecifierSet(">=3.6")

    def test_missing_comma_pattern_matches_unicode_digits(self) -> None:
        """Rust's `regex` crate matches `\\d` against Unicode decimal
        digits (Unicode category Nd) by default, the same as Python's `re`
        for a `str` pattern -- neither engine special-cases this relative
        to the other, so a non-ASCII digit is fixed up identically to an
        ASCII one at the regex layer, even though `packaging` itself will
        go on to reject the result as an invalid PEP 440 specifier.
        """
        # U+0663 ARABIC-INDIC DIGIT THREE, categorized as Nd like ASCII '3'.
        assert _MISSING_COMMA_RE.sub(r"\1,\2", "\u0663>=1") == "\u0663,>=1"
