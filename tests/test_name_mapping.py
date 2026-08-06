"""Unit tests for `reroll.name_mapping`."""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import cast

import pytest
from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name
from packaging.version import Version
from pydantic import ValidationError

from reroll.name_mapping import (
    Candidate,
    CandidateSource,
    NameMapper,
    NameMappers,
    UnresolvedCandidates,
    aggregator_mapper,
    exact_version,
    map_name,
    static_mapper,
)

# --------------------------------------------------------------------------
# `exact_version`
# --------------------------------------------------------------------------


class TestExactVersion:
    @pytest.mark.parametrize(
        "raw",
        [
            "1.2.3",
            "1!2.0",
            "1.2.3+abc.1",
            "1.2.3.dev0",
            "1.2.3.post1",
            "1.2.3rc1",
        ],
    )
    def test_round_trips(self, raw: str) -> None:
        version = Version(raw)

        specifier = exact_version(version)

        assert specifier == SpecifierSet(f"=={version}")
        assert specifier.contains(version)


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

    def test_conda_name_validated_per_cep_26(self) -> None:
        with pytest.raises(ValidationError):
            Candidate(
                conda_name="Bad--Name",
                probability=0.5,
                source=CandidateSource.OTHER,
                mapper="test",
            )

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
    specifier: SpecifierSet,
    candidates: Sequence[Candidate],
) -> str | Sequence[Candidate]:
    del specifier, candidates
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
        specifier: SpecifierSet,
        candidates: Sequence[Candidate],
    ) -> str | Sequence[Candidate]:
        del name, specifier, candidates
        self.hits += 1
        return self.result


class _BoundMethodOwner:
    def lookup(
        self,
        name: NormalizedName,
        specifier: SpecifierSet,
        candidates: Sequence[Candidate],
    ) -> str | Sequence[Candidate]:
        del specifier, candidates
        return f"bound-{name}"


class TestCallableShapes:
    def test_function(self) -> None:
        assert map_name("Requests", SpecifierSet(""), (_function_mapper,)) == "conda-requests"

    def test_lambda(self) -> None:
        mapper: NameMapper = lambda name, specifier, candidates: f"lambda-{name}"  # noqa: E731
        assert map_name("Requests", SpecifierSet(""), (mapper,)) == "lambda-requests"

    def test_closure(self) -> None:
        def make_mapper(prefix: str) -> NameMapper:
            def _mapper(
                name: NormalizedName,
                specifier: SpecifierSet,
                candidates: Sequence[Candidate],
            ) -> str | Sequence[Candidate]:
                del specifier, candidates
                return f"{prefix}-{name}"

            return _mapper

        mapper = make_mapper("closure")
        assert map_name("Requests", SpecifierSet(""), (mapper,)) == "closure-requests"

    def test_functools_partial(self) -> None:
        def _mapper(
            prefix: str,
            name: NormalizedName,
            specifier: SpecifierSet,
            candidates: Sequence[Candidate],
        ) -> str | Sequence[Candidate]:
            del specifier, candidates
            return f"{prefix}-{name}"

        mapper = functools.partial(_mapper, "partial")
        assert map_name("Requests", SpecifierSet(""), (mapper,)) == "partial-requests"

    def test_bound_method(self) -> None:
        owner = _BoundMethodOwner()
        assert map_name("Requests", SpecifierSet(""), (owner.lookup,)) == "bound-requests"

    def test_stateful_instance_counts_hits_and_is_reused(self) -> None:
        mapper = _StatefulMapper("stateful-result")

        first = map_name("Requests", SpecifierSet(""), (mapper,))
        second = map_name("Other", SpecifierSet(""), (mapper,))

        assert first == "stateful-result"
        assert second == "stateful-result"
        assert mapper.hits == 2


def _different_parameter_names(
    pypi_name: str, spec: SpecifierSet, seen: Sequence[Candidate]
) -> str | Sequence[Candidate]:
    """Used only for the static typecheck assertion below: a `NameMapper`
    annotates its parameters positionally, so an implementation naming them
    anything else must still satisfy the alias.
    """
    del pypi_name, spec, seen
    return "tinylib"


_typecheck_assignment: NameMapper = _different_parameter_names


