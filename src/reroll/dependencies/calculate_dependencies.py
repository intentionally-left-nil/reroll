"""Compute a wheel's conda `depends`/`extra_depends` MatchSpecs by running
docs/wheel_to_conda_dependencies.md's "Calculating extras" algorithm.
"""

from __future__ import annotations

from markerpry import TRUE, parse_marker
from packaging.requirements import Requirement
from pydantic import BaseModel, ConfigDict

from reroll.dependencies.conditional_dependency import conditional_dependency
from reroll.dependencies.extras import find_extras
from reroll.dependencies.pep508_to_matchspec import pep508_to_matchspec
from reroll.dependencies.python import python_dependencies, python_range
from reroll.dependencies.requires_dist import strip_interpreter_requirements
from reroll.filename import WheelConfig
from reroll.matchspec import CondaExtraName, MatchSpecStr
from reroll.name_mapping import NameMappers
from reroll.subdir import CondaSubdir
from reroll.wheel_metadata import WheelMetadata

__all__ = ["WheelDependencies", "calculate_dependencies"]


class WheelDependencies(BaseModel):
    """A wheel's converted conda dependencies: `depends`, the MatchSpecs
    every install of the package needs, and `extra_depends`, the
    additional MatchSpecs needed per extra (keyed by normalized extra
    name, `reroll.dependencies.extras.find_extras`) only when that extra
    is requested. `WheelRecord` (`reroll.__init__`) inherits this model
    rather than redeclaring its own copies of these two fields, so both
    get the same MatchSpec/extra-name validation.

    A MatchSpec never appears in both `depends` and one of
    `extra_depends`' lists -- `calculate_dependencies` removes any exact
    string match from the latter, per docs/wheel_to_conda_dependencies.md's
    "Splitting base dependencies from extras". Two different extras may
    still share a MatchSpec between themselves; that's not deduplicated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    depends: tuple[MatchSpecStr, ...]
    extra_depends: dict[CondaExtraName, tuple[MatchSpecStr, ...]]


def calculate_dependencies(
    config: WheelConfig,
    metadata: WheelMetadata,
    mappers: NameMappers,
    *,
    subdir: CondaSubdir | None,
    allow_pre: bool = False,
) -> WheelDependencies:
    """`metadata.requires_dist`'s conda dependencies for one target
    environment: `subdir=None` for a noarch record, or one specific
    `CondaSubdir` for an arch-specific one.

    Every candidate extra `find_extras` discovers is evaluated against
    every `Requires-Dist` entry (after stripping bare interpreter
    requirements, `reroll.dependencies.requires_dist.strip_interpreter_requirements`),
    in addition to the base (`extra=""`) -- so an entry with no `extra`
    clause at all is initially a candidate for `depends` *and* every
    extra's list, but any exact string match is then removed from the
    latter (docs/wheel_to_conda_dependencies.md's "Splitting base
    dependencies from extras" -- see `WheelDependencies`).
    `python`/`python_abi` (`reroll.dependencies.python.python_dependencies`)
    are appended to `depends` last, matching real conda-pypi output's field
    order.

    Raises `reroll.errors.PythonRangeMismatchError` if `config`'s
    filename-implied Python range and `metadata.requires_python` don't
    intersect -- the caller should not generate a repodata record for
    this wheel.

    Raises `reroll.errors.NeedsArchSplitError` if `subdir` is `None` and
    some entry's marker still refers to a platform-specific key after
    evaluation -- the caller must retry once per `CondaSubdir` instead of
    emitting a single noarch record; this function does not perform that
    retry itself.

    Raises `reroll.errors.UnconvertableMarkerError` or
    `reroll.errors.UnconvertableRequirementError` if any entry turns out to
    be unrepresentable in conda for `subdir` -- the whole wheel record
    should not be emitted.
    """
    python_version = python_range(config, metadata)
    requires_dist = strip_interpreter_requirements(metadata.requires_dist)
    extra_names = find_extras(requires_dist)

    depends: list[str] = []
    extra_depends: dict[str, list[str]] = {name: [] for name in extra_names}
    for extra_name in ("", *extra_names):
        target = depends if extra_name == "" else extra_depends[extra_name]
        for entry in requires_dist:
            matchspec = _entry_dependency(
                entry,
                extra=extra_name,
                python_version=python_version,
                subdir=subdir,
                mappers=mappers,
                allow_pre=allow_pre,
            )
            if matchspec is not None:
                target.append(matchspec)

    depends.extend(python_dependencies(config, metadata))
    return WheelDependencies(
        depends=tuple(depends),
        extra_depends=_dedupe_extras(depends, extra_depends),
    )


def _entry_dependency(
    entry: str,
    *,
    extra: str,
    python_version: tuple[int, int | None],
    subdir: CondaSubdir | None,
    mappers: NameMappers,
    allow_pre: bool,
) -> str | None:
    """`entry`'s MatchSpec for `extra`'s environment, or `None` if
    `entry`'s marker rules it out entirely.
    """
    requirement = Requirement(entry)
    marker_node = parse_marker(requirement.marker) if requirement.marker is not None else TRUE
    residual = conditional_dependency(
        marker_node, extra=extra, python_version=python_version, subdir=subdir
    )
    if residual is None:
        return None
    bare_entry = _bare_entry(requirement)
    final_entry = bare_entry if residual == "" else f"{bare_entry}; {residual}"
    return pep508_to_matchspec(final_entry, mappers, allow_pre=allow_pre)


def _bare_entry(requirement: Requirement) -> str:
    """`requirement` as a PEP 508 string, its own marker excluded --
    `"name[extras]specifier"`, or `"name[extras]@ url"` for a direct URL
    reference. The URL must survive this reconstruction even though
    reroll can never convert it -- dropping it here would silently turn a
    direct-URL requirement into a bare, unconstrained one instead of
    surfacing `pep508_to_matchspec`'s `UnconvertableRequirementError`.
    """
    parts = [requirement.name]
    if requirement.extras:
        parts.append(f"[{','.join(sorted(requirement.extras))}]")
    if requirement.url:
        parts.append(f"@ {requirement.url}")
    elif requirement.specifier:
        parts.append(str(requirement.specifier))
    return "".join(parts)


def _dedupe_extras(
    depends: list[str], extra_depends: dict[str, list[str]]
) -> dict[str, tuple[str, ...]]:
    """`extra_depends`, with any MatchSpec already an exact string match in
    `depends` removed from each extra's list -- never across two different
    extras (`WheelDependencies`).
    """
    return {
        name: tuple(matchspec for matchspec in deps if matchspec not in depends)
        for name, deps in extra_depends.items()
    }
