"""Unit tests for `reroll.errors`."""

from __future__ import annotations

import logging

import pytest

from reroll.errors import (
    CacheWriteError,
    ConfigLoadError,
    DatabaseError,
    NetworkFetchError,
    RerollError,
    RerollInvalidWheelError,
    RerollRuntimeError,
    RerollScopeError,
    RerollUnconvertableError,
    UnexpectedError,
    UpstreamDataError,
)

# --------------------------------------------------------------------------
# Hierarchy
# --------------------------------------------------------------------------


class TestHierarchy:
    @pytest.mark.parametrize(
        "category",
        [RerollScopeError, RerollInvalidWheelError, RerollUnconvertableError, RerollRuntimeError],
    )
    def test_category_is_a_reroll_error(self, category: type[RerollError]) -> None:
        assert issubclass(category, RerollError)

    def test_reroll_error_is_an_exception(self) -> None:
        assert issubclass(RerollError, Exception)

    @pytest.mark.parametrize(
        "leaf",
        [
            NetworkFetchError,
            CacheWriteError,
            UpstreamDataError,
            DatabaseError,
            ConfigLoadError,
            UnexpectedError,
        ],
    )
    def test_runtime_leaf_is_a_runtime_error(self, leaf: type[RerollRuntimeError]) -> None:
        assert issubclass(leaf, RerollRuntimeError)


# --------------------------------------------------------------------------
# Category logging: each category logs itself at construction, to its own
# category logger, at the level docs/errors_and_logging.md assigns it.
# --------------------------------------------------------------------------


class TestCategoryLogging:
    def test_scope_error_logs_at_info_to_the_scope_logger(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="reroll.scope"):
            RerollScopeError("musl is out of scope")

        (record,) = caplog.records
        assert record.name == "reroll.scope"
        assert record.levelno == logging.INFO
        assert "musl is out of scope" in record.message

    def test_invalid_wheel_error_logs_at_warning_to_the_invalid_logger(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="reroll.invalid"):
            RerollInvalidWheelError("malformed filename")

        (record,) = caplog.records
        assert record.name == "reroll.invalid"
        assert record.levelno == logging.WARNING
        assert "malformed filename" in record.message

    def test_unconvertable_error_logs_at_warning_to_the_unconvertable_logger(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="reroll.unconvertable"):
            RerollUnconvertableError("no matchspec equivalent")

        (record,) = caplog.records
        assert record.name == "reroll.unconvertable"
        assert record.levelno == logging.WARNING
        assert "no matchspec equivalent" in record.message

    def test_runtime_error_logs_at_error_to_the_runtime_logger(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="reroll.runtime"):
            RerollRuntimeError("network is down")

        (record,) = caplog.records
        assert record.name == "reroll.runtime"
        assert record.levelno == logging.ERROR
        assert "network is down" in record.message

    def test_leaf_error_logs_through_its_category(self, caplog: pytest.LogCaptureFixture) -> None:
        """A leaf exception's message reaches its category's logger too --
        raising a leaf is the only logging call a call site needs to make.
        """
        with caplog.at_level(logging.ERROR, logger="reroll.runtime"):
            NetworkFetchError("could not reach endoflife.date")

        (record,) = caplog.records
        assert record.name == "reroll.runtime"
        assert "could not reach endoflife.date" in record.message

    def test_category_loggers_are_children_of_the_reroll_logger(self) -> None:
        for name in ("reroll.scope", "reroll.invalid", "reroll.unconvertable", "reroll.runtime"):
            assert logging.getLogger(name).parent is logging.getLogger("reroll")

    def test_constructing_without_raising_still_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Logging happens at construction, not at `raise` -- an exception
        object that is merely instantiated (e.g. built and returned rather
        than raised) still gets recorded.
        """
        with caplog.at_level(logging.INFO, logger="reroll.scope"):
            RerollScopeError("built but never raised")

        assert len(caplog.records) == 1


# --------------------------------------------------------------------------
# `UnexpectedError` -- a safety net for a failure that doesn't fit any of
# the other leaves, wrapping whatever underlying exception triggered it.
# --------------------------------------------------------------------------


class TestUnexpectedError:
    def test_wraps_an_arbitrary_exception_via_chaining(self) -> None:
        original = KeyError("boom")

        def _raise_wrapped() -> None:
            try:
                raise original
            except KeyError as exc:
                raise UnexpectedError(f"unexpected failure: {exc}") from exc

        with pytest.raises(UnexpectedError) as exc_info:
            _raise_wrapped()

        assert exc_info.value.__cause__ is original

    def test_logs_at_error_to_the_runtime_logger(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.ERROR, logger="reroll.runtime"):
            UnexpectedError("unexpected failure: KeyError('boom')")

        (record,) = caplog.records
        assert record.name == "reroll.runtime"
        assert record.levelno == logging.ERROR
