"""Unit tests for `reroll.filename`."""

from __future__ import annotations

import logging

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import ValidationError

from reroll.filename import (
    AbiKind,
    Arch,
    PlatformFamily,
    PythonRequirement,
    WheelConfig,
    parse_filename,
    supported_archs,
)


def _config(
    *,
    name: str = "tinylib",
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
        name=name,
        version=version,
        build=build,
        interpreter=interpreter,
        abi=abi,
        platform=platform,
        arch=arch,
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
        with pytest.raises(ValidationError):
            _config(interpreter=interpreter)

    @pytest.mark.parametrize("interpreter", ["py2", "cp27", "py4", "cp40"])
    def test_rejects_non_python3_major(self, interpreter: str) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter=interpreter)

    def test_rejects_cp_without_a_minor(self) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter="cp3")

    @pytest.mark.parametrize("interpreter", ["py", "cp", "py3x"])
    def test_rejects_missing_or_non_digit_version(self, interpreter: str) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter=interpreter)


# --------------------------------------------------------------------------
# `abi` validator
# --------------------------------------------------------------------------


class TestAbiValidator:
    def test_accepts_none(self) -> None:
        assert _config(abi="none").abi == "none"

    @pytest.mark.parametrize(("interpreter", "abi"), [("cp313", "abi3"), ("cp315", "abi3t")])
    def test_accepts_abi3_variants(self, interpreter: str, abi: str) -> None:
        assert _config(interpreter=interpreter, abi=abi).abi == abi

    @pytest.mark.parametrize("abi", ["cp313", "cp313t"])
    def test_accepts_versioned_cpython_abi(self, abi: str) -> None:
        assert _config(interpreter="cp313", abi=abi).abi == abi

    @pytest.mark.parametrize("abi", ["cp313d", "cp313dm", "cp27mu"])
    def test_rejects_debug_pymalloc_unicode_suffixes(self, abi: str) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter="cp313", abi=abi)

    @pytest.mark.parametrize("abi", ["pypy310_pp73", "graalpy_38_native"])
    def test_rejects_non_cpython_abi(self, abi: str) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter="cp313", abi=abi)

    @pytest.mark.parametrize("abi", ["abi4", "abi3x"])
    def test_rejects_abi_that_is_not_abi3_or_abi3t(self, abi: str) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter="cp313", abi=abi)

    def test_rejects_versioned_abi_with_non_python3_major(self) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter="cp313", abi="cp208")


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

    @pytest.mark.parametrize("alias", ["manylinux1", "manylinux2010", "manylinux2014"])
    def test_accepts_legacy_manylinux_aliases(self, alias: str) -> None:
        config = _config(platform=f"{alias}_x86_64", arch=Arch.X86_64)
        assert config.platform == f"{alias}_x86_64"

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
        with pytest.raises(ValidationError):
            _config(platform="musllinux_1_2_x86_64", arch=Arch.X86_64)

    @pytest.mark.parametrize("platform", ["linux_x86_64", "linux_aarch64"])
    def test_rejects_bare_linux(self, platform: str) -> None:
        with pytest.raises(ValidationError):
            _config(platform=platform, arch=Arch.X86_64)

    @pytest.mark.parametrize("platform", ["manylinux_2_17_i686", "win32", "macosx_10_9_i386"])
    def test_rejects_32bit(self, platform: str) -> None:
        with pytest.raises(ValidationError):
            _config(platform=platform, arch=Arch.X86_64)

    def test_rejects_win_ia64(self) -> None:
        with pytest.raises(ValidationError):
            _config(platform="win_ia64", arch=Arch.X86_64)

    @pytest.mark.parametrize("arch_suffix", ["ppc64le", "s390x", "riscv64", "loongarch64", "ppc64"])
    def test_rejects_unsupported_linux_arches(self, arch_suffix: str) -> None:
        with pytest.raises(ValidationError):
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
        with pytest.raises(ValidationError):
            _config(platform=platform, arch=Arch.X86_64)

    @pytest.mark.parametrize("platform", ["ios_13_0_arm64_iphoneos", "android_21_arm64_v8a"])
    def test_rejects_mobile_platforms(self, platform: str) -> None:
        with pytest.raises(ValidationError):
            _config(platform=platform, arch=Arch.ARM64)

    @pytest.mark.parametrize("platform", ["emscripten_1_0_wasm32", "wasi_0_0_wasm32"])
    def test_rejects_web_platforms(self, platform: str) -> None:
        with pytest.raises(ValidationError):
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

    def test_cp_with_abi3_is_legal_when_minor_at_least_2(self) -> None:
        _config(interpreter="cp313", abi="abi3")

    def test_cp_with_abi3t_is_legal_when_minor_at_least_15(self) -> None:
        _config(interpreter="cp315", abi="abi3t")

    @pytest.mark.parametrize("abi", ["abi3", "cp313", "cp313t"])
    def test_rejects_generic_interpreter_with_non_none_abi(self, abi: str) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter="py3", abi=abi)

    def test_rejects_versioned_abi_minor_mismatch(self) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter="cp313", abi="cp312")

    def test_rejects_abi3_below_3_2(self) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter="cp31", abi="abi3")

    def test_rejects_abi3t_below_3_15(self) -> None:
        with pytest.raises(ValidationError):
            _config(interpreter="cp310", abi="abi3t")


