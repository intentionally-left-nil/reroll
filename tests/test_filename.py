"""Unit tests for `reroll.filename`."""

from __future__ import annotations

import itertools
import logging
from collections.abc import Sequence
from typing import cast

import pytest
from packaging.specifiers import SpecifierSet
from packaging.tags import Tag
from packaging.utils import NormalizedName
from packaging.version import Version

from reroll.errors import (
    InvalidAbiTagError,
    InvalidCondaNameError,
    InvalidInterpreterTagError,
    InvalidPythonRequirementRangeError,
    RerollError,
    UnresolvedCondaNameError,
    UnsupportedFreeThreadedVersionError,
    UnsupportedInterpreterError,
    UnsupportedInterpreterVersionError,
    UnsupportedPlatformError,
)
from reroll.filename import (
    AbiKind,
    Arch,
    InvalidFilenameError,
    PlatformFamily,
    PythonRequirement,
    UnsupportedPrereleaseError,
    WheelConfig,
    parse_filename,
    supported_archs,
)
from reroll.filename.abi3 import explode_abi3
from reroll.filename.python_requirement import minor_range
from reroll.name_mapping import (
    Candidate,
    CandidateSource,
    NameMappers,
    NameResolution,
    Winner,
    aggregator_mapper,
    passthrough_mapper,
)


def _config(
    *,
    normalized_pypi_name: str = "tinylib",
    conda_name: str = "tinylib",
    version: str = "1.2.3",
    build: tuple[()] | tuple[int, str] = (),
    interpreter: str = "py3",
    abi: str = "none",
    platform: str = "any",
    arch: Arch | None = None,
) -> WheelConfig:
    """A valid `WheelConfig` (py3-none-any) with fields overridden for tests
    that only care about one axis at a time.
    """
    return WheelConfig(
        normalized_pypi_name=normalized_pypi_name,
        conda_name=conda_name,
        version=version,
        build=build,
        interpreter=interpreter,
        abi=abi,
        platform=platform,
        arch=arch,
        name_resolution=NameResolution(
            pypi_name=normalized_pypi_name,
            winner=Winner(
                conda_name=conda_name,
                probability=0.0,
                source=CandidateSource.PASSTHROUGH,
                mapper="passthrough_mapper",
            ),
        ),
    )


# --------------------------------------------------------------------------
# `interpreter` validator
# --------------------------------------------------------------------------


class TestInterpreterValidator:
    @pytest.mark.parametrize(
        ("interpreter", "expected_minor"),
        [
            ("py3", 0),
            ("py38", 8),
            ("py313", 13),
            ("py3100", 100),
            ("cp313", 13),
            ("cp314", 14),
        ],
    )
    def test_accepts_valid_interpreter_tags(self, interpreter: str, expected_minor: int) -> None:
        abi = interpreter if interpreter.startswith("cp") else "none"
        config = _config(interpreter=interpreter, abi=abi)

        assert config.interpreter == interpreter
        assert config.python.minor == expected_minor

    @pytest.mark.parametrize(
        "interpreter",
        ["ip313", "pp310", "gp313"],
        ids=["ip313", "pp310", "gp313"],
    )
    def test_rejects_non_cpython_interpreter_prefix(self, interpreter: str) -> None:
        with pytest.raises(InvalidInterpreterTagError):
            _config(interpreter=interpreter)

    @pytest.mark.parametrize("interpreter", ["py2", "cp27", "py4", "cp40"])
    def test_rejects_non_python3_major(self, interpreter: str) -> None:
        with pytest.raises(UnsupportedInterpreterError):
            _config(interpreter=interpreter)

    def test_rejects_cp_without_a_minor(self) -> None:
        with pytest.raises(InvalidInterpreterTagError):
            _config(interpreter="cp3")

    @pytest.mark.parametrize("interpreter", ["py", "cp", "py3x"])
    def test_rejects_missing_or_non_digit_version(self, interpreter: str) -> None:
        with pytest.raises(InvalidInterpreterTagError):
            _config(interpreter=interpreter)


# --------------------------------------------------------------------------
# `cp*` interpreter tags below Python 3.4 (docs/wheel_filename.md: "Python
# Tag | abi tag | Allowed" table, background section on the python tag).
#
# Python 3.0-3.3 was never shipped on defaults or conda-forge. A `cp` tag
# paired with `none` or a matching versioned ABI *pins* that minor exactly,
# so it's unsupported below 3.4 -- no python 3.0-3.3 build exists to pin to.
#
# `abi3`/`abi3t` rows are covered separately: `WheelConfig` itself never
# accepts a raw `abi3`/`abi3t` tag at all (see `TestAbiValidator`) -- that
# ABI kind must already have been exploded into concrete per-minor tags by
# `explode_abi3` before reaching a `WheelConfig`, so the floor-legality
# table for `abi3`/`abi3t` (PEP 384's 3.2 floor, PEP 803's 3.15 floor) is
# tested against `explode_abi3` directly, in `TestAbi3FloorIsPoint2` near
# `TestExplodeAbi3`. A generic `py*` tag is a pure floor with no tie to a
# real interpreter at all, so it's unaffected by any of this -- `py32` is
# fine even though python 3.2 itself doesn't exist on those channels.
# --------------------------------------------------------------------------


class TestPythonVersionFloorTable:
    """Every `none`/versioned-ABI row of the doc's table, tested verbatim
    (the `abi3`/`abi3t` rows live in `TestAbi3FloorIsPoint2` instead).
    """

    @pytest.mark.parametrize(
        ("interpreter", "abi"),
        [
            ("py3", "none"),
            ("py32", "none"),
        ],
        ids=["py3-none", "py32-none"],
    )
    def test_allowed(self, interpreter: str, abi: str) -> None:
        _config(interpreter=interpreter, abi=abi)

    @pytest.mark.parametrize(
        ("interpreter", "abi", "expected"),
        [
            ("py32", "cp37", InvalidAbiTagError),
            ("py32", "abi3", InvalidAbiTagError),
            ("cp3", "none", InvalidInterpreterTagError),
            ("cp3", "abi3", InvalidInterpreterTagError),
            ("cp32", "none", UnsupportedInterpreterVersionError),
            ("cp32", "cp32", UnsupportedInterpreterVersionError),
            ("cp30", "abi3", InvalidAbiTagError),
            ("cp31", "abi3", InvalidAbiTagError),
        ],
        ids=[
            "py32-cp37",
            "py32-abi3",
            "cp3-none",
            "cp3-abi3",
            "cp32-none",
            "cp32-cp32",
            "cp30-abi3",
            "cp31-abi3",
        ],
    )
    def test_disallowed(self, interpreter: str, abi: str, expected: type[Exception]) -> None:
        """The `abi3` rows here are disallowed by the blanket
        `WheelConfig` rejection of any raw `abi3`/`abi3t` tag (regardless of
        interpreter) -- not by the floor logic `TestAbi3FloorIsPoint2`
        exercises against `explode_abi3`. Both routes land on
        `InvalidAbiTagError`.
        """
        with pytest.raises(expected):
            _config(interpreter=interpreter, abi=abi)


class TestCpythonBelow34Rejected:
    """Beyond the table's `cp32`/`cp3` rows: the `< 3.4` restriction applies
    to *any* minor below 4 when the tag pins an exact version (`none` or a
    matching versioned ABI). `abi3`'s own (lower, 3.2) floor is a property
    of `explode_abi3`, covered by `TestAbi3FloorIsPoint2` near
    `TestExplodeAbi3`.
    """

    @pytest.mark.parametrize("minor", [0, 1, 2, 3])
    def test_rejects_low_minor_with_none_abi(self, minor: int) -> None:
        with pytest.raises(UnsupportedInterpreterVersionError):
            _config(interpreter=f"cp3{minor}", abi="none")

    @pytest.mark.parametrize("minor", [0, 1, 2, 3])
    def test_rejects_low_minor_with_matching_versioned_abi(self, minor: int) -> None:
        with pytest.raises(UnsupportedInterpreterVersionError):
            _config(interpreter=f"cp3{minor}", abi=f"cp3{minor}")

    def test_3_4_itself_is_the_floor_for_none_and_versioned_pins(self) -> None:
        """3.4 is explicitly allowed ('less than 3.4' is the cutoff) for the
        two *exact-pin* shapes.
        """
        _config(interpreter="cp34", abi="none")
        _config(interpreter="cp34", abi="cp34")


