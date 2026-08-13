"""Shared conda MatchSpec string types for wheel dependency data.

See docs/matchspec.md.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator
from rattler import MatchSpec
from rattler.exceptions import InvalidMatchSpecError

from reroll.errors import UnconvertableRequirementError

_EXTRA_NAME_RE = re.compile(r"^[a-z0-9_.+-]{1,64}$")
"""CEP-29's `extras=[...]` bracket key grammar
(docs/matchspec.md#extras-name-normalization): 1-64 characters of
`[a-z0-9_.+-]`, with no restriction on the starting character.
"""


def validate_matchspec(value: str) -> str:
    """Return `value` unchanged if py-rattler accepts it as a conda
    MatchSpec (CEP-29).

    Raises `UnconvertableRequirementError` otherwise.
    """
    try:
        MatchSpec(value)
    except InvalidMatchSpecError as exc:
        raise UnconvertableRequirementError(f"{value!r} is not a valid matchspec") from exc
    return value


MatchSpecStr = Annotated[str, AfterValidator(validate_matchspec)]
"""A `str` guaranteed to parse as a conda MatchSpec (CEP-29)."""


def validate_extra_name(value: str) -> str:
    """Return `value` unchanged if it satisfies CEP-29's `extras=[...]`
    bracket key grammar.

    Raises `UnconvertableRequirementError` otherwise. Never mutates its
    input -- a caller wanting PyPI-side normalization first should apply
    `packaging.utils.canonicalize_name` before this check, same as
    `reroll.conda_package_name.validate_package_name` does not lowercase
    or otherwise repair a bad conda package name.
    """
    if not _EXTRA_NAME_RE.match(value):
        raise UnconvertableRequirementError(
            f"{value!r} is not a legal conda extra name (CEP-29): must be 1-64 characters "
            "of [a-z0-9_.+-]"
        )
    return value


CondaExtraName = Annotated[str, AfterValidator(validate_extra_name)]
"""A `str` guaranteed to satisfy CEP-29's `extras=[...]` bracket key grammar."""