class TestArbitraryParameterNames:
    def test_still_callable_as_a_mapper(self) -> None:
        assert map_name("tinylib", SpecifierSet(""), (_different_parameter_names,)) == "tinylib"


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
        self.calls: list[tuple[NormalizedName, SpecifierSet, Sequence[Candidate]]] = []

    def __call__(
        self,
        name: NormalizedName,
        specifier: SpecifierSet,
        candidates: Sequence[Candidate],
    ) -> str | Sequence[Candidate]:
        self.calls.append((name, specifier, candidates))
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

        result = map_name("tinylib", SpecifierSet(""), (first, second))

        assert result == "second-result"
        assert len(first.calls) == 1
        assert len(second.calls) == 1

    def test_first_str_wins_and_later_mappers_are_not_called(self) -> None:
        first = _Spy(result="first-result")
        second = _Spy(result="second-result")

        result = map_name("tinylib", SpecifierSet(""), (first, second))

        assert result == "first-result"
        assert len(first.calls) == 1
        assert len(second.calls) == 0

    def test_first_mapper_receives_an_empty_candidate_sequence(self) -> None:
        spy = _Spy(result="whatever")

        map_name("tinylib", SpecifierSet(""), (spy,))

        ((_name, _specifier, candidates),) = spy.calls
        assert candidates == ()

    def test_candidates_flow_unchanged_from_one_mapper_to_the_next(self) -> None:
        contributed = (_candidate(),)

        class _Contributor:
            def __call__(
                self,
                name: NormalizedName,
                specifier: SpecifierSet,
                candidates: Sequence[Candidate],
            ) -> Sequence[Candidate]:
                del name, specifier, candidates
                return contributed

        second = _Spy(result="resolved")

        map_name("tinylib", SpecifierSet(""), (_Contributor(), second))

        ((_name, _specifier, received),) = second.calls
        assert received is contributed

    def test_mapper_receives_canonicalized_name(self) -> None:
        spy = _Spy(result="whatever")

        map_name("Zope_Interface", SpecifierSet(""), (spy,))

        ((name, _specifier, _candidates),) = spy.calls
        assert name == "zope-interface"

    def test_mapper_receives_the_same_specifier_object(self) -> None:
        spy = _Spy(result="whatever")
        specifier = SpecifierSet(">=1.0,<2.0")

        map_name("tinylib", specifier, (spy,))

        ((_name, received, _candidates),) = spy.calls
        assert received is specifier

    def test_empty_specifier_set_is_accepted(self) -> None:
        spy = _Spy(result="whatever")
        specifier = SpecifierSet("")

        map_name("tinylib", specifier, (spy,))

        ((_name, received, _candidates),) = spy.calls
        assert received is specifier

    def test_multi_clause_specifier_is_accepted(self) -> None:
        spy = _Spy(result="whatever")
        specifier = SpecifierSet(">=1.0,<2.0,!=1.5")

        map_name("tinylib", specifier, (spy,))

        ((_name, received, _candidates),) = spy.calls
        assert received is specifier


# --------------------------------------------------------------------------
# `NameMappers` -- the chain itself must be non-empty
# --------------------------------------------------------------------------


class TestNonEmptyMapperChain:
    def test_empty_chain_raises_value_error(self) -> None:
        empty: NameMappers = cast(NameMappers, ())

        with pytest.raises(ValueError, match="at least one mapper"):
            map_name("tinylib", SpecifierSet(""), empty)

    def test_empty_chain_never_reaches_unresolved_candidates(self) -> None:
        """The empty-chain rejection is a `ValueError`, distinct from --
        and raised before -- the `UnresolvedCandidates` a non-empty chain
        that never resolves would raise.
        """
        empty: NameMappers = cast(NameMappers, ())

        try:
            map_name("tinylib", SpecifierSet(""), empty)
        except UnresolvedCandidates:
            pytest.fail("expected ValueError, not UnresolvedCandidates")
        except ValueError:
            pass


# --------------------------------------------------------------------------
# `UnresolvedCandidates`
# --------------------------------------------------------------------------


class TestUnresolvedCandidates:
    def test_raised_when_every_mapper_has_no_opinion(self) -> None:
        mappers = (_Spy(result=None), _Spy(result=None))

        with pytest.raises(UnresolvedCandidates) as exc_info:
            map_name("Zope_Interface", SpecifierSet(""), mappers)

        assert exc_info.value.name == "zope-interface"
        assert exc_info.value.candidates == ()

    def test_carries_the_final_candidate_sequence(self) -> None:
        contributed = (_candidate(conda_name="tzdata"),)

        def _contributor(
            name: NormalizedName,
            specifier: SpecifierSet,
            candidates: Sequence[Candidate],
        ) -> Sequence[Candidate]:
            del name, specifier, candidates
            return contributed

        with pytest.raises(UnresolvedCandidates) as exc_info:
            map_name("tinylib", SpecifierSet(""), (_contributor,))

        assert exc_info.value.candidates == contributed

    def test_preserves_duplicate_conda_names_from_different_sources(self) -> None:
        duplicates = (
            _candidate(conda_name="tzdata", source=CandidateSource.PARSELMOUTH, mapper="a"),
            _candidate(conda_name="tzdata", source=CandidateSource.GRAYSKULL, mapper="b"),
        )

        def _contributor(
            name: NormalizedName,
            specifier: SpecifierSet,
            candidates: Sequence[Candidate],
        ) -> Sequence[Candidate]:
            del name, specifier, candidates
            return duplicates

        with pytest.raises(UnresolvedCandidates) as exc_info:
            map_name("tinylib", SpecifierSet(""), (_contributor,))

        assert len(exc_info.value.candidates) == 2
        assert exc_info.value.candidates[0].source is CandidateSource.PARSELMOUTH
        assert exc_info.value.candidates[1].source is CandidateSource.GRAYSKULL

    def test_carries_its_attributes_directly(self) -> None:
        specifier = SpecifierSet("")
        candidates = (_candidate(),)

        exc = UnresolvedCandidates("opencv", specifier, candidates)

        assert exc.name == "opencv"
        assert exc.specifier is specifier
        assert exc.candidates == candidates

    def test_defaults_to_no_candidates(self) -> None:
        exc = UnresolvedCandidates("opencv", SpecifierSet(""))

        assert exc.candidates == ()


