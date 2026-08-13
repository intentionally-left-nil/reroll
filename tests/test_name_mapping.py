"""Unit tests for `reroll.name_mapping`."""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import cast

import pytest
from packaging.utils import NormalizedName, canonicalize_name
from pydantic import ValidationError

from reroll.errors import UnresolvedCondaNameError
from reroll.name_mapping import (
    Candidate,
    CandidateSource,
    NameMapper,
    NameMappers,
    aggregator_mapper,
    map_name,
    static_mapper,
)

# --------------------------------------------------------------------------
# `CandidateSource`
# --------------------------------------------------------------------------


class TestCandidateSource:
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (CandidateSource.PARSELMOUTH, "parselmouth"),
            (CandidateSource.GRAYSKULL, "grayskull"),
            (CandidateSource.CONDA_LOCK, "conda-lock"),
            (CandidateSource.OTHER, "other"),
        ],
    )
    def test_fixed_values(self, member: CandidateSource, value: str) -> None:
        assert member.value == value


# --------------------------------------------------------------------------
# `Candidate`
# --------------------------------------------------------------------------


class TestCandidate:
    def test_construction(self) -> None:
        candidate = Candidate(
            conda_name="python-tzdata",
            probability=0.9,
            source=CandidateSource.PARSELMOUTH,
            mapper="parselmouth_lookup",
        )

        assert candidate.conda_name == "python-tzdata"
        assert candidate.probability == 0.9
        assert candidate.source is CandidateSource.PARSELMOUTH
        assert candidate.mapper == "parselmouth_lookup"

    @pytest.mark.parametrize("probability", [0.0, 0.5, 1.0])
    def test_probability_bounds_are_inclusive(self, probability: float) -> None:
        candidate = Candidate(
            conda_name="tzdata",
            probability=probability,
            source=CandidateSource.OTHER,
            mapper="test",
        )

        assert candidate.probability == probability

    @pytest.mark.parametrize("probability", [-0.01, 1.01, -1.0, 2.0])
    def test_probability_out_of_bounds_rejected(self, probability: float) -> None:
        with pytest.raises(ValidationError):
            Candidate(
                conda_name="tzdata",
                probability=probability,
                source=CandidateSource.OTHER,
                mapper="test",
            )

    def test_source_rejects_a_value_outside_the_fixed_set(self) -> None:
        """`source` is restricted to `parselmouth`/`grayskull`/`conda-lock`/
        `other` -- any other string is rejected, not silently accepted as a
        fifth, ad-hoc source.
        """
        with pytest.raises(ValidationError):
            Candidate(
                conda_name="tzdata",
                probability=0.5,
                source=cast(CandidateSource, "pip"),
                mapper="test",
            )

    def test_mapper_distinguishes_two_candidates_sharing_one_source(self) -> None:
        """`mapper` is independent of `source`: two candidates can share a
        `source` while naming different contributing mappers, so a logic
        mapper can tell them apart even though it can't tell them apart by
        `source` alone.
        """
        first = Candidate(
            conda_name="tzdata",
            probability=0.6,
            source=CandidateSource.PARSELMOUTH,
            mapper="parselmouth_exact_version",
        )
        second = Candidate(
            conda_name="tzdata",
            probability=0.3,
            source=CandidateSource.PARSELMOUTH,
            mapper="parselmouth_fuzzy_match",
        )

        assert first.source == second.source
        assert first.mapper != second.mapper

    def test_conda_name_is_not_validated_against_cep_26(self) -> None:
        """CEP 26 validation is deferred entirely to whatever consumes the
        final chosen name (e.g. `WheelConfig` in `reroll.filename`) -- an
        intermediate candidate is just one mapper's still-unproven guess,
        so it is not required to already be a legal name.
        """
        candidate = Candidate(
            conda_name="Bad--Name",
            probability=0.5,
            source=CandidateSource.OTHER,
            mapper="test",
        )

        assert candidate.conda_name == "Bad--Name"

    def test_frozen(self) -> None:
        candidate = Candidate(
            conda_name="tzdata",
            probability=0.5,
            source=CandidateSource.OTHER,
            mapper="test",
        )

        attr = "probability"
        with pytest.raises(ValidationError):
            setattr(candidate, attr, 0.9)


