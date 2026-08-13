"""Evaluate one `Requires-Dist` marker against a wheel's known Python
pinning, extra selection, and (for an arch-specific record) target subdir.

Implements docs/wheel_to_conda_dependencies.md#calculating-conditional-dependencies.
"""

from __future__ import annotations

from collections.abc import Iterator

from markerpry import FALSE, TRUE, CompareNode, ContainsNode, Node, evaluate
from markerpry.modifiers.tighten import tighten_ranges

from reroll.dependencies.environment import arch_specific_environment, noarch_environment
from reroll.errors import NeedsArchSplitError, UnconvertableMarkerError
from reroll.subdir import CondaSubdir

_FULL_VERSION_KEYS = frozenset({"python_full_version", "implementation_version"})
_UNREDUCIBLE_COMPARATORS = frozenset({"==", "!="})
_ARCH_KEYS = frozenset({"platform_system", "platform_machine", "sys_platform", "os_name"})
_PERMITTED_KEYS = frozenset({"python_version", "python_full_version", "implementation_version"})


def conditional_dependency(
    marker: Node,
    *,
    extra: str,
    python_version: tuple[int, int | None],
    subdir: CondaSubdir | None,
) -> str | None:
    """Reduce `marker` -- one `Requires-Dist` entry's marker -- against a
    candidate environment built from `python_version`, `extra`, and
    `subdir`.

    `python_version` is `(floor, ceiling)`, the shape
    `reroll.filename.python_requirement.minor_range` returns: `python_version`/
    `python_full_version`/`implementation_version` are only fixed to a
    specific minor when this range covers exactly one (`ceiling == floor + 1`);
    otherwise those keys are left out of the environment, and a marker
    referencing them converts as-is instead of resolving.

    `extra` is `""` for the base dependency, or one of `find_extras`'
    normalized extra names -- either way, the value `marker`'s `extra`
    clauses (if any) are compared against.

    `subdir` is `None` for a noarch record, or a specific `CondaSubdir` for
    an arch-specific one -- this also selects `noarch_environment`/
    `arch_specific_environment` as the environment's platform-fixed base.

    Returns `None` if `marker` fully evaluates to false (no dependency
    should be added), `""` if it fully evaluates to true (add the
    dependency with no condition at all), or -- if evaluation is
    incomplete -- the remaining marker as a PEP 508 string (after
    `tighten_ranges`), left for a later stage to convert to a matchspec
    `when=` condition (`reroll.dependencies.marker_conversion.marker_condition`).

    Raises `NeedsArchSplitError` if `subdir` is `None` and `marker` still
    refers to `platform_system`/`platform_machine`/`sys_platform`/`os_name`
    after evaluation -- the caller must retry per-subdir instead of
    emitting a noarch record.

    Raises `UnconvertableMarkerError` if, after evaluation (and the noarch
    arch-split check above), `marker` still refers to any key besides
    `python_version`/`python_full_version`/`implementation_version` -- the
    dependency has no possible conda representation, and the whole record
    should not be emitted.
    """
    minor = _exact_minor(python_version)
    environment = (
        noarch_environment(minor) if subdir is None else arch_specific_environment(minor, subdir)
    )
    unreducible = _unreducible_full_version_keys(marker)
    environment = {key: value for key, value in environment.items() if key not in unreducible}
    environment = {**environment, "extra": [extra]}

    partial_evaluation = evaluate(marker, environment)
    if partial_evaluation == TRUE:
        return ""
    if partial_evaluation == FALSE:
        return None

    remaining_keys = _marker_keys(partial_evaluation)
    if subdir is None and remaining_keys & _ARCH_KEYS:
        raise NeedsArchSplitError(
            f"cannot represent marker {marker!r} in a noarch record: it still refers "
            f"to platform-specific key(s) {sorted(remaining_keys & _ARCH_KEYS)}"
        )
    unpermitted = remaining_keys - _PERMITTED_KEYS
    if unpermitted:
        raise UnconvertableMarkerError(
            f"cannot convert marker {marker!r}: it still refers to unpermitted "
            f"key(s) {sorted(unpermitted)} after evaluation"
        )
    return str(tighten_ranges(partial_evaluation))


def _exact_minor(python_version: tuple[int, int | None]) -> int | None:
    floor, ceiling = python_version
    return floor if ceiling == floor + 1 else None


def _unreducible_full_version_keys(marker: Node) -> frozenset[str]:
    """Which of `python_full_version`/`implementation_version` `marker`
    compares with an operator the reduction algorithm can't handle (`==`,
    `!=`, `in`, `not in`) -- the algorithm only covers ordered comparators
    (docs/matchspec.md#reducing-python_full_version--implementation_version-to-python_version),
    so a disqualifying use of either key anywhere in `marker` drops that
    key from the environment entirely, for the whole tree.
    """
    keys: set[str] = set()
    for node in _walk(marker):
        if (
            isinstance(node, ContainsNode)
            and node.key in _FULL_VERSION_KEYS
            or (
                isinstance(node, CompareNode)
                and node.key in _FULL_VERSION_KEYS
                and node.comparator in _UNREDUCIBLE_COMPARATORS
            )
        ):
            keys.add(node.key)
    return frozenset(keys)


def _marker_keys(node: Node) -> frozenset[str]:
    """Every marker key referenced anywhere in `node`."""
    return frozenset(n.key for n in _walk(node) if isinstance(n, (CompareNode, ContainsNode)))


def _walk(node: Node) -> Iterator[Node]:
    yield node
    if node.left is not None:
        yield from _walk(node.left)
    if node.right is not None:
        yield from _walk(node.right)
