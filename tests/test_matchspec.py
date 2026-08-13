"""Unit tests for `reroll.matchspec`."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from reroll.errors import UnconvertableRequirementError
from reroll.matchspec import CondaExtraName, MatchSpecStr, validate_extra_name, validate_matchspec

# --------------------------------------------------------------------------
# `validate_matchspec` -- direct
# --------------------------------------------------------------------------


class TestValidateMatchspecAccepts:
    @pytest.mark.parametrize(
        "value",
        [
            "python",
            "python >=3.9",
            "requests >=2.20,<3",
            'fastapi[extras=[all],when="__win"]',
            "__linux",
        ],
    )
    def test_accepts_valid_matchspecs(self, value: str) -> None:
        assert validate_matchspec(value) == value


class TestValidateMatchspecRejects:
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "python >=1.0,<",
            "fastapi[bogus_key=1]",
        ],
    )
    def test_rejects_invalid_matchspecs(self, value: str) -> None:
        with pytest.raises(UnconvertableRequirementError, match="not a valid matchspec"):
            validate_matchspec(value)

    def test_rejects_a_bare_bracketed_extra_without_the_extras_key(self) -> None:
        """docs/matchspec.md#requiring-a-dependency-with-an-extra: matchspec
        does not accept `fastapi[all]` on its own -- the `[]` bracket
        notation is reserved for `key=value` pairs (like `extras=[all]`),
        so an extra name must always go through that key.
        """
        with pytest.raises(UnconvertableRequirementError, match="not a valid matchspec"):
            validate_matchspec("fastapi[all]")

    def test_error_names_the_offending_value(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="bogus_key"):
            validate_matchspec("fastapi[bogus_key=1]")


# --------------------------------------------------------------------------
# `MatchSpecStr` -- through a pydantic model
# --------------------------------------------------------------------------


class _MatchSpecModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    spec: MatchSpecStr


class TestMatchSpecStrOnModel:
    def test_accepts_valid_matchspec(self) -> None:
        assert _MatchSpecModel(spec="python >=3.9").spec == "python >=3.9"

    def test_rejects_invalid_matchspec(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            _MatchSpecModel(spec="python >=1.0,<")


# --------------------------------------------------------------------------
# `validate_extra_name` -- direct
# --------------------------------------------------------------------------


class TestValidateExtraNameAccepts:
    @pytest.mark.parametrize(
        "value",
        [
            "standard",
            "all",
            "some-extra-name",
            "some_extra_name",
            "a",
            "0",
        ],
    )
    def test_accepts_valid_extra_names(self, value: str) -> None:
        assert validate_extra_name(value) == value

    def test_accepts_64_characters(self) -> None:
        value = "a" * 64
        assert validate_extra_name(value) == value


class TestValidateExtraNameRejects:
    @pytest.mark.parametrize(
        "value",
        [
            "Standard",  # uppercase
            "some extra name",  # space
            "some/extra",  # slash
            "",  # empty string
        ],
    )
    def test_rejects_invalid_extra_names(self, value: str) -> None:
        with pytest.raises(UnconvertableRequirementError, match="CEP-29"):
            validate_extra_name(value)

    def test_rejects_65_characters(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="CEP-29"):
            validate_extra_name("a" * 65)

    def test_error_names_the_offending_value(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="Standard"):
            validate_extra_name("Standard")

    def test_does_not_mutate_input(self) -> None:
        """The validator never repairs a value -- lowercasing or otherwise
        normalizing a bad name would hide the caller's bug. Callers wanting
        PyPI-side normalization apply `canonicalize_name` first.
        """
        with pytest.raises(UnconvertableRequirementError, match="CEP-29"):
            validate_extra_name("Standard")


# --------------------------------------------------------------------------
# `CondaExtraName` -- through a pydantic model
# --------------------------------------------------------------------------


class _ExtraNameModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    extra: CondaExtraName


class TestCondaExtraNameOnModel:
    def test_accepts_valid_name(self) -> None:
        assert _ExtraNameModel(extra="standard").extra == "standard"

    def test_rejects_invalid_name(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            _ExtraNameModel(extra="Standard")

    def test_rejects_over_length_name(self) -> None:
        with pytest.raises(UnconvertableRequirementError):
            _ExtraNameModel(extra="a" * 65)

    def test_error_names_the_offending_value(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="Standard"):
            _ExtraNameModel(extra="Standard")
