from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from reroll.name_mapping import AmbiguousCondaName, exact_version, map_name
from reroll.parselmouth_direct import (
    DEFAULT_BASE_URL,
    DEFAULT_CHANNEL,
    default_fetch,
    parselmouth_direct_mapper,
)

# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------


class _FakeFetch:
    """Records the URL(s) it was called with and returns a canned payload.

    `payload=None` simulates a 404 (Parselmouth has never seen this name).
    """

    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes | None:
        self.urls.append(url)
        if self.payload is None:
            return None
        return json.dumps(self.payload).encode()


def _response(conda_versions: dict[str, str]) -> dict[str, Any]:
    return {
        "format_version": "1.0",
        "channel": "conda-forge",
        "pypi_name": "whatever",
        "conda_versions": conda_versions,
    }


# --------------------------------------------------------------------------
# Request construction
# --------------------------------------------------------------------------


class TestRequest:
    def test_default_channel_and_base_url(self) -> None:
        fetch = _FakeFetch(_response({"1.0": "requests"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        mapper(canonicalize_name("requests"), SpecifierSet(""))

        assert fetch.urls == [f"{DEFAULT_BASE_URL}/{DEFAULT_CHANNEL}/requests.json"]

    def test_custom_channel(self) -> None:
        fetch = _FakeFetch(_response({"1.0": "requests"}))
        mapper = parselmouth_direct_mapper("main", fetch=fetch)

        mapper(canonicalize_name("requests"), SpecifierSet(""))

        assert fetch.urls == [f"{DEFAULT_BASE_URL}/main/requests.json"]

    def test_custom_base_url(self) -> None:
        fetch = _FakeFetch(_response({"1.0": "requests"}))
        mapper = parselmouth_direct_mapper(base_url="https://example.test/mapping", fetch=fetch)

        mapper(canonicalize_name("requests"), SpecifierSet(""))

        assert fetch.urls == ["https://example.test/mapping/conda-forge/requests.json"]

    def test_name_is_not_recanonicalized(self) -> None:
        """The mapper receives an already-canonicalized name (per the
        `NameMapper` contract) and must use it as-is.
        """
        fetch = _FakeFetch(_response({"1.0": "zope.interface"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        mapper(canonicalize_name("Zope_Interface"), SpecifierSet(""))

        assert fetch.urls == [f"{DEFAULT_BASE_URL}/{DEFAULT_CHANNEL}/zope-interface.json"]

    def test_channel_and_name_are_percent_encoded(self) -> None:
        fetch = _FakeFetch(None)
        mapper = parselmouth_direct_mapper("a/b", fetch=fetch)

        mapper(canonicalize_name("requests"), SpecifierSet(""))

        assert fetch.urls == [f"{DEFAULT_BASE_URL}/a%2Fb/requests.json"]


# --------------------------------------------------------------------------
# Resolution algorithm
# --------------------------------------------------------------------------


class TestResolution:
    def test_unknown_name_returns_none(self) -> None:
        """A 404 -- modeled by the fake fetch returning `None` -- means
        "no opinion", not an error.
        """
        fetch = _FakeFetch(None)
        mapper = parselmouth_direct_mapper(fetch=fetch)

        assert mapper(canonicalize_name("nonexistent"), SpecifierSet("")) is None

    def test_single_version_hit(self) -> None:
        fetch = _FakeFetch(_response({"2.31.0": "requests"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        result = mapper(canonicalize_name("requests"), exact_version(Version("2.31.0")))

        assert result == "requests"

    def test_unconstrained_specifier_matches_every_version(self) -> None:
        fetch = _FakeFetch(_response({"2.31.0": "requests", "2.32.0": "requests"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        result = mapper(canonicalize_name("requests"), SpecifierSet(""))

        assert result == "requests"

    def test_no_version_satisfies_specifier_returns_none(self) -> None:
        fetch = _FakeFetch(_response({"2.31.0": "requests"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        result = mapper(canonicalize_name("requests"), exact_version(Version("9.9.9")))

        assert result is None

    def test_unparsable_version_strings_are_skipped(self) -> None:
        fetch = _FakeFetch(_response({"not-a-version": "junk", "2.31.0": "requests"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        result = mapper(canonicalize_name("requests"), SpecifierSet(""))

        assert result == "requests"

    def test_disambiguated_by_a_constrained_specifier(self) -> None:
        """Two conda names exist overall, but the specifier only admits
        the version that maps to one of them -- so this is *not*
        ambiguous.
        """
        fetch = _FakeFetch(_response({"4.13.0": "libopencv", "5.0.0": "py-opencv"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        result = mapper(canonicalize_name("opencv-python"), exact_version(Version("4.13.0")))

        assert result == "libopencv"

    def test_ambiguous_when_matching_versions_disagree(self) -> None:
        fetch = _FakeFetch(_response({"4.13.0": "libopencv", "5.0.0": "py-opencv"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        with pytest.raises(AmbiguousCondaName) as exc_info:
            mapper(canonicalize_name("opencv-python"), SpecifierSet(""))

        assert exc_info.value.name == "opencv-python"
        assert exc_info.value.candidates == ("libopencv", "py-opencv")

    def test_ambiguous_candidates_are_sorted(self) -> None:
        fetch = _FakeFetch(_response({"5.0.0": "py-opencv", "4.13.0": "libopencv"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        with pytest.raises(AmbiguousCondaName) as exc_info:
            mapper(canonicalize_name("opencv-python"), SpecifierSet(""))

        assert exc_info.value.candidates == ("libopencv", "py-opencv")

    def test_matching_versions_agreeing_on_one_name_is_not_ambiguous(self) -> None:
        """Distinct *versions* mapping to the same conda name (the common
        case) must not be mistaken for ambiguity.
        """
        fetch = _FakeFetch(_response({"2.10.0": "requests", "2.31.0": "requests"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        assert mapper(canonicalize_name("requests"), SpecifierSet("")) == "requests"


# --------------------------------------------------------------------------
# Failure propagation
# --------------------------------------------------------------------------


class TestFailurePropagation:
    def test_fetch_exception_propagates(self) -> None:
        def _raiser(url: str) -> bytes | None:
            del url
            raise urllib.error.URLError("boom")

        mapper = parselmouth_direct_mapper(fetch=_raiser)

        with pytest.raises(urllib.error.URLError):
            mapper(canonicalize_name("requests"), SpecifierSet(""))

    def test_malformed_json_propagates(self) -> None:
        def _fetch(url: str) -> bytes | None:
            del url
            return b"not json"

        mapper = parselmouth_direct_mapper(fetch=_fetch)

        with pytest.raises(json.JSONDecodeError):
            mapper(canonicalize_name("requests"), SpecifierSet(""))

    def test_missing_conda_versions_key_propagates(self) -> None:
        def _fetch(url: str) -> bytes | None:
            del url
            return json.dumps({"format_version": "1.0"}).encode()

        mapper = parselmouth_direct_mapper(fetch=_fetch)

        with pytest.raises(KeyError):
            mapper(canonicalize_name("requests"), SpecifierSet(""))


# --------------------------------------------------------------------------
# End-to-end through `map_name`
# --------------------------------------------------------------------------


class TestUsedEndToEndThroughMapName:
    def test_hit(self) -> None:
        fetch = _FakeFetch(_response({"2.31.0": "requests"}))
        mapper = parselmouth_direct_mapper(fetch=fetch)

        result = map_name("Requests", exact_version(Version("2.31.0")), [mapper])

        assert result == "requests"

    def test_miss_falls_through_to_normalized_name(self) -> None:
        fetch = _FakeFetch(None)
        mapper = parselmouth_direct_mapper(fetch=fetch)

        result = map_name("Tinylib", SpecifierSet(""), [mapper])

        assert result == "tinylib"


class TestDefaultFetch:
    def test_success_returns_body(self) -> None:
        body = default_fetch("https://httpbin.org/json")

        assert body is not None

    def test_404_returns_none(self) -> None:
        assert default_fetch("https://httpbin.org/status/404") is None

    @pytest.mark.parametrize("status", [400, 500, 503])
    def test_other_error_statuses_propagate(self, status: int) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            default_fetch(f"https://httpbin.org/status/{status}")

        assert exc_info.value.code == status