# --------------------------------------------------------------------------
# `abi` validator
# --------------------------------------------------------------------------


class TestAbiValidator:
    def test_accepts_none(self) -> None:
        assert _config(abi="none").abi == "none"

    @pytest.mark.parametrize(("interpreter", "abi"), [("cp313", "abi3"), ("cp315", "abi3t")])
    def test_rejects_abi3_and_abi3t_directly(self, interpreter: str, abi: str) -> None:
        """A `WheelConfig` is the terminal, concrete form of one wheel tag
        -- `abi3`/`abi3t` are inherently non-terminal (a promise spanning
        many minors), so they must already have been exploded into concrete
        `cp3Y-cp3Y[t]` tags by `explode_abi3` before a `WheelConfig` is ever
        constructed. This holds regardless of whether the interpreter+ABI
        pairing would otherwise be a *legal* `abi3`/`abi3t` combination --
        that floor-legality question belongs to `explode_abi3`
        (`TestAbi3FloorIsPoint2`), not to `WheelConfig`.
        """
        with pytest.raises(InvalidAbiTagError):
            _config(interpreter=interpreter, abi=abi)

    @pytest.mark.parametrize("abi", ["cp313", "cp313t"])
    def test_accepts_versioned_cpython_abi(self, abi: str) -> None:
        assert _config(interpreter="cp313", abi=abi).abi == abi

    @pytest.mark.parametrize("abi", ["cp313d", "cp313dm", "cp313du", "cp313dmu", "cp313dt"])
    def test_rejects_debug_suffix(self, abi: str) -> None:
        """Only `d` (debug) blocks the wheel -- see `TestAbiBuildSuffixes`
        below for the full `d`/`m`/`u`/`t` combination matrix.
        """
        with pytest.raises(InvalidAbiTagError):
            _config(interpreter="cp313", abi=abi)

    @pytest.mark.parametrize("abi", ["pypy310_pp73", "graalpy_38_native"])
    def test_rejects_non_cpython_abi(self, abi: str) -> None:
        with pytest.raises(InvalidAbiTagError):
            _config(interpreter="cp313", abi=abi)

    @pytest.mark.parametrize("abi", ["abi4", "abi3x"])
    def test_rejects_abi_that_is_not_abi3_or_abi3t(self, abi: str) -> None:
        with pytest.raises(InvalidAbiTagError):
            _config(interpreter="cp313", abi=abi)

    def test_rejects_versioned_abi_with_non_python3_major(self) -> None:
        with pytest.raises(InvalidAbiTagError):
            _config(interpreter="cp313", abi="cp208")


# --------------------------------------------------------------------------
# ABI build suffixes: `d`/`m`/`u`/`t` combinations
#
# docs/wheel_filename.md: every CPython shipped on defaults/conda-forge from
# 3.4 onward is a pymalloc, wide-unicode build, so the `m` and `u` suffixes
# are silently stripped rather than treated as a mismatch. `d` (debug) is a
# real ABI difference and still blocks the wheel. `t` (free-threaded) is
# unrelated to any of that and is preserved verbatim. The four flags can
# appear in any subset and (per real-world wheels) in any order, so this
# exhaustively covers all 16 subsets of {d, m, u, t} rather than just a few
# hand-picked examples.
# --------------------------------------------------------------------------

_ABI_BUILD_FLAGS = ("d", "m", "u", "t")


def _abi_flag_combinations() -> list[frozenset[str]]:
    return [
        frozenset(combo)
        for r in range(len(_ABI_BUILD_FLAGS) + 1)
        for combo in itertools.combinations(_ABI_BUILD_FLAGS, r)
    ]


def _combo_id(flags: frozenset[str]) -> str:
    return "".join(sorted(flags)) or "none"


# Pre-partitioned by whether `d` is present, rather than generating the full
# 16-combination list once and having each test `pytest.skip()` its other
# half: a skip masks the difference between "not applicable" and "silently
# never ran", and would hide a future test that's *wrongly* skipped. Each
# `@pytest.mark.parametrize` below only ever sees cases it actually asserts
# on.
_COMBOS_WITH_D = [flags for flags in _abi_flag_combinations() if "d" in flags]
_COMBOS_WITHOUT_D = [flags for flags in _abi_flag_combinations() if "d" not in flags]


class TestAbiBuildSuffixes:
    @pytest.mark.parametrize("flags", _COMBOS_WITH_D, ids=_combo_id)
    def test_d_blocks_the_wheel_regardless_of_other_flags(self, flags: frozenset[str]) -> None:
        suffix = "".join(flag for flag in _ABI_BUILD_FLAGS if flag in flags)

        with pytest.raises(InvalidAbiTagError):
            _config(interpreter="cp313", abi=f"cp313{suffix}")

    @pytest.mark.parametrize("flags", _COMBOS_WITHOUT_D, ids=_combo_id)
    def test_m_and_u_are_stripped_when_d_is_absent(self, flags: frozenset[str]) -> None:
        suffix = "".join(flag for flag in _ABI_BUILD_FLAGS if flag in flags)

        config = _config(interpreter="cp313", abi=f"cp313{suffix}")

        expected_abi = "cp313t" if "t" in flags else "cp313"
        assert config.abi == expected_abi
        assert config.free_threaded is ("t" in flags)

    @pytest.mark.parametrize(
        ("abi", "expected"),
        [
            ("cp313m", "cp313"),
            ("cp313u", "cp313"),
            ("cp313mu", "cp313"),
            ("cp313um", "cp313"),
            ("cp313tm", "cp313t"),
            ("cp313mt", "cp313t"),
            ("cp313tmu", "cp313t"),
            ("cp313mut", "cp313t"),
        ],
    )
    def test_stripping_is_order_independent(self, abi: str, expected: str) -> None:
        """Real-world wheels don't agree on flag order (`cp36dm`, `cp37mu`,
        `cp313td` were all seen in the wild) -- the result must not depend
        on which order `m`/`u`/`t` appear in.
        """
        assert _config(interpreter="cp313", abi=abi).abi == expected


# --------------------------------------------------------------------------
# `platform` validator
# --------------------------------------------------------------------------


class TestPlatformValidator:
    def test_accepts_any(self) -> None:
        assert _config(platform="any", arch=None).platform == "any"

    def test_accepts_pep600_manylinux(self) -> None:
        config = _config(platform="manylinux_2_17_x86_64", arch=Arch.X86_64)
        assert config.platform == "manylinux_2_17_x86_64"

    def test_accepts_pep600_manylinux_aarch64(self) -> None:
        config = _config(platform="manylinux_2_17_aarch64", arch=Arch.ARM64)
        assert config.platform == "manylinux_2_17_aarch64"

    @pytest.mark.parametrize(
        ("alias", "arch_suffix", "arch"),
        [
            ("manylinux1", "x86_64", Arch.X86_64),
            ("manylinux1", "aarch64", Arch.ARM64),
            ("manylinux2010", "x86_64", Arch.X86_64),
            ("manylinux2010", "aarch64", Arch.ARM64),
            ("manylinux2014", "x86_64", Arch.X86_64),
            ("manylinux2014", "aarch64", Arch.ARM64),
        ],
    )
    def test_accepts_legacy_manylinux_aliases(
        self, alias: str, arch_suffix: str, arch: Arch
    ) -> None:
        config = _config(platform=f"{alias}_{arch_suffix}", arch=arch)
        assert config.platform == f"{alias}_{arch_suffix}"

    @pytest.mark.parametrize("arch_tag", ["x86_64", "arm64", "universal2"])
    def test_accepts_macos_arches(self, arch_tag: str) -> None:
        arch = Arch.X86_64 if arch_tag in ("x86_64",) else Arch.ARM64
        config = _config(platform=f"macosx_10_9_{arch_tag}", arch=arch)
        assert config.platform == f"macosx_10_9_{arch_tag}"

    @pytest.mark.parametrize(
        ("platform", "arch"),
        [("win_amd64", Arch.X86_64), ("win_arm64", Arch.ARM64)],
    )
    def test_accepts_windows(self, platform: str, arch: Arch) -> None:
        assert _config(platform=platform, arch=arch).platform == platform

    def test_rejects_musllinux(self) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform="musllinux_1_2_x86_64", arch=Arch.X86_64)

    @pytest.mark.parametrize("platform", ["linux_x86_64", "linux_aarch64"])
    def test_rejects_bare_linux(self, platform: str) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform=platform, arch=Arch.X86_64)

    @pytest.mark.parametrize("platform", ["manylinux_2_17_i686", "win32", "macosx_10_9_i386"])
    def test_rejects_32bit(self, platform: str) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform=platform, arch=Arch.X86_64)

    def test_rejects_win_ia64(self) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform="win_ia64", arch=Arch.X86_64)

    @pytest.mark.parametrize("arch_suffix", ["ppc64le", "s390x", "riscv64", "loongarch64", "ppc64"])
    def test_rejects_unsupported_linux_arches(self, arch_suffix: str) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform=f"manylinux_2_17_{arch_suffix}", arch=Arch.X86_64)

    @pytest.mark.parametrize(
        "platform",
        [
            "macosx_10_6_intel",
            "macosx_10_6_fat",
            "macosx_10_6_fat32",
            "macosx_10_6_fat64",
            "macosx_10_6_universal",
            "macosx_10_6_ppc",
            "macosx_10_6_ppc64",
        ],
    )
    def test_rejects_legacy_mac_formats(self, platform: str) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform=platform, arch=Arch.X86_64)

    @pytest.mark.parametrize("platform", ["ios_13_0_arm64_iphoneos", "android_21_arm64_v8a"])
    def test_rejects_mobile_platforms(self, platform: str) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform=platform, arch=Arch.ARM64)

    @pytest.mark.parametrize("platform", ["emscripten_1_0_wasm32", "wasi_0_0_wasm32"])
    def test_rejects_web_platforms(self, platform: str) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform=platform, arch=Arch.X86_64)


