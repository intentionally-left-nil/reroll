"""Unit tests for `reroll.errors`."""

from __future__ import annotations

import logging

import pytest

from reroll.errors import (
    CacheWriteError,
    ConfigLoadError,
    DatabaseError,
    InvalidAbiTagError,
    InvalidCondaNameError,
    InvalidFilenameError,
    InvalidInterpreterTagError,
    InvalidMetadataError,
    InvalidPythonRequirementRangeError,
    InvalidRequirementError,
    InvalidVersionSpecifierError,
    NeedsArchSplitError,
    NetworkFetchError,
    PythonRangeMismatchError,
    RerollError,
    RerollInvalidWheelError,
    RerollRuntimeError,
    RerollScopeError,
    RerollUnconvertableError,
    UnconvertableMarkerError,
    UnconvertableRequirementError,
    UnexpectedError,
    UnresolvedCondaNameError,
    UnsupportedAbi3FloorError,
    UnsupportedFreeThreadedVersionError,
    UnsupportedInterpreterError,
    UnsupportedInterpreterVersionError,
    UnsupportedPlatformError,
    UnsupportedPrereleaseError,
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

    @pytest.mark.parametrize(
        "leaf",
        [
            UnsupportedAbi3FloorError,
            UnsupportedFreeThreadedVersionError,
            UnsupportedInterpreterError,
            UnsupportedInterpreterVersionError,
            UnsupportedPlatformError,
            UnsupportedPrereleaseError,
        ],
    )
    def test_scope_leaf_is_a_scope_error(self, leaf: type[RerollScopeError]) -> None:
        assert issubclass(leaf, RerollScopeError)

    @pytest.mark.parametrize(
        "leaf",
        [
            InvalidAbiTagError,
            InvalidFilenameError,
            InvalidInterpreterTagError,
            InvalidMetadataError,
            InvalidPythonRequirementRangeError,
            InvalidRequirementError,
            InvalidVersionSpecifierError,
            PythonRangeMismatchError,
        ],
    )
    def test_invalid_wheel_leaf_is_an_invalid_wheel_error(
        self, leaf: type[RerollInvalidWheelError]
    ) -> None:
        assert issubclass(leaf, RerollInvalidWheelError)

    @pytest.mark.parametrize(
        "leaf",
        [
            InvalidCondaNameError,
            NeedsArchSplitError,
            UnconvertableMarkerError,
            UnconvertableRequirementError,
            UnresolvedCondaNameError,
        ],
    )
    def test_unconvertable_leaf_is_an_unconvertable_error(
        self, leaf: type[RerollUnconvertableError]
    ) -> None:
        assert issubclass(leaf, RerollUnconvertableError)

    def test_reroll_error_has_exactly_the_four_documented_direct_categories(self) -> None:
        """`docs/errors_and_logging.md` describes a 4-branch hierarchy --
        `RerollError` -> one of exactly 4 categories -> a leaf. No other
        class may subclass `RerollError` directly.
        """
        assert set(RerollError.__subclasses__()) == {
            RerollScopeError,
            RerollInvalidWheelError,
            RerollUnconvertableError,
            RerollRuntimeError,
        }

    @pytest.mark.parametrize(
        "category",
        [RerollScopeError, RerollInvalidWheelError, RerollUnconvertableError, RerollRuntimeError],
    )
    def test_category_is_not_a_sibling_category(self, category: type[RerollError]) -> None:
        """The 4 categories are mutually exclusive branches -- a caller
        catching one never accidentally catches another.
        """
        siblings = {
            RerollScopeError,
            RerollInvalidWheelError,
            RerollUnconvertableError,
            RerollRuntimeError,
        } - {category}

        for sibling in siblings:
            assert not issubclass(category, sibling)


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

    def test_invalid_wheel_error_also_logs_an_info_level_skip_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`docs/errors_and_logging.md`'s Info paragraph: "Any wheel
        failure gets an info-level message explaining why a wheel was
        skipped." `RerollInvalidWheelError` is a wheel failure (the doc's
        Invalid wheel data category), so constructing one should produce
        an INFO-level record in addition to its WARNING-level one.
        """
        with caplog.at_level(logging.DEBUG, logger="reroll"):
            RerollInvalidWheelError("malformed filename")

        info_records = [record for record in caplog.records if record.levelno == logging.INFO]
        assert info_records, "expected an info-level skip message in addition to the warning"

    def test_unconvertable_error_also_logs_an_info_level_skip_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Same doc claim as
        `test_invalid_wheel_error_also_logs_an_info_level_skip_message`,
        for the Unconvertable wheels category.
        """
        with caplog.at_level(logging.DEBUG, logger="reroll"):
            RerollUnconvertableError("no matchspec equivalent")

        info_records = [record for record in caplog.records if record.levelno == logging.INFO]
        assert info_records, "expected an info-level skip message in addition to the warning"

    def test_runtime_error_does_not_log_an_info_level_skip_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`docs/errors_and_logging.md`'s "any wheel failure" skip message
        is about the wheel currently being processed. A `RerollRuntimeError`
        says nothing about that wheel -- it means reroll's own environment
        (network, cache, database) is unstable -- so, unlike the other
        three categories, it should not get a wheel-skip message.
        """
        with caplog.at_level(logging.DEBUG, logger="reroll"):
            RerollRuntimeError("network is down")

        info_records = [record for record in caplog.records if record.levelno == logging.INFO]
        assert not info_records, "a runtime error is not a wheel failure and should not skip one"

    def test_setting_one_category_logger_level_does_not_affect_a_sibling(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Category loggers are independently controllable: silencing
        `reroll.scope` (e.g. below its usual Info level) must not silence
        `reroll.invalid`, which logs at its own, unrelated level.
        """
        with (
            caplog.at_level(logging.CRITICAL, logger="reroll.scope"),
            caplog.at_level(logging.WARNING, logger="reroll.invalid"),
        ):
            RerollScopeError("silenced by its own logger's level")
            RerollInvalidWheelError("still visible")

        (record,) = caplog.records
        assert record.name == "reroll.invalid"
        assert "still visible" in record.message


