"""Reroll's error hierarchy.

Every failure reroll raises while converting a wheel falls into exactly one
of four categories -- `RerollScopeError`, `RerollInvalidWheelError`,
`RerollUnconvertableError`, or `RerollRuntimeError` -- and every category
subclasses `RerollError`. See `docs/errors_and_logging.md` for what each
category means and when to use it. Every concrete error reroll raises is a
leaf of one of these four categories, and every leaf is defined here rather
than next to its raise site, so the whole hierarchy is visible in one place.

Each category logs itself, at construction, to its own logger
(`reroll.scope`/`reroll.invalid`/`reroll.unconvertable`/`reroll.runtime`, all
children of the `reroll` logger) at the level `docs/errors_and_logging.md`
assigns that category. Raising a `RerollError` is therefore the only logging
call a call site needs to make; callers who want more or less log volume for
one category tune its logger directly, e.g.
`logging.getLogger("reroll.unconvertable").setLevel(logging.ERROR)`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from reroll.name_mapping import Candidate

_ROOT_LOGGER = logging.getLogger("reroll")
"""Creating this eagerly, before any category logger, is what makes each
category logger's `.parent` resolve to it -- `logging.getLogger` otherwise
leaves an unconstructed ancestor as an internal placeholder, and a child
logger created first would report `root`, not `reroll`, as its parent.
"""


class RerollError(Exception):
    """Base class for every error reroll raises while converting a wheel."""


class RerollScopeError(RerollError):
    """A wheel that is valid, but outside reroll's deliberate scope -- e.g.
    an interpreter or platform reroll has chosen not to support yet. The
    only recourse for a caller is to open a bug against reroll itself.
    """

    _logger = logging.getLogger("reroll.scope")

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self._logger.info(str(self))


class UnsupportedAbi3FloorError(RerollScopeError):
    """`abi3` paired with an interpreter below CPython 3.2, `abi3`'s own
    floor.
    """


class UnsupportedFreeThreadedVersionError(RerollScopeError):
    """A free-threaded ABI (`cp3XXt`/`abi3t`) below the minimum CPython
    version a free-threaded build exists for.
    """


class UnsupportedInterpreterError(RerollScopeError):
    """The interpreter tag names an interpreter major version reroll does
    not support -- anything but CPython (or a generic `py`) 3.x.
    """


class UnsupportedInterpreterVersionError(RerollScopeError):
    """CPython below 3.4, which reroll does not support."""


class UnsupportedPlatformError(RerollScopeError):
    """The platform tag names a platform family, or an architecture within
    an otherwise-supported platform family, reroll does not support.
    """


class UnsupportedPrereleaseError(RerollScopeError):
    """A pre-release wheel version, rejected because the caller has not
    opted in via `allow_pre`.
    """


class RerollInvalidWheelError(RerollError):
    """The wheel's filename or METADATA does not conform to reroll's
    criteria for a well-formed wheel. Generally the wheel's fault; a caller
    with its own errata can fix the wheel up and retry.
    """

    _logger = logging.getLogger("reroll.invalid")

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self._logger.warning(str(self))


class InvalidAbiTagError(RerollInvalidWheelError):
    """The ABI tag does not match a supported shape, carries the
    unsupported `d` (debug) suffix, or pairs illegally with its interpreter
    tag.
    """


class InvalidFilenameError(RerollInvalidWheelError):
    """A wheel filename does not conform to the PyPI wheel filename spec."""


class InvalidInterpreterTagError(RerollInvalidWheelError):
    """The interpreter tag does not match the `(py|cp)` + digits grammar."""


class InvalidMetadataError(RerollInvalidWheelError):
    """A METADATA header meant to appear at most once was undecodable, or
    repeated with disagreeing values; or `Name` does not conform to the
    modern (PEP 508) name grammar.
    """


class InvalidPythonRequirementRangeError(RerollInvalidWheelError):
    """A `Requires-Python`/wheel-tag specifier set matches no Python 3
    minor at all, or a non-contiguous set of them.
    """


class InvalidRequirementError(RerollInvalidWheelError):
    """A `Requires-Dist` entry that does not parse as a PEP 508 requirement,
    even after every lenient fixup has been tried.
    """


class InvalidVersionSpecifierError(RerollInvalidWheelError):
    """A version-specifier field (e.g. `Requires-Python`) that does not
    parse as a PEP 440 specifier set, even after every lenient fixup has
    been tried.
    """


class RerollUnconvertableError(RerollError):
    """A valid wheel with no semantically equivalent conda representation.
    Reroll does not guess or loosen a requirement to work around this.
    """

    _logger = logging.getLogger("reroll.unconvertable")

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self._logger.warning(str(self))


class InvalidCondaNameError(RerollUnconvertableError):
    """A name mapped from PyPI to conda is not a legal conda package name
    (CEP 26) -- either exceeds the length limit or fails the name regex.
    """


class NeedsArchSplitError(RerollUnconvertableError):
    """A noarch record's `Requires-Dist` marker still refers to a
    platform-specific key (`platform_system`, `platform_machine`,
    `sys_platform`, or `os_name`) after evaluating it against the known
    Python pinning and extra. A single noarch record can't represent it;
    the caller must emit one record per `CondaSubdir` instead.
    """


class PythonRangeMismatchError(RerollUnconvertableError):
    """A wheel's filename-implied Python range and its `Requires-Python`
    metadata do not intersect at all -- no valid record can be emitted for
    it.
    """


class UnconvertableMarkerError(RerollUnconvertableError):
    """A marker construct with no matchspec equivalent: an `in`/`not in`
    test, a marker key without a matchspec equivalent, or an unrecognized
    value or comparator for a key that otherwise has one.
    """


class UnconvertableRequirementError(RerollUnconvertableError):
    """A PEP 508 requirement with no representable conda MatchSpec: a
    direct URL reference, a local version label, a pre-release version
    without `allow_pre`, an extra name over 64 characters once normalized,
    a marker referring to `extra`, or an assembled MatchSpec that fails
    py-rattler's own validation.
    """


class UnresolvedCondaNameError(RerollUnconvertableError):
    """Raised by `reroll.name_mapping.map_name` when every mapper in the
    chain has run and none of them returned a final conda name.
    """

    def __init__(
        self,
        name: str,
        candidates: Sequence[Candidate] = (),
    ) -> None:
        self.name = name
        self.candidates = tuple(candidates)
        super().__init__(
            f"no mapper resolved a conda name for {name!r}: candidates={self.candidates!r}"
        )


class RerollRuntimeError(RerollError):
    """A network, cache, or database failure. Unlike every other category,
    this says nothing about the wheel itself -- it means reroll's host
    environment is unstable, and batch processing should stop.
    """

    _logger = logging.getLogger("reroll.runtime")

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self._logger.error(str(self))


class NetworkFetchError(RerollRuntimeError):
    """A network request to an upstream data source failed."""


class CacheWriteError(RerollRuntimeError):
    """A local cache file could not be written or installed."""


class UpstreamDataError(RerollRuntimeError):
    """An upstream data source responded, but its content was not shaped as
    expected.
    """


class DatabaseError(RerollRuntimeError):
    """A local sqlite database could not be built, read, or written."""


class ConfigLoadError(RerollRuntimeError):
    """A locally packaged configuration or mapping table could not be
    loaded.
    """


class UnexpectedError(RerollRuntimeError):
    """A failure that doesn't fit any other leaf -- an unanticipated
    exception from reroll's own code or a third-party dependency it calls
    into. A `RerollRuntimeError` leaf, not its own category: reroll cannot
    say anything about the *wheel* when this happens, so the correct
    response is the same as any other runtime issue -- stop and
    investigate, rather than blame the wheel currently being processed.

    Always raise with `from`, chaining the original exception as `__cause__`,
    so the underlying failure is never actually lost -- only categorized.
    """