# --------------------------------------------------------------------------
# interpreter x ABI pairings
# --------------------------------------------------------------------------


class TestInterpreterAbiPairing:
    def test_py_star_with_none_is_legal(self) -> None:
        _config(interpreter="py38", abi="none")

    def test_cp_with_none_is_legal(self) -> None:
        _config(interpreter="cp313", abi="none")

    def test_cp_with_matching_versioned_abi_is_legal(self) -> None:
        _config(interpreter="cp313", abi="cp313")

    def test_cp_with_matching_free_threaded_abi_is_legal(self) -> None:
        _config(interpreter="cp314", abi="cp314t")

    @pytest.mark.parametrize("abi", ["abi3", "cp313", "cp313t"])
    def test_rejects_generic_interpreter_with_non_none_abi(self, abi: str) -> None:
        with pytest.raises(InvalidAbiTagError):
            _config(interpreter="py3", abi=abi)

    def test_rejects_versioned_abi_minor_mismatch(self) -> None:
        with pytest.raises(InvalidAbiTagError):
            _config(interpreter="cp313", abi="cp312")

    def test_rejects_abi3_regardless_of_floor(self) -> None:
        """A raw `abi3` tag is always rejected by `WheelConfig`
        (`TestAbiValidator.test_rejects_abi3_and_abi3t_directly`) --
        `cp31-abi3` is doubly invalid: below `abi3`'s own 3.2 floor
        (`TestAbi3FloorIsPoint2`, tested against `explode_abi3`) *and*
        never a legal `WheelConfig` input at all.
        """
        with pytest.raises(InvalidAbiTagError):
            _config(interpreter="cp31", abi="abi3")

    def test_rejects_abi3t_regardless_of_floor(self) -> None:
        with pytest.raises(InvalidAbiTagError):
            _config(interpreter="cp310", abi="abi3t")

    def test_rejects_versioned_free_threaded_below_313(self) -> None:
        """Free-threaded builds only exist from Python 3.13 onward
        (docs/wheel_to_conda_dependencies.md); a versioned free-threaded
        ABI below that minor is rejected the same way `abi3t` is rejected
        below its own 3.15 floor.
        """
        with pytest.raises(UnsupportedFreeThreadedVersionError):
            _config(interpreter="cp39", abi="cp39t")


# --------------------------------------------------------------------------
# `arch` membership
# --------------------------------------------------------------------------


class TestArchMembership:
    def test_any_platform_requires_arch_none(self) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform="any", arch=Arch.X86_64)

    def test_non_any_platform_requires_arch_not_none(self) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform="manylinux_2_17_x86_64", arch=None)

    def test_arch_must_be_in_supported_archs(self) -> None:
        with pytest.raises(UnsupportedPlatformError):
            _config(platform="manylinux_2_17_x86_64", arch=Arch.ARM64)

    def test_universal2_accepts_either_arch(self) -> None:
        _config(platform="macosx_11_0_universal2", arch=Arch.X86_64)
        _config(platform="macosx_11_0_universal2", arch=Arch.ARM64)


# --------------------------------------------------------------------------
# `python` / `abi_kind` / `free_threaded`
# --------------------------------------------------------------------------


class TestDerivedPythonAndAbiFields:
    """`abi3`/`abi3t` rows are excluded here: `WheelConfig` never accepts a
    raw `abi3`/`abi3t` tag (`TestAbiValidator`), so `AbiKind.STABLE` can
    never be a valid instance's `abi_kind`.
    """

    @pytest.mark.parametrize(
        ("interpreter", "abi", "minor", "exact", "abi_kind", "free_threaded"),
        [
            ("py3", "none", 0, False, AbiKind.NONE, False),
            ("py38", "none", 8, False, AbiKind.NONE, False),
            ("py313", "none", 13, False, AbiKind.NONE, False),
            ("cp313", "none", 13, True, AbiKind.NONE, False),
            ("cp313", "cp313", 13, True, AbiKind.VERSIONED, False),
            ("cp314", "cp314t", 14, True, AbiKind.VERSIONED, True),
        ],
    )
    def test_table(
        self,
        interpreter: str,
        abi: str,
        minor: int,
        exact: bool,
        abi_kind: AbiKind,
        free_threaded: bool,
    ) -> None:
        config = _config(interpreter=interpreter, abi=abi)

        assert config.python == PythonRequirement(minor=minor, exact=exact)
        assert config.abi_kind is abi_kind
        assert config.free_threaded is free_threaded

    def test_py_star_floors_while_cp_star_pins(self) -> None:
        """`py*` tags are floors (installable on every later minor) while
        `cp*` tags pin an exact minor, even with no ABI requirement at all.
        This asymmetry is real, not a bug -- assert it explicitly so it
        can't be "simplified" away.
        """
        py_config = _config(interpreter="py313", abi="none")
        cp_config = _config(interpreter="cp313", abi="none")

        assert py_config.python == PythonRequirement.floor(13)
        assert cp_config.python == PythonRequirement.pinned(13)
        assert py_config.python != cp_config.python


# --------------------------------------------------------------------------
# `PythonRequirement.version` / `.specifier`
# --------------------------------------------------------------------------


class TestPythonRequirement:
    def test_floor_version_and_specifier(self) -> None:
        req = PythonRequirement.floor(8)

        assert req.version == "3.8"
        assert req.specifier == SpecifierSet(">=3.8,<4")

    def test_pinned_version_and_specifier(self) -> None:
        req = PythonRequirement.pinned(13)

        assert req.version == "3.13"
        assert req.specifier == SpecifierSet("==3.13.*")

    def test_specifier_intersects_with_requires_python(self) -> None:
        req = PythonRequirement.floor(8)
        requires_python = SpecifierSet(">=3.9,<3.13,!=3.10.1")

        intersection = req.specifier & requires_python

        assert intersection.contains("3.11.0")
        assert not intersection.contains("3.8.0")
        assert not intersection.contains("3.13.0")
        assert not intersection.contains("3.10.1")


# --------------------------------------------------------------------------
# `minor_range`
# --------------------------------------------------------------------------


