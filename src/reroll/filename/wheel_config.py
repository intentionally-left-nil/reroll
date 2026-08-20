"""One repodata record's worth of parsed wheel-filename fields."""

from __future__ import annotations

from packaging.utils import BuildTag, NormalizedName, canonicalize_name
from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator, model_validator

from reroll.conda_package_name import CondaPackageName
from reroll.errors import InvalidAbiTagError, UnsupportedPlatformError
from reroll.filename.abi import check_interpreter_abi, normalize_abi, parse_abi
from reroll.filename.enums import AbiKind, Arch, PlatformFamily
from reroll.filename.interpreter import parse_interpreter
from reroll.filename.platform import PlatformInfo, classify_platform
from reroll.filename.py_version import PyVersion
from reroll.filename.python_requirement import PythonRequirement
from reroll.name_mapping import NameResolution


class WheelConfig(BaseModel):
    """One repodata record's worth of PyPI-vocabulary information, plus the
    one conda concept this module knows about: `conda_name`.

    `normalized_pypi_name`/`conda_name`/`version`/`build` are repeated on
    every config derived from one filename even though they are invariant
    across them, because a consumer iterating configs to emit records needs
    them on each item. `name_resolution` is the `NameResolution` `conda_name`
    itself came from -- also invariant, for the same reason.
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
    name_resolution: NameResolution

    _platform_info: PlatformInfo = PrivateAttr()
    """Cached result of classifying `platform`, computed once by
    `_validate_cross_fields`. Reading this instead of re-calling
    `classify_platform(self.platform)` from every property means there is
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
        parse_interpreter(value)
        return value

    @field_validator("abi")
    @classmethod
    def _validate_abi(cls, value: str) -> str:
        """Validate, then normalize: `parse_abi` raises on a `d` suffix or
        any other unsupported shape. A `WheelConfig` is the terminal,
        concrete form of one wheel tag, so a `STABLE`-kind ABI (`abi3`/
        `abi3t`) is rejected outright -- it must already have been exploded
        into concrete per-minor ABIs (`reroll.filename.abi3.explode_abi3`)
        before reaching this constructor. `normalize_abi` strips the
        harmless `m`/`u` suffixes so the stored (and eventually emitted)
        `abi` tag never carries them.
        """
        info = parse_abi(value)
        if info.kind is AbiKind.STABLE:
            raise InvalidAbiTagError(
                f"abi3/abi3t must be exploded into concrete per-minor ABIs before "
                f"constructing a WheelConfig, got {value!r}"
            )
        return normalize_abi(value)

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> WheelConfig:
        check_interpreter_abi(self.interpreter, self.abi)
        info = classify_platform(self.platform)
        if info is None:
            raise UnsupportedPlatformError(f"unsupported platform tag: {self.platform!r}")
        self._platform_info = info
        self._validate_arch_membership(info)
        return self

    def _validate_arch_membership(self, info: PlatformInfo) -> None:
        """`arch` must be in `supported_archs(platform)`."""
        if info.family is PlatformFamily.ANY:
            if self.arch is not None:
                raise UnsupportedPlatformError("arch must be None when platform is 'any'")
        elif self.arch not in info.archs:
            raise UnsupportedPlatformError(
                f"arch {self.arch!r} unsupported for platform {self.platform!r}"
            )

    @property
    def python(self) -> PythonRequirement:
        """The Python constraint this tag implies."""
        prefix, _, interp_minor = parse_interpreter(self.interpreter)
        abi_info = parse_abi(self.abi)
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
        return parse_abi(self.abi).kind

    @property
    def free_threaded(self) -> bool:
        return parse_abi(self.abi).free_threaded

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
