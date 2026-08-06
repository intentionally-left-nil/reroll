"""Scoring a `(pypi_name, conda_name)` pair's evidence into a probability."""

from __future__ import annotations

from dataclasses import dataclass

from reroll.parselmouth_mapper.names import NameAxis
from reroll.parselmouth_mapper.versions import VersionState, dominant_version_state

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
    version_state = dominant_version_state(
        {
            VersionState.AGREES: evidence.n_versions_agree,
            VersionState.DISAGREES: evidence.n_versions_disagree,
        }
    )
    contradictions = int(evidence.vendored_only) + int(evidence.claimed_by_other)
    return round(
        BASE_PROBABILITY
        * _NAME_FACTOR[evidence.name_axis]
        * _VERSION_FACTOR[version_state]
        * _CONTRADICTION_FACTOR[contradictions],
        4,
    )


_NAME_FACTOR: dict[NameAxis, float] = {
    NameAxis.SAME: 1.00,
    NameAxis.NEAR: 0.75,
    NameAxis.SUBPACKAGE: 0.60,
    NameAxis.UNRELATED: 0.40,
}
_VERSION_FACTOR: dict[VersionState, float] = {
    VersionState.AGREES: 1.00,
    VersionState.NO_SIGNAL: 0.80,
    VersionState.DISAGREES: 0.45,
}
_CONTRADICTION_FACTOR: dict[int, float] = {0: 1.00, 1: 0.35, 2: 0.05}