class TestMinorRange:
    def test_open_ended_floor_has_no_ceiling(self) -> None:
        assert minor_range(SpecifierSet(">=3.8,<4")) == (8, None)

    def test_agrees_with_python_requirement_floor(self) -> None:
        assert minor_range(PythonRequirement.floor(8).specifier) == (8, None)

    def test_single_minor_pin_is_a_width_one_range(self) -> None:
        assert minor_range(SpecifierSet("==3.13.*")) == (13, 14)

    def test_agrees_with_python_requirement_pinned(self) -> None:
        assert minor_range(PythonRequirement.pinned(13).specifier) == (13, 14)

    def test_two_sided_range(self) -> None:
        assert minor_range(SpecifierSet(">=3.9,<3.12")) == (9, 12)

    def test_gap_in_the_middle_is_rejected(self) -> None:
        """`!=3.9.*` carves a hole out of an otherwise-open range -- no
        `(floor, ceiling)` pair can represent "3.8, then 3.10 onward, but
        not 3.9", so this is rejected rather than silently approximated.
        """
        with pytest.raises(InvalidPythonRequirementRangeError, match="not a contiguous"):
            minor_range(SpecifierSet(">=3.8,!=3.9.*,<3.12"))

    def test_no_satisfying_minor_is_rejected(self) -> None:
        with pytest.raises(InvalidPythonRequirementRangeError, match="not a contiguous"):
            minor_range(SpecifierSet("<3.0"))

    # ----------------------------------------------------------------------
    # Micro-level boundaries that fall strictly inside a minor -- real
    # `Requires-Python` values (unlike a wheel filename's own tag, which is
    # always minor-aligned) can have these, and a minor is still "in range"
    # if *any* of its patch releases satisfy the specifiers, not just its
    # `.0` release.
    # ----------------------------------------------------------------------

    def test_lower_bound_mid_minor_still_counts_that_minor(self) -> None:
        """`>=3.9.16` doesn't satisfy `3.9.0`, but 3.9.16 and every later
        3.9.x release still make minor 9 the correct floor.
        """
        assert minor_range(SpecifierSet(">=3.9.16")) == (9, None)

    def test_upper_bound_mid_minor_still_counts_that_minor(self) -> None:
        """`<3.9.5` excludes `3.9.5` onward, but 3.9.0-3.9.4 still make
        minor 9 the correct (inclusive) top of the range.
        """
        assert minor_range(SpecifierSet(">=3.5,<3.9.5")) == (5, 10)

    def test_lower_bound_exactly_at_minor_start_is_unaffected(self) -> None:
        assert minor_range(SpecifierSet(">=3.9.0")) == (9, None)

    def test_exclusive_lower_bound_just_above_minor_start_still_counts_it(self) -> None:
        """`>3.9.0` excludes only the single `3.9.0` release; `3.9.1`
        onward still satisfies, so minor 9 is still the floor.
        """
        assert minor_range(SpecifierSet(">3.9.0")) == (9, None)

    def test_exclusive_upper_bound_exactly_at_minor_start_excludes_it(self) -> None:
        """`<3.9.0` excludes every 3.9.x release outright (unlike
        `<3.9.5`), so minor 9 is correctly not part of the range.
        """
        assert minor_range(SpecifierSet(">=3.5,<3.9.0")) == (5, 9)

    def test_inclusive_upper_bound_mid_minor_still_counts_that_minor(self) -> None:
        assert minor_range(SpecifierSet(">=3.5,<=3.9.5")) == (5, 10)

    def test_single_patch_exclusion_does_not_remove_the_minor(self) -> None:
        """`!=3.9.7` only excludes that one release; every other 3.9.x
        release still satisfies, unlike a wildcard exclusion.
        """
        assert minor_range(SpecifierSet(">=3.8,!=3.9.7,<3.12")) == (8, 12)

    def test_micro_level_floor_and_ceiling_in_the_same_minor(self) -> None:
        """Both bounds land inside minor 9 without touching its `.0`
        release on either end; 9 is still the sole satisfying minor.
        """
        assert minor_range(SpecifierSet(">=3.9.16,<3.9.99")) == (9, 10)

    def test_compatible_release_with_patch_component(self) -> None:
        """`~=3.9.5` means `>=3.9.5,==3.9.*` -- floor lands mid-minor 9,
        ceiling is the aligned start of minor 10.
        """
        assert minor_range(SpecifierSet("~=3.9.5")) == (9, 10)

    def test_compatible_release_without_patch_component(self) -> None:
        """`~=3.9` means `>=3.9,==3.*` -- an open floor at minor 9, same
        shape as `>=3.9,<4`.
        """
        assert minor_range(SpecifierSet("~=3.9")) == (9, None)

    def test_unparseable_legacy_equality_clause_is_ignored_as_an_anchor(self) -> None:
        """`===notaversion` (PEP 440 arbitrary equality) isn't a real
        `Version` once its trailing `.*` (absent here) is stripped, so it
        contributes no extra probe micros -- but it still applies as a
        real constraint, which no `3.x.y` release satisfies, matching no
        minor at all.
        """
        with pytest.raises(InvalidPythonRequirementRangeError, match="not a contiguous"):
            minor_range(SpecifierSet("===notaversion"))

    # ----------------------------------------------------------------------
    # Pre-release (alpha/beta/rc/dev) bounds -- e.g. a package pinned to a
    # Python beta during a preview cycle. `Version` comparison already
    # orders these below their own final release, so no dedicated
    # prerelease-aware probing is needed, but the shape is worth pinning
    # down explicitly since it's a distinct, real `Requires-Python` input.
    # ----------------------------------------------------------------------

    def test_prerelease_floor_at_a_minors_start(self) -> None:
        """`>=3.13.0b1` is satisfied by final `3.13.0` (a final release
        always sorts after its own beta), so minor 13's `.0` baseline
        probe alone already finds it -- no different from `>=3.13`.
        """
        assert minor_range(SpecifierSet(">=3.13.0b1")) == (13, None)

    def test_prerelease_floor_mid_minor(self) -> None:
        """`>=3.9.5b1` is not satisfied by `3.9.0`, but final `3.9.5`
        onward does satisfy it -- the same mid-minor-floor handling as a
        plain `>=3.9.5`, just anchored at a prerelease version.
        """
        assert minor_range(SpecifierSet(">=3.9.5b1")) == (9, None)

    def test_prerelease_ceiling_mid_minor(self) -> None:
        """`<3.9.5rc1` still leaves `3.9.0`-`3.9.4` satisfying, so minor 9
        is still included -- a prerelease ceiling doesn't behave any
        differently from a plain micro-level one.
        """
        assert minor_range(SpecifierSet(">=3.5,<3.9.5rc1")) == (5, 10)

    def test_prerelease_exclusion_does_not_remove_the_minor(self) -> None:
        """`!=3.9.0rc1` excludes only that one prerelease; every actual
        release in minor 9 (final `3.9.0` included) still satisfies.
        """
        assert minor_range(SpecifierSet(">=3.8,!=3.9.0rc1,<3.12")) == (8, 12)

    # ----------------------------------------------------------------------
    # Multiple/adjacent `!=` hole exclusions.
    # ----------------------------------------------------------------------

    def test_multiple_single_patch_exclusions_in_one_minor_still_satisfied(self) -> None:
        """Two separate patch-level exclusions in the same minor still
        leave other 3.9.x releases satisfying it, same as one.
        """
        assert minor_range(SpecifierSet(">=3.8,!=3.9.1,!=3.9.2,<3.12")) == (8, 12)

    def test_hole_exactly_at_a_mid_minor_floors_anchor_still_counts_it(self) -> None:
        """`!=3.9.16` excludes exactly the version `>=3.9.16` would
        otherwise start at, but `3.9.17` onward still satisfies both, so
        minor 9 is still the floor.
        """
        assert minor_range(SpecifierSet(">=3.9.16,!=3.9.16")) == (9, None)

    def test_consecutive_exclusions_past_a_mid_minor_floor_still_satisfied(self) -> None:
        """Excluding the first two patches past a mid-minor floor still
        leaves later patches in the same minor satisfying it.
        """
        assert minor_range(SpecifierSet(">=3.9.16,!=3.9.17,!=3.9.18")) == (9, None)

    def test_two_separate_full_minor_exclusions_is_rejected(self) -> None:
        """`!=3.7.*` and `!=3.9.*` carve two separate holes out of an
        otherwise-open range -- still not representable as one
        `(floor, ceiling)` pair, same as a single hole.
        """
        with pytest.raises(InvalidPythonRequirementRangeError, match="not a contiguous"):
            minor_range(SpecifierSet(">=3.6,!=3.7.*,!=3.9.*,<3.11"))

    def test_two_adjacent_full_minor_exclusions_is_rejected(self) -> None:
        """`!=3.7.*,!=3.8.*` together carve out two consecutive minors --
        still a gap, not a shifted floor.
        """
        with pytest.raises(InvalidPythonRequirementRangeError, match="not a contiguous"):
            minor_range(SpecifierSet(">=3.6,!=3.7.*,!=3.8.*,<3.10"))


