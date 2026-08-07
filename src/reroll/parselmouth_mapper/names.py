"""Parsing conda artifact filenames, and classifying how related a conda
package's spelling is to a PyPI name's.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from packaging.utils import canonicalize_name


class NameClass(StrEnum):
    """How `conda_name`'s spelling relates to `pypi_name`'s."""

    IDENTICAL = "identical"
    PUNCTUATION = "punctuation"
    CONVENTION = "convention"
    TOKEN_PREFIX = "token_prefix"
    UNRELATED = "unrelated"


class NameAxis(StrEnum):
    """`NameClass`, folded together with an affix-stripped edit distance."""

    SAME = "same"
    NEAR = "near"
    SUBPACKAGE = "subpackage"
    UNRELATED = "unrelated"


@lru_cache(maxsize=4096)
def classify_name_relation(conda_name: str, pypi_name: str) -> NameClass:
    """Classify how `conda_name`'s spelling relates to `pypi_name`'s.

    Both are PEP 503-canonicalized before comparison, so case and
    separator differences are not, by themselves, evidence of anything.
    """
    conda = canonicalize_name(conda_name)
    pypi = canonicalize_name(pypi_name)
    if conda == pypi:
        return NameClass.IDENTICAL
    if conda.replace("-", "") == pypi.replace("-", ""):
        return NameClass.PUNCTUATION
    conda_variants = _stripped_variants(conda, _CONDA_PREFIXES, _CONDA_SUFFIXES)
    pypi_variants = _stripped_variants(pypi, _PYPI_PREFIXES, _PYPI_SUFFIXES)
    if conda_variants & pypi_variants:
        return NameClass.CONVENTION
    conda_tokens, pypi_tokens = conda.split("-"), pypi.split("-")
    if (
        conda_tokens[0] == pypi_tokens[0]
        or conda_tokens[: len(pypi_tokens)] == pypi_tokens
        or pypi_tokens[: len(conda_tokens)] == conda_tokens
    ):
        return NameClass.TOKEN_PREFIX
    return NameClass.UNRELATED


@lru_cache(maxsize=4096)
def variant_distance(conda_name: str, pypi_name: str) -> int:
    """Minimum edit distance between any affix-stripped, separator-free
    spelling of `conda_name` and any of `pypi_name`.

    Every `IDENTICAL`, `PUNCTUATION`, or `CONVENTION` pair (per
    `classify_name_relation`) scores 0.
    """
    conda_cores = {
        v.replace("-", "")
        for v in _stripped_variants(canonicalize_name(conda_name), _CONDA_PREFIXES, _CONDA_SUFFIXES)
    }
    pypi_cores = {
        v.replace("-", "")
        for v in _stripped_variants(canonicalize_name(pypi_name), _PYPI_PREFIXES, _PYPI_SUFFIXES)
    }
    return min(_levenshtein_distance(a, b) for a in conda_cores for b in pypi_cores)


@lru_cache(maxsize=4096)
def name_axis(conda_name: str, pypi_name: str) -> NameAxis:
    """Fold `classify_name_relation` and `variant_distance` into one axis.

    Distance is checked before the `TOKEN_PREFIX` shape: a sub-package name
    that happens to be a near-miss spelling of its parent (e.g.
    `asapdiscovery-ml` vs `asapdiscovery`, distance 2) is `NEAR`, not
    `SUBPACKAGE`.
    """
    name_class = classify_name_relation(conda_name, pypi_name)
    if name_class in (NameClass.IDENTICAL, NameClass.PUNCTUATION, NameClass.CONVENTION):
        return NameAxis.SAME
    distance = variant_distance(conda_name, pypi_name)
    if distance == 0:
        return NameAxis.SAME
    if distance <= 2:
        return NameAxis.NEAR
    if name_class is NameClass.TOKEN_PREFIX:
        return NameAxis.SUBPACKAGE
    return NameAxis.UNRELATED


def parse_conda_filename(conda_filename: str) -> tuple[str, str]:
    """Recover `(name, version)` from a conda artifact filename.

    A conda filename is `{name}-{version}-{build}.{conda,tar.bz2}`. Neither
    `version` nor `build` may contain `-`, so the last two `-`-separated
    fields are always version and build, however many hyphens `name` has.

    Raises `ValueError` if `conda_filename` does not end in a known conda
    archive suffix, or has too few `-`-separated fields to contain a name.
    """
    stem = None
    for suffix in _KNOWN_CONDA_SUFFIXES:
        if conda_filename.endswith(suffix):
            stem = conda_filename[: -len(suffix)]
            break
    if stem is None:
        raise ValueError(f"not a conda archive filename (unknown suffix): {conda_filename!r}")
    parts = stem.split("-")
    if len(parts) < 3:
        raise ValueError(f"not a conda artifact filename: {conda_filename!r}")
    return "-".join(parts[:-2]), parts[-2]


_CONDA_PREFIXES = ("python-", "py-", "r-", "lib", "python_")
_CONDA_SUFFIXES = ("-python", "-py", "-cpp", "-base", "-core", "-split")
_PYPI_PREFIXES = ("python-", "py-")
_PYPI_SUFFIXES = (
    "-python",
    "-py",
    "-cpu",
    "-gpu",
    "-headless",
    "-bin",
    "-binary",
    "-wheel",
    "-nightly",
)
_KNOWN_CONDA_SUFFIXES = (".conda", ".tar.bz2")


@lru_cache(maxsize=4096)
def _stripped_variants(
    name: str, prefixes: tuple[str, ...], suffixes: tuple[str, ...]
) -> frozenset[str]:
    """Every affix-stripped spelling of `name`, plus a `py`-prefix toggle
    (`pytorch` <-> `torch`). `name` must already be PEP 503-canonicalized.
    """
    variants = {name}
    for prefix in prefixes:
        if name.startswith(prefix) and len(name) > len(prefix):
            variants.add(name[len(prefix) :])
    for suffix in suffixes:
        if name.endswith(suffix) and len(name) > len(suffix):
            variants.add(name[: -len(suffix)])
    variants |= {v[2:] for v in list(variants) if v.startswith("py") and len(v) > 4}
    variants |= {f"py{v}" for v in list(variants)}
    return frozenset(variants)


def _levenshtein_distance(a: str, b: str) -> int:
    """Iterative edit distance: O(len(a) * len(b)) time, O(len(b)) space."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        current = [i + 1]
        for j, cb in enumerate(b):
            current.append(min(previous[j + 1] + 1, current[j] + 1, previous[j] + (ca != cb)))
        previous = current
    return previous[-1]