# --------------------------------------------------------------------------
# Callable-shape acceptance for `NameMapper`
# --------------------------------------------------------------------------


def _function_mapper(
    name: NormalizedName,
    candidates: Sequence[Candidate],
) -> str | Sequence[Candidate]:
    del candidates
    return f"conda-{name}"


class _StatefulMapper:
    """A stateful class instance with `__call__` that counts hits and is
    reused across calls. Always resolves to a fixed `str` result.
    """

    def __init__(self, result: str) -> None:
        self.result = result
        self.hits = 0

    def __call__(
        self,
        name: NormalizedName,
        candidates: Sequence[Candidate],
    ) -> str | Sequence[Candidate]:
        del name, candidates
        self.hits += 1
        return self.result


class _BoundMethodOwner:
    def lookup(
        self,
        name: NormalizedName,
        candidates: Sequence[Candidate],
    ) -> str | Sequence[Candidate]:
        del candidates
        return f"bound-{name}"


class TestCallableShapes:
    def test_function(self) -> None:
        assert map_name("Requests", (_function_mapper,)) == "conda-requests"

    def test_lambda(self) -> None:
        mapper: NameMapper = lambda name, candidates: f"lambda-{name}"  # noqa: E731
        assert map_name("Requests", (mapper,)) == "lambda-requests"

    def test_closure(self) -> None:
        def make_mapper(prefix: str) -> NameMapper:
            def _mapper(
                name: NormalizedName,
                candidates: Sequence[Candidate],
            ) -> str | Sequence[Candidate]:
                del candidates
                return f"{prefix}-{name}"

            return _mapper

        mapper = make_mapper("closure")
        assert map_name("Requests", (mapper,)) == "closure-requests"

    def test_functools_partial(self) -> None:
        def _mapper(
            prefix: str,
            name: NormalizedName,
            candidates: Sequence[Candidate],
        ) -> str | Sequence[Candidate]:
            del candidates
            return f"{prefix}-{name}"

        mapper = functools.partial(_mapper, "partial")
        assert map_name("Requests", (mapper,)) == "partial-requests"

    def test_bound_method(self) -> None:
        owner = _BoundMethodOwner()
        assert map_name("Requests", (owner.lookup,)) == "bound-requests"

    def test_stateful_instance_counts_hits_and_is_reused(self) -> None:
        mapper = _StatefulMapper("stateful-result")

        first = map_name("Requests", (mapper,))
        second = map_name("Other", (mapper,))

        assert first == "stateful-result"
        assert second == "stateful-result"
        assert mapper.hits == 2


def _different_parameter_names(
    pypi_name: str, seen: Sequence[Candidate]
) -> str | Sequence[Candidate]:
    """Used only for the static typecheck assertion below: a `NameMapper`
    annotates its parameters positionally, so an implementation naming them
    anything else must still satisfy the alias.
    """
    del pypi_name, seen
    return "tinylib"


_typecheck_assignment: NameMapper = _different_parameter_names


class TestArbitraryParameterNames:
    def test_still_callable_as_a_mapper(self) -> None:
        assert map_name("tinylib", (_different_parameter_names,)) == "tinylib"


# --------------------------------------------------------------------------
# Chain resolution
# --------------------------------------------------------------------------


class _Spy:
    """Records what it was called with. Returns `result` if given,
    otherwise passes `candidates` through unchanged (the "no opinion"
    behavior every mapper must support).
    """

    def __init__(self, result: str | None = None) -> None:
        self.result = result
        self.calls: list[tuple[NormalizedName, Sequence[Candidate]]] = []

    def __call__(
        self,
        name: NormalizedName,
        candidates: Sequence[Candidate],
    ) -> str | Sequence[Candidate]:
        self.calls.append((name, candidates))
        return candidates if self.result is None else self.result


