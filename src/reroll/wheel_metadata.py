"""Parse a wheel's `METADATA` file into the fields reroll needs."""

from __future__ import annotations

import logging
from typing import Annotated

from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression
from packaging.metadata import RawMetadata, parse_email
from packaging.utils import InvalidName, canonicalize_name
from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, field_validator

from reroll.errors import InvalidMetadataError
from reroll.filename.py_version import PyVersion
from reroll.filename.python_requirement import minor_range
from reroll.lenient_parser import parse_lenient_requirement, parse_lenient_version_specifiers

_LICENSE_CLASSIFIER_PREFIX = "License :: "

_logger = logging.getLogger(__name__)


def _normalize_dist_name(value: str) -> str:
    """PEP 503 normalization, and a hard rejection of PEP 345-style names:
    `canonicalize_name(validate=True)` only accepts the modern (PEP 508)
    name grammar.
    """
    try:
        return canonicalize_name(value, validate=True)
    except InvalidName as exc:
        raise InvalidMetadataError(f"invalid Name {value!r}: {exc}") from exc


_NormalizedDistName = Annotated[str, AfterValidator(_normalize_dist_name)]
"""A `str` PyPI distribution name, canonicalized per PEP 503 and rejected
if it doesn't also match the modern (PEP 508) name grammar.

Deliberately a plain `str` annotated with a validator -- not
`packaging.utils.NormalizedName` -- so the field's static type matches what
it actually accepts as input.
"""

_NormalizedExtraName = Annotated[str, AfterValidator(canonicalize_name)]
"""A `str` PyPI extra name, canonicalized per PEP 503 *without* the PEP 508
grammar check `_NormalizedDistName` applies -- per `docs/wheel_metadata.md`,
`Provides-Extra` is deliberately more lenient than `Name`, so this can never
fail to validate.
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
    provides_extra: tuple[_NormalizedExtraName, ...] = ()

    @field_validator("license_expression")
    @classmethod
    def _validate_license_expression(cls, value: str | None) -> str | None:
        """`None` for both an absent header and one that fails to parse as
        a valid SPDX expression -- unlike most fields, per
        `docs/wheel_metadata.md`. Publishing tools and PyPI are supposed to
        reject an invalid `License-Expression` at upload time (PEP 639),
        but pip/uv never parse this field, so a bad value has no bearing
        on whether the wheel installs.
        """
        if value is None:
            return None
        try:
            return str(canonicalize_license_expression(value))
        except InvalidLicenseExpression:
            _logger.debug("Dropping invalid License-Expression %r", value)
            return None

    @field_validator("requires_python")
    @classmethod
    def _validate_requires_python(cls, value: str | None) -> str | None:
        """`None` is passed through unchanged; otherwise the value must
        parse as a PEP 440 specifier set (leniently, like `requires_dist`)
        *and* imply a contiguous Python 3 minor range (`minor_range`) --
        `reroll.dependencies` intersects this against a wheel's
        filename-implied range, which only a single contiguous range
        supports.
        """
        if value is None:
            return None
        specifiers = parse_lenient_version_specifiers(value)
        minor_range(specifiers)
        return str(specifiers)

    @field_validator("requires_dist")
    @classmethod
    def _validate_requires_dist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Each entry must parse as a PEP 508 requirement, trying
        `packaging.requirements.Requirement` first and falling back to
        uv's `LenientRequirement` fixups on failure -- see
        `reroll.lenient_parser`.
        """
        return tuple(str(parse_lenient_requirement(item)) for item in value)


def parse_metadata(metadata: str) -> WheelMetadata:
    """Parse a wheel's `.dist-info/METADATA` contents into a `WheelMetadata`.
    Only fields that reroll cares about are recorded.

    Raises `InvalidMetadataError` for a missing or malformed `name`/
    `version` (or any other field that fails its own validation), in
    addition to the header-level failures `_single_value_field` already
    raises.
    """
    raw, unparsed = parse_email(metadata)
    classifiers = raw.get("classifiers") or []
    try:
        return WheelMetadata.model_validate(
            {
                "name": _single_value_field(raw, unparsed, "name", "name"),
                "version": _single_value_field(raw, unparsed, "version", "version"),
                "license_expression": _single_value_field(
                    raw, unparsed, "license_expression", "license-expression"
                ),
                "license": _single_value_field(raw, unparsed, "license", "license"),
                "license_classifiers": tuple(
                    classifier
                    for classifier in classifiers
                    if classifier.startswith(_LICENSE_CLASSIFIER_PREFIX)
                ),
                "requires_python": _single_value_field(
                    raw, unparsed, "requires_python", "requires-python"
                ),
                "requires_dist": tuple(raw.get("requires_dist") or ()),
                "provides_extra": tuple(raw.get("provides_extra") or ()),
            }
        )
    except ValidationError as exc:
        raise InvalidMetadataError(f"invalid METADATA: {exc}") from exc


def _single_value_field(
    raw: RawMetadata, unparsed: dict[str, list[str]], raw_name: str, email_name: str
) -> str | None:
    """The METADATA value for a header that's supposed to appear at most
    once. Returns the value itself if `parse_email` parsed it; `None` if
    the header is entirely absent; the shared value if the header repeats
    with every occurrence byte-identical (not actually ambiguous -- real
    wheels do this, e.g. the OZI build backend emitting `Name` twice).

    Raises `InvalidMetadataError` otherwise -- repeated with differing
    values, or undecodable. `parse_email` routes a single undecodable
    occurrence to `unparsed` as a length-1 list, so that case always
    raises here too: there's nothing to compare it against.
    """
    value = raw.get(raw_name)
    if value is not None:
        return value
    duplicates = unparsed.get(email_name)
    if duplicates is not None and len(duplicates) > 1 and len(set(duplicates)) == 1:
        return duplicates[0]
    if email_name in unparsed:
        raise InvalidMetadataError(
            f"{email_name!r} header is repeated with disagreeing values, or undecodable: "
            f"{unparsed[email_name]!r}"
        )
    return None
