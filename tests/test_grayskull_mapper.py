"""Unit tests for `reroll.grayskull_mapper`."""

from __future__ import annotations

from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from reroll.grayskull_mapper import grayskull_mapper
from reroll.name_mapping import (
    Candidate,
    CandidateSource,
    UnresolvedCandidates,
    aggregator_mapper,
    map_name,
)

_FIXTURE_YAML = """\
annoy:
  conda_forge: python-annoy
  import_name: annoy

build:
  conda_forge: python-build
  import_name: build
"""

_MALFORMED_FIXTURE_YAML = """\
annoy:
  conda_forge: python-annoy
  import_name: annoy

badpkg:
  conda_forge: Bad--Name
  import_name: badpkg
"""


@pytest.fixture
def fixture_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(_FIXTURE_YAML)
    return config_file


@pytest.fixture
def malformed_fixture_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "malformed-config.yaml"
    config_file.write_text(_MALFORMED_FIXTURE_YAML)
    return config_file


def _other_candidate(conda_name: str = "tzdata") -> Candidate:
    return Candidate(
        conda_name=conda_name,
        probability=0.5,
        source=CandidateSource.OTHER,
        mapper="test",
    )


# --------------------------------------------------------------------------
# Hits
# --------------------------------------------------------------------------


class TestGrayskullMapperHit:
    def test_hit_contributes_a_candidate_with_probability_one(self, fixture_config: Path) -> None:
        mapper = grayskull_mapper(fixture_config)

        result = mapper(canonicalize_name("annoy"), SpecifierSet(""), ())

        assert result == (
            Candidate(
                conda_name="python-annoy",
                probability=1.0,
                source=CandidateSource.GRAYSKULL,
                mapper="grayskull_config",
            ),
        )

    def test_hit_never_returns_a_str_the_chain_is_not_stopped(self, fixture_config: Path) -> None:
        mapper = grayskull_mapper(fixture_config)

        result = mapper(canonicalize_name("annoy"), SpecifierSet(""), ())

        assert not isinstance(result, str)

    def test_hit_appends_to_candidates_from_earlier_mappers(self, fixture_config: Path) -> None:
        mapper = grayskull_mapper(fixture_config)
        earlier = (_other_candidate(),)

        result = mapper(canonicalize_name("annoy"), SpecifierSet(""), earlier)

        assert result == (
            earlier[0],
            Candidate(
                conda_name="python-annoy",
                probability=1.0,
                source=CandidateSource.GRAYSKULL,
                mapper="grayskull_config",
            ),
        )

    def test_specifier_is_ignored(self, fixture_config: Path) -> None:
        mapper = grayskull_mapper(fixture_config)

        with_low = mapper(canonicalize_name("annoy"), SpecifierSet("==1.0"), ())
        with_high = mapper(canonicalize_name("annoy"), SpecifierSet(">=2.0"), ())

        assert with_low == with_high


# --------------------------------------------------------------------------
# Misses
# --------------------------------------------------------------------------


class TestGrayskullMapperMiss:
    def test_miss_returns_the_input_candidates_object_unchanged(self, fixture_config: Path) -> None:
        mapper = grayskull_mapper(fixture_config)
        candidates = (_other_candidate(),)

        result = mapper(canonicalize_name("requests"), SpecifierSet(""), candidates)

        assert result is candidates

    def test_miss_on_empty_candidates_returns_empty(self, fixture_config: Path) -> None:
        mapper = grayskull_mapper(fixture_config)

        result = mapper(canonicalize_name("requests"), SpecifierSet(""), ())

        assert result == ()


# --------------------------------------------------------------------------
# Malformed entries -- CEP 26 is not checked here (see `reroll.name_mapping`)
# --------------------------------------------------------------------------


class TestGrayskullMapperMalformedEntry:
    def test_construction_does_not_raise(self, malformed_fixture_config: Path) -> None:
        grayskull_mapper(malformed_fixture_config)

    def test_malformed_entry_is_still_contributed_as_a_candidate(
        self, malformed_fixture_config: Path
    ) -> None:
        mapper = grayskull_mapper(malformed_fixture_config)

        result = mapper(canonicalize_name("badpkg"), SpecifierSet(""), ())

        assert result == (
            Candidate(
                conda_name="Bad--Name",
                probability=1.0,
                source=CandidateSource.GRAYSKULL,
                mapper="grayskull_config",
            ),
        )

    def test_other_entries_in_the_same_table_still_resolve(
        self, malformed_fixture_config: Path
    ) -> None:
        mapper = grayskull_mapper(malformed_fixture_config)

        result = mapper(canonicalize_name("annoy"), SpecifierSet(""), ())

        assert result == (
            Candidate(
                conda_name="python-annoy",
                probability=1.0,
                source=CandidateSource.GRAYSKULL,
                mapper="grayskull_config",
            ),
        )


# --------------------------------------------------------------------------
# End to end, through `map_name`
# --------------------------------------------------------------------------


class TestGrayskullMapperEndToEnd:
    def test_hit_followed_by_aggregator_leaves_candidates_unresolved(
        self, fixture_config: Path
    ) -> None:
        """`aggregator_mapper` doesn't collapse multiple candidates to one
        name, so `map_name` raises `UnresolvedCandidates` here.
        """
        mapper = grayskull_mapper(fixture_config)

        with pytest.raises(UnresolvedCandidates) as exc_info:
            map_name("annoy", SpecifierSet(""), (mapper, aggregator_mapper))

        (candidate,) = exc_info.value.candidates
        assert candidate.conda_name == "python-annoy"
        assert candidate.probability == 1.0
        assert candidate.source is CandidateSource.GRAYSKULL

    def test_miss_followed_by_aggregator_falls_back_to_the_normalized_name(
        self, fixture_config: Path
    ) -> None:
        mapper = grayskull_mapper(fixture_config)

        result = map_name("requests", SpecifierSet(""), (mapper, aggregator_mapper))

        assert result == "requests"


# --------------------------------------------------------------------------
# Default config -- the file shipped inside the grayskull wheel
# --------------------------------------------------------------------------


class TestGrayskullMapperDefaultConfig:
    def test_default_config_is_loaded_from_the_installed_grayskull_package(self) -> None:
        mapper = grayskull_mapper()

        result = mapper(canonicalize_name("annoy"), SpecifierSet(""), ())

        assert result == (
            Candidate(
                conda_name="python-annoy",
                probability=1.0,
                source=CandidateSource.GRAYSKULL,
                mapper="grayskull_config",
            ),
        )

    def test_default_config_miss_for_a_name_absent_from_grayskulls_table(self) -> None:
        mapper = grayskull_mapper()
        name = canonicalize_name("this-package-does-not-exist-anywhere")

        result = mapper(name, SpecifierSet(""), ())

        assert result == ()
