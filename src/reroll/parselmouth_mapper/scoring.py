"""Scoring a `(pypi_name, conda_name)` pair's evidence into a probability."""

from __future__ import annotations

from dataclasses import dataclass

from reroll.parselmouth_mapper.names import NameAxis

BASE_PROBABILITY = 0.95
"""Probability of a pair whose every axis is maximally favorable. Kept
below 1.0 because parselmouth data, even at its best, is never a static
override: `probability=1.0` is reserved for a mapper that is authoritative
by construction (e.g. a hand-maintained table).
"""


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """Aggregate evidence for one `(pypi_name, conda_name)` pair, as stored
    in the `pypi_conda_mapping` table (`pypi_name` is the aggregate's key,
    not a field, since callers always already know which `pypi_name` they
    asked for).
    """

    conda_name: str
    name_axis: NameAxis
    n_versions_agree: int
    n_versions_no_signal: int
    n_versions_disagree: int
    vendored_only: bool
    claimed_by_other: bool


def score_evidence(evidence: CandidateEvidence) -> float:
    """Multiplicative probability from three independent axes: how related
    the names are, whether a version corroborates the pair, and whether
    another pair's evidence contradicts this one.
    """
    contradictions = int(evidence.vendored_only) + int(evidence.claimed_by_other)
    return round(
        BASE_PROBABILITY
        * _NAME_FACTOR[evidence.name_axis]
        * _version_factor(evidence)
        * _CONTRADICTION_FACTOR[contradictions],
        4,
    )


def _version_factor(evidence: CandidateEvidence) -> float:
    """How strongly this package's PyPI versions, taken together, corroborate
    `evidence.conda_name`.

    `NO_SIGNAL` versions are excluded, as neutral evidence. With no
    informative versions, returns `_NO_SIGNAL_FACTOR`; with only agreement or
    only disagreement, `_AGREES_FACTOR` or `_DISAGREES_FACTOR` respectively.
    Otherwise, linearly interpolates between those two by the agreeing share
    of informative versions, so one disagreeing version against many
    agreeing ones barely moves the score, and vice versa.
    """
    agree, disagree = evidence.n_versions_agree, evidence.n_versions_disagree
    if agree == 0 and disagree == 0:
        return _NO_SIGNAL_FACTOR
    if disagree == 0:
        return _AGREES_FACTOR
    if agree == 0:
        return _DISAGREES_FACTOR
    share = agree / (agree + disagree)
    return _DISAGREES_FACTOR + (_AGREES_FACTOR - _DISAGREES_FACTOR) * share


_NAME_FACTOR: dict[NameAxis, float] = {
    NameAxis.SAME: 1.00,
    NameAxis.NEAR: 0.75,
    NameAxis.SUBPACKAGE: 0.60,
    NameAxis.UNRELATED: 0.40,
}
_AGREES_FACTOR = 1.00
_NO_SIGNAL_FACTOR = 0.80
_DISAGREES_FACTOR = 0.45
_CONTRADICTION_FACTOR: dict[int, float] = {0: 1.00, 1: 0.35, 2: 0.05}
