"""Persisting parselmouth relations into sqlite, aggregating them into
per-`(pypi_name, conda_name)` evidence, and keeping a local copy of the
database current with upstream.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from packaging.utils import NormalizedName, canonicalize_name

from reroll.errors import CacheWriteError, DatabaseError
from reroll.parselmouth_mapper.ingest import (
    DEFAULT_RELATIONS_URL,
    download_relations,
    iter_relations,
)
from reroll.parselmouth_mapper.names import NameAxis, name_axis, parse_conda_filename
from reroll.parselmouth_mapper.types import RelationRow
from reroll.parselmouth_mapper.versions import (
    VersionState,
    dominant_version_state,
    version_state,
)


def open_parselmouth_database(
    db_path: Path, *, relations_url: str = DEFAULT_RELATIONS_URL
) -> sqlite3.Connection:
    """Open a parselmouth mapping database at `db_path`, rebuilding it from
    `relations_url` only if upstream has changed since it was last built here.
    Returns an open connection to (the possibly just-rebuilt) `db_path`.

    Raises `DatabaseError` if the rebuilt sqlite database itself can't be
    written, or `CacheWriteError` if the completed rebuild can't be
    installed at `db_path`.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    previous_etag = _stored_etag(db_path, relations_url)
    with tempfile.TemporaryDirectory() as scratch:
        scratch_path = Path(scratch)
        result = download_relations(
            relations_url, dest=scratch_path / "relations.jsonl.gz", etag=previous_etag
        )
        if not result.changed:
            return sqlite3.connect(db_path)

        build_path = scratch_path / "relations.sqlite3"
        try:
            build_connection = sqlite3.connect(build_path)
            try:
                write_relations(build_connection, iter_relations(result.path))
                _store_etag(build_connection, relations_url, result.etag)
            finally:
                build_connection.close()
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"failed to build parselmouth database from {relations_url!r}: {exc}"
            ) from exc

        staged_fd, staged_name = tempfile.mkstemp(dir=db_path.parent, prefix=f".{db_path.name}.")
        os.close(staged_fd)
        staged_path = Path(staged_name)
        try:
            shutil.copy2(build_path, staged_path)
            staged_path.replace(db_path)
        except OSError as exc:
            staged_path.unlink(missing_ok=True)
            raise CacheWriteError(
                f"failed to install parselmouth database at {db_path}: {exc}"
            ) from exc
        except BaseException:
            staged_path.unlink(missing_ok=True)
            raise
    return sqlite3.connect(db_path)


