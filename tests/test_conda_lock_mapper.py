"""Unit tests for `reroll.conda_lock_mapper`."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from conda_lock.lookup import _get_pypi_lookup
from packaging.utils import canonicalize_name

import reroll.conda_lock_mapper as conda_lock_mapper_module
from reroll.conda_lock_mapper import (
    AMBIGUOUS_PROBABILITY,
    COLLIDING_PROBABILITY,
    STATIC_PROBABILITY,
    UNAMBIGUOUS_PROBABILITY,
    MappingEntry,
    PriorityEntry,
    _candidate_table,
    build_candidates,
    candidate_mapper,
    conda_lock_mapper,
)
from reroll.name_mapping import (
    Candidate,
    CandidateSource,
    UnresolvedCandidates,
    aggregator_mapper,
    map_name,
)


def _entry(
    pypi_name: str,
    conda_name: str,
    import_name: str,
    mapping_source: str = "regro-bot",
) -> MappingEntry:
    return {
        "pypi_name": pypi_name,
        "conda_name": conda_name,
        "import_name": import_name,
        "mapping_source": mapping_source,
    }


def _priority(import_name: str, *ranked_conda_names: str) -> PriorityEntry:
    return {"import_name": import_name, "ranked_conda_names": list(ranked_conda_names)}


def _other_candidate(conda_name: str = "tzdata") -> Candidate:
    return Candidate(
        conda_name=conda_name,
        probability=0.5,
        source=CandidateSource.OTHER,
        mapper="test",
    )


def _only(result: str | Sequence[Candidate]) -> Candidate:
    """Narrow a mapper result down to the single `Candidate` it contributed."""
    assert not isinstance(result, str)
    (candidate,) = result
    return candidate


@pytest.fixture(autouse=True)
def _clear_table_cache() -> Iterator[None]:
    """Keep memoized tables from leaking between tests.

    `_get_pypi_lookup` is conda-lock's own process-global cache, so it has to
    be cleared alongside ours.
    """
    _candidate_table.cache_clear()
    _get_pypi_lookup.cache_clear()
    yield
    _candidate_table.cache_clear()
    _get_pypi_lookup.cache_clear()


# --------------------------------------------------------------------------
# Probability tiers are ordered
# --------------------------------------------------------------------------


class TestProbabilityTiers:
    def test_static_is_certain(self) -> None:
        assert STATIC_PROBABILITY == 1.0

    def test_tiers_are_strictly_ordered(self) -> None:
        assert (
            STATIC_PROBABILITY
            > UNAMBIGUOUS_PROBABILITY
            > AMBIGUOUS_PROBABILITY
            > COLLIDING_PROBABILITY
        )

    def test_tiers_are_valid_probabilities(self) -> None:
        for probability in (
            STATIC_PROBABILITY,
            UNAMBIGUOUS_PROBABILITY,
            AMBIGUOUS_PROBABILITY,
            COLLIDING_PROBABILITY,
        ):
            assert 0.0 <= probability <= 1.0


# --------------------------------------------------------------------------
# `build_candidates`: the pure scoring logic
# --------------------------------------------------------------------------


class TestBuildCandidatesStatic:
    def test_static_entry_gets_certain_probability(self) -> None:
        candidates = build_candidates(
            mapping={"build": _entry("build", "python-build", "build", "static")},
            import_priority=[_priority("build", "python-build")],
            raw_mappings=[_entry("build", "python-build", "build", "static")],
        )
        assert candidates[canonicalize_name("build")].probability == STATIC_PROBABILITY

    def test_static_entry_beats_a_canonicalization_collision(self) -> None:
        # `graphviz` collides (py-graphviz vs python-graphviz) but a human
        # settled it in the static override table, so it stays certain.
        raw = [
            _entry("graphviz", "python-graphviz", "graphviz", "static"),
            _entry("graphviz", "py-graphviz", "graphviz"),
        ]
        candidates = build_candidates(
            mapping={"graphviz": _entry("graphviz", "python-graphviz", "graphviz", "static")},
            import_priority=[_priority("graphviz", "py-graphviz", "python-graphviz")],
            raw_mappings=raw,
        )
        assert candidates[canonicalize_name("graphviz")].probability == STATIC_PROBABILITY

    def test_unrecognized_mapping_source_is_treated_as_graph_derived(self) -> None:
        candidates = build_candidates(
            mapping={"annoy": _entry("annoy", "python-annoy", "annoy", "something-new")},
            import_priority=[_priority("annoy", "python-annoy")],
            raw_mappings=[_entry("annoy", "python-annoy", "annoy", "something-new")],
        )
        assert candidates[canonicalize_name("annoy")].probability == UNAMBIGUOUS_PROBABILITY


class TestBuildCandidatesAmbiguity:
    def test_single_ranked_conda_name_is_unambiguous(self) -> None:
        candidates = build_candidates(
            mapping={"annoy": _entry("annoy", "python-annoy", "annoy")},
            import_priority=[_priority("annoy", "python-annoy")],
            raw_mappings=[_entry("annoy", "python-annoy", "annoy")],
        )
        candidate = candidates[canonicalize_name("annoy")]
        assert candidate.conda_name == "python-annoy"
        assert candidate.probability == UNAMBIGUOUS_PROBABILITY

    def test_multiple_ranked_conda_names_lowers_probability(self) -> None:
        # A graph-centrality tie-break happened for import name `Levenshtein`:
        # the winner is chosen by HITS score, not by correctness.
        candidates = build_candidates(
            mapping={"levenshtein": _entry("levenshtein", "levenshtein", "Levenshtein")},
            import_priority=[_priority("Levenshtein", "levenshtein", "python-levenshtein")],
            raw_mappings=[_entry("levenshtein", "levenshtein", "Levenshtein")],
        )
        assert candidates[canonicalize_name("levenshtein")].probability == AMBIGUOUS_PROBABILITY

    def test_import_name_absent_from_priority_table_is_unambiguous(self) -> None:
        candidates = build_candidates(
            mapping={"annoy": _entry("annoy", "python-annoy", "annoy")},
            import_priority=[],
            raw_mappings=[_entry("annoy", "python-annoy", "annoy")],
        )
        assert candidates[canonicalize_name("annoy")].probability == UNAMBIGUOUS_PROBABILITY


class TestBuildCandidatesCollisions:
    def test_canonicalization_collision_lowers_probability(self) -> None:
        # `requests-cache` and `requests_cache` canonicalize to the same key
        # but name distinct conda packages, so the surviving entry is a coin
        # flip between two aliases.
        raw = [
            _entry("requests-cache", "requests-cache", "requests_cache"),
            _entry("requests_cache", "requests_cache", "requests_cache"),
        ]
        candidates = build_candidates(
            mapping={
                "requests-cache": _entry("requests-cache", "requests-cache", "requests_cache")
            },
            import_priority=[_priority("requests_cache", "requests-cache")],
            raw_mappings=raw,
        )
        assert candidates[canonicalize_name("requests-cache")].probability == COLLIDING_PROBABILITY

    def test_collision_beats_ambiguity(self) -> None:
        raw = [
            _entry("requests-cache", "requests-cache", "requests_cache"),
            _entry("requests_cache", "requests_cache", "requests_cache"),
        ]
        candidates = build_candidates(
            mapping={
                "requests-cache": _entry("requests-cache", "requests-cache", "requests_cache")
            },
            import_priority=[_priority("requests_cache", "requests-cache", "requests_cache")],
            raw_mappings=raw,
        )
        assert candidates[canonicalize_name("requests-cache")].probability == COLLIDING_PROBABILITY

    def test_repeated_spelling_naming_one_conda_package_is_not_a_collision(self) -> None:
        # Two raw rows, same canonical PyPI name, same conda name: nothing is
        # actually ambiguous.
        raw = [
            _entry("brent-search", "brent-search", "brent_search"),
            _entry("brent_search", "brent-search", "brent_search"),
        ]
        candidates = build_candidates(
            mapping={"brent-search": _entry("brent-search", "brent-search", "brent_search")},
            import_priority=[_priority("brent_search", "brent-search")],
            raw_mappings=raw,
        )
        assert candidates[canonicalize_name("brent-search")].probability == UNAMBIGUOUS_PROBABILITY


class TestBuildCandidatesDigestLikeNames:
    """Upstream records a content digest as the `pypi_name` when a recipe's
    source URL has no project-name path segment. Such rows are kept and scored
    like any other: they are unreachable (a lookup key comes from a real wheel
    filename), so guessing which names are "not real" would only risk dropping
    a legitimate mapping.
    """

    def test_digest_like_key_is_retained(self) -> None:
        digest = "0164e082b29777fbc56c2373b68da93d23a29a040787e7f1c65e1562f133"
        candidates = build_candidates(
            mapping={digest: _entry(digest, "django-jsoneditor", "jsoneditor")},
            import_priority=[],
            raw_mappings=[],
        )
        assert candidates[canonicalize_name(digest)].conda_name == "django-jsoneditor"

    def test_digest_like_rows_take_part_in_collision_detection(self) -> None:
        # No special-casing: if the same digest really did name two conda
        # packages, that is a collision like any other.
        digest = "a" * 40
        candidates = build_candidates(
            mapping={digest: _entry(digest, "one", "shared_import")},
            import_priority=[],
            raw_mappings=[
                _entry(digest, "one", "shared_import"),
                _entry(digest, "two", "shared_import"),
            ],
        )
        assert candidates[canonicalize_name(digest)].probability == COLLIDING_PROBABILITY

    def test_a_long_hex_name_is_not_treated_as_special(self) -> None:
        # A real project could legitimately be named only from hex characters;
        # it must score exactly as any other unambiguous entry would.
        candidates = build_candidates(
            mapping={"deadbeef" * 4: _entry("deadbeef" * 4, "python-deadbeef", "deadbeef")},
            import_priority=[_priority("deadbeef", "python-deadbeef")],
            raw_mappings=[_entry("deadbeef" * 4, "python-deadbeef", "deadbeef")],
        )
        candidate = candidates[canonicalize_name("deadbeef" * 4)]
        assert candidate.conda_name == "python-deadbeef"
        assert candidate.probability == UNAMBIGUOUS_PROBABILITY


class TestBuildCandidatesProvenance:
    def test_source_is_conda_lock(self) -> None:
        candidates = build_candidates(
            mapping={"annoy": _entry("annoy", "python-annoy", "annoy")},
            import_priority=[],
            raw_mappings=[],
        )
        assert candidates[canonicalize_name("annoy")].source is CandidateSource.CONDA_LOCK

    def test_mapper_name_is_recorded(self) -> None:
        candidates = build_candidates(
            mapping={"annoy": _entry("annoy", "python-annoy", "annoy")},
            import_priority=[],
            raw_mappings=[],
        )
        assert candidates[canonicalize_name("annoy")].mapper

    def test_keys_are_canonicalized(self) -> None:
        candidates = build_candidates(
            mapping={
                "Zope.Interface": _entry("Zope.Interface", "zope.interface", "zope.interface")
            },
            import_priority=[],
            raw_mappings=[],
        )
        assert set(candidates) == {canonicalize_name("zope-interface")}

    def test_conda_name_is_not_validated_against_cep_26(self) -> None:
        # Same contract as every other mapper: a malformed name in the source
        # table is contributed as an ordinary candidate, not rejected here.
        candidates = build_candidates(
            mapping={"badpkg": _entry("badpkg", "Bad--Name", "badpkg")},
            import_priority=[],
            raw_mappings=[],
        )
        assert candidates[canonicalize_name("badpkg")].conda_name == "Bad--Name"


# --------------------------------------------------------------------------
# `candidate_mapper`: turning a candidate table into a `NameMapper`
# --------------------------------------------------------------------------


def _table(conda_name: str = "python-annoy") -> dict[Any, Candidate]:
    return {
        canonicalize_name("annoy"): Candidate(
            conda_name=conda_name,
            probability=UNAMBIGUOUS_PROBABILITY,
            source=CandidateSource.CONDA_LOCK,
            mapper="test",
        )
    }


class TestCandidateMapperHit:
    def test_hit_contributes_its_candidate(self) -> None:
        mapper = candidate_mapper(_table())
        result = mapper(canonicalize_name("annoy"), ())
        assert result == (_table()[canonicalize_name("annoy")],)

    def test_hit_does_not_end_the_chain(self) -> None:
        mapper = candidate_mapper(_table())
        result = mapper(canonicalize_name("annoy"), ())
        assert not isinstance(result, str)

    def test_hit_appends_after_earlier_candidates(self) -> None:
        mapper = candidate_mapper(_table())
        earlier = _other_candidate()
        result = mapper(canonicalize_name("annoy"), (earlier,))
        assert list(result) == [earlier, _table()[canonicalize_name("annoy")]]


class TestCandidateMapperMiss:
    def test_miss_returns_candidates_unchanged(self) -> None:
        mapper = candidate_mapper(_table())
        earlier = (_other_candidate(),)
        assert mapper(canonicalize_name("nonexistent"), earlier) is earlier

    def test_miss_on_empty_candidates(self) -> None:
        mapper = candidate_mapper(_table())
        assert mapper(canonicalize_name("nonexistent"), ()) == ()


# --------------------------------------------------------------------------
# `conda_lock_mapper`: loading from local files (no network)
# --------------------------------------------------------------------------


@pytest.fixture
def local_tables(tmp_path: Path) -> tuple[Path, Path, Path]:
    mapping = tmp_path / "grayskull_pypi_mapping.json"
    priority = tmp_path / "import_name_priority_mapping.json"
    names = tmp_path / "name_mapping.json"
    mapping.write_text(
        json.dumps(
            {
                "annoy": _entry("annoy", "python-annoy", "annoy"),
                "build": _entry("build", "python-build", "build", "static"),
                "levenshtein": _entry("levenshtein", "levenshtein", "Levenshtein"),
            }
        )
    )
    priority.write_text(
        json.dumps(
            [
                _priority("annoy", "python-annoy"),
                _priority("build", "python-build"),
                _priority("Levenshtein", "levenshtein", "python-levenshtein"),
            ]
        )
    )
    names.write_text(
        json.dumps(
            [
                _entry("annoy", "python-annoy", "annoy"),
                _entry("build", "python-build", "build", "static"),
                _entry("levenshtein", "levenshtein", "Levenshtein"),
            ]
        )
    )
    return mapping, priority, names


class TestCondaLockMapperFromFiles:
    def test_static_entry_is_certain(self, local_tables: tuple[Path, Path, Path]) -> None:
        mapping, priority, names = local_tables
        mapper = conda_lock_mapper(
            mapping_url=mapping, priority_url=priority, name_mapping_url=names
        )
        candidate = _only(mapper(canonicalize_name("build"), ()))
        assert candidate.conda_name == "python-build"
        assert candidate.probability == STATIC_PROBABILITY

    def test_unambiguous_entry(self, local_tables: tuple[Path, Path, Path]) -> None:
        mapping, priority, names = local_tables
        mapper = conda_lock_mapper(
            mapping_url=mapping, priority_url=priority, name_mapping_url=names
        )
        candidate = _only(mapper(canonicalize_name("annoy"), ()))
        assert candidate.probability == UNAMBIGUOUS_PROBABILITY

    def test_ambiguous_entry(self, local_tables: tuple[Path, Path, Path]) -> None:
        mapping, priority, names = local_tables
        mapper = conda_lock_mapper(
            mapping_url=mapping, priority_url=priority, name_mapping_url=names
        )
        candidate = _only(mapper(canonicalize_name("levenshtein"), ()))
        assert candidate.probability == AMBIGUOUS_PROBABILITY

    def test_miss_passes_through(self, local_tables: tuple[Path, Path, Path]) -> None:
        mapping, priority, names = local_tables
        mapper = conda_lock_mapper(
            mapping_url=mapping, priority_url=priority, name_mapping_url=names
        )
        assert mapper(canonicalize_name("nonexistent"), ()) == ()

    def test_file_url_scheme_is_accepted(self, local_tables: tuple[Path, Path, Path]) -> None:
        mapping, priority, names = local_tables
        mapper = conda_lock_mapper(
            mapping_url=f"file://{mapping}",
            priority_url=f"file://{priority}",
            name_mapping_url=f"file://{names}",
        )
        candidate = _only(mapper(canonicalize_name("annoy"), ()))
        assert candidate.conda_name == "python-annoy"


class TestCondaLockMapperFromHttp:
    def test_http_urls_are_fetched_through_conda_locks_cache(
        self, local_tables: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mapping, priority, names = local_tables
        by_url = {
            "https://example.test/grayskull_pypi_mapping.json": mapping,
            "https://example.test/import_name_priority_mapping.json": priority,
            "https://example.test/name_mapping.json": names,
        }
        requested: list[str] = []

        def fake_download(url: str, *, cache_subdir_name: str) -> bytes:
            requested.append(url)
            return by_url[url].read_bytes()

        # The mapping is fetched from inside conda-lock's `_get_pypi_lookup`,
        # the sibling tables from this module, so both bindings are patched.
        monkeypatch.setattr("conda_lock.lookup.cached_download_file", fake_download)
        monkeypatch.setattr("reroll.conda_lock_mapper.cached_download_file", fake_download)
        mapper = conda_lock_mapper(
            mapping_url="https://example.test/grayskull_pypi_mapping.json",
            priority_url="https://example.test/import_name_priority_mapping.json",
            name_mapping_url="https://example.test/name_mapping.json",
        )
        assert _only(mapper(canonicalize_name("annoy"), ())).conda_name == ("python-annoy")
        assert sorted(requested) == sorted(by_url)


class TestCondaLockMapperCaching:
    def test_repeated_construction_reuses_the_parsed_tables(
        self, local_tables: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mapping, priority, names = local_tables
        real_read = conda_lock_mapper_module._read_sibling
        reads: list[str] = []

        def counting_read(source: Path | str) -> bytes:
            reads.append(str(source))
            return real_read(source)

        monkeypatch.setattr(conda_lock_mapper_module, "_read_sibling", counting_read)
        for _ in range(3):
            conda_lock_mapper(mapping_url=mapping, priority_url=priority, name_mapping_url=names)
        # Two sibling tables, read once between them -- not once per mapper.
        assert sorted(reads) == sorted([str(priority), str(names)])

    def test_same_sources_yield_the_same_table(self, local_tables: tuple[Path, Path, Path]) -> None:
        mapping, priority, names = local_tables
        args = (str(mapping), str(priority), str(names))
        assert _candidate_table(*args) is _candidate_table(*args)

    def test_different_urls_are_cached_separately(
        self, local_tables: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        mapping, priority, names = local_tables
        other = tmp_path / "other_mapping.json"
        other.write_text(json.dumps({"annoy": _entry("annoy", "annoy-but-different", "annoy")}))
        first = conda_lock_mapper(
            mapping_url=mapping, priority_url=priority, name_mapping_url=names
        )
        second = conda_lock_mapper(mapping_url=other, priority_url=priority, name_mapping_url=names)
        assert _only(first(canonicalize_name("annoy"), ())).conda_name == ("python-annoy")
        assert _only(second(canonicalize_name("annoy"), ())).conda_name == ("annoy-but-different")

    def test_yaml_mapping_table_is_accepted(self, tmp_path: Path) -> None:
        # conda-lock's loader dispatches on extension, so the upstream
        # `.yaml` flavour of the mapping works with no extra code here.
        mapping = tmp_path / "grayskull_pypi_mapping.yaml"
        mapping.write_text(
            "annoy:\n"
            "  pypi_name: annoy\n"
            "  conda_name: python-annoy\n"
            "  import_name: annoy\n"
            "  mapping_source: regro-bot\n"
        )
        priority = tmp_path / "import_name_priority_mapping.json"
        priority.write_text(json.dumps([_priority("annoy", "python-annoy")]))
        names = tmp_path / "name_mapping.json"
        names.write_text(json.dumps([_entry("annoy", "python-annoy", "annoy")]))
        mapper = conda_lock_mapper(
            mapping_url=mapping, priority_url=priority, name_mapping_url=names
        )
        assert _only(mapper(canonicalize_name("annoy"), ())).conda_name == ("python-annoy")


# --------------------------------------------------------------------------
# End to end through `map_name`
# --------------------------------------------------------------------------


class TestCondaLockMapperEndToEnd:
    def test_hit_plus_aggregator_still_needs_a_deciding_mapper(
        self, local_tables: tuple[Path, Path, Path]
    ) -> None:
        mapping, priority, names = local_tables
        mapper = conda_lock_mapper(
            mapping_url=mapping, priority_url=priority, name_mapping_url=names
        )
        with pytest.raises(UnresolvedCandidates) as excinfo:
            map_name("annoy", (mapper, aggregator_mapper))
        assert [c.conda_name for c in excinfo.value.candidates] == ["python-annoy"]

    def test_miss_plus_aggregator_falls_back_to_normalized_name(
        self, local_tables: tuple[Path, Path, Path]
    ) -> None:
        mapping, priority, names = local_tables
        mapper = conda_lock_mapper(
            mapping_url=mapping, priority_url=priority, name_mapping_url=names
        )
        assert map_name("No_Such.Pkg", (mapper, aggregator_mapper)) == "no-such-pkg"


# --------------------------------------------------------------------------
# Against the real published mapping (network)
# --------------------------------------------------------------------------


class TestCondaLockMapperDefaultTables:
    @pytest.mark.network
    def test_known_mappings_resolve(self) -> None:
        mapper = conda_lock_mapper()
        annoy = _only(mapper(canonicalize_name("annoy"), ()))
        assert annoy.conda_name == "python-annoy"
        # `tzdata` is the canonical Grayskull failure case; conda-forge-bot
        # pins it in the static override table.
        tzdata = _only(mapper(canonicalize_name("tzdata"), ()))
        assert tzdata.conda_name == "python-tzdata"
        assert tzdata.probability == STATIC_PROBABILITY

    @pytest.mark.network
    def test_nonexistent_name_misses(self) -> None:
        mapper = conda_lock_mapper()
        assert mapper(canonicalize_name("zpfqzvrj"), ()) == ()
