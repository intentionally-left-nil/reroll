"""Unit tests for `reroll.reroll`."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import reroll.wheel_record as wheel_record_module
from reroll import reroll
from reroll.errors import InvalidWheelArchiveError
from reroll.name_mapping import passthrough_mapper, static_mapper


def _make_wheel(
    directory: Path,
    *,
    filename: str = "tinylib-1.2.3-py3-none-any.whl",
    name: str = "tinylib",
    version: str = "1.2.3",
    requires_dist: tuple[str, ...] = (),
) -> Path:
    """A `.whl` (zip) file at `directory / filename`, with a minimal but
    well-formed `.dist-info/METADATA` entry built from `name`/`version`/
    `requires_dist`.
    """
    metadata_lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    metadata_lines.extend(f"Requires-Dist: {requirement}" for requirement in requires_dist)
    metadata = "\n".join(metadata_lines) + "\n\n"
    path = directory / filename
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA", metadata)
    return path


class TestRerollEndToEnd:
    def test_converts_a_wheel_file_into_its_repodata_record(self, tmp_path: Path) -> None:
        wheel = _make_wheel(tmp_path, requires_dist=("requests>=2.20",))

        (record,) = reroll(wheel, mappers=(passthrough_mapper,))

        assert record.name == "tinylib"
        assert record.version == "1.2.3"
        assert record.build == "py3_none_any_0"
        assert record.build_number == 0
        assert record.subdir == "noarch"
        assert record.noarch == "python"
        assert record.depends == ("requests >=2.20", "python >=3.0")
        assert record.fn == "tinylib-1.2.3-py3-none-any.whl"

    def test_accepts_a_str_path(self, tmp_path: Path) -> None:
        wheel = _make_wheel(tmp_path)

        (record,) = reroll(str(wheel), mappers=(passthrough_mapper,))

        assert record.name == "tinylib"

    def test_fn_is_the_bare_filename_not_the_full_path(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "dir"
        nested.mkdir(parents=True)
        wheel = _make_wheel(nested)

        (record,) = reroll(wheel, mappers=(passthrough_mapper,))

        assert record.fn == "tinylib-1.2.3-py3-none-any.whl"

    def test_multiple_wheel_configs_produce_multiple_records(self, tmp_path: Path) -> None:
        wheel = _make_wheel(tmp_path, filename="tinylib-1.2.3-cp39-abi3-manylinux_2_17_x86_64.whl")

        records = reroll(wheel, mappers=(passthrough_mapper,), abi3_upper_bound="3.10")

        assert {record.build for record in records} == {
            "cp39_cp39_manylinux_2_17_x86_64_0",
            "cp310_cp310_manylinux_2_17_x86_64_0",
        }

    def test_allow_pre_is_passed_through(self, tmp_path: Path) -> None:
        wheel = _make_wheel(
            tmp_path, filename="tinylib-1.2.3rc1-py3-none-any.whl", version="1.2.3rc1"
        )

        (record,) = reroll(wheel, mappers=(passthrough_mapper,), allow_pre=True)

        assert record.version == "1.2.3.rc1"

    def test_sha256_size_url_default_to_none(self, tmp_path: Path) -> None:
        wheel = _make_wheel(tmp_path)

        (record,) = reroll(wheel, mappers=(passthrough_mapper,))

        assert record.sha256 is None
        assert record.size is None
        assert record.url is None

    def test_sha256_size_url_are_passed_through_when_given(self, tmp_path: Path) -> None:
        wheel = _make_wheel(tmp_path)

        (record,) = reroll(
            wheel,
            mappers=(passthrough_mapper,),
            sha256="abc123",
            size=42,
            url="https://example.org/tinylib-1.2.3-py3-none-any.whl",
        )

        assert record.sha256 == "abc123"
        assert record.size == 42
        assert record.url == "https://example.org/tinylib-1.2.3-py3-none-any.whl"

    def test_a_wheel_without_a_metadata_entry_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "tinylib-1.2.3-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("tinylib-1.2.3.dist-info/WHEEL", "Wheel-Version: 1.0\n\n")

        with pytest.raises(InvalidWheelArchiveError):
            reroll(path, mappers=(passthrough_mapper,))


class TestRerollMappers:
    def test_defaults_to_default_mappers_when_none_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`reroll()` itself has no opinion on the default -- it just
        forwards `mappers=None` through to `get_wheel_records`
        (`reroll.wheel_record`), which is where the fallback to
        `default_mappers()` actually lives.
        """
        monkeypatch.setattr(wheel_record_module, "default_mappers", lambda: (passthrough_mapper,))
        wheel = _make_wheel(tmp_path)

        (record,) = reroll(wheel)

        assert record.name == "tinylib"

    def test_explicit_mappers_override_the_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        def _unused_default_mappers() -> tuple[object, ...]:
            nonlocal calls
            calls += 1
            return (passthrough_mapper,)

        monkeypatch.setattr(wheel_record_module, "default_mappers", _unused_default_mappers)
        wheel = _make_wheel(tmp_path)

        (record,) = reroll(wheel, mappers=(static_mapper({"tinylib": "tiny-lib"}),))

        assert record.name == "tiny-lib"
        assert calls == 0