def write_relations(connection: sqlite3.Connection, rows: Iterable[RelationRow]) -> None:
    """Classify and persist `rows` into `pypi_claim`, then recompute
    `pypi_conda_mapping` from the result.

    A row whose `conda_filename` disagrees with its own `conda_name` is
    dropped instead of written: parselmouth's own name/version parsing for
    that one raw row already disagrees with itself, so nothing it reports
    can be trusted either for or against a pair. This is scoped to the
    individual row, not to every row sharing its `(conda_name,
    conda_version)`.

    Creates the schema if it does not already exist.
    """
    _create_schema(connection)
    for row in rows:
        classified = _classify(row)
        if classified.filename_mismatch:
            continue
        connection.execute(
            "INSERT OR IGNORE INTO pypi_claim "
            "(conda_name, conda_version, pypi_name, pypi_version, version_state) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                classified.conda_name,
                classified.conda_version,
                classified.pypi_name,
                classified.pypi_version,
                classified.row_version_state.value,
            ),
        )
    connection.commit()
    _refresh_pypi_conda_mapping(connection)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS parselmouth_version (
    url TEXT PRIMARY KEY,
    etag TEXT,
    fetched_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS pypi_claim (
    conda_name TEXT NOT NULL,
    conda_version TEXT NOT NULL,
    pypi_name TEXT NOT NULL,
    pypi_version TEXT NOT NULL,
    version_state TEXT NOT NULL,
    PRIMARY KEY (conda_name, conda_version, pypi_name, pypi_version)
) STRICT;

CREATE TABLE IF NOT EXISTS pypi_conda_mapping (
    pypi_name TEXT NOT NULL,
    conda_name TEXT NOT NULL,
    name_axis TEXT NOT NULL,
    n_versions INTEGER NOT NULL,
    n_versions_agree INTEGER NOT NULL,
    n_versions_no_signal INTEGER NOT NULL,
    n_versions_disagree INTEGER NOT NULL,
    vendored_only INTEGER NOT NULL,
    claimed_by_other INTEGER NOT NULL,
    PRIMARY KEY (pypi_name, conda_name)
) STRICT;
"""


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA_SQL)


def _refresh_pypi_conda_mapping(connection: sqlite3.Connection) -> None:
    """Recompute `pypi_conda_mapping` from the raw `pypi_claim` rows already
    stored in `connection`.

    Version-level counts (`n_versions_*`) are computed per distinct
    `pypi_version`, not per raw claim row: a version built for many
    platforms would otherwise cast many more "votes" than a version built
    for one, which has nothing to do with whether the pair's identity is
    correct.

    `vendored_only` corroboration is keyed by `(conda_name, conda_version)`,
    not by which specific build reported the corroborating claim: a name
    genuinely tied to that release is evidence against every other name
    claiming the same release, not just the one build that happened to
    report it.
    """
    rows = connection.execute(
        "SELECT conda_name, conda_version, pypi_name, pypi_version, version_state FROM pypi_claim"
    ).fetchall()

    artifact_agrees: dict[tuple[str, str], int] = {}
    for conda_name, conda_version, _pypi_name, _pypi_version, row_state in rows:
        if row_state == VersionState.AGREES.value:
            key = (conda_name, conda_version)
            artifact_agrees[key] = artifact_agrees.get(key, 0) + 1

    claimants: dict[str, set[str]] = {}
    for conda_name, _conda_version, pypi_name, _pypi_version, row_state in rows:
        if (
            row_state == VersionState.AGREES.value
            and name_axis(conda_name, pypi_name) is NameAxis.SAME
        ):
            claimants.setdefault(conda_name, set()).add(pypi_name)

    pairs: dict[tuple[str, str], list[tuple[str, str]]] = {}
    per_version: dict[tuple[str, str], dict[str, list[tuple[str, str]]]] = {}
    for conda_name, conda_version, pypi_name, pypi_version, row_state in rows:
        pairs.setdefault((pypi_name, conda_name), []).append((conda_version, row_state))
        per_version.setdefault((pypi_name, conda_name), {}).setdefault(pypi_version, []).append(
            (conda_version, row_state)
        )

    mapping_rows = []
    for (pypi_name, conda_name), pair in pairs.items():
        version_verdicts = _reduce_versions(per_version[(pypi_name, conda_name)])
        version_counts = Counter(state for _conda_version, state in version_verdicts)
        n_versions_agree = version_counts[VersionState.AGREES]
        n_versions_no_signal = version_counts[VersionState.NO_SIGNAL]
        n_versions_disagree = version_counts[VersionState.DISAGREES]

        vendored_only = n_versions_agree == 0 and any(
            state != VersionState.AGREES.value
            and artifact_agrees.get((conda_name, conda_version), 0) > 0
            for conda_version, state in pair
        )
        claimed_by_other = bool(claimants.get(conda_name, set()) - {pypi_name})
        mapping_rows.append(
            (
                pypi_name,
                conda_name,
                name_axis(conda_name, pypi_name).value,
                len(version_verdicts),
                n_versions_agree,
                n_versions_no_signal,
                n_versions_disagree,
                int(vendored_only),
                int(claimed_by_other),
            )
        )

    connection.execute("DELETE FROM pypi_conda_mapping")
    connection.executemany(
        "INSERT INTO pypi_conda_mapping "
        "(pypi_name, conda_name, name_axis, n_versions, n_versions_agree, "
        " n_versions_no_signal, n_versions_disagree, vendored_only, claimed_by_other) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        mapping_rows,
    )
    connection.commit()


def _stored_etag(db_path: Path, url: str) -> str | None:
    """The `ETag` recorded the last time `open_parselmouth_database` built
    `db_path` from `url`. `None` if `db_path` does not exist, or exists but
    was built from a different `url` (e.g. a different channel).

    Raises `DatabaseError` if `db_path` exists but can't be read as sqlite.
    """
    if not db_path.exists():
        return None
    try:
        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                "SELECT etag FROM parselmouth_version WHERE url = ?", (url,)
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise DatabaseError(f"failed to read parselmouth database at {db_path}: {exc}") from exc
    return None if row is None else row[0]


def _store_etag(connection: sqlite3.Connection, url: str, etag: str | None) -> None:
    connection.execute(
        "INSERT INTO parselmouth_version (url, etag, fetched_at) VALUES (?, ?, ?) "
        "ON CONFLICT (url) DO UPDATE SET etag = excluded.etag, fetched_at = excluded.fetched_at",
        (url, etag, datetime.now(UTC).isoformat()),
    )
    connection.commit()


@dataclass(frozen=True, slots=True)
class _ClassifiedRelation:
    conda_name: str
    conda_version: str
    filename_mismatch: bool
    pypi_name: NormalizedName
    pypi_version: str
    row_version_state: VersionState


def _classify(row: RelationRow) -> _ClassifiedRelation:
    filename_name, conda_version = parse_conda_filename(row["conda_filename"])
    return _ClassifiedRelation(
        conda_name=row["conda_name"],
        conda_version=conda_version,
        filename_mismatch=canonicalize_name(filename_name) != canonicalize_name(row["conda_name"]),
        pypi_name=canonicalize_name(row["pypi_name"]),
        pypi_version=row["pypi_version"],
        row_version_state=version_state(conda_version, row["pypi_version"]),
    )


def _reduce_versions(
    by_version: Mapping[str, list[tuple[str, str]]],
) -> list[tuple[str, VersionState]]:
    """One `(conda_version, VersionState)` pair per distinct `pypi_version`
    in `by_version` (mapping a `pypi_version` to that version's own raw
    `(conda_version, version_state)` claims), reduced via
    `dominant_version_state`. `conda_version` is any one of that version's
    claims' own value: artifacts built for the same declared `pypi_version`
    share it in practice.
    """
    reduced = []
    for claims in by_version.values():
        counts = Counter(VersionState(state) for _conda_version, state in claims)
        conda_version = claims[0][0]
        reduced.append((conda_version, dominant_version_state(counts)))
    return reduced