def _candidate(
    conda_name: str = "tzdata",
    probability: float = 0.5,
    source: CandidateSource = CandidateSource.OTHER,
    mapper: str = "test",
) -> Candidate:
    return Candidate(conda_name=conda_name, probability=probability, source=source, mapper=mapper)


class TestChainResolution:
    def test_no_opinion_falls_through_to_the_next_mapper(self) -> None:
        first = _Spy(result=None)
        second = _Spy(result="second-result")

        result = map_name("tinylib", (first, second))

        assert result == "second-result"
        assert len(first.calls) == 1
        assert len(second.calls) == 1

    def test_first_str_wins_and_later_mappers_are_not_called(self) -> None:
        first = _Spy(result="first-result")
        second = _Spy(result="second-result")

        result = map_name("tinylib", (first, second))

        assert result == "first-result"
        assert len(first.calls) == 1
        assert len(second.calls) == 0

    def test_first_mapper_receives_an_empty_candidate_sequence(self) -> None:
        spy = _Spy(result="whatever")

        map_name("tinylib", (spy,))

        ((_name, candidates),) = spy.calls
        assert candidates == ()

    def test_candidates_flow_unchanged_from_one_mapper_to_the_next(self) -> None:
        contributed = (_candidate(),)

        class _Contributor:
            def __call__(
                self,
                name: NormalizedName,
                candidates: Sequence[Candidate],
            ) -> Sequence[Candidate]:
                del name, candidates
                return contributed

        second = _Spy(result="resolved")

        map_name("tinylib", (_Contributor(), second))

        ((_name, received),) = second.calls
        assert received is contributed

    def test_mapper_receives_canonicalized_name(self) -> None:
        spy = _Spy(result="whatever")

        map_name("Zope_Interface", (spy,))

        ((name, _candidates),) = spy.calls
        assert name == "zope-interface"


# --------------------------------------------------------------------------
# `NameMappers` -- the chain itself must be non-empty
# --------------------------------------------------------------------------


class TestNonEmptyMapperChain:
    def test_empty_chain_raises_value_error(self) -> None:
        empty: NameMappers = cast(NameMappers, ())

        with pytest.raises(ValueError, match="at least one mapper"):
            map_name("tinylib", empty)

    def test_empty_chain_never_reaches_unresolved_candidates(self) -> None:
        """The empty-chain rejection is a `ValueError`, distinct from --
        and raised before -- the `UnresolvedCondaNameError` a non-empty chain
        that never resolves would raise.
        """
        empty: NameMappers = cast(NameMappers, ())

        try:
            map_name("tinylib", empty)
        except UnresolvedCondaNameError:
            pytest.fail("expected ValueError, not UnresolvedCondaNameError")
        except ValueError:
            pass


# --------------------------------------------------------------------------
# `UnresolvedCondaNameError`
# --------------------------------------------------------------------------


class TestUnresolvedCondaNameError:
    def test_raised_when_every_mapper_has_no_opinion(self) -> None:
        mappers = (_Spy(result=None), _Spy(result=None))

        with pytest.raises(UnresolvedCondaNameError) as exc_info:
            map_name("Zope_Interface", mappers)

        assert exc_info.value.name == "zope-interface"
        assert exc_info.value.candidates == ()

    def test_carries_the_final_candidate_sequence(self) -> None:
        contributed = (_candidate(conda_name="tzdata"),)

        def _contributor(
            name: NormalizedName,
            candidates: Sequence[Candidate],
        ) -> Sequence[Candidate]:
            del name, candidates
            return contributed

        with pytest.raises(UnresolvedCondaNameError) as exc_info:
            map_name("tinylib", (_contributor,))

        assert exc_info.value.candidates == contributed

    def test_preserves_duplicate_conda_names_from_different_sources(self) -> None:
        duplicates = (
            _candidate(conda_name="tzdata", source=CandidateSource.PARSELMOUTH, mapper="a"),
            _candidate(conda_name="tzdata", source=CandidateSource.GRAYSKULL, mapper="b"),
        )

        def _contributor(
            name: NormalizedName,
            candidates: Sequence[Candidate],
        ) -> Sequence[Candidate]:
            del name, candidates
            return duplicates

        with pytest.raises(UnresolvedCondaNameError) as exc_info:
            map_name("tinylib", (_contributor,))

        assert len(exc_info.value.candidates) == 2
        assert exc_info.value.candidates[0].source is CandidateSource.PARSELMOUTH
        assert exc_info.value.candidates[1].source is CandidateSource.GRAYSKULL

    def test_carries_its_attributes_directly(self) -> None:
        candidates = (_candidate(),)

        exc = UnresolvedCondaNameError("opencv", candidates)

        assert exc.name == "opencv"
        assert exc.candidates == candidates

    def test_defaults_to_no_candidates(self) -> None:
        exc = UnresolvedCondaNameError("opencv")

        assert exc.candidates == ()


