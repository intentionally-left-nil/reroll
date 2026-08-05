"""Pluggable PyPI -> conda name mapping.

Background research (why mapping is hard, what the ecosystem tools do, how
Parselmouth's data is shaped) lives in `docs/pypi_conda_mapping.md`. This
module implements the resolution machinery only -- CEP 26 validation of the
*output* lives in `reroll.conda_package_name`, and Parselmouth support of
any kind is out of scope here (see `specs/name_mapping.md`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name
from packaging.version import Version
from pydantic import RootModel, field_validator

from reroll.conda_package_name import CondaPackageName

__all__ = [
    "AmbiguousCondaName",
    "NameMapper",
    "exact_version",
    "map_name",
    "static_mapper",
]

NameMapper = Callable[[NormalizedName, SpecifierSet], str | None]
"""A callable taking a canonicalized PyPI name and a version specifier,
returning the conda name it maps to, or `None` to mean "I have no opinion,
ask the next mapper". A plain `Callable` alias -- not a `Protocol` -- so a
function, `lambda`, closure, `functools.partial`, bound method, or a
stateful class instance with `__call__` all satisfy it with no registration
and no base class.

A mapper owns its own disambiguation policy: it may return any single `str`
it likes, or raise `AmbiguousCondaName` if it recognizes the package but
cannot produce one answer.
"""


class AmbiguousCondaName(Exception):
    """Raised by a mapper that recognizes `name` but cannot produce a
    single conda name for it, for either reason:

    - Multiple conda candidates exist for one `(name, specifier)` pair.
    - The conda name is not constant across the specifier's range.

    Raising this aborts the whole chain in `map_name`: later mappers are
    not consulted, because a dumber mapper must not be allowed to paper
    over a known-ambiguous name with a confident wrong guess.
    """

    def __init__(
        self,
        name: str,
        specifier: SpecifierSet,
        candidates: Iterable[str] = (),
    ) -> None:
        self.name = name
        self.specifier = specifier
        self.candidates = tuple(candidates)
        super().__init__(
            f"ambiguous conda name for {name!r} (specifier={specifier!s}): "
            f"candidates={self.candidates!r}"
        )


def exact_version(version: Version) -> SpecifierSet:
    """`SpecifierSet(f"=={version}")` -- the only place that construction
    appears, so the edge cases (epochs, local versions, dev/post/rc
    segments) are pinned once.
    """
    return SpecifierSet(f"=={version}")


def map_name(name: str, specifier: SpecifierSet, mappers: Sequence[NameMapper]) -> str:
    """Resolve `name` to its conda equivalent by trying each of `mappers`
    in order and returning the first non-`None` result verbatim.

    `name` is canonicalized before any mapper sees it, so every mapper
    receives an identical, already-normalized name and none of them needs
    to re-implement normalization. If every mapper returns `None`
    (including an empty `mappers` sequence), the canonicalized PyPI name is
    returned as the fallback.

    The returned `str` is **not** guaranteed to satisfy CEP 26 -- that
    validation is the pydantic layer's job. This function raises nothing
    itself: `AmbiguousCondaName` and any other mapper exception propagate
    untouched.
    """
    normalized = canonicalize_name(name)
    for mapper in mappers:
        result = mapper(normalized, specifier)
        if result is not None:
            return result
    return normalized


def static_mapper(table: Mapping[str, str]) -> NameMapper:
    """Build a `NameMapper` from a literal table -- the grayskull-style
    exception list that is the first thing most users will need.

    The table is validated at construction time (every entry, not just the
    first bad one, is reported in one `ValidationError`). The specifier is
    ignored: a static table is version-independent by construction, so this
    mapper never raises `AmbiguousCondaName`.
    """
    validated = _StaticTable.model_validate(table).root

    def _lookup(name: NormalizedName, specifier: SpecifierSet) -> str | None:
        del specifier
        return validated.get(name)

    return _lookup


class _StaticTable(RootModel[dict[NormalizedName, CondaPackageName]]):
    """Validates a `static_mapper` table at construction: keys are PEP 503
    canonicalized and values must satisfy CEP 26. A `RootModel` (rather
    than a loop calling `validate_package_name`) reports every bad entry at
    once with its key in `loc`, instead of stopping at the first.

    Key canonicalization runs as a plain `field_validator` on `root`,
    `mode="before"`, rather than through an `Annotated` key type: the only
    exported name callers need for "a canonicalized PyPI name" is
    `packaging.utils.NormalizedName` itself, so this module does not mint a
    second, confusingly similar type just to carry that one validator.
    """

    @field_validator("root", mode="before")
    @classmethod
    def _canonicalize_keys(cls, value: Mapping[str, str]) -> dict[str, str]:
        return {canonicalize_name(key): entry for key, entry in value.items()}