# --------------------------------------------------------------------------
# `platform_version`
# --------------------------------------------------------------------------


class TestPlatformVersion:
    def test_any_has_no_platform_version(self) -> None:
        assert _config(platform="any", arch=None).platform_version is None

    def test_windows_has_no_platform_version(self) -> None:
        assert _config(platform="win_amd64", arch=Arch.X86_64).platform_version is None

    def test_pep600_manylinux_reads_tag_version(self) -> None:
        config = _config(platform="manylinux_2_28_x86_64", arch=Arch.X86_64)
        assert config.platform_version == (2, 28)

    def test_manylinux2014_normalizes_to_2_17_but_platform_stays_verbatim(
        self,
    ) -> None:
        config = _config(platform="manylinux2014_x86_64", arch=Arch.X86_64)

        assert config.platform == "manylinux2014_x86_64"
        assert config.platform_version == (2, 17)

    def test_legacy_alias_aarch64_reads_the_same_mapped_version_as_x86_64(self) -> None:
        config = _config(platform="manylinux2014_aarch64", arch=Arch.ARM64)
        assert config.platform_version == (2, 17)

    def test_macos_x86_64_reads_tag_version(self) -> None:
        config = _config(platform="macosx_10_9_x86_64", arch=Arch.X86_64)
        assert config.platform_version == (10, 9)

    def test_macos_arm64_clamps_to_11_0(self) -> None:
        config = _config(platform="macosx_10_9_universal2", arch=Arch.ARM64)
        assert config.platform_version == (11, 0)

    def test_macos_arm64_above_11_0_is_unclamped(self) -> None:
        config = _config(platform="macosx_12_0_arm64", arch=Arch.ARM64)
        assert config.platform_version == (12, 0)

    def test_macos_x86_64_half_of_universal2_is_unclamped(self) -> None:
        config = _config(platform="macosx_10_9_universal2", arch=Arch.X86_64)
        assert config.platform_version == (10, 9)


# --------------------------------------------------------------------------
# `platform_family`
# --------------------------------------------------------------------------


class TestPlatformFamily:
    def test_any(self) -> None:
        assert _config(platform="any", arch=None).platform_family is PlatformFamily.ANY

    def test_manylinux(self) -> None:
        config = _config(platform="manylinux_2_17_x86_64", arch=Arch.X86_64)
        assert config.platform_family is PlatformFamily.MANYLINUX

    def test_macos(self) -> None:
        config = _config(platform="macosx_10_9_x86_64", arch=Arch.X86_64)
        assert config.platform_family is PlatformFamily.MACOS

    def test_windows(self) -> None:
        config = _config(platform="win_amd64", arch=Arch.X86_64)
        assert config.platform_family is PlatformFamily.WINDOWS


# --------------------------------------------------------------------------
# name normalization / version coercion
# --------------------------------------------------------------------------


class TestNameAndVersionCoercion:
    def test_name_is_pep503_normalized(self) -> None:
        config = _config(normalized_pypi_name="Re_Roll.X")
        assert config.normalized_pypi_name == "re-roll-x"

    def test_consecutive_mixed_separators_collapse_to_one_hyphen(self) -> None:
        """docs/wheel_filename.md's normalization algorithm is
        `re.sub(r"[-_.]+", "-", name).lower()` -- a *run* of `-`/`_`/`.` in
        any combination collapses to a single hyphen, not one hyphen per
        separator character.
        """
        config = _config(normalized_pypi_name="Foo--Bar__Baz..Qux._-Quux")
        assert config.normalized_pypi_name == "foo-bar-baz-qux-quux"

    def test_version_is_normalized(self) -> None:
        config = _config(version="1.2.3.alpha1")
        assert config.version == Version("1.2.3a1")
        assert str(config.version) == "1.2.3a1"


# --------------------------------------------------------------------------
# ABI explosion: `explode_abi3` (docs/wheel_filename.md: "ABI explosion")
#
# `abi3`/`abi3t` are not themselves installable tags -- they are a promise
# ("compatible with every later minor") that has to be made concrete before
# a `WheelConfig` can express a Python constraint. `explode_abi3` turns one
# `cp3X-abi3-*` tag into one `cp3Y-cp3Y-*` tag per minor from `X` up through
# a caller-supplied ceiling, since nothing in the filename itself carries an
# upper bound. Before exploding, it also checks that `(interpreter, abi)` is
# itself a legal pairing (via `check_interpreter_abi`) -- a tag that fails
# this check is left alone rather than exploded, since a `WheelConfig` never
# accepts a raw `abi3`/`abi3t` tag (`TestAbiValidator`) and will reject it.
# --------------------------------------------------------------------------


def _tag(interpreter: str, abi: str, platform: str = "manylinux_2_17_x86_64") -> Tag:
    return Tag(interpreter, abi, platform)


class TestExplodeAbi3:
    def test_non_abi3_tags_are_returned_unchanged(self) -> None:
        tags = {_tag("cp313", "cp313"), _tag("py3", "none", "any")}

        assert explode_abi3(tags, abi3_upper_bound="3.13") == frozenset(tags)

    def test_expands_cp_abi3_across_the_full_range(self) -> None:
        result = explode_abi3({_tag("cp39", "abi3")}, abi3_upper_bound="3.11")

        assert result == {
            _tag("cp39", "cp39"),
            _tag("cp310", "cp310"),
            _tag("cp311", "cp311"),
        }

    def test_original_abi3_tag_is_not_itself_in_the_result(self) -> None:
        result = explode_abi3({_tag("cp39", "abi3")}, abi3_upper_bound="3.9")

        assert _tag("cp39", "abi3") not in result

    def test_abi3t_expands_to_a_free_threaded_abi_per_minor(self) -> None:
        result = explode_abi3({_tag("cp315", "abi3t")}, abi3_upper_bound="3.16")

        assert result == {
            _tag("cp315", "cp315t"),
            _tag("cp316", "cp316t"),
        }

    def test_platform_is_preserved_on_every_generated_variant(self) -> None:
        result = explode_abi3({_tag("cp313", "abi3", "win_amd64")}, abi3_upper_bound="3.14")

        assert all(tag.platform == "win_amd64" for tag in result)

    def test_floor_above_upper_bound_leaves_the_tag_unchanged(self) -> None:
        """A legal pairing whose floor is already past `abi3_upper_bound`
        would explode to an empty per-minor range; instead of vanishing, it
        is left alone -- the same treatment `check_interpreter_abi`-illegal
        pairings get just below -- so `WheelConfig` still sees, and rejects,
        the raw tag rather than the wheel silently losing every config.
        """
        tag = _tag("cp316", "abi3")

        assert explode_abi3({tag}, abi3_upper_bound="3.15") == {tag}

    def test_single_minor_range_when_floor_equals_upper_bound(self) -> None:
        result = explode_abi3({_tag("cp313", "abi3")}, abi3_upper_bound="3.13")

        assert result == {_tag("cp313", "cp313")}

    def test_deduplicates_against_a_tag_already_present(self) -> None:
        result = explode_abi3(
            {_tag("cp39", "abi3"), _tag("cp310", "cp310")}, abi3_upper_bound="3.10"
        )

        assert result == {_tag("cp39", "cp39"), _tag("cp310", "cp310")}

    def test_deduplicates_overlapping_ranges_from_two_abi3_tags(self) -> None:
        """Compressed-tag expansion (`cp39.cp310-abi3-*`) hands `explode_abi3`
        two separate input tags whose exploded ranges overlap at `cp310`;
        the overlap must collapse to one tag, not two.
        """
        result = explode_abi3(
            {_tag("cp39", "abi3"), _tag("cp310", "abi3")}, abi3_upper_bound="3.10"
        )

        assert result == {_tag("cp39", "cp39"), _tag("cp310", "cp310")}

    @pytest.mark.parametrize("interpreter", ["py38", "cp3", "cp40"])
    def test_leaves_non_cp3x_interpreters_paired_with_abi3_unchanged(
        self, interpreter: str
    ) -> None:
        """These pairings are already invalid (checked by
        `check_interpreter_abi`, via a bad interpreter shape rather than a
        floor); explosion must not silently drop them -- leaving the tag
        alone lets `WheelConfig` reject and log it, exactly as it would with
        no explosion involved at all.
        """
        tag = _tag(interpreter, "abi3")

        assert explode_abi3({tag}, abi3_upper_bound="3.13") == {tag}

    def test_legal_low_floor_is_exploded_and_then_cleaned_up_downstream(self) -> None:
        """`cp32-abi3` is a *legal* pairing (`TestAbi3FloorIsPoint2`), so
        `explode_abi3` explodes it in full -- `cp32/cp32` and `cp33/cp33`
        are only invalid on a *different* ground (CPython < 3.4, enforced
        by `WheelConfig` itself), which `explode_abi3` does not need to
        know about.
        """
        result = explode_abi3({_tag("cp32", "abi3")}, abi3_upper_bound="3.6")

        assert result == {
            _tag("cp32", "cp32"),
            _tag("cp33", "cp33"),
            _tag("cp34", "cp34"),
            _tag("cp35", "cp35"),
            _tag("cp36", "cp36"),
        }

    def test_does_not_resolve_a_default_upper_bound_when_no_abi3_tag_is_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail() -> int:
            raise AssertionError("must not look up a default when nothing needs exploding")

        monkeypatch.setattr("reroll.filename.python_latest_release.latest_python_minor", _fail)

        tags = {_tag("cp313", "cp313")}
        assert explode_abi3(tags, abi3_upper_bound=None) == frozenset(tags)

    def test_none_upper_bound_uses_the_default_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("reroll.filename.python_latest_release.latest_python_minor", lambda: 11)

        result = explode_abi3({_tag("cp39", "abi3")}, abi3_upper_bound=None)

        assert result == {
            _tag("cp39", "cp39"),
            _tag("cp310", "cp310"),
            _tag("cp311", "cp311"),
        }

    @pytest.mark.parametrize("upper_bound", ["3.15.3", "15", "abc", "3.", "3"])
    def test_rejects_malformed_upper_bound(self, upper_bound: str) -> None:
        with pytest.raises(ValueError, match="abi3_upper_bound"):
            explode_abi3({_tag("cp39", "abi3")}, abi3_upper_bound=upper_bound)


