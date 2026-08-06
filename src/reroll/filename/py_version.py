"""Pydantic-compatible `packaging.version.Version` field type."""

from __future__ import annotations

from typing import Annotated, Any

from packaging.version import Version
from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema


def _coerce_version(value: Any) -> Version:
    return value if isinstance(value, Version) else Version(str(value))


class _PyVersionAnnotation:
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            _coerce_version,
            serialization=core_schema.to_string_ser_schema(),
        )


PyVersion = Annotated[Version, _PyVersionAnnotation]
"""`packaging.version.Version`, coercing from `str` and serializing back to
one. `arbitrary_types_allowed=True` is not an alternative here: it disables
coercion (a `str` input would be rejected outright) and would leak the raw
`Version` object into `model_dump()`."""
