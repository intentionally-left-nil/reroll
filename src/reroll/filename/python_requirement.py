"""The Python version constraint a wheel tag implies."""

from __future__ import annotations

from packaging.specifiers import SpecifierSet
from pydantic import BaseModel, ConfigDict


class PythonRequirement(BaseModel):
    """The Python constraint a wheel tag implies.

    Only two shapes exist -- a floor or a pinned minor -- and no filename can
    express a patch version, so `minor: int` + `exact: bool` covers the
    entire domain without being able to represent an impossible state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    minor: int
    exact: bool

    @property
    def version(self) -> str:
        """`"3.13"` -- the only place `"3."` appears."""
        return f"3.{self.minor}"

    @property
    def specifier(self) -> SpecifierSet:
        """`"==3.13.*"` if pinned, else `">=3.13,<4"` -- the only place
        `"<4"` appears. A plain `@property`, not `@computed_field`:
        `SpecifierSet` has no pydantic core schema, so a `computed_field`
        return type would fail to generate one.
        """
        if self.exact:
            return SpecifierSet(f"=={self.version}.*")
        return SpecifierSet(f">={self.version},<4")

    @classmethod
    def floor(cls, minor: int) -> PythonRequirement:
        return cls(minor=minor, exact=False)

    @classmethod
    def pinned(cls, minor: int) -> PythonRequirement:
        return cls(minor=minor, exact=True)