# --------------------------------------------------------------------------
# `explode_abi3`'s floor-legality pre-check (docs/wheel_filename.md: "Python
# Tag | abi tag | Allowed" table's `abi3` rows, plus `abi3t`'s own floor).
#
# `abi3` (PEP 384) is a floor, not a pin, introduced at Python 3.2. `abi3t`
# (PEP 803) has a much higher floor: 3.15. A tag below its ABI's own floor
# is illegitimate on its own terms -- not merely narrowed by some other
# rule -- so `explode_abi3` must reject it *before* exploding, via
# `check_interpreter_abi`, rather than exploding it and hoping a later
# check (e.g. `WheelConfig`'s `< 3.4` rule) happens to clean it up. That
# hope holds for plain `abi3` (2 < 4, so the low end is always caught
# anyway) but not for `abi3t`: nothing else in `WheelConfig` enforces a
# free-threading-specific floor, so a `cp310-abi3t` tag left unchecked
# would incorrectly explode into "valid-looking" free-threaded 3.10
# configs that don't correspond to any real CPython build.
# --------------------------------------------------------------------------


class TestAbi3FloorIsPoint2:
    @pytest.mark.parametrize("minor", [0, 1])
    def test_abi3_below_its_own_floor_is_left_unexploded(self, minor: int) -> None:
        tag = _tag(f"cp3{minor}", "abi3")

        assert explode_abi3({tag}, abi3_upper_bound="3.13") == {tag}

    @pytest.mark.parametrize("minor", [2, 3, 4, 13])
    def test_abi3_at_or_above_its_own_floor_is_exploded(self, minor: int) -> None:
        result = explode_abi3({_tag(f"cp3{minor}", "abi3")}, abi3_upper_bound=f"3.{minor}")

        assert result == {_tag(f"cp3{minor}", f"cp3{minor}")}

    @pytest.mark.parametrize("minor", [0, 1, 13, 14])
    def test_abi3t_below_its_own_much_higher_floor_is_left_unexploded(self, minor: int) -> None:
        """`minor=13`/`14` is the important case here, not `0`/`1`: those
        are close enough to `abi3t`'s real 3.15 floor that a bug which only
        checks "is this a `cp3<digits>` interpreter" (rather than the
        floor itself) would not catch them.
        """
        tag = _tag(f"cp3{minor}", "abi3t")

        assert explode_abi3({tag}, abi3_upper_bound="3.16") == {tag}

    def test_abi3t_at_its_own_floor_is_exploded(self) -> None:
        result = explode_abi3({_tag("cp315", "abi3t")}, abi3_upper_bound="3.15")

        assert result == {_tag("cp315", "cp315t")}


# --------------------------------------------------------------------------
# `parse_filename` orchestration
# --------------------------------------------------------------------------


class TestParseFilename:
    def test_happy_path(self) -> None:
        (config,) = parse_filename("tinylib-1.2.3-py3-none-any.whl", mappers=(passthrough_mapper,))

        assert config.normalized_pypi_name == "tinylib"
        assert config.version == Version("1.2.3")
        assert config.interpreter == "py3"
        assert config.abi == "none"
        assert config.platform == "any"
        assert config.arch is None

    def test_compressed_tags_expand_to_multiple_configs(self) -> None:
        configs = parse_filename(
            "tinylib-1.2.3-py2.py3-none-any.whl", mappers=(passthrough_mapper,)
        )

        assert [c.interpreter for c in configs] == ["py3"]

    def test_compressed_tags_expand_all_valid_combinations(self) -> None:
        configs = parse_filename(
            "tinylib-1.2.3-py38.py39-none-any.whl", mappers=(passthrough_mapper,)
        )

        assert [c.interpreter for c in configs] == ["py38", "py39"]

    def test_universal2_produces_exactly_two_configs(self) -> None:
        configs = parse_filename(
            "tinylib-1.2.3-cp313-cp313-macosx_10_9_universal2.whl", mappers=(passthrough_mapper,)
        )

        assert len(configs) == 2
        assert {c.arch for c in configs} == {Arch.X86_64, Arch.ARM64}

    def test_unparseable_filename_raises(self) -> None:
        with pytest.raises(InvalidFilenameError):
            parse_filename("not-a-wheel-filename", mappers=(passthrough_mapper,))

    def test_unparseable_filename_logs_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            caplog.at_level(logging.WARNING, logger="reroll.invalid"),
            pytest.raises(InvalidFilenameError),
        ):
            parse_filename("not-a-wheel-filename", mappers=(passthrough_mapper,))

        assert "unparseable" in caplog.text

    def test_all_tags_unsupported_raises(self) -> None:
        with pytest.raises(UnsupportedPlatformError):
            parse_filename(
                "tinylib-1.2.3-cp313-cp313-musllinux_1_2_x86_64.whl", mappers=(passthrough_mapper,)
            )

    def test_rejection_logs_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            caplog.at_level(logging.DEBUG, logger="reroll.filename"),
            pytest.raises(UnsupportedPlatformError),
        ):
            parse_filename(
                "tinylib-1.2.3-cp313-cp313-musllinux_1_2_x86_64.whl",
                mappers=(passthrough_mapper,),
            )

        assert "rejected" in caplog.text

    def test_build_tag_is_parsed(self) -> None:
        (config,) = parse_filename(
            "tinylib-1.2.3-1mybuild-py3-none-any.whl", mappers=(passthrough_mapper,)
        )

        assert config.build == (1, "mybuild")

    def test_pymalloc_and_unicode_abi_suffixes_are_stripped_end_to_end(self) -> None:
        """`m`/`u` are supported (not filtered out) *and* stripped from the
        `abi` tag reroll hands back to the caller -- both halves of
        docs/wheel_filename.md's decision, exercised through the public
        `parse_filename` entry point rather than `WheelConfig` directly.
        """
        (config,) = parse_filename(
            "tinylib-1.2.3-cp313-cp313mu-manylinux_2_17_x86_64.whl", mappers=(passthrough_mapper,)
        )

        assert config.abi == "cp313"

    def test_debug_abi_suffix_still_drops_the_wheel_end_to_end(self) -> None:
        with pytest.raises(InvalidAbiTagError):
            parse_filename(
                "tinylib-1.2.3-cp313-cp313dmu-manylinux_2_17_x86_64.whl",
                mappers=(passthrough_mapper,),
            )

    def test_sort_is_deterministic_across_calls(self) -> None:
        filename = "tinylib-1.2.3-py38.py39.py310-none-any.whl"

        first = parse_filename(filename, mappers=(passthrough_mapper,))
        second = parse_filename(filename, mappers=(passthrough_mapper,))

        assert first == second
        assert [c.interpreter for c in first] == ["py310", "py38", "py39"]

    def test_partial_support_keeps_only_the_supported_tags(self) -> None:
        """A compressed tag set where one alternative is legal and one isn't
        (`py3-cp313` combined with `none` ABI) should keep only the
        installable one.
        """
        configs = parse_filename(
            "tinylib-1.2.3-py3.cp313-none-any.whl", mappers=(passthrough_mapper,)
        )

        assert {c.interpreter for c in configs} == {"py3", "cp313"}

    def test_compressed_explosion_can_produce_only_nonsensical_combos(self) -> None:
        """docs/wheel_filename.md's "Compressed tags" example:
        `py312.py313-cp312.cp313-any` explodes to the cross product
        `py312-cp312`, `py312-cp313`, `py313-cp312`, `py313-cp313` -- and
        "no CPython version ever advertises the ABI except its exact
        version" (a generic `py*` tag paired with anything but `none`),
        so every one of those four combinations is individually invalid.
        With nothing left to keep, the whole filename is rejected.
        """
        with pytest.raises(InvalidAbiTagError):
            parse_filename(
                "tinylib-1.2.3-py312.py313-cp312.cp313-any.whl", mappers=(passthrough_mapper,)
            )


