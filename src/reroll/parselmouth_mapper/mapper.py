"""Building a `NameMapper` from a parselmouth evidence database."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName

from reroll.name_mapping import Candidate, CandidateSource, NameMapper
from reroll.parselmouth_mapper.names import NameAxis
from reroll.parselmouth_mapper.scoring import CandidateEvidence, score_evidence

_MAPPER_NAME = "parselmouth_relations"


def parselmouth_mapper(connection: sqlite3.Connection) -> NameMapper:
    """Build a `NameMapper` backed by `connection`'s `pypi_conda_mapping`
    table (see `write_relations`).

    Every surviving row for `name` is contributed as its own `Candidate`:
    most of parselmouth's data is correct, so a low-confidence row is still
    worth a low `probability`, not a rejection -- the aggregator downstream
    decides what to do with it. A miss returns `candidates` unchanged.

    `specifier` is currently ignored: every candidate is scored from its
    pair's whole version history, regardless of which version a caller
    asked about.
    """

    def _lookup(
        name: NormalizedName,
        specifier: SpecifierSet,
        candidates: Sequence[Candidate],
    ) -> Sequence[Candidate]:
        del specifier
        rows = connection.execute(
            "SELECT conda_name, name_axis, n_versions_agree, n_versions_no_signal, "
            "n_versions_disagree, vendored_only, claimed_by_other "
            "FROM pypi_conda_mapping WHERE pypi_name = ?",
            (name,),
        ).fetchall()
        if not rows:
            return candidates
        contributed = [
            Candidate(
                conda_name=conda_name,
                probability=score_evidence(
                    CandidateEvidence(
                        conda_name=conda_name,
                        name_axis=NameAxis(name_axis_value),
                        n_versions_agree=n_versions_agree,
                        n_versions_no_signal=n_versions_no_signal,
                        n_versions_disagree=n_versions_disagree,
                        vendored_only=bool(vendored_only),
                        claimed_by_other=bool(claimed_by_other),
                    )
                ),
                source=CandidateSource.PARSELMOUTH,
                mapper=_MAPPER_NAME,
            )
            for (
                conda_name,
                name_axis_value,
                n_versions_agree,
                n_versions_no_signal,
                n_versions_disagree,
                vendored_only,
                claimed_by_other,
            ) in rows
        ]
        return (*candidates, *contributed)

    return _lookup
