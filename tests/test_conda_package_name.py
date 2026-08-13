"""Unit tests for `reroll.conda_package_name`."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from reroll.conda_package_name import CondaPackageName, validate_package_name
from reroll.errors import InvalidCondaNameError

# --------------------------------------------------------------------------
# `validate_package_name` -- direct
# --------------------------------------------------------------------------


class TestValidatePackageNameAccepts:
    @pytest.mark.parametrize(
        "value",
        [
            "python",
            "ruamel.yaml",
            "_libgcc_mutex",
            "numpy-base",
            "zope-interface",
            "a",
            "0",
            "1foo",
        ],
    )
    def test_accepts_real_conda_names(self, value: str) -> None:
        assert validate_package_name(value) == value

    @pytest.mark.parametrize("value", ["foo_", "foo-", "foo."])
    def test_accepts_trailing_separators(self, value: str) -> None:
        """CEP 26's regex matches a trailing separator even though the
        prose reads as if it should not. The regex, not the prose, is the
        normative artifact -- this quirk is pinned deliberately and must
        not be "fixed" later.
        """
        assert validate_package_name(value) == value

    def test_accepts_64_characters(self) -> None:
        value = "a" * 64
        assert validate_package_name(value) == value


class TestValidatePackageNameRejects:
    @pytest.mark.parametrize(
        "value",
        [
            "Requests",  # uppercase
            "foo--bar",  # consecutive separators
            "a..b",  # consecutive separators
            "foo-_bar",  # consecutive separators
            "-foo",  # leading separator
            ".foo",  # leading separator
            "__cuda",  # virtual-package form
            "",  # empty string
        ],
    )
    def test_rejects_invalid_names(self, value: str) -> None:
        with pytest.raises(InvalidCondaNameError, match="CEP 26"):
            validate_package_name(value)

    def test_rejects_65_characters(self) -> None:
        with pytest.raises(InvalidCondaNameError, match="65"):
            validate_package_name("a" * 65)

    def test_error_names_the_offending_value(self) -> None:
        with pytest.raises(InvalidCondaNameError, match="Requests"):
            validate_package_name("Requests")

    def test_does_not_mutate_input(self) -> None:
        """The validator never repairs a value -- lowercasing or otherwise
        fixing a bad name would hide the caller's bug.
        """
        with pytest.raises(InvalidCondaNameError, match="CEP 26"):
            validate_package_name("Requests")


# --------------------------------------------------------------------------
# `CondaPackageName` -- through a pydantic model
# --------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CondaPackageName


class TestCondaPackageNameOnModel:
    def test_accepts_valid_name(self) -> None:
        assert _Model(name="numpy-base").name == "numpy-base"

    def test_rejects_invalid_name(self) -> None:
        with pytest.raises(InvalidCondaNameError):
            _Model(name="Requests")

    def test_rejects_over_length_name(self) -> None:
        with pytest.raises(InvalidCondaNameError):
            _Model(name="a" * 65)

    def test_error_names_the_offending_value(self) -> None:
        with pytest.raises(InvalidCondaNameError, match="Requests"):
            _Model(name="Requests")