# --------------------------------------------------------------------------
# `parse_filename` <-> ABI explosion
# --------------------------------------------------------------------------


class TestParseFilenameAbi3Explosion:
    def test_abi3_wheel_explodes_into_one_config_per_minor(self) -> None:
        configs = parse_filename(
            "tinylib-1.2.3-cp39-abi3-manylinux_2_17_x86_64.whl",
            mappers=(passthrough_mapper,),
            abi3_upper_bound="3.11",
        )

        assert {(c.interpreter, c.abi) for c in configs} == {
            ("cp39", "cp39"),
            ("cp310", "cp310"),
            ("cp311", "cp311"),
        }

    def test_abi3t_wheel_explodes_with_the_free_threaded_abi(self) -> None:
        configs = parse_filename(
            "tinylib-1.2.3-cp315-abi3t-manylinux_2_17_x86_64.whl",
            mappers=(passthrough_mapper,),
            abi3_upper_bound="3.16",
        )

        assert {(c.interpreter, c.abi) for c in configs} == {
            ("cp315", "cp315t"),
            ("cp316", "cp316t"),
        }

    @pytest.mark.parametrize("start_minor", [2, 3])
    def test_low_floor_abi3_only_yields_configs_from_3_4_upward(self, start_minor: int) -> None:
        """End-to-end version of `TestExplodeAbi3`'s low-floor case: the
        sub-3.4 exploded tags are real `WheelConfig` rejections, not just
        absent from `explode_abi3`'s own output. Both `cp32`/`abi3` and
        `cp33`/`abi3` are the two "yes" `abi3` rows in the doc's Allowed
        table -- both legal at the `abi3` shape level, both still missing
        their sub-3.4 minors from the final output.
        """
        configs = parse_filename(
            f"tinylib-1.2.3-cp3{start_minor}-abi3-manylinux_2_17_x86_64.whl",
            mappers=(passthrough_mapper,),
            abi3_upper_bound="3.5",
        )

        assert {c.interpreter for c in configs} == {"cp34", "cp35"}

    @pytest.mark.parametrize("minor", [0, 1])
    def test_below_abi3s_own_floor_is_dropped_entirely_not_salvaged(self, minor: int) -> None:
        """docs/wheel_filename.md: a tag below `abi3`'s own 3.2 floor is
        dropped "as a strong suggestion the filename is bogus" -- it must
        not be exploded and then partially rescued by the unrelated `< 3.4`
        rule the way `cp32-abi3` is. Both `cp30`/`abi3` and `cp31`/`abi3`
        are "no" rows in the doc's Allowed table.
        """
        with pytest.raises(InvalidAbiTagError):
            parse_filename(
                f"tinylib-1.2.3-cp3{minor}-abi3-manylinux_2_17_x86_64.whl",
                mappers=(passthrough_mapper,),
                abi3_upper_bound="3.6",
            )

    def test_below_abi3ts_own_floor_is_dropped_entirely(self) -> None:
        """The `abi3t` analogue of the case above -- and the one where
        skipping the pre-explosion floor check would actually matter: with
        no check, `cp310-abi3t` would explode into "valid-looking"
        free-threaded 3.10+ configs, since nothing else in `WheelConfig`
        enforces `abi3t`'s free-threading-specific 3.15 floor.
        """
        with pytest.raises(InvalidAbiTagError):
            parse_filename(
                "tinylib-1.2.3-cp310-abi3t-manylinux_2_17_x86_64.whl",
                mappers=(passthrough_mapper,),
                abi3_upper_bound="3.16",
            )

    def test_compressed_and_abi3_expansion_dedupe_together(self) -> None:
        """`cp39.cp310-abi3-*` compressed-expands to two input tags whose
        exploded ranges overlap at `cp310`; the wheel must still produce
        exactly one `cp310` config.
        """
        configs = parse_filename(
            "tinylib-1.2.3-cp39.cp310-abi3-manylinux_2_17_x86_64.whl",
            mappers=(passthrough_mapper,),
            abi3_upper_bound="3.10",
        )

        assert {c.interpreter for c in configs} == {"cp39", "cp310"}
        assert len(configs) == 2

    def test_default_upper_bound_comes_from_the_latest_release_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("reroll.filename.python_latest_release.latest_python_minor", lambda: 11)

        configs = parse_filename(
            "tinylib-1.2.3-cp39-abi3-manylinux_2_17_x86_64.whl", mappers=(passthrough_mapper,)
        )

        assert {c.interpreter for c in configs} == {"cp39", "cp310", "cp311"}

    def test_non_abi3_wheel_never_calls_the_default_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail() -> int:
            raise AssertionError("must not look up a default when nothing needs exploding")

        monkeypatch.setattr("reroll.filename.python_latest_release.latest_python_minor", _fail)

        parse_filename(
            "tinylib-1.2.3-cp313-cp313-manylinux_2_17_x86_64.whl", mappers=(passthrough_mapper,)
        )


# --------------------------------------------------------------------------
# `parse_filename` when every tag `explode_abi3` would otherwise drop,
# leaving `tags` (and hence `errors`) empty (docs/wheel_filename.md;
# reroll-data's reroll_failure_analysis notebook, section 5a: 253 rows as
# `unexpected: IndexError`).
# --------------------------------------------------------------------------


class TestParseFilenameEmptyTagsAfterAbi3Explosion:
    """A compound abi3 tag (e.g. `abi3.abi3t`, two distinct `Tag`s from
    `packaging.utils.parse_wheel_filename`) whose interpreter minor is
    already past `abi3_upper_bound` is a *legal* `(interpreter, abi)`
    pairing, so without `explode_abi3` leaving it unchanged, exploding it
    would yield an empty per-minor range for every tag -- leaving nothing
    (not even a rejection) for `parse_filename`'s `if not configs: raise
    errors[-1]` to raise.

    Real repro in the corpus: `ast-serialize` /
    `ast_serialize-0.7.0-cp315-abi3.abi3t-macosx_10_12_x86_64.whl`, and its
    sibling platform wheels.
    """

    def test_compound_abi3_tag_past_the_upper_bound_raises_a_reroll_error(self) -> None:
        with pytest.raises(RerollError):
            parse_filename(
                "ast_serialize-0.7.0-cp315-abi3.abi3t-macosx_10_12_x86_64.whl",
                mappers=(passthrough_mapper,),
                abi3_upper_bound="3.13",
            )