# --------------------------------------------------------------------------
# Exception propagation
# --------------------------------------------------------------------------


class TestExceptionPropagation:
    def test_unrelated_mapper_exception_propagates(self) -> None:
        def _buggy(
            name: NormalizedName,
            candidates: Sequence[Candidate],
        ) -> str | Sequence[Candidate]:
            del name, candidates
            raise KeyError("boom")

        with pytest.raises(KeyError):
            map_name("tinylib", (_buggy,))

    def test_exception_aborts_the_chain(self) -> None:
        def _buggy(
            name: NormalizedName,
            candidates: Sequence[Candidate],
        ) -> str | Sequence[Candidate]:
            del name, candidates
            raise KeyError("boom")

        spy = _Spy(result="never-reached")

        with pytest.raises(KeyError):
            map_name("tinylib", (_buggy, spy))

        assert len(spy.calls) == 0


# --------------------------------------------------------------------------
# `static_mapper`
# --------------------------------------------------------------------------


class TestStaticMapper:
    def test_hit(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert mapper(canonicalize_name("tzdata"), ()) == "python-tzdata"

    def test_miss_returns_the_input_candidates_object_unchanged(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})
        candidates = (_candidate(),)

        result = mapper(canonicalize_name("requests"), candidates)

        assert result is candidates

    def test_non_canonical_keys_are_normalized_at_construction(self) -> None:
        mapper = static_mapper({"Zope-Interface": "zope.interface"})

        assert mapper(canonicalize_name("zope-interface"), ()) == "zope.interface"

    def test_value_is_not_validated_against_cep_26(self) -> None:
        """Mirrors `Candidate.conda_name`: validation is deferred entirely
        to whatever consumes the final chosen name, not performed here --
        so a hit is returned as-is even if it is not (yet) a legal name.
        """
        mapper = static_mapper({"tzdata": "Bad--Name"})

        assert mapper(canonicalize_name("tzdata"), ()) == "Bad--Name"

    def test_used_end_to_end_through_map_name_on_a_hit(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert map_name("tzdata", (mapper,)) == "python-tzdata"

    def test_used_end_to_end_through_map_name_raises_on_a_miss(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        with pytest.raises(UnresolvedCondaNameError) as exc_info:
            map_name("requests", (mapper,))

        assert exc_info.value.candidates == ()


# --------------------------------------------------------------------------
# `aggregator_mapper`
# --------------------------------------------------------------------------


def _grayskull_candidate(conda_name: str = "python-annoy") -> Candidate:
    return _candidate(
        conda_name=conda_name,
        probability=1.0,
        source=CandidateSource.GRAYSKULL,
        mapper="grayskull_config",
    )


def _conda_lock_candidate(conda_name: str, probability: float) -> Candidate:
    return _candidate(
        conda_name=conda_name,
        probability=probability,
        source=CandidateSource.CONDA_LOCK,
        mapper="conda_forge_bot_graph",
    )


def _parselmouth_candidate(conda_name: str, probability: float) -> Candidate:
    return _candidate(
        conda_name=conda_name,
        probability=probability,
        source=CandidateSource.PARSELMOUTH,
        mapper="parselmouth_relations",
    )


class TestAggregatorMapper:
    def test_empty_candidates_resolves_to_the_normalized_name(self) -> None:
        result = aggregator_mapper(canonicalize_name("tinylib"), ())

        assert result == "tinylib"

    def test_fallback_does_not_enforce_the_64_character_limit(self) -> None:
        """The normalized-name fallback is deferred entirely to whatever
        consumes the final chosen name (e.g. `CondaPackageName` in
        `reroll.filename.wheel_config`) -- `aggregator_mapper` itself
        performs no length check, so an over-limit name passes straight
        through rather than being rejected here.
        """
        long_name = canonicalize_name("a" * 65)

        result = aggregator_mapper(long_name, ())

        assert result == long_name
        assert len(result) > 64

    # A grayskull candidate is authoritative.

    def test_grayskull_candidate_alone_is_taken(self) -> None:
        result = aggregator_mapper(canonicalize_name("annoy"), (_grayskull_candidate(),))

        assert result == "python-annoy"

    def test_grayskull_beats_parselmouth(self) -> None:
        candidates = (
            _parselmouth_candidate("annoy", 0.9),
            _grayskull_candidate("python-annoy"),
        )

        result = aggregator_mapper(canonicalize_name("annoy"), candidates)

        assert result == "python-annoy"

    def test_grayskull_beats_a_certain_conda_lock_candidate(self) -> None:
        candidates = (
            _conda_lock_candidate("annoy-lock", 1.0),
            _grayskull_candidate("python-annoy"),
        )

        result = aggregator_mapper(canonicalize_name("annoy"), candidates)

        assert result == "python-annoy"

    # A certain (probability 1.0) conda-lock candidate is a static override.

    def test_certain_conda_lock_candidate_alone_is_taken(self) -> None:
        candidates = (_conda_lock_candidate("python-tzdata", 1.0),)

        result = aggregator_mapper(canonicalize_name("tzdata"), candidates)

        assert result == "python-tzdata"

    def test_certain_conda_lock_candidate_beats_parselmouth(self) -> None:
        candidates = (
            _parselmouth_candidate("tzdata", 0.9),
            _conda_lock_candidate("python-tzdata", 1.0),
        )

        result = aggregator_mapper(canonicalize_name("tzdata"), candidates)

        assert result == "python-tzdata"

    # A name proposed by two or more distinct mappers wins the vote.

    def test_two_mappers_agreeing_on_a_name_wins(self) -> None:
        candidates = (
            _conda_lock_candidate("x", 0.6),
            _parselmouth_candidate("x", 0.5),
        )

        result = aggregator_mapper(canonicalize_name("tinylib"), candidates)

        assert result == "x"

    def test_votes_count_every_candidate_not_just_each_mappers_top_pick(self) -> None:
        candidates = (
            _parselmouth_candidate("y", 0.9),
            _parselmouth_candidate("x", 0.3),
            _conda_lock_candidate("x", 0.6),
        )

        result = aggregator_mapper(canonicalize_name("tinylib"), candidates)

        assert result == "x"

    def test_one_mapper_voting_twice_for_a_name_is_still_one_vote(self) -> None:
        candidates = (
            _candidate(conda_name="x", probability=0.5, mapper="m1"),
            _candidate(conda_name="x", probability=0.6, mapper="m1"),
        )

        result = aggregator_mapper(canonicalize_name("tinylib"), candidates)

        assert result is candidates

    def test_vote_tie_breaks_first_on_distinct_mapper_count(self) -> None:
        candidates = (
            _candidate(conda_name="alpha", probability=0.9, mapper="m1"),
            _candidate(conda_name="alpha", probability=0.9, mapper="m2"),
            _candidate(conda_name="beta", probability=0.4, mapper="m1"),
            _candidate(conda_name="beta", probability=0.4, mapper="m2"),
            _candidate(conda_name="beta", probability=0.4, mapper="m3"),
        )

        result = aggregator_mapper(canonicalize_name("tinylib"), candidates)

        assert result == "beta"

    def test_vote_tie_then_breaks_on_summed_probability(self) -> None:
        candidates = (
            _candidate(conda_name="alpha", probability=0.5, mapper="m1"),
            _candidate(conda_name="alpha", probability=0.5, mapper="m2"),
            _candidate(conda_name="beta", probability=0.6, mapper="m1"),
            _candidate(conda_name="beta", probability=0.6, mapper="m2"),
        )

        result = aggregator_mapper(canonicalize_name("tinylib"), candidates)

        assert result == "beta"

    def test_a_full_vote_tie_breaks_on_the_lexicographically_smallest_name(self) -> None:
        candidates = (
            _candidate(conda_name="beta", probability=0.5, mapper="m1"),
            _candidate(conda_name="beta", probability=0.5, mapper="m2"),
            _candidate(conda_name="alpha", probability=0.5, mapper="m1"),
            _candidate(conda_name="alpha", probability=0.5, mapper="m2"),
        )

        result = aggregator_mapper(canonicalize_name("tinylib"), candidates)

        assert result == "alpha"

    def test_disagreeing_mappers_with_no_consensus_defer(self) -> None:
        candidates = (
            _conda_lock_candidate("x", 0.6),
            _parselmouth_candidate("y", 0.5),
        )

        result = aggregator_mapper(canonicalize_name("tinylib"), candidates)

        assert result is candidates

    # A sole mapper's candidate needs confidence -- or parselmouth's restraint.

    def test_single_non_parselmouth_mapper_at_high_confidence_is_taken(self) -> None:
        candidates = (_conda_lock_candidate("python-annoy", 0.9),)

        result = aggregator_mapper(canonicalize_name("annoy"), candidates)

        assert result == "python-annoy"

    def test_single_non_parselmouth_mapper_below_the_confidence_threshold_defers(self) -> None:
        candidates = (_conda_lock_candidate("levenshtein", 0.6),)

        result = aggregator_mapper(canonicalize_name("levenshtein"), candidates)

        assert result is candidates

    def test_parselmouth_as_the_only_mapper_with_exactly_one_candidate_is_taken(self) -> None:
        candidates = (_parselmouth_candidate("opencv", 0.2),)

        result = aggregator_mapper(canonicalize_name("opencv"), candidates)

        assert result == "opencv"

    def test_parselmouth_with_multiple_candidates_below_the_confidence_threshold_defers(
        self,
    ) -> None:
        """More than one candidate loses parselmouth's unconditional
        "only candidate" exemption, so it's held to the same probability
        threshold as any other sole mapper -- and must still defer below
        it.
        """
        candidates = (
            _parselmouth_candidate("opencv", 0.85),
            _parselmouth_candidate("opencv-python", 0.4),
        )

        result = aggregator_mapper(canonicalize_name("opencv"), candidates)

        assert result is candidates

    # A single mapper's candidates are held to the same probability
    # threshold regardless of source -- including parselmouth, once it has
    # contributed more than one candidate (its own unconditional exemption,
    # tested above, applies only to a lone candidate).

    def test_single_mapper_parselmouth_multiple_candidates_at_confidence_threshold_resolves(
        self,
    ) -> None:
        """Real corpus data (`01OS`/`01os-0.0.1-py3-none-any.whl`,
        `fastapi`): a single mapper (`parselmouth_relations`) offers two
        `probability=0.95` candidates for the same PyPI name. Resolves to
        the best (here, tied) candidate.
        """
        candidates = (
            _parselmouth_candidate("fastapi", 0.95),
            _parselmouth_candidate("fastapi-core", 0.95),
        )

        result = aggregator_mapper(canonicalize_name("fastapi"), candidates)

        assert result == "fastapi"

    def test_single_mapper_parselmouth_multiple_candidates_low_probability_noise_resolves_to_winner(
        self,
    ) -> None:
        """Real corpus data (`01memories`/`01memories-0.0.27-py3-none-any.whl`,
        `pillow`): four near-zero-probability candidates alongside the one
        that should win (0.9413, comfortably above the 0.9 threshold).
        """
        candidates = (
            _parselmouth_candidate("arm_pyart", 0.0086),
            _parselmouth_candidate("finesse", 0.0086),
            _parselmouth_candidate("pillow", 0.9413),
            _parselmouth_candidate("pyautogui", 0.0086),
            _parselmouth_candidate("rosco", 0.0086),
        )

        result = aggregator_mapper(canonicalize_name("pillow"), candidates)

        assert result == "pillow"

    def test_single_mapper_parselmouth_multiple_candidates_below_confidence_threshold_defers(
        self,
    ) -> None:
        """A single-mapper parselmouth result with more than one candidate
        must still defer when the best candidate is below 0.9.
        """
        candidates = (
            _parselmouth_candidate("levenshtein", 0.5),
            _parselmouth_candidate("python-levenshtein", 0.3),
        )

        result = aggregator_mapper(canonicalize_name("levenshtein"), candidates)

        assert result is candidates

    def test_used_end_to_end_the_fastapi_corpus_case_resolves_through_map_name(
        self,
    ) -> None:
        """The same `fastapi` case as above, but driven through `map_name`
        with a stub contributing mapper -- matching how
        `reroll_data.reroll_index_demo.wheel_to_records` actually calls
        this chain end to end. Reproduces the exact input stored for
        `01OS`/`01os-0.0.1-py3-none-any.whl` in the corpus.
        """
        contributed = (
            _parselmouth_candidate("fastapi", 0.95),
            _parselmouth_candidate("fastapi-core", 0.95),
        )

        def _contributor(
            name: NormalizedName,
            candidates: Sequence[Candidate],
        ) -> Sequence[Candidate]:
            del name, candidates
            return contributed

        result = map_name("fastapi", (_contributor, aggregator_mapper))

        assert result == "fastapi"

    # End to end through `map_name`.

    def test_used_end_to_end_falls_back_to_normalized_name(self) -> None:
        assert map_name("Zope_Interface", (aggregator_mapper,)) == ("zope-interface")

    def test_used_end_to_end_the_64_character_limit_is_not_enforced_by_map_name(self) -> None:
        """`map_name` returns whatever string a mapper (here,
        `aggregator_mapper`'s fallback) resolves to, uninspected -- CEP 26
        length/shape validation is a downstream concern, not part of the
        chain-resolution contract.
        """
        result = map_name("a" * 65, (aggregator_mapper,))

        assert result == "a" * 65

    def test_used_end_to_end_after_a_no_opinion_mapper(self) -> None:
        no_opinion = _Spy(result=None)

        result = map_name("tinylib", (no_opinion, aggregator_mapper))

        assert result == "tinylib"

    def test_used_end_to_end_returns_the_name_the_aggregator_resolves(self) -> None:
        contributed = (_grayskull_candidate("python-annoy"),)

        def _contributor(
            name: NormalizedName,
            candidates: Sequence[Candidate],
        ) -> Sequence[Candidate]:
            del name, candidates
            return contributed

        result = map_name("annoy", (_contributor, aggregator_mapper))

        assert result == "python-annoy"

    def test_used_end_to_end_raises_when_the_aggregator_defers(self) -> None:
        contributed = (
            _conda_lock_candidate("x", 0.6),
            _parselmouth_candidate("y", 0.5),
        )

        def _contributor(
            name: NormalizedName,
            candidates: Sequence[Candidate],
        ) -> Sequence[Candidate]:
            del name, candidates
            return contributed

        with pytest.raises(UnresolvedCondaNameError) as exc_info:
            map_name("tinylib", (_contributor, aggregator_mapper))

        assert exc_info.value.candidates == contributed
