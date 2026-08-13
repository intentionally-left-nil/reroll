"""Parse PyPI wheel filenames into the configs the record layer needs.

Each filename can expand to zero, one, or several `WheelConfig`s -- one per
supported `(tag, arch)` combination.
"""

from __future__ import annotations

import logging

from packaging.tags import Tag
from packaging.utils import InvalidWheelFilename, parse_wheel_filename

from reroll.errors import InvalidFilenameError, RerollError, UnsupportedPrereleaseError
from reroll.filename.abi3 import explode_abi3
from reroll.filename.enums import AbiKind, Arch, PlatformFamily
from reroll.filename.platform import supported_archs
from reroll.filename.python_requirement import PythonRequirement
from reroll.filename.wheel_config import WheelConfig
from reroll.name_mapping import NameMappers, map_name

__all__ = [
    "AbiKind",
    "Arch",
    "InvalidFilenameError",
    "PlatformFamily",
    "PythonRequirement",
    "UnsupportedPrereleaseError",
    "WheelConfig",
    "parse_filename",
    "supported_archs",
]

logger = logging.getLogger(__name__)


def parse_filename(
    filename: str,
    mappers: NameMappers,
    *,
    abi3_upper_bound: str | None = None,
    allow_pre: bool = False,
) -> tuple[WheelConfig, ...]:
    """Parse a wheel filename into one or more `WheelConfig`s.

    Raises `InvalidFilenameError` for an unparseable filename,
    `UnsupportedPrereleaseError` for a pre-release version rejected by
    `allow_pre`, `reroll.errors.UnresolvedCondaNameError` for a PyPI
    name no mapper resolved, or -- if every `(tag, arch)` combination the
    filename expands to is individually rejected -- whichever `RerollError`
    the last of them raised. A filename with *some* supported combinations
    and some unsupported ones drops the unsupported ones silently (each
    logged at `DEBUG`) and returns the rest.

    `abi3_upper_bound` (a minor-only version string like `"3.15"`) caps how
    far `abi3`/`abi3t` tags are exploded into concrete per-minor tags
    (`reroll.filename.abi3.explode_abi3`); `None` (the default) resolves it
    from `reroll.filename.python_latest_release.latest_python_minor`.

    `allow_pre` gates whether a pre-release version (`dev`/`a`/`b`/`rc`) is
    accepted at all; a post-release alone does not count as a pre-release.
    This only governs the wheel's own version -- dependency version
    specifiers are unaffected.
    """
    try:
        name, version, build, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename as exc:
        raise InvalidFilenameError(f"unparseable wheel filename {filename!r}: {exc}") from exc

    if not allow_pre and version.is_prerelease:
        raise UnsupportedPrereleaseError(
            f"rejected pre-release version {version} for wheel filename {filename!r}: "
            "allow_pre is not set"
        )

    tags = explode_abi3(tags, abi3_upper_bound=abi3_upper_bound)

    # The name is tag-invariant, so this is resolved once,
    # before the tag loop below -- not once per `(tag, arch)` combination.
    conda_name = map_name(name, mappers)

    configs: list[WheelConfig] = []
    errors: list[RerollError] = []
    for tag in sorted(tags, key=_tag_sort_key):
        # An unsupported platform's `supported_archs()` is `()`; without the
        # `or [None]` fallback the loop below would never run for it, and no
        # rejection reason would be recorded. With it, one construction
        # attempt is made with `arch=None`, which fails the arch-membership
        # validator with a precise message -- keeping every rejection
        # reason flowing through the single `except RerollError` below.
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
            except RerollError as exc:
                logger.debug(
                    "rejected wheel config for %r (tag=%s, arch=%s): %s", filename, tag, arch, exc
                )
                errors.append(exc)

    if not configs:
        raise errors[-1]

    # `packaging` returns tags as a frozenset, whose iteration order varies
    # under hash randomization; repodata must be reproducible, so sort
    # explicitly. No dedup needed: tags are already unique within the
    # frozenset and `supported_archs` returns distinct values.
    configs.sort(key=_sort_key)
    return tuple(configs)


def _tag_sort_key(tag: Tag) -> tuple[str, str, str]:
    return (tag.interpreter, tag.abi, tag.platform)


def _sort_key(config: WheelConfig) -> tuple[str, str, str, str]:
    return (
        config.interpreter,
        config.abi,
        config.platform,
        config.arch.value if config.arch is not None else "",
    )