# --------------------------------------------------------------------------
# `parse_filename` <-> `allow_pre` (docs/wheel_filename.md: "A package
# version may only contain pre-release tags ... if the `allow_pre` setting
# is true").
# --------------------------------------------------------------------------


class TestParseFilenameAllowPre:
    @pytest.mark.parametrize(
        "version",
        ["1.2.3rc1", "1.2.3a1", "1.2.3b1", "1.2.3.dev1"],
        ids=["rc", "alpha", "beta", "dev"],
    )
    def test_prerelease_version_rejected_by_default(self, version: str) -> None:
        with pytest.raises(UnsupportedPrereleaseError):
            parse_filename(f"tinylib-{version}-py3-none-any.whl", mappers=(passthrough_mapper,))

    @pytest.mark.parametrize(
        "version",
        ["1.2.3rc1", "1.2.3a1", "1.2.3b1", "1.2.3.dev1"],
        ids=["rc", "alpha", "beta", "dev"],
    )
    def test_prerelease_version_allowed_with_allow_pre(self, version: str) -> None:
        (config,) = parse_filename(
            f"tinylib-{version}-py3-none-any.whl",
            mappers=(passthrough_mapper,),
            allow_pre=True,
        )

        assert config.version == Version(version)

    def test_postrelease_version_is_not_a_prerelease(self) -> None:
        """A post-release (`.postN`) is unaffected by `allow_pre`: it is
        accepted whether or not the flag is set.
        """
        (config,) = parse_filename(
            "tinylib-1.2.3.post1-py3-none-any.whl", mappers=(passthrough_mapper,)
        )

        assert config.version == Version("1.2.3.post1")

    def test_prerelease_rejection_logs_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            caplog.at_level(logging.INFO, logger="reroll.scope"),
            pytest.raises(UnsupportedPrereleaseError),
        ):
            parse_filename("tinylib-1.2.3rc1-py3-none-any.whl", mappers=(passthrough_mapper,))

        assert caplog.records
        assert all(record.levelno == logging.INFO for record in caplog.records)

    def test_allow_pre_defaults_to_false(self) -> None:
        with pytest.raises(UnsupportedPrereleaseError):
            parse_filename("tinylib-1.2.3rc1-py3-none-any.whl", mappers=(passthrough_mapper,))


# --------------------------------------------------------------------------
# `parse_filename` <-> name mapping
# --------------------------------------------------------------------------


class TestParseFilenameNameMapping:
    def test_empty_mapper_chain_raises_value_error(self) -> None:
        empty: NameMappers = cast(NameMappers, ())

        with pytest.raises(ValueError, match="at least one mapper"):
            parse_filename("tinylib-1.2.3-py3-none-any.whl", mappers=empty)

    def test_passthrough_mapper_falls_back_to_normalized_pypi_name(self) -> None:
        (config,) = parse_filename("tinylib-1.2.3-py3-none-any.whl", mappers=(passthrough_mapper,))

        assert config.conda_name == config.normalized_pypi_name == "tinylib"

    def test_mapper_result_reaches_conda_name(self) -> None:
        def _mapper(
            name: NormalizedName, candidates: Sequence[Candidate]
        ) -> Winner | Sequence[Candidate]:
            del name, candidates
            return Winner(
                conda_name="python-tinylib",
                probability=1.0,
                source=CandidateSource.OTHER,
                mapper="test",
            )

        (config,) = parse_filename("tinylib-1.2.3-py3-none-any.whl", mappers=(_mapper,))

        assert config.conda_name == "python-tinylib"
        assert config.normalized_pypi_name == "tinylib"

    def test_mapper_is_invoked_exactly_once_for_multiple_configs(self) -> None:
        calls = 0

        def _mapper(
            name: NormalizedName, candidates: Sequence[Candidate]
        ) -> Winner | Sequence[Candidate]:
            del name
            nonlocal calls
            calls += 1
            return candidates

        configs = parse_filename(
            "tinylib-1.2.3-cp313-cp313-macosx_10_9_universal2.whl",
            mappers=(_mapper, aggregator_mapper, passthrough_mapper),
        )

        assert len(configs) == 2
        assert calls == 1

    def test_both_new_fields_are_identical_across_multi_config_filenames(self) -> None:
        def _mapper(
            name: NormalizedName, candidates: Sequence[Candidate]
        ) -> Winner | Sequence[Candidate]:
            del name, candidates
            return Winner(
                conda_name="python-tinylib",
                probability=1.0,
                source=CandidateSource.OTHER,
                mapper="test",
            )

        configs = parse_filename(
            "tinylib-1.2.3-cp313-cp313-macosx_10_9_universal2.whl", mappers=(_mapper,)
        )

        assert len(configs) == 2
        assert {c.normalized_pypi_name for c in configs} == {"tinylib"}
        assert {c.conda_name for c in configs} == {"python-tinylib"}

    def test_unresolved_candidates_raises(self) -> None:
        def _no_opinion(
            name: NormalizedName, candidates: Sequence[Candidate]
        ) -> Winner | Sequence[Candidate]:
            del name
            return candidates

        with pytest.raises(UnresolvedCondaNameError):
            parse_filename("tinylib-1.2.3-py3-none-any.whl", mappers=(_no_opinion,))

    def test_unresolved_candidates_logs_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        def _no_opinion(
            name: NormalizedName, candidates: Sequence[Candidate]
        ) -> Winner | Sequence[Candidate]:
            del name
            return candidates

        with (
            caplog.at_level(logging.WARNING, logger="reroll.unconvertable"),
            pytest.raises(UnresolvedCondaNameError),
        ):
            parse_filename("tinylib-1.2.3-py3-none-any.whl", mappers=(_no_opinion,))

        assert caplog.records
        assert all(record.levelno == logging.WARNING for record in caplog.records)

    def test_overlong_conda_name_raises(self) -> None:
        def _mapper(
            name: NormalizedName, candidates: Sequence[Candidate]
        ) -> Winner | Sequence[Candidate]:
            del name, candidates
            return Winner(
                conda_name="a" * 65, probability=1.0, source=CandidateSource.OTHER, mapper="test"
            )

        with pytest.raises(InvalidCondaNameError):
            parse_filename("tinylib-1.2.3-py3-none-any.whl", mappers=(_mapper,))

    def test_overlong_conda_name_logs_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        def _mapper(
            name: NormalizedName, candidates: Sequence[Candidate]
        ) -> Winner | Sequence[Candidate]:
            del name, candidates
            return Winner(
                conda_name="a" * 65, probability=1.0, source=CandidateSource.OTHER, mapper="test"
            )

        with (
            caplog.at_level(logging.DEBUG, logger="reroll.filename"),
            pytest.raises(InvalidCondaNameError),
        ):
            parse_filename("tinylib-1.2.3-py3-none-any.whl", mappers=(_mapper,))

        assert "rejected" in caplog.text


# --------------------------------------------------------------------------
# Module-level helper: `supported_archs`
# --------------------------------------------------------------------------


class TestSupportedArchs:
    def test_any(self) -> None:
        assert supported_archs("any") == (None,)

    def test_manylinux_x86_64(self) -> None:
        assert supported_archs("manylinux_2_17_x86_64") == (Arch.X86_64,)

    def test_manylinux_aarch64(self) -> None:
        assert supported_archs("manylinux_2_17_aarch64") == (Arch.ARM64,)

    def test_macos_x86_64(self) -> None:
        assert supported_archs("macosx_10_9_x86_64") == (Arch.X86_64,)

    def test_macos_arm64(self) -> None:
        assert supported_archs("macosx_11_0_arm64") == (Arch.ARM64,)

    def test_macos_universal2(self) -> None:
        assert supported_archs("macosx_10_9_universal2") == (Arch.X86_64, Arch.ARM64)

    def test_win_amd64(self) -> None:
        assert supported_archs("win_amd64") == (Arch.X86_64,)

    def test_win_arm64(self) -> None:
        assert supported_archs("win_arm64") == (Arch.ARM64,)

    def test_unsupported_platform(self) -> None:
        assert supported_archs("musllinux_1_2_x86_64") == ()
