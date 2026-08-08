"""Parse a wheel's `METADATA` file into the fields reroll needs."""

from __future__ import annotations

from typing import Annotated

from packaging.licenses import canonicalize_license_expression
from packaging.metadata import parse_email
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
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
        return str(SpecifierSet(value))

    @field_validator("requires_dist")
    @classmethod
    def _validate_requires_dist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Each entry must parse as a PEP 508 requirement. `packaging`
        already upgrades the older PEP 345 parenthesized-version,
        `sys.platform`-marker style to modern syntax, so `str(Requirement(...))`
        is enough -- no separate old/new-style handling is needed.
        """
        return tuple(str(Requirement(item)) for item in value)


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
