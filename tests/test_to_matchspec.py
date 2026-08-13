"""Unit tests for `reroll.to_matchspec`."""

from __future__ import annotations

import pytest

import reroll
from reroll.errors import UnconvertableRequirementError
from reroll.name_mapping import aggregator_mapper, static_mapper


class TestToMatchspec:
    def test_converts_a_pep_508_entry_to_a_matchspec(self) -> None:
        assert (
            reroll.to_matchspec("requests>=2.0.0", mappers=(aggregator_mapper,))
            == "requests >=2.0.0"
        )

    def test_matches_the_readme_example(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact example from the README's "PEP to Matchspec helper"
        section. `default_mappers()` is stubbed out -- it builds
        network/db-backed mappers (docs/pypi_conda_mapping.md) that aren't
        available in CI -- with a stand-in that resolves this name the
        same way the real chain does: falling through to the bare
        `aggregator_mapper` normalization.
        """
        monkeypatch.setattr(reroll, "default_mappers", lambda: (aggregator_mapper,))

        assert (
            reroll.to_matchspec('packageA ; python_version < "3.9"')
            == 'packagea[when="python<3.9.0a0"]'
        )

    def test_defaults_to_default_mappers_when_none_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`to_matchspec()` itself has no opinion on the default -- it just
        falls back to `default_mappers()` when `mappers` is `None`.
        """
        monkeypatch.setattr(reroll, "default_mappers", lambda: (aggregator_mapper,))

        assert reroll.to_matchspec("requests") == "requests"

    def test_explicit_mappers_override_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = 0

        def _unused_default_mappers() -> tuple[object, ...]:
            nonlocal calls
            calls += 1
            return (aggregator_mapper,)

        monkeypatch.setattr(reroll, "default_mappers", _unused_default_mappers)

        assert (
            reroll.to_matchspec(
                "requests", mappers=(static_mapper({"requests": "python-requests"}),)
            )
            == "python-requests"
        )
        assert calls == 0

    def test_allow_pre_is_passed_through(self) -> None:
        assert (
            reroll.to_matchspec("requests==1.0.0rc1", mappers=(aggregator_mapper,), allow_pre=True)
            == "requests ==1.0.0.rc1"
        )

    def test_allow_pre_defaults_to_false(self) -> None:
        with pytest.raises(UnconvertableRequirementError, match="pre-release"):
            reroll.to_matchspec("requests==1.0.0rc1", mappers=(aggregator_mapper,))
