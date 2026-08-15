"""Assemble a wheel's `WheelRecord`(s) from its parsed METADATA and filename."""

from __future__ import annotations

from reroll.default_mappers import default_mappers
from reroll.dependencies import WheelDependencies, wheel_dependencies
from reroll.dependencies.version_format import format_version
from reroll.errors import MetadataFilenameMismatchError
from reroll.filename import WheelConfig, parse_filename
from reroll.license import convert_license
from reroll.name_mapping import NameMappers
from reroll.wheel_metadata import WheelMetadata

__all__ = ["WheelRecord", "get_wheel_records"]


class WheelRecord(WheelDependencies):
    """A single wheel's contribution to a repodata.json `v3.whl` map.

    Inherits `depends`/`extra_depends` from `WheelDependencies`
    (`reroll.dependencies`) rather than redeclaring them, so a record's
    dependency fields are validated identically to the ones
    `reroll.dependencies.calculate_dependencies` produces.

    `sha256`, `size`, and `url` are never derived from the wheel itself
    (docs/wheel_record.md) -- they stay `None` unless a caller supplies
    them, e.g. from a PyPI simple index entry.
    """

    name: str
    version: str
    build: str
    build_number: int
    subdir: str
    fn: str
    noarch: str | None = None
    license: str | None = None
    sha256: str | None = None
    size: int | None = None
    url: str | None = None


def get_wheel_records(
    metadata: WheelMetadata,
    filename: str,
    *,
    mappers: NameMappers | None = None,
    allow_pre: bool = False,
    abi3_upper_bound: str | None = None,
    sha256: str | None = None,
    size: int | None = None,
    url: str | None = None,
) -> tuple[WheelRecord, ...]:
    """`metadata`/`filename`'s repodata record(s): one per `WheelConfig`
    `reroll.filename.parse_filename` derives from `filename`, times one per
    target `reroll.dependencies.wheel_dependencies` resolves for that
    config (a noarch record, an arch-specific one per `CondaSubdir`, or an
    arch-split retry -- see that function).

    `abi3_upper_bound` is passed straight through to both
    `parse_filename` (bounding `abi3`/`abi3t` tag explosion) and
    `wheel_dependencies` (bounding a residual `python_version in
    "<literal>"` marker's conversion) -- the same minor-only version
    string caps both.

    Every record's `version` comes from `metadata.version` (the METADATA
    `Version` header), CEP-33-formatted (`format_version`) rather than left
    as PEP 440 -- docs/wheel_record.md.

    `sha256`, `size`, and `url` are never computed here -- each is set on
    every returned record only if the caller passes it in (docs/wheel_record.md).

    Raises `reroll.errors.MetadataFilenameMismatchError` if `metadata.name`
    or `metadata.version` disagrees with `filename`'s own name or version
    segment, once both are normalized -- docs/wheel_metadata.md.

    Raises whatever `reroll.filename.parse_filename` or
    `reroll.dependencies.wheel_dependencies` raise: `InvalidFilenameError`,
    `UnsupportedPrereleaseError`, `UnresolvedCondaNameError`, or any other
    `RerollError` subclass a rejected `(tag, arch)` combination or
    dependency conversion produces.
    """
    mappers = mappers or default_mappers()
    configs = _dedupe_configs(
        parse_filename(filename, mappers, abi3_upper_bound=abi3_upper_bound, allow_pre=allow_pre)
    )
    _validate_metadata_matches_filename(metadata, configs, filename)
    license_expression = convert_license(metadata)
    version = format_version(metadata.version)
    records: list[WheelRecord] = []
    for config in configs:
        deps_by_subdir = wheel_dependencies(
            config, metadata, mappers, allow_pre=allow_pre, abi3_upper_bound=abi3_upper_bound
        )
        for subdir, dependencies in deps_by_subdir.items():
            records.append(
                WheelRecord(
                    name=config.conda_name,
                    version=version,
                    build=_build_string(config),
                    build_number=0,
                    subdir=subdir.value if subdir is not None else "noarch",
                    noarch="python" if subdir is None else None,
                    license=license_expression,
                    depends=dependencies.depends,
                    extra_depends=dependencies.extra_depends,
                    fn=filename,
                    sha256=sha256,
                    size=size,
                    url=url,
                )
            )
    return tuple(records)


def _validate_metadata_matches_filename(
    metadata: WheelMetadata, configs: tuple[WheelConfig, ...], filename: str
) -> None:
    """Raises `MetadataFilenameMismatchError` if `metadata.name` or
    `metadata.version` disagrees with `configs`' filename-derived name or
    version -- docs/wheel_metadata.md's "The Name and version must match
    the filename". `normalized_pypi_name`/`version` are invariant across
    every `WheelConfig` one filename expands to (`WheelConfig`'s
    docstring), so checking the first checks them all.
    """
    config = configs[0]
    if metadata.name == config.normalized_pypi_name and metadata.version == config.version:
        return
    raise MetadataFilenameMismatchError(
        f"METADATA Name/Version ({metadata.name!r} {metadata.version}) does not match "
        f"filename {filename!r} ({config.normalized_pypi_name!r} {config.version})"
    )


def _dedupe_configs(configs: tuple[WheelConfig, ...]) -> tuple[WheelConfig, ...]:
    """`configs`, keeping only the first entry for each distinct
    `(interpreter, abi, platform)` combination.

    A platform tag with more than one supported architecture (a macOS
    `universal2` tag's `Arch.X86_64`/`Arch.ARM64` pair) yields one
    `WheelConfig` per architecture from `parse_filename`, but
    `wheel_dependencies` fans out to every subdir a platform tag implies on
    its own -- it never reads `config.arch` -- so processing both configs
    would emit the same records twice.
    """
    seen: set[tuple[str, str, str]] = set()
    deduped: list[WheelConfig] = []
    for config in configs:
        key = (config.interpreter, config.abi, config.platform)
        if key not in seen:
            seen.add(key)
            deduped.append(config)
    return tuple(deduped)


def _build_string(config: WheelConfig) -> str:
    """`config`'s CEP 26 build string: `{interpreter}_{abi}_{platform}_0` --
    the wheel's own filename tags in place of the CEP's
    `py{PY_MAJOR_VERSION}` convention, and always build number `0`
    (docs/wheel_record.md).
    """
    return f"{config.interpreter}_{config.abi}_{config.platform}_0"
