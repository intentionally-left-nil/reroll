"""Unit tests for `reroll.default_mappers`."""

from __future__ import annotations

import importlib

import pytest

from reroll.name_mapping import aggregator_mapper, passthrough_mapper

# `import reroll.default_mappers` would resolve to the function re-exported
# by `reroll/__init__.py` (it shadows the submodule of the same name), so
# `importlib.import_module` is used to get the actual module to patch.
_default_mappers_module = importlib.import_module("reroll.default_mappers")

# Each of grayskull_mapper, conda_lock_mapper, overrides_mapper, and
# parselmouth_mapper is fully tested in its own module; here we only need
# to confirm `default_mappers` calls all four in order and appends
# `aggregator_mapper` then `passthrough_mapper` last, so the factories are
# stubbed out rather than built for real.


class TestDefaultMappers:
    def test_composes_grayskull_conda_lock_overrides_parselmouth_then_the_aggregator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        grayskull_stub = object()
        conda_lock_stub = object()
        overrides_stub = object()
        parselmouth_stub = object()
        monkeypatch.setattr(_default_mappers_module, "grayskull_mapper", lambda: grayskull_stub)
        monkeypatch.setattr(_default_mappers_module, "conda_lock_mapper", lambda: conda_lock_stub)
        monkeypatch.setattr(_default_mappers_module, "overrides_mapper", lambda: overrides_stub)
        monkeypatch.setattr(_default_mappers_module, "parselmouth_mapper", lambda: parselmouth_stub)

        assert _default_mappers_module.default_mappers() == (
            grayskull_stub,
            conda_lock_stub,
            overrides_stub,
            parselmouth_stub,
            aggregator_mapper,
            passthrough_mapper,
        )