# --------------------------------------------------------------------------
# `arch` membership
# --------------------------------------------------------------------------


class TestArchMembership:
    def test_any_platform_requires_arch_none(self) -> None:
        with pytest.raises(ValidationError):
            _config(platform="any", arch=Arch.X86_64)

    def test_non_any_platform_requires_arch_not_none(self) -> None:
        with pytest.raises(ValidationError):
            _config(platform="manylinux_2_17_x86_64", arch=None)

    def test_arch_must_be_in_supported_archs(self) -> None:
        with pytest.raises(ValidationError):
            _config(platform="manylinux_2_17_x86_64", arch=Arch.ARM64)

    def test_universal2_accepts_either_arch(self) -> None:
        _config(platform="macosx_11_0_universal2", arch=Arch.X86_64)
        _config(platform="macosx_11_0_universal2", arch=Arch.ARM64)


# --------------------------------------------------------------------------
# `python` / `abi_kind` / `free_threaded`
# --------------------------------------------------------------------------


class TestDerivedPythonAndAbiFields:
    @pytest.mark.parametrize(
        ("interpreter", "abi", "minor", "exact", "abi_kind", "free_threaded"),
        [
            ("py3", "none", 0, False, AbiKind.NONE, False),
            ("py38", "none", 8, False, AbiKind.NONE, False),
            ("py313", "none", 13, False, AbiKind.NONE, False),
            ("cp313", "none", 13, True, AbiKind.NONE, False),
            ("cp313", "cp313", 13, True, AbiKind.VERSIONED, False),
            ("cp314", "cp314t", 14, True, AbiKind.VERSIONED, True),
            ("cp313", "abi3", 13, False, AbiKind.STABLE, False),
            ("cp315", "abi3t", 15, False, AbiKind.STABLE, True),
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
        config = _config(name="Re_Roll.X")
        assert config.name == "re-roll-x"

    def test_version_is_normalized(self) -> None:
        config = _config(version="1.2.3.alpha1")
        assert config.version == Version("1.2.3a1")
        assert str(config.version) == "1.2.3a1"


# --------------------------------------------------------------------------
# `parse_filename` orchestration
# --------------------------------------------------------------------------


class TestParseFilename:
    def test_happy_path(self) -> None:
        (config,) = parse_filename("tinylib-1.2.3-py3-none-any.whl")

        assert config.name == "tinylib"
        assert config.version == Version("1.2.3")
        assert config.interpreter == "py3"
        assert config.abi == "none"
        assert config.platform == "any"
        assert config.arch is None

    def test_compressed_tags_expand_to_multiple_configs(self) -> None:
        configs = parse_filename("tinylib-1.2.3-py2.py3-none-any.whl")

        assert [c.interpreter for c in configs] == ["py3"]

    def test_compressed_tags_expand_all_valid_combinations(self) -> None:
        configs = parse_filename("tinylib-1.2.3-py38.py39-none-any.whl")

        assert [c.interpreter for c in configs] == ["py38", "py39"]

    def test_universal2_produces_exactly_two_configs(self) -> None:
        configs = parse_filename("tinylib-1.2.3-cp313-cp313-macosx_10_9_universal2.whl")

        assert len(configs) == 2
        assert {c.arch for c in configs} == {Arch.X86_64, Arch.ARM64}

    def test_unparseable_filename_returns_empty_tuple(self) -> None:
        assert parse_filename("not-a-wheel-filename") == ()

    def test_unparseable_filename_logs_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="reroll.filename"):
            parse_filename("not-a-wheel-filename")

        assert "unparseable" in caplog.text

    def test_all_tags_unsupported_returns_empty_tuple(self) -> None:
        assert parse_filename("tinylib-1.2.3-cp313-cp313-musllinux_1_2_x86_64.whl") == ()

    def test_rejection_logs_validation_errors_at_debug(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="reroll.filename"):
            parse_filename("tinylib-1.2.3-cp313-cp313-musllinux_1_2_x86_64.whl")

        assert "rejected" in caplog.text

    def test_build_tag_is_parsed(self) -> None:
        (config,) = parse_filename("tinylib-1.2.3-1mybuild-py3-none-any.whl")

        assert config.build == (1, "mybuild")

    def test_sort_is_deterministic_across_calls(self) -> None:
        filename = "tinylib-1.2.3-py38.py39.py310-none-any.whl"

        first = parse_filename(filename)
        second = parse_filename(filename)

        assert first == second
        assert [c.interpreter for c in first] == ["py310", "py38", "py39"]

    def test_partial_support_keeps_only_the_supported_tags(self) -> None:
        """A compressed tag set where one alternative is legal and one isn't
        (`py3-cp313` combined with `none` ABI) should keep only the
        installable one.
        """
        configs = parse_filename("tinylib-1.2.3-py3.cp313-none-any.whl")

        assert {c.interpreter for c in configs} == {"py3", "cp313"}


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