# --------------------------------------------------------------------------
# Catching semantics: catching `RerollError` catches every category and
# every leaf; catching one category never catches a sibling category.
# --------------------------------------------------------------------------


class TestCatchingSemantics:
    @pytest.mark.parametrize(
        "category",
        [RerollScopeError, RerollInvalidWheelError, RerollUnconvertableError, RerollRuntimeError],
    )
    def test_catching_reroll_error_catches_every_category(
        self, category: type[RerollError]
    ) -> None:
        with pytest.raises(RerollError):
            raise category("boom")

    def test_catching_scope_error_lets_invalid_wheel_error_pass_through(self) -> None:
        with pytest.raises(RerollInvalidWheelError):
            _raise_unless_caught_as(RerollInvalidWheelError, "malformed filename", RerollScopeError)

    def test_catching_invalid_wheel_error_lets_unconvertable_error_pass_through(self) -> None:
        with pytest.raises(RerollUnconvertableError):
            _raise_unless_caught_as(
                RerollUnconvertableError, "no matchspec equivalent", RerollInvalidWheelError
            )

    def test_catching_unconvertable_error_lets_runtime_error_pass_through(self) -> None:
        with pytest.raises(RerollRuntimeError):
            _raise_unless_caught_as(RerollRuntimeError, "host is down", RerollUnconvertableError)

    def test_catching_runtime_error_lets_scope_error_pass_through(self) -> None:
        with pytest.raises(RerollScopeError):
            _raise_unless_caught_as(RerollScopeError, "out of scope", RerollRuntimeError)


# --------------------------------------------------------------------------
# `UnresolvedCondaNameError` -- raised by `reroll.name_mapping.map_name`
# when every mapper in the chain has run and none resolved a conda name.
# --------------------------------------------------------------------------


class TestUnresolvedCondaNameError:
    def test_message_format(self) -> None:
        exc = UnresolvedCondaNameError("tinylib")

        assert str(exc) == "no mapper resolved a conda name for 'tinylib': candidates=()"

    def test_logs_at_warning_to_the_unconvertable_logger(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`UnresolvedCondaNameError` overrides `__init__` for its own
        `name`/`candidates` fields, but still delegates to
        `RerollUnconvertableError.__init__` -- construction must still log,
        same as any other leaf of the category.
        """
        with caplog.at_level(logging.WARNING, logger="reroll.unconvertable"):
            UnresolvedCondaNameError("tinylib")

        (record,) = caplog.records
        assert record.name == "reroll.unconvertable"
        assert record.levelno == logging.WARNING
        assert "tinylib" in record.message


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


def _raise_unless_caught_as(
    to_raise: type[RerollError], message: str, catch_as: type[RerollError]
) -> None:
    """Raise `to_raise(message)` inside a `try` that only catches
    `catch_as`, failing the test if `catch_as` actually catches it. Used to
    demonstrate that one category's `except` does not catch a sibling
    category's exception.
    """
    try:
        raise to_raise(message)
    except catch_as:
        pytest.fail(f"{to_raise.__name__} should not be caught as {catch_as.__name__}")
