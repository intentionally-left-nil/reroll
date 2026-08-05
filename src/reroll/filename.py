"""Parse PyPI wheel filenames into the configs the record layer needs.

A wheel filename encodes a name, version, optional build tag, and a
compatibility tag triple (interpreter / ABI / platform). That triple is the
*only* source of installability constraints for a wheel -- dependencies,
`Requires-Python`, and other metadata live in `METADATA` and are parsed
elsewhere. Keeping the two separate lets this module be tested exhaustively
from string literals.

Each `WheelConfig` this module produces corresponds to exactly one downstream
repodata record. A single filename can produce zero configs (e.g. a musl or
PyPy wheel -- routine when indexing a channel with millions of files, so
rejections are logged at `DEBUG` rather than raised), one config, or several
(compressed tag expansion, or a fat `universal2` binary that covers two
architectures).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any

from packaging.specifiers import SpecifierSet
from packaging.utils import (
    BuildTag,
    InvalidWheelFilename,
    NormalizedName,
    canonicalize_name,
    parse_wheel_filename,
)
from packaging.version import Version
from pydantic import (
    BaseModel,
    ConfigDict,
    GetCoreSchemaHandler,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import core_schema

from reroll.conda_package_name import CondaPackageName
from reroll.name_mapping import AmbiguousCondaName, NameMapper, exact_version, map_name

__all__ = [
    "AbiKind",
    "Arch",
    "PlatformFamily",
    "PythonRequirement",
    "WheelConfig",
    "parse_filename",
    "supported_archs",
]

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class AbiKind(Enum):
    NONE = "none"
    STABLE = "stable"
    VERSIONED = "versioned"


class PlatformFamily(Enum):
    ANY = "any"
    MANYLINUX = "manylinux"
    MACOS = "macos"
    WINDOWS = "windows"


class Arch(Enum):
    X86_64 = "x86_64"
    ARM64 = "arm64"


# --------------------------------------------------------------------------
# Annotated wrapper -- module-private
# --------------------------------------------------------------------------


def _coerce_version(value: Any) -> Version:
    return value if isinstance(value, Version) else Version(str(value))


class _PyVersionAnnotation:
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            _coerce_version,
            serialization=core_schema.to_string_ser_schema(),
        )


PyVersion = Annotated[Version, _PyVersionAnnotation]
"""`packaging.version.Version`, coercing from `str` and serializing back to
one. `arbitrary_types_allowed=True` is not an alternative here: it disables
coercion (a `str` input would be rejected outright) and would leak the raw
`Version` object into `model_dump()`."""


# --------------------------------------------------------------------------
# Interpreter tag grammar
# --------------------------------------------------------------------------

_INTERPRETER_RE = re.compile(r"^(py|cp)(\d)(\d*)$")


def _parse_interpreter(tag: str) -> tuple[str, int, int]:
    """Return `(prefix, major, minor)`. Raises `ValueError` if invalid.

    Grammar: `(py|cp)` followed by digits. The first digit is the major
    version; all remaining digits are the minor -- there is no way to encode
    a patch version in a wheel tag.

    Whether a given minor is actually *supported* depends on which ABI it's
    paired with (a `cp32-none` pin is unsupported but `cp32-abi3` isn't), so
    that check lives in `_check_interpreter_abi`, not here.
    """
    match = _INTERPRETER_RE.match(tag)
    if match is None:
        raise ValueError(f"invalid interpreter tag: {tag!r}")
    prefix, major_str, minor_str = match.groups()
    major = int(major_str)
    if major != 3:
        raise ValueError(f"unsupported interpreter major version: {tag!r}")
    if prefix == "cp" and minor_str == "":
        raise ValueError(f"'cp' interpreter tag requires an exact minor version: {tag!r}")
    minor = int(minor_str) if minor_str else 0
    return prefix, major, minor


# --------------------------------------------------------------------------
# ABI tag grammar
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _AbiInfo:
    kind: AbiKind
    minor: int | None
    free_threaded: bool


_ABI_CP_RE = re.compile(r"^cp(\d+)([dmut]*)$")


def _parse_abi(tag: str) -> _AbiInfo:
    """Return the parsed ABI shape. Raises `ValueError` if invalid.

    Accepts `none`; `abi3`/`abi3t`; `cp` + digits with an optional build
    suffix combining `d`/`m`/`u`/`t` in any order. Per
    docs/wheel_filename.md, only `d` (debug) is unsupported -- every CPython
    shipped on defaults/conda-forge from 3.4 onward is already a pymalloc,
    wide-unicode build, so `m`/`u` are harmless and `_normalize_abi` strips
    them rather than rejecting the wheel. Non-CPython ABIs are rejected by
    simply not matching any of these forms.
    """
    if tag == "none":
        return _AbiInfo(kind=AbiKind.NONE, minor=None, free_threaded=False)
    if tag in ("abi3", "abi3t"):
        return _AbiInfo(kind=AbiKind.STABLE, minor=None, free_threaded=tag.endswith("t"))

    match = _ABI_CP_RE.match(tag)
    if match is None:
        raise ValueError(f"invalid abi tag: {tag!r}")
    digits, suffix = match.groups()
    major = int(digits[0])
    if major != 3:
        raise ValueError(f"unsupported abi major version: {tag!r}")
    if "d" in suffix:
        raise ValueError(f"debug abi suffix is not supported: {tag!r}")
    minor = int(digits[1:]) if len(digits) > 1 else 0
    return _AbiInfo(kind=AbiKind.VERSIONED, minor=minor, free_threaded="t" in suffix)


def _normalize_abi(tag: str) -> str:
    """Canonicalize a `cp`-style ABI tag by dropping the `m`/`u` build
    suffixes reroll silently ignores (docs/wheel_filename.md). `none`,
    `abi3`, and `abi3t` have no such suffixes and pass through unchanged.
    Must only be called after `_parse_abi` has confirmed `tag` is valid
    (i.e. does not carry `d`), so the only suffix left to preserve is `t`.
    """
    match = _ABI_CP_RE.match(tag)
    if match is None:
        return tag
    digits, suffix = match.groups()
    return f"cp{digits}t" if "t" in suffix else f"cp{digits}"


def _check_interpreter_abi(interpreter: str, abi: str) -> None:
    """Cross-field validation for the legal (interpreter, ABI) pairings.
    Raises `ValueError` for everything else.

    Only six pairing *shapes* are legal: any interpreter with the `none`
    ABI; a `cpXY` interpreter with a `cpXY`/`cpXYt` ABI whose minor matches;
    or a `cpXY` interpreter with `abi3` (minor >= 2) or `abi3t` (minor >=
    15). Beyond that shape check, a `cp` tag paired with `none` or a
    matching versioned ABI *pins* that minor exactly, and Python 3.0-3.3 was
    never shipped on defaults or conda-forge, so those two shapes also
    require minor >= 4. `abi3`/`abi3t` loosen the tag to a floor instead, so
    they get their own (already-checked) floors rather than the 3.4 one --
    `cp32-abi3` is fine even though python 3.2 itself doesn't exist on those
    channels, because the floor resolves up to whatever 3.4+ IS available.
    """
    prefix, _, interp_minor = _parse_interpreter(interpreter)
    abi_info = _parse_abi(abi)

    if abi_info.kind is AbiKind.NONE:
        # Any interpreter tag may pair with the `none` ABI -- but a `cp` tag
        # pins that minor exactly, so it still needs to be >= 3.4 to resolve.
        if prefix == "cp" and interp_minor < 4:
            raise ValueError(f"CPython < 3.4 is unsupported: {interpreter!r}")
        return

    if prefix == "py":
        # A generic interpreter tag never advertises an ABI requirement --
        # no real interpreter emits a `py*` tag paired with anything but
        # `none`.
        raise ValueError(
            f"generic interpreter tag {interpreter!r} requires the 'none' ABI, got {abi!r}"
        )

    if abi_info.kind is AbiKind.VERSIONED:
        # A versioned ABI's minor must match the interpreter's, and (like
        # `none`) pins that minor exactly.
        if abi_info.minor != interp_minor:
            raise ValueError(f"versioned abi {abi!r} minor must match interpreter {interpreter!r}")
        if interp_minor < 4:
            raise ValueError(f"CPython < 3.4 is unsupported: {interpreter!r}")
        return

    # STABLE (abi3 / abi3t). The floor comes from the interpreter tag's own
    # minor, not from the acceptance-gate versions below -- a `cp313-abi3`
    # wheel installs on 3.13+ but not on 3.12, even though `abi3` itself is
    # legal all the way back to 3.2.
    if abi_info.free_threaded:
        if interp_minor < 15:
            raise ValueError(f"'abi3t' requires CPython >= 3.15, got {interpreter!r}")
    elif interp_minor < 2:
        raise ValueError(f"'abi3' requires CPython >= 3.2, got {interpreter!r}")


# --------------------------------------------------------------------------
# Platform tag grammar and architecture fan-out
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _PlatformInfo:
    family: PlatformFamily
    version: tuple[int, int] | None  # raw tag version; None for ANY/WINDOWS
    archs: tuple[Arch, ...]  # architectures this platform tag supports


_ARCH_BY_TAG = {"x86_64": Arch.X86_64, "aarch64": Arch.ARM64, "arm64": Arch.ARM64}

_LEGACY_MANYLINUX_VERSIONS = {
    "1": (2, 5),
    "2010": (2, 12),
    "2014": (2, 17),
}

_MANYLINUX_PEP600_RE = re.compile(r"^manylinux_(\d+)_(\d+)_(x86_64|aarch64)$")
_MANYLINUX_LEGACY_RE = re.compile(r"^manylinux(1|2010|2014)_(x86_64|aarch64)$")
_MACOS_RE = re.compile(r"^macosx_(\d+)_(\d+)_(x86_64|arm64|universal2)$")
_WINDOWS_RE = re.compile(r"^win_(amd64|arm64)$")


def _classify_platform(platform: str) -> _PlatformInfo | None:
    """Parse a platform tag into its family, raw version, and supported
    architectures. Returns `None` for anything reroll does not support.
    """
    if platform == "any":
        return _PlatformInfo(PlatformFamily.ANY, None, ())

    match = _MANYLINUX_PEP600_RE.match(platform)
    if match is not None:
        gmaj, gmin, arch = match.groups()
        return _PlatformInfo(
            PlatformFamily.MANYLINUX, (int(gmaj), int(gmin)), (_ARCH_BY_TAG[arch],)
        )

    match = _MANYLINUX_LEGACY_RE.match(platform)
    if match is not None:
        alias, arch = match.groups()
        return _PlatformInfo(
            PlatformFamily.MANYLINUX, _LEGACY_MANYLINUX_VERSIONS[alias], (_ARCH_BY_TAG[arch],)
        )

    match = _MACOS_RE.match(platform)
    if match is not None:
        maj, min_, arch = match.groups()
        version = (int(maj), int(min_))
        archs = (Arch.X86_64, Arch.ARM64) if arch == "universal2" else (_ARCH_BY_TAG[arch],)
        return _PlatformInfo(PlatformFamily.MACOS, version, archs)

    match = _WINDOWS_RE.match(platform)
    if match is not None:
        arch = match.group(1)
        win_arch = Arch.X86_64 if arch == "amd64" else Arch.ARM64
        return _PlatformInfo(PlatformFamily.WINDOWS, None, (win_arch,))

    return None


def supported_archs(platform: str) -> tuple[Arch | None, ...]:
    """The architectures a platform tag fans out to.

    `universal2` is the only multi-valued case: it is a fat binary covering
    both x86_64 and arm64, so it fans out to two configs that differ only in
    architecture -- the reason `arch` is a stored field on `WheelConfig`
    rather than a derived one. An unsupported platform yields `()`.
    """
    info = _classify_platform(platform)
    if info is None:
        return ()
    if info.family is PlatformFamily.ANY:
        return (None,)
    return info.archs


# --------------------------------------------------------------------------
# PythonRequirement
# --------------------------------------------------------------------------


class PythonRequirement(BaseModel):
    """The Python constraint a wheel tag implies.

    Only two shapes exist -- a floor or a pinned minor -- and no filename can
    express a patch version, so `minor: int` + `exact: bool` covers the
    entire domain without being able to represent an impossible state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    minor: int
    exact: bool

    @property
    def version(self) -> str:
        """`"3.13"` -- the only place `"3."` appears."""
        return f"3.{self.minor}"

    @property
    def specifier(self) -> SpecifierSet:
        """`"==3.13.*"` if pinned, else `">=3.13,<4"` -- the only place
        `"<4"` appears. A plain `@property`, not `@computed_field`:
        `SpecifierSet` has no pydantic core schema, so a `computed_field`
        return type would fail to generate one.
        """
        if self.exact:
            return SpecifierSet(f"=={self.version}.*")
        return SpecifierSet(f">={self.version},<4")

    @classmethod
    def floor(cls, minor: int) -> PythonRequirement:
        return cls(minor=minor, exact=False)

    @classmethod
    def pinned(cls, minor: int) -> PythonRequirement:
        return cls(minor=minor, exact=True)


# --------------------------------------------------------------------------
# WheelConfig
# --------------------------------------------------------------------------


class WheelConfig(BaseModel):
    """One repodata record's worth of PyPI-vocabulary information, plus the
    one conda concept this module knows about: `conda_name`.

    `normalized_pypi_name`/`conda_name`/`version`/`build` are repeated on
    every config derived from one filename even though they are invariant
    across them, because a consumer iterating configs to emit records needs
    them on each item.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    normalized_pypi_name: NormalizedName
    conda_name: CondaPackageName
    version: PyVersion
    build: BuildTag = ()
    interpreter: str
    abi: str
    platform: str
    arch: Arch | None

    _platform_info: _PlatformInfo = PrivateAttr()
    """Cached result of classifying `platform`, computed once by
    `_validate_cross_fields`. Reading this instead of re-calling
    `_classify_platform(self.platform)` from every property means there is
    exactly one place that must handle an unparseable `platform` -- the
    validator that legitimately sees unvalidated input -- rather than that
    same "impossible" `None` branch recurring in each property with no way
    for it to ever actually be reached.
    """

    @field_validator("normalized_pypi_name")
    @classmethod
    def _validate_normalized_pypi_name(cls, value: str) -> NormalizedName:
        """PEP 503 normalization: `canonicalize_name` -- e.g. `"Re_Roll.X"`
        -> `"re-roll-x"`.
        """
        return canonicalize_name(value)

    @field_validator("interpreter")
    @classmethod
    def _validate_interpreter(cls, value: str) -> str:
        _parse_interpreter(value)
        return value

    @field_validator("abi")
    @classmethod
    def _validate_abi(cls, value: str) -> str:
        """Validate, then normalize: `_parse_abi` raises on a `d` suffix or
        any other unsupported shape, and `_normalize_abi` strips the
        harmless `m`/`u` suffixes so the stored (and eventually emitted)
        `abi` tag never carries them.
        """
        _parse_abi(value)
        return _normalize_abi(value)

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> WheelConfig:
        _check_interpreter_abi(self.interpreter, self.abi)
        info = _classify_platform(self.platform)
        if info is None:
            raise ValueError(f"unsupported platform tag: {self.platform!r}")
        self._platform_info = info
        self._validate_arch_membership(info)
        return self

    def _validate_arch_membership(self, info: _PlatformInfo) -> None:
        """`arch` must be in `supported_archs(platform)`."""
        if info.family is PlatformFamily.ANY:
            if self.arch is not None:
                raise ValueError("arch must be None when platform is 'any'")
        elif self.arch not in info.archs:
            raise ValueError(f"arch {self.arch!r} unsupported for platform {self.platform!r}")

    @property
    def python(self) -> PythonRequirement:
        """The Python constraint this tag implies."""
        prefix, _, interp_minor = _parse_interpreter(self.interpreter)
        abi_info = _parse_abi(self.abi)
        if abi_info.kind is AbiKind.STABLE:
            return PythonRequirement.floor(interp_minor)
        if abi_info.kind is AbiKind.VERSIONED:
            return PythonRequirement.pinned(interp_minor)
        # NONE: the interpreter prefix decides. `py*` tags are floors --
        # `packaging` treats a generic interpreter tag as compatible with
        # every later minor -- while `cp*` tags pin the exact minor even
        # with no ABI requirement at all. This asymmetry is real, not an
        # oversight: a `py32-none-any` wheel from 2011 installs on today's
        # Python, but a `cp313-none-any` wheel does not install on 3.14.
        if prefix == "py":
            return PythonRequirement.floor(interp_minor)
        return PythonRequirement.pinned(interp_minor)

    @property
    def abi_kind(self) -> AbiKind:
        return _parse_abi(self.abi).kind

    @property
    def free_threaded(self) -> bool:
        return _parse_abi(self.abi).free_threaded

    @property
    def platform_family(self) -> PlatformFamily:
        return self._platform_info.family

    @property
    def platform_version(self) -> tuple[int, int] | None:
        """Glibc or macOS floor, clamped per arch."""
        info = self._platform_info
        if info.version is None:
            return None
        if info.family is PlatformFamily.MACOS and self.arch is Arch.ARM64:
            # arm64 macOS did not exist before 11.0, so the arm64 half of a
            # `universal2` tag has an effective floor of 11.0 even when the
            # tag's own version is lower (it's carried by the x86_64 half).
            return max(info.version, (11, 0))
        return info.version


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _sort_key(config: WheelConfig) -> tuple[str, str, str, str]:
    return (
        config.interpreter,
        config.abi,
        config.platform,
        config.arch.value if config.arch is not None else "",
    )


def parse_filename(filename: str, mappers: Sequence[NameMapper]) -> tuple[WheelConfig, ...]:
    """Parse a wheel filename into zero or more `WheelConfig`s.

    `mappers` is required, with no default: an empty chain (`()`) is a
    legitimate policy ("always use the normalized PyPI name"), but it must
    be requested explicitly rather than silently assumed by a caller who
    forgot the argument.

    Never raises for filename input: an unparseable filename or a filename
    with no supported `(tag, arch)` combination both return `()`, and the
    reason is logged at `DEBUG`. A mapper that raises `AmbiguousCondaName`
    also yields `()`, but is logged at `WARNING` since (unlike the other
    rejections) it has a concrete, actionable fix -- add a disambiguating
    mapper. Any other exception a mapper raises propagates: the "never
    raises" contract covers filename input only, not mapper bugs.
    """
    try:
        name, version, build, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename as exc:
        logger.debug("unparseable wheel filename %r: %s", filename, exc)
        return ()

    # The name and version are tag-invariant, so this is resolved once,
    # before the tag loop below -- not once per `(tag, arch)` combination.
    try:
        conda_name = map_name(name, exact_version(version), mappers)
    except AmbiguousCondaName as exc:
        logger.warning("ambiguous conda name for wheel filename %r: %s", filename, exc)
        return ()

    configs: list[WheelConfig] = []
    for tag in tags:
        # An unsupported platform's `supported_archs()` is `()`; without the
        # `or [None]` fallback the loop below would never run for it, and no
        # rejection reason would be logged. With it, one construction attempt
        # is made with `arch=None`, which fails the arch-membership validator
        # with a precise message -- keeping every rejection reason flowing
        # through the single `ValidationError` logging site below.
        for arch in supported_archs(tag.platform) or [None]:
            try:
                configs.append(
                    WheelConfig(
                        normalized_pypi_name=name,
                        conda_name=conda_name,
                        version=version,
                        build=build,
                        interpreter=tag.interpreter,
                        abi=tag.abi,
                        platform=tag.platform,
                        arch=arch,
                    )
                )
            except ValidationError as exc:
                logger.debug(
                    "rejected wheel config for %r (tag=%s, arch=%s): %s",
                    filename,
                    tag,
                    arch,
                    exc.errors(),
                )

    # `packaging` returns tags as a frozenset, whose iteration order varies
    # under hash randomization; repodata must be reproducible, so sort
    # explicitly. No dedup needed: tags are already unique within the
    # frozenset and `supported_archs` returns distinct values.
    configs.sort(key=_sort_key)
    return tuple(configs)
