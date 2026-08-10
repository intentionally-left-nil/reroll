"""Parse PyPI wheel filenames into the configs the record layer needs.

Each filename can expand to zero, one, or several `WheelConfig`s -- one per
supported `(tag, arch)` combination.
"""

from __future__ import annotations

import logging

from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from pydantic import ValidationError

from reroll.filename.abi3 import explode_abi3
from reroll.filename.enums import AbiKind, Arch, PlatformFamily
from reroll.filename.platform import supported_archs
from reroll.filename.python_requirement import PythonRequirement
from reroll.filename.wheel_config import WheelConfig
from reroll.name_mapping import NameMappers, UnresolvedCandidates, map_name

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


def parse_filename(
    filename: str, mappers: NameMappers, *, abi3_upper_bound: str | None = None
) -> tuple[WheelConfig, ...]:
    """Parse a wheel filename into zero or more `WheelConfig`s.

    Never raises for filename input: an unparseable filename or a filename
    with no supported `(tag, arch)` combination both return `()`, and the
    reason is logged at `DEBUG`. A mapper chain that raises
    `UnresolvedCandidates` also yields `()`, but is logged at `WARNING`
    since (unlike the other rejections) it has a concrete, actionable fix

    `abi3_upper_bound` (a minor-only version string like `"3.15"`) caps how
    far `abi3`/`abi3t` tags are exploded into concrete per-minor tags
    (`reroll.filename.abi3.explode_abi3`); `None` (the default) resolves it
    from `reroll.filename.python_latest_release.latest_python_minor`.
    """
    try:
        name, version, build, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename as exc:
        logger.debug("unparseable wheel filename %r: %s", filename, exc)
        return ()

    tags = explode_abi3(tags, abi3_upper_bound=abi3_upper_bound)

    # The name is tag-invariant, so this is resolved once,
    # before the tag loop below -- not once per `(tag, arch)` combination.
    try:
        conda_name = map_name(name, mappers)
    except UnresolvedCandidates as exc:
        logger.warning("unresolved conda name for wheel filename %r: %s", filename, exc)
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


def _sort_key(config: WheelConfig) -> tuple[str, str, str, str]:
    return (
        config.interpreter,
        config.abi,
        config.platform,
        config.arch.value if config.arch is not None else "",
    )
