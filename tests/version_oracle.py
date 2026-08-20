"""Equivalence oracle: compares pip/uv's own PEP 440 specifier evaluation
(via `packaging.specifiers.SpecifierSet`) against the plain (no `when=`)
version clause reroll's matchspec conversion produces for a dependency's
own version specifier -- so a test can check the conversion is actually
equivalent for a candidate version, not just that it matches one expected
string.
"""

from __future__ import annotations

from collections.abc import Iterable

from packaging.specifiers import SpecifierSet
from packaging.version import Version as PypiVersion
from rattler import Version as CondaVersion
from rattler import VersionSpec

from reroll.dependencies.pep508_to_matchspec import pep508_to_matchspec
from reroll.dependencies.version_format import format_version
from reroll.name_mapping import passthrough_mapper

_PACKAGE_NAME = "pkg"
"""An arbitrary name `passthrough_mapper` passes through unchanged, so
`matchspec_version` can strip it back off to get the bare version clause.
"""


def matchspec_version(specifier: str, *, allow_pre: bool = False) -> str:
    """The plain matchspec version clause `pep508_to_matchspec` produces
    for the bare PEP 440 specifier (set) `specifier` (e.g.
    `">=1.0.0,<2.0.0"` or `"~=3.13.2"`), via the same production code a
    real dependency's version goes through.
    """
    matchspec = pep508_to_matchspec(
        f"{_PACKAGE_NAME}{specifier}", (passthrough_mapper,), allow_pre=allow_pre
    )
    return matchspec.removeprefix(f"{_PACKAGE_NAME} ")


def pip_matches(specifier: str, candidate: str) -> bool:
    """Whether pip/uv would consider `candidate` (a PyPI/PEP 440-spelled
    version) to satisfy `specifier`, per `SpecifierSet.contains` --
    `prereleases=True` asks the pure range question (is `candidate`
    mathematically within the specifier's bounds), independent of pip's
    separate policy of otherwise excluding prereleases by default; conda
    has no equivalent policy layer at the `VersionSpec.matches` level, so
    this is the fair comparison.
    """
    return SpecifierSet(specifier).contains(candidate, prereleases=True)


def matchspec_matches(matchspec_version_clause: str, candidate: str) -> bool:
    """Whether py-rattler's `VersionSpec` would consider `candidate` (a
    PyPI/PEP 440-spelled version, converted to conda's CEP-33 spelling
    here) to satisfy `matchspec_version_clause`.
    """
    conda_version = CondaVersion(format_version(PypiVersion(candidate)))
    return VersionSpec(matchspec_version_clause).matches(conda_version)


def assert_matchspec_agrees_with_pip(
    specifier: str, candidates: Iterable[str], *, allow_pre: bool = False
) -> None:
    """Converts `specifier` to a matchspec version clause once, then
    asserts that clause's `matchspec_matches` result agrees with
    `pip_matches` independently, for every candidate in `candidates` --
    the equivalence the conversion exists to preserve.
    """
    clause = matchspec_version(specifier, allow_pre=allow_pre)
    for candidate in candidates:
        pip_result = pip_matches(specifier, candidate)
        matchspec_result = matchspec_matches(clause, candidate)
        assert pip_result == matchspec_result, (
            f"specifier {specifier!r} -> matchspec {clause!r} disagrees with pip for "
            f"candidate={candidate!r}: pip says {pip_result}, matchspec says {matchspec_result}"
        )
