"""Unit tests for `reroll.overrides_mapper`."""

from __future__ import annotations

import pytest
from packaging.utils import canonicalize_name

from reroll.name_mapping import Candidate, CandidateSource, map_name
from reroll.overrides_mapper import overrides_mapper

_TABLE = {
    "onnxruntime": "onnxruntime",
    "onnxruntime-gpu": "onnxruntime",
    "modal": "modal-client",
    "pyqtwebengine": "pyqtwebengine",
    "dspy": "dspy",
    "scikit-learn-intelex": "scikit-learn-intelex",
    "mdahole2": "mdahole2",
    "mathicsscript": "mathics3-frontend-cli",
    "pylibiio": "pylibiio",
    "pyplaid": "plaid",
    "simhash-py": "simhash-py",
    "grip-nulling": "grip-nulling",
    "functools32": "functools32",
    "pyqtchart": "pyqtchart",
}


def _other_candidate() -> Candidate:
    return Candidate(
        conda_name="tzdata",
        probability=0.5,
        source=CandidateSource.OTHER,
        mapper="test",
    )


# --------------------------------------------------------------------------
# Hits
# --------------------------------------------------------------------------


class TestOverridesMapperHit:
    @pytest.mark.parametrize(("pypi_name", "conda_name"), sorted(_TABLE.items()))
    def test_hit_returns_the_hardcoded_conda_name_directly(
        self, pypi_name: str, conda_name: str
    ) -> None:
        mapper = overrides_mapper()

        result = mapper(canonicalize_name(pypi_name), ())

        assert result == conda_name

    def test_hit_ends_the_chain_the_result_is_a_str(self) -> None:
        mapper = overrides_mapper()

        result = mapper(canonicalize_name("onnxruntime-gpu"), ())

        assert isinstance(result, str)

    def test_hit_ignores_candidates_from_earlier_mappers(self) -> None:
        mapper = overrides_mapper()
        earlier = (_other_candidate(),)

        result = mapper(canonicalize_name("modal"), earlier)

        assert result == "modal-client"


# --------------------------------------------------------------------------
# Misses
# --------------------------------------------------------------------------


class TestOverridesMapperMiss:
    def test_miss_returns_the_input_candidates_object_unchanged(self) -> None:
        mapper = overrides_mapper()
        candidates = (_other_candidate(),)

        result = mapper(canonicalize_name("requests"), candidates)

        assert result is candidates

    def test_miss_on_empty_candidates_returns_empty(self) -> None:
        mapper = overrides_mapper()

        result = mapper(canonicalize_name("requests"), ())

        assert result == ()


# --------------------------------------------------------------------------
# End to end, through `map_name`
# --------------------------------------------------------------------------


class TestOverridesMapperEndToEnd:
    def test_hit_resolves_without_needing_a_further_mapper(self) -> None:
        mapper = overrides_mapper()

        result = map_name("onnxruntime-gpu", (mapper,))

        assert result == "onnxruntime"
