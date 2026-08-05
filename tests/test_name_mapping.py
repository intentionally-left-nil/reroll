"""Unit tests for `reroll.name_mapping`."""

from __future__ import annotations

import functools

import pytest
from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name
from packaging.version import Version
from pydantic import ValidationError

from reroll.name_mapping import (
    AmbiguousCondaName,
    NameMapper,
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
# Callable-shape acceptance for `NameMapper`
# --------------------------------------------------------------------------


def _function_mapper(name: NormalizedName, specifier: SpecifierSet) -> str | None:
    del specifier
    return f"conda-{name}"


class _StatefulMapper:
    """A stateful class instance with `__call__` that counts hits and is
    reused across calls.
    """

    def __init__(self, result: str | None) -> None:
        self.result = result
        self.hits = 0

    def __call__(self, name: NormalizedName, specifier: SpecifierSet) -> str | None:
        del name, specifier
        self.hits += 1
        return self.result


class _BoundMethodOwner:
    def lookup(self, name: NormalizedName, specifier: SpecifierSet) -> str | None:
        del specifier
        return f"bound-{name}"


class TestCallableShapes:
    def test_function(self) -> None:
        assert map_name("Requests", SpecifierSet(""), [_function_mapper]) == "conda-requests"

    def test_lambda(self) -> None:
        mapper: NameMapper = lambda name, specifier: f"lambda-{name}"  # noqa: E731
        assert map_name("Requests", SpecifierSet(""), [mapper]) == "lambda-requests"

    def test_closure(self) -> None:
        def make_mapper(prefix: str) -> NameMapper:
            def _mapper(name: NormalizedName, specifier: SpecifierSet) -> str | None:
                del specifier
                return f"{prefix}-{name}"

            return _mapper

        mapper = make_mapper("closure")
        assert map_name("Requests", SpecifierSet(""), [mapper]) == "closure-requests"

    def test_functools_partial(self) -> None:
        def _mapper(prefix: str, name: NormalizedName, specifier: SpecifierSet) -> str | None:
            del specifier
            return f"{prefix}-{name}"

        mapper = functools.partial(_mapper, "partial")
        assert map_name("Requests", SpecifierSet(""), [mapper]) == "partial-requests"

    def test_bound_method(self) -> None:
        owner = _BoundMethodOwner()
        assert map_name("Requests", SpecifierSet(""), [owner.lookup]) == "bound-requests"

    def test_stateful_instance_counts_hits_and_is_reused(self) -> None:
        mapper = _StatefulMapper("stateful-result")

        first = map_name("Requests", SpecifierSet(""), [mapper])
        second = map_name("Other", SpecifierSet(""), [mapper])

        assert first == "stateful-result"
        assert second == "stateful-result"
        assert mapper.hits == 2


def _different_parameter_names(pypi_name: str, spec: SpecifierSet) -> str | None:
    """Used only for the static typecheck assertion below: a `NameMapper`
    annotates its parameters positionally, so an implementation naming them
    anything else must still satisfy the alias.
    """
    del pypi_name, spec
    return None


_typecheck_assignment: NameMapper = _different_parameter_names


class TestArbitraryParameterNames:
    def test_still_callable_as_a_mapper(self) -> None:
        assert map_name("tinylib", SpecifierSet(""), [_different_parameter_names]) == "tinylib"


# --------------------------------------------------------------------------
# Chain resolution
# --------------------------------------------------------------------------


class _Spy:
    def __init__(self, result: str | None = None) -> None:
        self.result = result
        self.calls: list[tuple[NormalizedName, SpecifierSet]] = []

    def __call__(self, name: NormalizedName, specifier: SpecifierSet) -> str | None:
        self.calls.append((name, specifier))
        return self.result


class TestChainResolution:
    def test_none_falls_through_to_the_next_mapper(self) -> None:
        first = _Spy(result=None)
        second = _Spy(result="second-result")

        result = map_name("tinylib", SpecifierSet(""), [first, second])

        assert result == "second-result"
        assert len(first.calls) == 1
        assert len(second.calls) == 1

    def test_first_non_none_wins_and_later_mappers_are_not_called(self) -> None:
        first = _Spy(result="first-result")
        second = _Spy(result="second-result")

        result = map_name("tinylib", SpecifierSet(""), [first, second])

        assert result == "first-result"
        assert len(first.calls) == 1
        assert len(second.calls) == 0

    def test_empty_sequence_falls_back_to_normalized_name(self) -> None:
        assert map_name("Zope_Interface", SpecifierSet(""), []) == "zope-interface"

    def test_all_none_falls_back_to_normalized_name(self) -> None:
        mappers = [_Spy(result=None), _Spy(result=None)]

        result = map_name("Zope_Interface", SpecifierSet(""), mappers)

        assert result == "zope-interface"

    def test_mapper_receives_canonicalized_name(self) -> None:
        spy = _Spy(result="whatever")

        map_name("Zope_Interface", SpecifierSet(""), [spy])

        ((name, _specifier),) = spy.calls
        assert name == "zope-interface"

    def test_mapper_receives_the_same_specifier_object(self) -> None:
        spy = _Spy(result="whatever")
        specifier = SpecifierSet(">=1.0,<2.0")

        map_name("tinylib", specifier, [spy])

        ((_name, received),) = spy.calls
        assert received is specifier

    def test_empty_specifier_set_is_accepted(self) -> None:
        spy = _Spy(result="whatever")
        specifier = SpecifierSet("")

        map_name("tinylib", specifier, [spy])

        ((_name, received),) = spy.calls
        assert received is specifier

    def test_multi_clause_specifier_is_accepted(self) -> None:
        spy = _Spy(result="whatever")
        specifier = SpecifierSet(">=1.0,<2.0,!=1.5")

        map_name("tinylib", specifier, [spy])

        ((_name, received),) = spy.calls
        assert received is specifier


# --------------------------------------------------------------------------
# Exception propagation
# --------------------------------------------------------------------------


class TestExceptionPropagation:
    def test_ambiguous_conda_name_aborts_the_chain(self) -> None:
        def _raiser(name: NormalizedName, specifier: SpecifierSet) -> str | None:
            raise AmbiguousCondaName(name, specifier, candidates=("opencv", "py-opencv"))

        spy = _Spy(result="never-reached")

        with pytest.raises(AmbiguousCondaName):
            map_name("opencv", SpecifierSet(""), [_raiser, spy])

        assert len(spy.calls) == 0

    def test_ambiguous_conda_name_carries_its_attributes(self) -> None:
        specifier = SpecifierSet("")
        exc = AmbiguousCondaName("opencv", specifier, candidates=("opencv", "py-opencv"))

        assert exc.name == "opencv"
        assert exc.specifier is specifier
        assert exc.candidates == ("opencv", "py-opencv")

    def test_ambiguous_conda_name_defaults_to_no_candidates(self) -> None:
        exc = AmbiguousCondaName("opencv", SpecifierSet(""))

        assert exc.candidates == ()

    def test_unrelated_mapper_exception_propagates(self) -> None:
        def _buggy(name: NormalizedName, specifier: SpecifierSet) -> str | None:
            del name, specifier
            raise KeyError("boom")

        with pytest.raises(KeyError):
            map_name("tinylib", SpecifierSet(""), [_buggy])


# --------------------------------------------------------------------------
# `static_mapper`
# --------------------------------------------------------------------------


class TestStaticMapper:
    def test_hit(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert mapper(canonicalize_name("tzdata"), SpecifierSet("")) == "python-tzdata"

    def test_miss(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert mapper(canonicalize_name("requests"), SpecifierSet("")) is None

    def test_non_canonical_keys_are_normalized_at_construction(self) -> None:
        mapper = static_mapper({"Zope-Interface": "zope.interface"})

        assert mapper(canonicalize_name("zope-interface"), SpecifierSet("")) == "zope.interface"

    def test_specifier_is_ignored(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert mapper(canonicalize_name("tzdata"), SpecifierSet("==1.0")) == "python-tzdata"
        assert mapper(canonicalize_name("tzdata"), SpecifierSet(">=2.0")) == "python-tzdata"

    def test_never_raises_ambiguous(self) -> None:
        """A static table is version-independent by construction, so it
        never has grounds to raise `AmbiguousCondaName`.
        """
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert mapper(canonicalize_name("tzdata"), SpecifierSet(">=1.0,<2.0")) == "python-tzdata"

    def test_bad_table_reports_every_invalid_value_with_its_key(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            static_mapper({"good": "python-tzdata", "bad-one": "Bad--Name", "bad-two": "a" * 65})

        errors = exc_info.value.errors()
        locs = {error["loc"][0] for error in errors}
        assert locs == {"bad-one", "bad-two"}

    def test_used_end_to_end_through_map_name(self) -> None:
        mapper = static_mapper({"tzdata": "python-tzdata"})

        assert map_name("tzdata", SpecifierSet(""), [mapper]) == "python-tzdata"
        assert map_name("requests", SpecifierSet(""), [mapper]) == "requests"