# --------------------------------------------------------------------------
# Exception propagation
# --------------------------------------------------------------------------


class TestExceptionPropagation:
    def test_unrelated_mapper_exception_propagates(self) -> None:
        def _buggy(
            name: NormalizedName,
            specifier: SpecifierSet,
            candidates: Sequence[Candidate],
        ) -> str | Sequence[Candidate]:
            del name, specifier, candidates
            raise KeyError("boom")

        with pytest.raises(KeyError):
            map_name("tinylib", SpecifierSet(""), (_buggy,))

    def test_exception_aborts_the_chain(self) -> None:
        def _buggy(
            name: NormalizedName,
            specifier: SpecifierSet,
            candidates: Sequence[Candidate],
        ) -> str | Sequence[Candidate]:
            del name, specifier, candidates
            raise KeyError("boom")

        spy = _Spy(result="never-reached")

        with pytest.raises(KeyError):
            map_name("tinylib", SpecifierSet(""), (_buggy, spy))

        assert len(spy.calls) == 0


# --------------------------------------------------------------------------
# `static_mapper`
# --------------------------------------------------------------------------


class TestStaticMapper:
    def test_hit(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert mapper(canonicalize_name("tzdata"), SpecifierSet(""), ()) == "python-tzdata"

    def test_miss_returns_the_input_candidates_object_unchanged(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})
        candidates = (_candidate(),)

        result = mapper(canonicalize_name("requests"), SpecifierSet(""), candidates)

        assert result is candidates

    def test_non_canonical_keys_are_normalized_at_construction(self) -> None:
        mapper = static_mapper({"Zope-Interface": "zope.interface"})

        assert mapper(canonicalize_name("zope-interface"), SpecifierSet(""), ()) == "zope.interface"

    def test_specifier_is_ignored(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert mapper(canonicalize_name("tzdata"), SpecifierSet("==1.0"), ()) == "python-tzdata"
        assert mapper(canonicalize_name("tzdata"), SpecifierSet(">=2.0"), ()) == "python-tzdata"

    def test_bad_table_reports_every_invalid_value_with_its_key(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            static_mapper({"good": "python-tzdata", "bad-one": "Bad--Name", "bad-two": "a" * 65})

        errors = exc_info.value.errors()
        locs = {error["loc"][0] for error in errors}
        assert locs == {"bad-one", "bad-two"}

    def test_used_end_to_end_through_map_name_on_a_hit(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert map_name("tzdata", SpecifierSet(""), (mapper,)) == "python-tzdata"

    def test_used_end_to_end_through_map_name_raises_on_a_miss(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        with pytest.raises(UnresolvedCandidates) as exc_info:
            map_name("requests", SpecifierSet(""), (mapper,))

        assert exc_info.value.candidates == ()


# --------------------------------------------------------------------------
# `aggregator_mapper`
# --------------------------------------------------------------------------


class TestAggregatorMapper:
    def test_empty_candidates_resolves_to_the_normalized_name(self) -> None:
        result = aggregator_mapper(canonicalize_name("tinylib"), SpecifierSet(""), ())

        assert result == "tinylib"

    def test_non_empty_candidates_pass_through_unchanged(self) -> None:
        candidates = (_candidate(),)

        result = aggregator_mapper(canonicalize_name("tinylib"), SpecifierSet(""), candidates)

        assert result is candidates

    def test_specifier_is_ignored(self) -> None:
        name = canonicalize_name("tinylib")

        assert aggregator_mapper(name, SpecifierSet("==1.0"), ()) == "tinylib"
        assert aggregator_mapper(name, SpecifierSet(">=2.0"), ()) == "tinylib"

    def test_used_end_to_end_falls_back_to_normalized_name(self) -> None:
        assert map_name("Zope_Interface", SpecifierSet(""), (aggregator_mapper,)) == (
            "zope-interface"
        )

    def test_used_end_to_end_after_a_no_opinion_mapper(self) -> None:
        no_opinion = _Spy(result=None)

        result = map_name("tinylib", SpecifierSet(""), (no_opinion, aggregator_mapper))

        assert result == "tinylib"

    def test_used_end_to_end_still_raises_when_candidates_are_left_unresolved(self) -> None:
        contributed = (_candidate(),)

        def _contributor(
            name: NormalizedName,
            specifier: SpecifierSet,
            candidates: Sequence[Candidate],
        ) -> Sequence[Candidate]:
            del name, specifier, candidates
            return contributed

        with pytest.raises(UnresolvedCandidates) as exc_info:
            map_name("tinylib", SpecifierSet(""), (_contributor, aggregator_mapper))

        assert exc_info.value.candidates == contributed
