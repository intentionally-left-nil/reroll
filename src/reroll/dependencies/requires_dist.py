"""`Requires-Dist` entries removed before conda dependency conversion."""

from __future__ import annotations

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_INTERPRETER_NAMES = frozenset({"python", "cpython", "pypy", "graalpy"})


def strip_interpreter_requirements(requires_dist: tuple[str, ...]) -> tuple[str, ...]:
    """`requires_dist` with every unconditional (marker-free) dependency on
    the interpreter itself removed -- `python`, `cpython`, `pypy`, or
    `graalpy`, regardless of any version or extras qualifier.

    PyPI can't be name-squatted this way, so no real wheel needs this, but
    conda has a real `python` package: translating such an entry literally
    would create a bogus dependency (docs/wheel_to_conda_dependencies.md).
    A marker-qualified reference to one of these names is left in place --
    it belongs to marker conversion, not this rule.
    """
    return tuple(entry for entry in requires_dist if not _is_bare_interpreter(entry))


def _is_bare_interpreter(entry: str) -> bool:
    requirement = Requirement(entry)
    return requirement.marker is None and canonicalize_name(requirement.name) in _INTERPRETER_NAMES
