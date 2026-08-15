"""Equivalence oracle: compares pip/uv's own marker evaluation (via
`packaging.markers.Marker`) against reroll's matchspec `when=` conversion,
for a resolved Python full version -- so a test can check the conversion is
actually equivalent, not just that it matches one expected string.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable

from markerpry import parse
from packaging.markers import Marker
from packaging.version import Version as PypiVersion
from rattler import Version as CondaVersion
from rattler import VersionSpec

from reroll.dependencies.marker_conversion import marker_condition
from reroll.dependencies.version_format import format_version

_ATOM_PATTERN = re.compile(r"python(?:==|!=|>=|<=|=|>|<)[^\s()]+|__[a-z]+")
"""Every leaf `marker_condition` can emit: a `python<op>...` version
comparison (including the bare `=` fuzzy match a glob literal converts to)
or a virtual package bare name (`__linux`, ...). `[^\\s()]+`, not `\\S+`,
so an atom that's itself wrapped in parens (any same-operator chain of 3+
terms, `docs/matchspec.md`) doesn't swallow the closing `)` into the match
-- a version spec never contains a paren itself.
"""


def matchspec_condition(marker: str, *, abi3_upper_bound: str | None = None) -> str:
    """reroll's matchspec `when=` condition for the PEP 508 marker string
    `marker`, via the same `marker_condition` production code
    `pep508_to_matchspec` calls.
    """
    return marker_condition(parse(marker), abi3_upper_bound=abi3_upper_bound)


def pip_evaluates(marker: str, python_full_version: str) -> bool:
    """Whether pip/uv would keep a dependency guarded by `marker` once
    resolved against a CPython interpreter at `python_full_version` (e.g.
    `"3.9.0rc1"`), per `packaging.markers.Marker.evaluate`.
    """
    major, minor = python_full_version.split(".")[:2]
    environment = {
        "python_version": f"{major}.{minor}",
        "python_full_version": python_full_version,
        "implementation_version": python_full_version,
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
    }
    return Marker(marker).evaluate(environment)


def matchspec_evaluates(condition: str, python_full_version: str) -> bool:
    """Whether py-rattler's `VersionSpec` would keep a dependency guarded by
    the matchspec `when=` condition `condition`, once `python` resolves to
    `python_full_version` (a PyPI/PEP 440-spelled version).
    """
    conda_version = CondaVersion(format_version(PypiVersion(python_full_version)))

    def _resolve_atom(match: re.Match[str]) -> str:
        atom = match.group(0)
        if atom.startswith("__"):
            return "True"
        return str(VersionSpec(atom.removeprefix("python")).matches(conda_version))

    return _eval_bool_expression(_ATOM_PATTERN.sub(_resolve_atom, condition))


def assert_matchspec_agrees_with_pip(
    marker: str, python_full_versions: Iterable[str], *, abi3_upper_bound: str | None = None
) -> None:
    """Converts `marker` to a matchspec condition once, then asserts that
    condition's `matchspec_evaluates` result agrees with `pip_evaluates`
    independently, for every `python_full_versions` candidate -- the
    equivalence the conversion exists to preserve.
    """
    condition = matchspec_condition(marker, abi3_upper_bound=abi3_upper_bound)
    for python_full_version in python_full_versions:
        pip_result = pip_evaluates(marker, python_full_version)
        matchspec_result = matchspec_evaluates(condition, python_full_version)
        assert pip_result == matchspec_result, (
            f"marker {marker!r} -> matchspec {condition!r} disagrees with pip for "
            f"python_full_version={python_full_version!r}: "
            f"pip says {pip_result}, matchspec says {matchspec_result}"
        )


def assert_pip_is_constant(
    marker: str, expected: bool, python_full_versions: Iterable[str]
) -> None:
    """Asserts `pip_evaluates(marker, ...)` is `expected` for every
    candidate in `python_full_versions` -- the ground-truth check backing
    a case where `marker_condition` declines to convert `marker` at all
    because no matchspec fragment can represent a constant.
    """
    for python_full_version in python_full_versions:
        assert pip_evaluates(marker, python_full_version) is expected, python_full_version


def _eval_bool_expression(expression: str) -> bool:
    """Evaluates `expression`, a boolean expression of `True`/`False`
    literals combined with `and`/`or`/parens -- `matchspec_evaluates`'s
    atom substitution never produces anything else -- without resorting to
    `eval`.
    """
    return _eval_node(ast.parse(expression, mode="eval").body)


def _eval_node(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(value) for value in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    raise AssertionError(f"unexpected boolean expression node: {ast.dump(node)}")
