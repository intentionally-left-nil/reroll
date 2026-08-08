"""Parse a wheel's `METADATA` file into the fields reroll needs."""

from __future__ import annotations

import re
from typing import Annotated

from packaging.licenses import canonicalize_license_expression
from packaging.metadata import parse_email
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from pydantic import AfterValidator, BaseModel, ConfigDict, field_validator

from reroll.filename.py_version import PyVersion

_LICENSE_CLASSIFIER_PREFIX = "License :: "


def _normalize_dist_name(value: str) -> str:
    """PEP 503 normalization, and a hard rejection of PEP 345-style names:
    `canonicalize_name(validate=True)` only accepts the modern (PEP 508)
    name grammar.
    """
    return canonicalize_name(value, validate=True)


_NormalizedDistName = Annotated[str, AfterValidator(_normalize_dist_name)]
"""A `str` PyPI distribution or extra name, canonicalized per PEP 503.

Deliberately a plain `str` annotated with a validator -- not
`packaging.utils.NormalizedName` -- so the field's static type matches what
it actually accepts as input.
"""


class WheelMetadata(BaseModel):
    """The subset of a wheel's `METADATA` fields reroll needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: _NormalizedDistName
    version: PyVersion
    license_expression: str | None = None
    license: str | None = None
    license_classifiers: tuple[str, ...] = ()
    requires_python: str | None = None
    requires_dist: tuple[str, ...] = ()
    provides_extra: tuple[_NormalizedDistName, ...] = ()

    @field_validator("license_expression")
    @classmethod
    def _validate_license_expression(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(canonicalize_license_expression(value))

    @field_validator("requires_python")
    @classmethod
    def _validate_requires_python(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            specifiers = SpecifierSet(value)
        except InvalidSpecifier:
            specifiers = SpecifierSet(_insert_missing_specifier_separators(value))
        return str(specifiers)

    @field_validator("requires_dist")
    @classmethod
    def _validate_requires_dist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Each entry must parse as a PEP 508 requirement. `packaging`
        already upgrades the older PEP 345 parenthesized-version,
        `sys.platform`-marker style to modern syntax, so `str(Requirement(...))`
        is enough -- no separate old/new-style handling is needed.
        """
        return tuple(str(_parse_requirement(item)) for item in value)


def parse_metadata(metadata: str) -> WheelMetadata:
    """Parse a wheel's `.dist-info/METADATA` contents into a `WheelMetadata`.
    Only fields that reroll cares about are recorded
    """
    raw, _unparsed = parse_email(metadata)
    classifiers = raw.get("classifiers") or []
    return WheelMetadata.model_validate(
        {
            "name": raw.get("name") or "",
            "version": raw.get("version") or "",
            "license_expression": raw.get("license_expression"),
            "license": raw.get("license"),
            "license_classifiers": tuple(
                classifier
                for classifier in classifiers
                if classifier.startswith(_LICENSE_CLASSIFIER_PREFIX)
            ),
            "requires_python": raw.get("requires_python"),
            "requires_dist": tuple(raw.get("requires_dist") or ()),
            "provides_extra": tuple(raw.get("provides_extra") or ()),
        }
    )


_MISSING_SPECIFIER_SEPARATOR_RE = re.compile(r"(?<=[0-9*])(?=!=|===|==|>=|<=|~=|<|>)")


def _insert_missing_specifier_separators(value: str) -> str:
    """Repairs a `requires_python` value with no separator between adjacent
    clauses -- e.g. `!=3.4.*!=3.5.*` instead of `!=3.4.*, !=3.5.*`, seen in
    real, published wheel metadata -- by inserting a comma wherever one
    clause's version segment is immediately followed by another clause's
    operator.
    """
    return _MISSING_SPECIFIER_SEPARATOR_RE.sub(",", value)


_LEGACY_NAME_PREFIX_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*(?:\s*\[[^\]]*\])?)\s*")


def _split_legacy_parenthesized_version(value: str) -> tuple[str, str, str] | None:
    """Splits `value` into `(name_and_extras, specifier, rest)` if it starts
    with `<name>[extras] (<specifier>)<rest>` -- PEP 345's optional
    parenthesized-version shape. Paren depth is tracked (not just matched to
    the first `)`) so a stray nested paren in a malformed `specifier` can't
    be mistaken for the group's closing paren. Returns `None` if `value`
    isn't shaped like this at all, or its parens never balance back to 0.
    """
    match = _LEGACY_NAME_PREFIX_RE.match(value)
    if match is None:
        return None
    rest = value[match.end() :]
    if not rest.startswith("("):
        return None
    depth = 1
    for index in range(1, len(rest)):
        if rest[index] == "(":
            depth += 1
        elif rest[index] == ")":
            depth -= 1
            if depth == 0:
                return match["name"], rest[1:index], rest[index + 1 :]
    return None


def _parse_requirement(value: str) -> Requirement:
    """Parses a `requires_dist` entry, repairing one known-bad shape: some
    old wheel builders quote the version inside a PEP 345 parenthesized
    specifier -- e.g. `python-version (>='3.10')`, seen in real, published
    wheel metadata -- which PEP 440 doesn't allow, since version specifiers
    never contain quote characters. Only the parenthesized portion is
    touched, so a trailing marker's own quoted string literals (and its own
    grouping parens, if any) survive untouched.
    """
    try:
        return Requirement(value)
    except InvalidRequirement:
        split = _split_legacy_parenthesized_version(value)
        if split is None:
            raise
        name_and_extras, specifier, rest = split
        specifier = specifier.replace("'", "").replace('"', "")
        return Requirement(f"{name_and_extras}({specifier}){rest}")
