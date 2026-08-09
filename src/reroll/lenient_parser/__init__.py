r"""Lenient PEP 508 requirement and PEP 440 specifier parsing.

A Python port of uv's `LenientRequirement` and `LenientVersionSpecifiers`
(`crates/uv-pypi-types/src/lenient_requirement.rs`), per the
"LenientRequirement fixups" decisions in `docs/wheel_metadata.md`. See this
package's `README.md` for the MIT license this port is used under and how
it's kept in sync with uv's source.

Deliberate deviations from the Rust source, either to fit Python idiom or
to close a rust-to-python porting gap:
- No `LenientRequirement`/`LenientVersionSpecifiers` newtype wrapper: Rust
  needs one to add a foreign `FromStr` impl; Python has no such
  restriction, so `parse_lenient_requirement`/
  `parse_lenient_version_specifiers` return `packaging`'s own
  `Requirement`/`SpecifierSet` directly.
- The "removing trailing comma" fixup's replacement is a literal empty
  string, not a `\1` backreference: the Rust pattern (`,\s*$`) has no
  capture group, and uv's replacement (`${1}`) silently expands to `""`
  when the referenced group doesn't exist. Python's `re` raises
  `error: invalid group reference` for that same backreference, so the
  fixup spells out the empty string it evaluates to instead.
- That same fixup's `$` is written as `\Z`: Python's (non-`MULTILINE`) `$`
  additionally matches just before a trailing `\n`, which Rust's `regex`
  crate `$` does not. `\s*` already consumes any such trailing newline
  before the anchor is reached, so the two are equivalent for every input
  in this module's tests -- `\Z` just removes the ambiguity for any future
  fixup that might not have that `\s*` cushion.
- A successful fixup is logged at debug level, not warning: uv's `warn!`
  reflects its position as a fallback of last resort; reroll's own
  `docs/wheel_metadata.md` documents a successful fixup as an accepted,
  expected outcome, not something to warn about.

None of `FIXUPS`' patterns use backreferences or lookaround, so Rust's
non-backtracking `regex` crate and Python's backtracking `re` module agree
on every match here; likewise both treat `\d` and `\s` as Unicode-aware by
default, so neither engine special-cases non-ASCII digits or whitespace
relative to the other.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet

__all__ = [
    "parse_lenient_requirement",
    "parse_lenient_version_specifiers",
]

_logger = logging.getLogger(__name__)


def parse_lenient_requirement(value: str) -> Requirement:
    """Parses `value` as a PEP 508 requirement, applying uv's `FIXUPS` to
    correct common errors if the strict parse fails. Raises
    `InvalidRequirement` if `value` cannot be parsed even after every
    fixup has been tried.
    """
    return _parse_with_fixups(value, "requirement", Requirement, InvalidRequirement)


def parse_lenient_version_specifiers(value: str) -> SpecifierSet:
    """Parses `value` as a PEP 440 version specifier set, applying uv's
    `FIXUPS` to correct common errors if the strict parse fails. Raises
    `InvalidSpecifier` if `value` cannot be parsed even after every fixup
    has been tried.
    """
    return _parse_with_fixups(value, "version specifier", SpecifierSet, InvalidSpecifier)


# Given `>=7.2.0<8.0.0`, rewrite to `>=7.2.0,<8.0.0`.
_MISSING_COMMA_RE = re.compile(r"(\d)([<>=~^!])")
# Given `!=~5.0,>=4.12`, rewrite to `!=5.0.*,>=4.12`.
_NOT_EQUAL_TILDE_RE = re.compile(r"!=~((?:\d\.)*\d)")
# Given `>=1.9.*`, rewrite to `>=1.9`.
_STAR_AFTER_COMPARISON_RE = re.compile(r"(<=|>=|<|>)(?:\s*)(\d+(\.\d+)*)\.\*")
# Given `!=3.0*`, rewrite to `!=3.0.*`.
_MISSING_DOT_RE = re.compile(r"(\d\.\d)+\*")
# Given `>=3.6,`, rewrite to `>=3.6`.
_TRAILING_COMMA_RE = re.compile(r",\s*\Z")
# Given `>dev`, rewrite to `>0.0.0dev`.
_GREATER_THAN_DEV_RE = re.compile(r">dev")
# Given `>=9.0.0a1.0`, rewrite to `>=9.0.0a1`.
_TRAILING_ALPHA_ZERO_RE = re.compile(r"(\d+(\.\d)*(a|b|rc|post|dev)\d+)\.0")
# Given `>= '2.7'`, rewrite to `>= 2.7`, but never touch a `;`-delimited marker,
# which can have quotes legitimately (e.g. `python_version >= '3.7'`).
_STRAY_QUOTES_RE = re.compile(r"['\"]")

_FIXUPS: list[tuple[Callable[[str], str], str]] = [
    (lambda input: _MISSING_COMMA_RE.sub(r"\1,\2", input), "inserting missing comma"),
    (
        lambda input: _NOT_EQUAL_TILDE_RE.sub(r"!=\1.*", input),
        "replacing invalid tilde with wildcard",
    ),
    (
        lambda input: _STAR_AFTER_COMPARISON_RE.sub(r"\1\2", input),
        "removing star after comparison operator other than equal and not equal",
    ),
    (lambda input: _MISSING_DOT_RE.sub(r"\1.*", input), "inserting missing dot"),
    (lambda input: _TRAILING_COMMA_RE.sub("", input), "removing trailing comma"),
    (lambda input: _GREATER_THAN_DEV_RE.sub(">0.0.0dev", input), "assuming 0.0.0dev"),
    (lambda input: _TRAILING_ALPHA_ZERO_RE.sub(r"\1", input), "removing trailing zero"),
    (lambda input: _remove_stray_quotes(input), "removing stray quotes"),
]


def _parse_with_fixups[T](
    value: str,
    type_name: str,
    parse: Callable[[str], T],
    error_type: type[Exception],
) -> T:
    try:
        return parse(value)
    except error_type as err:
        patched_input = value
        messages: list[str] = []
        for fixup, message in _FIXUPS:
            patched = fixup(patched_input)
            if patched == patched_input:
                continue
            messages.append(message)
            try:
                result = parse(patched)
            except error_type:
                patched_input = patched
                continue
            _logger.debug(
                "Fixing invalid %s by %s (before: `%s`; after: `%s`)",
                type_name,
                ", ".join(messages),
                value,
                patched,
            )
            return result
        raise err


def _remove_stray_quotes(input: str) -> str:
    marker_index = input.find(";")
    if marker_index == -1:
        return _STRAY_QUOTES_RE.sub("", input)
    requirement = _STRAY_QUOTES_RE.sub("", input[:marker_index])
    return requirement + input[marker_index:]
