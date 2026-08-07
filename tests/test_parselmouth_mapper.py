"""Unit tests for `reroll.parselmouth_mapper`."""

from __future__ import annotations

import gc
import gzip
import io
import json
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Iterator
from email.message import Message
from pathlib import Path

import pytest
from packaging.utils import canonicalize_name

from reroll.name_mapping import Candidate, CandidateSource
from reroll.parselmouth_mapper import (
    BASE_PROBABILITY,
    DEFAULT_RELATIONS_URL,
    CandidateEvidence,
    NameAxis,
    NameClass,
    RelationRow,
    VersionClass,
    VersionState,
    classify_name_relation,
    classify_version,
    dominant_version_state,
    download_relations,
    iter_relations,
    name_axis,
    open_parselmouth_database,
    parse_conda_filename,
    parselmouth_mapper,
    score_evidence,
    variant_distance,
    version_sort_key,
    version_state,
    write_relations,
)
from reroll.parselmouth_mapper.mapper import _mapper_from_connection
from reroll.parselmouth_mapper.names import _levenshtein_distance

# --------------------------------------------------------------------------
# `parse_conda_filename`
# --------------------------------------------------------------------------


class TestParseCondaFilename:
    def test_simple_name(self) -> None:
        assert parse_conda_filename("requests-2.31.0-pyhd8ed1ab_0.conda") == (
            "requests",
            "2.31.0",
        )

    def test_hyphenated_name_is_not_split(self) -> None:
        assert parse_conda_filename("python-tzdata-2021.5-pyhd8ed1ab_0.conda") == (
            "python-tzdata",
            "2021.5",
        )

    def test_tar_bz2_suffix(self) -> None:
        assert parse_conda_filename("21cmfast-3.0.2-py38hd394728_0.tar.bz2") == (
            "21cmfast",
            "3.0.2",
        )

    def test_missing_known_suffix_raises(self) -> None:
        with pytest.raises(ValueError, match="conda-filename|filename"):
            parse_conda_filename("requests-2.31.0-pyhd8ed1ab_0.whl")

    def test_too_few_segments_raises(self) -> None:
        with pytest.raises(ValueError, match="not a conda artifact filename"):
            parse_conda_filename("onlyname.conda")


# --------------------------------------------------------------------------
# `classify_version`
# --------------------------------------------------------------------------


class TestClassifyVersion:
    def test_exact(self) -> None:
        assert classify_version("3.0.10", "3.0.10") is VersionClass.EXACT

    def test_pep440_equal(self) -> None:
        assert classify_version("1.0", "1.0.0") is VersionClass.PEP440_EQUAL

    def test_post_only(self) -> None:
        assert classify_version("2.4.1", "2.4.1.post101") is VersionClass.POST_ONLY

    def test_local_only(self) -> None:
        assert classify_version("3.3.0", "3.3.0+gitd9a1100d") is VersionClass.LOCAL_ONLY

    def test_dev_only(self) -> None:
        assert classify_version("1.0.0", "1.0.0.dev3") is VersionClass.DEV_ONLY

    def test_pre_only(self) -> None:
        assert classify_version("1.0rc1", "1.0rc2") is VersionClass.PRE_ONLY

    def test_epoch_only(self) -> None:
        assert classify_version("1!2.0", "2.0") is VersionClass.EPOCH_ONLY

    def test_suffix_multi(self) -> None:
        assert classify_version("0.14.1", "0.14.1a0+cb7b105") is VersionClass.SUFFIX_MULTI

    def test_release_prefix(self) -> None:
        # setuptools-scm writing extra trailing zero components: (6,25,7)
        # is a prefix of (6,25,7,0,2).
        assert classify_version("6.25.7", "6.25.7.0.2") is VersionClass.RELEASE_PREFIX

    def test_differ(self) -> None:
        assert classify_version("3.0.10", "2021.5") is VersionClass.DIFFER

    def test_unparseable_conda_side(self) -> None:
        assert classify_version("6b719af_dirty", "1.0.0") is VersionClass.UNPARSEABLE

    def test_unparseable_pypi_side(self) -> None:
        assert classify_version("1.0.0", "6b719af_dirty") is VersionClass.UNPARSEABLE

    def test_conda_underscore_retry(self) -> None:
        # Conda versions may not contain "-"; `1.0.0_rc1` is the conda
        # spelling of PEP 440's `1.0.0rc1`.
        assert (
            classify_version("1.0.0_rc1", "1.0.0rc1") is VersionClass.EXACT
            or classify_version("1.0.0_rc1", "1.0.0rc1") is VersionClass.PEP440_EQUAL
        )


# --------------------------------------------------------------------------
# `version_state`
# --------------------------------------------------------------------------


class TestVersionState:
    def test_exact_agrees(self) -> None:
        assert version_state("3.0.10", "3.0.10") is VersionState.AGREES

    def test_suffix_only_agrees(self) -> None:
        assert version_state("2.4.1", "2.4.1.post101") is VersionState.AGREES

    def test_all_zero_is_no_signal(self) -> None:
        assert version_state("3.1.3", "0.0.0") is VersionState.NO_SIGNAL

    def test_bare_zero_is_no_signal(self) -> None:
        assert version_state("3.1.3", "0") is VersionState.NO_SIGNAL

    def test_unknown_local_is_no_signal(self) -> None:
        assert version_state("3.1.3", "0+unknown") is VersionState.NO_SIGNAL

    def test_unparseable_pypi_side_is_no_signal(self) -> None:
        assert version_state("1.0.0", "6b719af_dirty") is VersionState.NO_SIGNAL

    def test_genuine_mismatch_is_disagrees(self) -> None:
        # The psycopg/tzdata vendoring case: a real, different release.
        assert version_state("3.0.10", "2021.5") is VersionState.DISAGREES

    def test_release_prefix_that_is_not_all_zero_is_disagrees(self) -> None:
        assert version_state("6.25.7", "6.25.7.0.2") is VersionState.DISAGREES


# --------------------------------------------------------------------------
# `classify_name_relation`
# --------------------------------------------------------------------------


class TestClassifyNameRelation:
    def test_identical(self) -> None:
        assert classify_name_relation("requests", "requests") is NameClass.IDENTICAL

    def test_punctuation(self) -> None:
        assert classify_name_relation("i-pi", "ipi") is NameClass.PUNCTUATION

    def test_convention_prefix(self) -> None:
        assert classify_name_relation("python-tzdata", "tzdata") is NameClass.CONVENTION

    def test_convention_suffix(self) -> None:
        assert classify_name_relation("msgpack-python", "msgpack") is NameClass.CONVENTION

    def test_convention_py_toggle(self) -> None:
        assert classify_name_relation("pytorch", "torch") is NameClass.CONVENTION

    def test_token_prefix(self) -> None:
        assert classify_name_relation("dagster-dbt", "dagster") is NameClass.TOKEN_PREFIX

    def test_unrelated(self) -> None:
        assert classify_name_relation("psycopg", "tzdata") is NameClass.UNRELATED

    def test_case_and_separators_are_normalized(self) -> None:
        assert classify_name_relation("Zope.Interface", "zope_interface") is NameClass.IDENTICAL


# --------------------------------------------------------------------------
# `variant_distance`
# --------------------------------------------------------------------------


class TestLevenshteinDistance:
    def test_identical_strings(self) -> None:
        assert _levenshtein_distance("abc", "abc") == 0

    def test_against_empty_string(self) -> None:
        assert _levenshtein_distance("abc", "") == 3
        assert _levenshtein_distance("", "abc") == 3

    def test_both_empty(self) -> None:
        assert _levenshtein_distance("", "") == 0


class TestVariantDistance:
    def test_identical_is_zero(self) -> None:
        assert variant_distance("requests", "requests") == 0

    def test_convention_prefix_is_zero(self) -> None:
        assert variant_distance("python-tzdata", "tzdata") == 0

    def test_py_toggle_is_zero(self) -> None:
        assert variant_distance("pytorch", "torch") == 0

    def test_small_typo_is_near(self) -> None:
        assert variant_distance("xontrib-kitty", "xontib-kitty") <= 2

    def test_renamed_distribution_is_near(self) -> None:
        assert variant_distance("py-sirius-ms", "pysirius") <= 2

    def test_unrelated_names_are_far(self) -> None:
        assert variant_distance("psycopg", "tzdata") > 2

    def test_short_unrelated_names_are_far(self) -> None:
        # `avl` is short enough that an un-guarded edit distance to
        # `agriculture-vlab` would look deceptively small; affix-stripping
        # to cores first keeps it far.
        assert variant_distance("agriculture-vlab", "avl") > 2


# --------------------------------------------------------------------------
# `name_axis`
# --------------------------------------------------------------------------


class TestNameAxis:
    def test_identical_is_same(self) -> None:
        assert name_axis("requests", "requests") is NameAxis.SAME

    def test_convention_is_same(self) -> None:
        assert name_axis("python-tzdata", "tzdata") is NameAxis.SAME

    def test_near_miss_is_near(self) -> None:
        assert name_axis("xontrib-kitty", "xontib-kitty") is NameAxis.NEAR

    def test_near_beats_subpackage_shape(self) -> None:
        # "asapdiscovery-ml" is a token_prefix of "asapdiscovery" *and*
        # within edit distance 2 of it; near must win the tie.
        assert name_axis("asapdiscovery", "asapdiscovery-ml") is NameAxis.NEAR

    def test_token_prefix_far_away_is_subpackage(self) -> None:
        assert name_axis("dagster", "dagster-postgres") is NameAxis.SUBPACKAGE

    def test_unrelated_is_unrelated(self) -> None:
        assert name_axis("psycopg", "tzdata") is NameAxis.UNRELATED

    def test_short_unrelated_is_unrelated_not_near(self) -> None:
        assert name_axis("agriculture-vlab", "avl") is NameAxis.UNRELATED

    def test_zero_distance_promotes_even_when_the_rule_based_classifier_disagrees(self) -> None:
        # `variant_distance`'s cross product of stripped variants is a wider
        # search than `classify_name_relation`'s convention check (which
        # never strips "-" out of a variant before comparing): stripping
        # "python-" from "python-foo-bar" and then dropping separators
        # lands exactly on "foobar", even though the rule-based classifier
        # alone calls the pair unrelated.
        assert classify_name_relation("python-foo-bar", "foobar") is NameClass.UNRELATED
        assert variant_distance("python-foo-bar", "foobar") == 0
        assert name_axis("python-foo-bar", "foobar") is NameAxis.SAME


# --------------------------------------------------------------------------
# `dominant_version_state` / `score_evidence`
# --------------------------------------------------------------------------


def _evidence(
    conda_name: str = "example",
    name_axis_value: NameAxis = NameAxis.SAME,
    n_versions_agree: int = 1,
    n_versions_no_signal: int = 0,
    n_versions_disagree: int = 0,
    vendored_only: bool = False,
    claimed_by_other: bool = False,
) -> CandidateEvidence:
    return CandidateEvidence(
        conda_name=conda_name,
        name_axis=name_axis_value,
        n_versions_agree=n_versions_agree,
        n_versions_no_signal=n_versions_no_signal,
        n_versions_disagree=n_versions_disagree,
        vendored_only=vendored_only,
        claimed_by_other=claimed_by_other,
    )


class TestDominantVersionState:
    def test_any_agreement_wins(self) -> None:
        counts = {VersionState.AGREES: 1, VersionState.NO_SIGNAL: 5, VersionState.DISAGREES: 5}
        assert dominant_version_state(counts) is VersionState.AGREES

    def test_disagreement_beats_no_signal(self) -> None:
        counts = {VersionState.NO_SIGNAL: 5, VersionState.DISAGREES: 1}
        assert dominant_version_state(counts) is VersionState.DISAGREES

    def test_all_no_signal(self) -> None:
        assert dominant_version_state({VersionState.NO_SIGNAL: 3}) is VersionState.NO_SIGNAL

    def test_no_evidence_at_all_is_no_signal(self) -> None:
        assert dominant_version_state({}) is VersionState.NO_SIGNAL


class TestVersionSortKey:
    def test_orders_by_release(self) -> None:
        assert version_sort_key("1.2.0") > version_sort_key("1.1.0")

    def test_epoch_outranks_release(self) -> None:
        # A naive `.release`-only comparison would put `1!0.1` (release
        # `(0, 1)`) before `2.0` (release `(2, 0)`) -- epoch must win first.
        assert version_sort_key("1!0.1") > version_sort_key("2.0")

    def test_unparseable_sorts_before_every_parseable_version(self) -> None:
        assert version_sort_key("not-a-version") < version_sort_key("0.0.1")

    def test_unparseable_versions_order_among_themselves_by_raw_string(self) -> None:
        assert version_sort_key("a-version") < version_sort_key("b-version")

    def test_pep440_equal_parseable_versions_are_equal(self) -> None:
        assert version_sort_key("1.0") == version_sort_key("1.0.0")

    def test_equal_unparseable_versions_are_equal(self) -> None:
        assert version_sort_key("not-a-version") == version_sort_key("not-a-version")

    def test_a_parseable_and_an_unparseable_version_are_never_equal(self) -> None:
        assert version_sort_key("1.0.0") != version_sort_key("not-a-version")

    def test_comparison_against_a_non_key_is_not_equal(self) -> None:
        assert version_sort_key("1.0.0") != "1.0.0"


class TestScoreEvidence:
    def test_best_case_is_base_probability(self) -> None:
        assert score_evidence(_evidence()) == BASE_PROBABILITY

    def test_name_axis_lowers_score_monotonically(self) -> None:
        same = score_evidence(_evidence(name_axis_value=NameAxis.SAME))
        near = score_evidence(_evidence(name_axis_value=NameAxis.NEAR))
        sub = score_evidence(_evidence(name_axis_value=NameAxis.SUBPACKAGE))
        unrelated = score_evidence(_evidence(name_axis_value=NameAxis.UNRELATED))
        assert same > near > sub > unrelated > 0.0

    def test_version_disagreement_lowers_score(self) -> None:
        agrees = score_evidence(_evidence(n_versions_agree=1, n_versions_disagree=0))
        disagrees = score_evidence(_evidence(n_versions_agree=0, n_versions_disagree=1))
        assert agrees > disagrees

    def test_no_signal_sits_between_agrees_and_disagrees(self) -> None:
        agrees = score_evidence(
            _evidence(n_versions_agree=1, n_versions_no_signal=0, n_versions_disagree=0)
        )
        no_signal = score_evidence(
            _evidence(n_versions_agree=0, n_versions_no_signal=1, n_versions_disagree=0)
        )
        disagrees = score_evidence(
            _evidence(n_versions_agree=0, n_versions_no_signal=0, n_versions_disagree=1)
        )
        assert agrees > no_signal > disagrees

    def test_one_contradiction_lowers_score(self) -> None:
        clean = score_evidence(_evidence(vendored_only=False, claimed_by_other=False))
        one = score_evidence(_evidence(vendored_only=True, claimed_by_other=False))
        assert clean > one

    def test_two_contradictions_lowers_score_further(self) -> None:
        one = score_evidence(_evidence(vendored_only=True, claimed_by_other=False))
        both = score_evidence(_evidence(vendored_only=True, claimed_by_other=True))
        assert one > both

    def test_result_is_a_valid_probability(self) -> None:
        for value in (
            score_evidence(_evidence()),
            score_evidence(
                _evidence(
                    name_axis_value=NameAxis.UNRELATED,
                    n_versions_agree=0,
                    n_versions_disagree=1,
                    vendored_only=True,
                    claimed_by_other=True,
                )
            ),
            score_evidence(_evidence(n_versions_agree=50, n_versions_disagree=30)),
        ):
            assert 0.0 <= value <= 1.0

    def test_mixed_evidence_scores_lower_than_clean_agreement_at_the_same_volume(
        self,
    ) -> None:
        # Same agreeing volume, but the second package also has substantial
        # contradicting evidence, so it must score lower.
        clean = score_evidence(_evidence(n_versions_agree=50, n_versions_disagree=0))
        mixed = score_evidence(_evidence(n_versions_agree=50, n_versions_disagree=30))
        assert clean > mixed

    def test_mixed_evidence_sits_strictly_between_the_pure_agree_and_disagree_scores(
        self,
    ) -> None:
        clean_agree = score_evidence(_evidence(n_versions_agree=1, n_versions_disagree=0))
        mixed = score_evidence(_evidence(n_versions_agree=5, n_versions_disagree=5))
        clean_disagree = score_evidence(_evidence(n_versions_agree=0, n_versions_disagree=1))
        assert clean_agree > mixed > clean_disagree

    def test_more_disagreement_lowers_the_mixed_score_further(self) -> None:
        few_disagree = score_evidence(_evidence(n_versions_agree=50, n_versions_disagree=1))
        many_disagree = score_evidence(_evidence(n_versions_agree=50, n_versions_disagree=30))
        assert few_disagree > many_disagree

    def test_more_agreeing_volume_raises_the_mixed_score(self) -> None:
        # 50 agreeing versions is stronger corroborating evidence than 1, even against
        # the same fixed number of disagreeing versions.
        low_volume = score_evidence(_evidence(n_versions_agree=1, n_versions_disagree=1))
        high_volume = score_evidence(_evidence(n_versions_agree=50, n_versions_disagree=1))
        assert high_volume > low_volume

    def test_a_single_disagreement_against_many_agreements_barely_moves_the_score(
        self,
    ) -> None:
        clean = score_evidence(_evidence(n_versions_agree=1000, n_versions_disagree=0))
        almost_clean = score_evidence(_evidence(n_versions_agree=1000, n_versions_disagree=1))
        assert clean > almost_clean
        assert clean - almost_clean < 0.001


# --------------------------------------------------------------------------
# `write_relations`: sqlite build pipeline
# --------------------------------------------------------------------------


def _row(
    conda_name: str,
    conda_filename: str,
    pypi_name: str,
    pypi_version: str,
) -> RelationRow:
    return {
        "conda_name": conda_name,
        "conda_filename": conda_filename,
        "pypi_name": pypi_name,
        "pypi_version": pypi_version,
    }


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()


def _mapping_row(
    connection: sqlite3.Connection, pypi_name: str, conda_name: str
) -> sqlite3.Row | None:
    connection.row_factory = sqlite3.Row
    return connection.execute(
        "SELECT * FROM pypi_conda_mapping WHERE pypi_name = ? AND conda_name = ?",
        (canonicalize_name(pypi_name), conda_name),
    ).fetchone()


class TestWriteRelationsSimpleSelfMapping:
    def test_self_mapping_agrees_and_is_not_contradicted(
        self, connection: sqlite3.Connection
    ) -> None:
        write_relations(
            connection,
            [_row("requests", "requests-2.31.0-pyhd8ed1ab_0.conda", "requests", "2.31.0")],
        )
        row = _mapping_row(connection, "requests", "requests")
        assert row is not None
        assert row["n_versions_agree"] == 1
        assert row["vendored_only"] == 0
        assert row["claimed_by_other"] == 0
        assert row["name_axis"] == NameAxis.SAME.value


class TestWriteRelationsVendoring:
    """The motivating case from parselmouth#82: `psycopg` vendors `tzdata`'s
    `.dist-info`, so one artifact contributes both a self-mapping relation
    and a vendored one.
    """

    @pytest.fixture(autouse=True)
    def _build(self, connection: sqlite3.Connection) -> None:
        write_relations(
            connection,
            [
                _row(
                    "psycopg",
                    "psycopg-3.0.10-py310h5588dad_0.conda",
                    "psycopg",
                    "3.0.10",
                ),
                _row(
                    "psycopg",
                    "psycopg-3.0.10-py310h5588dad_0.conda",
                    "tzdata",
                    "2021.5",
                ),
            ],
        )
        self.connection = connection

    def test_self_mapping_is_not_vendored(self) -> None:
        row = _mapping_row(self.connection, "psycopg", "psycopg")
        assert row is not None
        assert row["vendored_only"] == 0

    def test_vendored_relation_is_flagged(self) -> None:
        row = _mapping_row(self.connection, "tzdata", "psycopg")
        assert row is not None
        assert row["n_versions_agree"] == 0
        assert row["vendored_only"] == 1


class TestWriteRelationsVendoringAcrossBuilds:
    """Corroboration for `vendored_only` is keyed by `(conda_name,
    conda_version)`, not by which specific build reported it: a name-parsing
    slip on one build of a release can be recognized as vendoring even when
    a *different* build of the identical release is the one that agrees.
    """

    def test_corroboration_from_a_different_build_of_the_same_release_still_counts(
        self, connection: sqlite3.Connection
    ) -> None:
        write_relations(
            connection,
            [
                # One build's own dist-info correctly names "kwant".
                _row("kwant", "kwant-1.1.2-np112py27_0.tar.bz2", "kwant", "1.1.2"),
                # A *different* build of the same release has a garbled
                # pypi_name and a real, disagreeing version.
                _row(
                    "kwant",
                    "kwant-1.1.2-np111py27_0.tar.bz2",
                    "kwant-unknown-py2-7-macosx",
                    "10.6",
                ),
            ],
        )
        row = _mapping_row(connection, "kwant-unknown-py2-7-macosx", "kwant")
        assert row is not None
        assert row["n_versions_agree"] == 0
        assert row["vendored_only"] == 1


class TestWriteRelationsLegitimateVersionGap:
    """A recipe whose version legitimately differs from PyPI metadata (e.g.
    a feedstock a few releases behind) must NOT be flagged as vendored: the
    artifact has no version-agreeing sibling to be displaced *by*.
    """

    def test_lone_mismatched_relation_is_not_vendored(self, connection: sqlite3.Connection) -> None:
        write_relations(
            connection,
            [
                _row(
                    "dagster-postgres",
                    "dagster-postgres-1.0.13-pyhd8ed1ab_0.conda",
                    "dagster-postgres",
                    "0.16.13",
                )
            ],
        )
        row = _mapping_row(connection, "dagster-postgres", "dagster-postgres")
        assert row is not None
        assert row["n_versions_disagree"] == 1
        assert row["vendored_only"] == 0


class TestWriteRelationsClaimedByOther:
    def test_second_claimant_is_flagged(self, connection: sqlite3.Connection) -> None:
        write_relations(
            connection,
            [
                # `boto3-stubs` self-consistently claims itself for one build.
                _row(
                    "boto3-stubs",
                    "boto3-stubs-1.34.0-pyhd8ed1ab_0.conda",
                    "boto3-stubs",
                    "1.34.0",
                ),
                # A release `boto3-stubs`'s own feedstock skipped: the only
                # candidate is a `botocore-stubs` build vendoring an exact pin.
                _row(
                    "boto3-stubs",
                    "boto3-stubs-1.34.1-pyhd8ed1ab_0.conda",
                    "botocore-stubs",
                    "1.34.1",
                ),
            ],
        )
        row = _mapping_row(connection, "botocore-stubs", "boto3-stubs")
        assert row is not None
        assert row["claimed_by_other"] == 1

    def test_the_claimant_itself_is_not_flagged_against_itself(
        self, connection: sqlite3.Connection
    ) -> None:
        write_relations(
            connection,
            [
                _row(
                    "boto3-stubs",
                    "boto3-stubs-1.34.0-pyhd8ed1ab_0.conda",
                    "boto3-stubs",
                    "1.34.0",
                )
            ],
        )
        row = _mapping_row(connection, "boto3-stubs", "boto3-stubs")
        assert row is not None
        assert row["claimed_by_other"] == 0


class TestWriteRelationsFilenameMismatch:
    """parselmouth#83: a handful of rows carry a `conda_name` that disagrees
    with the name embedded in `conda_filename`. Such a row is dropped at
    ingestion -- never written to `pypi_claim` at all -- scoped to that one
    raw row, not to every row sharing its `(conda_name, conda_version)`.
    """

    def test_mismatched_row_is_never_written(self, connection: sqlite3.Connection) -> None:
        write_relations(
            connection,
            [
                _row(
                    "mock",
                    "conda-package-handling-1.3.0-py27_0.tar.bz2",
                    "mock",
                    "2.0.0",
                )
            ],
        )
        assert connection.execute("SELECT COUNT(*) FROM pypi_claim").fetchone()[0] == 0

    def test_mismatched_relation_is_excluded_from_candidates(
        self, connection: sqlite3.Connection
    ) -> None:
        write_relations(
            connection,
            [
                _row(
                    "mock",
                    "conda-package-handling-1.3.0-py27_0.tar.bz2",
                    "mock",
                    "2.0.0",
                )
            ],
        )
        assert _mapping_row(connection, "mock", "mock") is None

    def test_mismatched_row_does_not_poison_a_clean_row_at_the_same_conda_version(
        self, connection: sqlite3.Connection
    ) -> None:
        # The real parselmouth#83 shape: a corrupted row and a clean row can
        # share the exact same (conda_name, conda_version) -- the corrupted
        # one must still be dropped without affecting the clean one.
        write_relations(
            connection,
            [
                _row(
                    "mock",
                    "conda-package-handling-1.3.0-py27_0.tar.bz2",
                    "mock",
                    "2.0.0",
                ),
                _row("mock", "mock-1.3.0-py36_0.tar.bz2", "mock", "1.3.0"),
            ],
        )
        row = _mapping_row(connection, "mock", "mock")
        assert row is not None
        assert row["n_versions_agree"] == 1
        assert row["n_versions_disagree"] == 0


class TestWriteRelationsMappingIsRederivable:
    def test_can_be_rerun_without_new_data(self, connection: sqlite3.Connection) -> None:
        write_relations(
            connection,
            [_row("requests", "requests-2.31.0-pyhd8ed1ab_0.conda", "requests", "2.31.0")],
        )
        write_relations(connection, [])
        row = _mapping_row(connection, "requests", "requests")
        assert row is not None
        assert row["n_versions_agree"] == 1


class TestWriteRelationsVersionLevelAggregation:
    """`n_versions_*` count distinct `pypi_version`s, not raw claim rows: a
    version built for many platforms must not outweigh a version built for
    one.
    """

    def test_multiple_builds_of_one_version_count_as_one_version(
        self, connection: sqlite3.Connection
    ) -> None:
        write_relations(
            connection,
            [
                _row("foo", "foo-1.0.0-py38.conda", "foo", "1.0.0"),
                _row("foo", "foo-1.0.0-py39.conda", "foo", "1.0.0"),
            ],
        )
        row = _mapping_row(connection, "foo", "foo")
        assert row is not None
        assert row["n_versions"] == 1
        assert row["n_versions_agree"] == 1


# --------------------------------------------------------------------------
# `_mapper_from_connection`
# --------------------------------------------------------------------------


@pytest.fixture
def built_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    try:
        write_relations(
            conn,
            [
                _row(
                    "python-tzdata",
                    "python-tzdata-2021.5-pyhd8ed1ab_0.conda",
                    "tzdata",
                    "2021.5",
                ),
                _row(
                    "psycopg",
                    "psycopg-3.0.10-py310h5588dad_0.conda",
                    "psycopg",
                    "3.0.10",
                ),
                _row(
                    "psycopg",
                    "psycopg-3.0.10-py310h5588dad_0.conda",
                    "tzdata",
                    "2021.5",
                ),
            ],
        )
        yield conn
    finally:
        conn.close()


class TestParselmouthMapperHit:
    def test_all_surviving_candidates_are_emitted(
        self, built_connection: sqlite3.Connection
    ) -> None:
        mapper = _mapper_from_connection(built_connection)
        result = mapper(canonicalize_name("tzdata"), ())
        assert not isinstance(result, str)
        assert {c.conda_name for c in result} == {"python-tzdata", "psycopg"}

    def test_every_candidate_is_attributed_to_parselmouth(
        self, built_connection: sqlite3.Connection
    ) -> None:
        mapper = _mapper_from_connection(built_connection)
        result = mapper(canonicalize_name("tzdata"), ())
        assert not isinstance(result, str)
        assert all(c.source is CandidateSource.PARSELMOUTH for c in result)

    def test_hit_never_ends_the_chain(self, built_connection: sqlite3.Connection) -> None:
        mapper = _mapper_from_connection(built_connection)
        result = mapper(canonicalize_name("tzdata"), ())
        assert not isinstance(result, str)

    def test_hit_appends_after_earlier_candidates(
        self, built_connection: sqlite3.Connection
    ) -> None:
        mapper = _mapper_from_connection(built_connection)
        earlier = Candidate(
            conda_name="something-else",
            probability=0.5,
            source=CandidateSource.OTHER,
            mapper="test",
        )
        result = mapper(canonicalize_name("tzdata"), (earlier,))
        assert not isinstance(result, str)
        assert result[0] == earlier

    def test_self_mapping_scores_higher_than_the_vendored_one(
        self, built_connection: sqlite3.Connection
    ) -> None:
        mapper = _mapper_from_connection(built_connection)
        result = mapper(canonicalize_name("tzdata"), ())
        assert not isinstance(result, str)
        by_name = {c.conda_name: c.probability for c in result}
        assert by_name["python-tzdata"] > by_name["psycopg"]


class TestParselmouthMapperMiss:
    def test_miss_returns_candidates_unchanged(self, built_connection: sqlite3.Connection) -> None:
        mapper = _mapper_from_connection(built_connection)
        earlier = (
            Candidate(
                conda_name="whatever",
                probability=0.5,
                source=CandidateSource.OTHER,
                mapper="test",
            ),
        )
        assert mapper(canonicalize_name("nonexistent"), earlier) is earlier

    def test_miss_on_empty_candidates(self, built_connection: sqlite3.Connection) -> None:
        mapper = _mapper_from_connection(built_connection)
        assert mapper(canonicalize_name("nonexistent"), ()) == ()


# --------------------------------------------------------------------------
# Ingest: `iter_relations` / `download_relations` / `build_parselmouth_database`
# --------------------------------------------------------------------------


def _write_gzip_jsonl(path: Path, rows: list[dict[str, str | None]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class TestIterRelations:
    def test_yields_only_the_fields_the_mapper_needs(self, tmp_path: Path) -> None:
        path = tmp_path / "relations.jsonl.gz"
        _write_gzip_jsonl(
            path,
            [
                {
                    "conda_name": "requests",
                    "conda_filename": "requests-2.31.0-pyhd8ed1ab_0.conda",
                    "conda_hash": "hash1",
                    "pypi_name": "requests",
                    "pypi_version": "2.31.0",
                    "channel": "conda-forge",
                    "direct_url": None,
                }
            ],
        )
        (row,) = list(iter_relations(path))
        assert row == {
            "conda_name": "requests",
            "conda_filename": "requests-2.31.0-pyhd8ed1ab_0.conda",
            "pypi_name": "requests",
            "pypi_version": "2.31.0",
        }

    def test_multiple_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "relations.jsonl.gz"
        _write_gzip_jsonl(
            path,
            [
                {
                    "conda_name": "requests",
                    "conda_filename": "requests-2.31.0-pyhd8ed1ab_0.conda",
                    "pypi_name": "requests",
                    "pypi_version": "2.31.0",
                },
                {
                    "conda_name": "urllib3",
                    "conda_filename": "urllib3-2.0.0-pyhd8ed1ab_0.conda",
                    "pypi_name": "urllib3",
                    "pypi_version": "2.0.0",
                },
            ],
        )
        assert len(list(iter_relations(path))) == 2


class _FakeHTTPResponse:
    def __init__(self, payload: bytes, etag: str | None) -> None:
        self._remaining = payload
        self.headers: dict[str, str] = {} if etag is None else {"ETag": etag}

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        chunk = self._remaining if size < 0 else self._remaining[:size]
        self._remaining = self._remaining[len(chunk) :]
        return chunk


def _not_modified() -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.test", 304, "Not Modified", Message(), None)


def _install_fake_urlopen(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[_FakeHTTPResponse | urllib.error.HTTPError]
) -> list[str | None]:
    """Monkeypatch `urllib.request.urlopen` to return/raise each of
    `outcomes` in order. Returns the list of `If-None-Match` headers each
    call actually sent (`None` when the call sent none).
    """
    remaining = list(outcomes)
    sent_if_none_match: list[str | None] = []

    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeHTTPResponse:
        sent_if_none_match.append(request.headers.get("If-none-match"))
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return sent_if_none_match


class TestDownloadRelations:
    def test_first_download_sends_no_conditional_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dest = tmp_path / "relations.jsonl.gz"
        sent = _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(b"hello", "etag-1")])
        result = download_relations("https://example.test/relations.jsonl.gz", dest=dest)
        assert sent == [None]
        assert result.changed is True
        assert result.etag == "etag-1"
        assert dest.read_bytes() == b"hello"

    def test_conditional_header_is_sent_when_an_etag_is_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dest = tmp_path / "relations.jsonl.gz"
        sent = _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(b"hello", "etag-2")])
        download_relations("https://example.test/relations.jsonl.gz", dest=dest, etag="etag-1")
        assert sent == ["etag-1"]

    def test_unchanged_upstream_returns_304_and_leaves_dest_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dest = tmp_path / "relations.jsonl.gz"
        _install_fake_urlopen(monkeypatch, [_not_modified()])
        result = download_relations(
            "https://example.test/relations.jsonl.gz", dest=dest, etag="etag-1"
        )
        assert result.changed is False
        assert result.etag == "etag-1"
        assert not dest.exists()

    def test_other_http_errors_propagate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dest = tmp_path / "relations.jsonl.gz"
        not_found = urllib.error.HTTPError(
            "https://example.test", 404, "Not Found", Message(), None
        )
        _install_fake_urlopen(monkeypatch, [not_found])
        with pytest.raises(urllib.error.HTTPError):
            download_relations("https://example.test/relations.jsonl.gz", dest=dest, etag="etag-1")

    def test_missing_etag_header_on_response_is_reported_as_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dest = tmp_path / "relations.jsonl.gz"
        _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(b"hello", None)])
        result = download_relations("https://example.test/relations.jsonl.gz", dest=dest)
        assert result.etag is None


def _gzip_line(row: dict[str, str]) -> bytes:
    """One `relations.jsonl.gz` payload containing a single JSON line."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as handle:
        handle.write((json.dumps(row) + "\n").encode("utf-8"))
    return buffer.getvalue()


_REQUESTS_ROW = {
    "conda_name": "requests",
    "conda_filename": "requests-2.31.0-pyhd8ed1ab_0.conda",
    "pypi_name": "requests",
    "pypi_version": "2.31.0",
}
_URLLIB3_ROW = {
    "conda_name": "urllib3",
    "conda_filename": "urllib3-2.0.0-pyhd8ed1ab_0.conda",
    "pypi_name": "urllib3",
    "pypi_version": "2.0.0",
}


class TestOpenParselmouthDatabase:
    def test_first_call_builds_a_fresh_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(_gzip_line(_REQUESTS_ROW), "etag-1")])
        db_path = tmp_path / "parselmouth.sqlite3"

        connection = open_parselmouth_database(db_path)
        try:
            mapper = _mapper_from_connection(connection)
            result = mapper(canonicalize_name("requests"), ())
            assert not isinstance(result, str)
            assert result[0].conda_name == "requests"
        finally:
            connection.close()

    def test_repeat_call_with_no_upstream_change_does_not_rebuild(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = tmp_path / "parselmouth.sqlite3"

        _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(_gzip_line(_REQUESTS_ROW), "etag-1")])
        open_parselmouth_database(db_path).close()
        built_at = db_path.stat().st_mtime_ns

        sent = _install_fake_urlopen(monkeypatch, [_not_modified()])
        connection = open_parselmouth_database(db_path)
        try:
            assert sent == ["etag-1"]
            assert db_path.stat().st_mtime_ns == built_at
            mapper = _mapper_from_connection(connection)
            result = mapper(canonicalize_name("requests"), ())
            assert not isinstance(result, str)
            assert result[0].conda_name == "requests"
        finally:
            connection.close()

    def test_upstream_change_triggers_a_rebuild(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = tmp_path / "parselmouth.sqlite3"

        _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(_gzip_line(_REQUESTS_ROW), "etag-1")])
        open_parselmouth_database(db_path).close()

        sent = _install_fake_urlopen(
            monkeypatch, [_FakeHTTPResponse(_gzip_line(_URLLIB3_ROW), "etag-2")]
        )
        connection = open_parselmouth_database(db_path)
        try:
            assert sent == ["etag-1"]
            mapper = _mapper_from_connection(connection)
            old = mapper(canonicalize_name("requests"), ())
            new = mapper(canonicalize_name("urllib3"), ())
            assert old == ()
            assert not isinstance(new, str)
            assert new[0].conda_name == "urllib3"
        finally:
            connection.close()

    def test_a_connection_opened_before_the_swap_keeps_working(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = tmp_path / "parselmouth.sqlite3"

        _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(_gzip_line(_REQUESTS_ROW), "etag-1")])
        old_connection = open_parselmouth_database(db_path)

        _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(_gzip_line(_URLLIB3_ROW), "etag-2")])
        new_connection = open_parselmouth_database(db_path)
        try:
            old_mapper = _mapper_from_connection(old_connection)
            old_result = old_mapper(canonicalize_name("requests"), ())
            assert not isinstance(old_result, str)
            assert old_result[0].conda_name == "requests"
        finally:
            old_connection.close()
            new_connection.close()

    def test_a_failed_rebuild_leaves_the_existing_database_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = tmp_path / "parselmouth.sqlite3"
        _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(_gzip_line(_REQUESTS_ROW), "etag-1")])
        open_parselmouth_database(db_path).close()

        broken_row = {**_REQUESTS_ROW, "conda_filename": "requests-2.31.0-pyhd8ed1ab_0.whl"}
        _install_fake_urlopen(
            monkeypatch, [_FakeHTTPResponse(_gzip_line(broken_row), "etag-broken")]
        )
        with pytest.raises(ValueError, match="conda archive filename"):
            open_parselmouth_database(db_path)

        assert set(tmp_path.iterdir()) == {db_path}
        connection = sqlite3.connect(db_path)
        try:
            mapper = _mapper_from_connection(connection)
            result = mapper(canonicalize_name("requests"), ())
            assert not isinstance(result, str)
            assert result[0].conda_name == "requests"
        finally:
            connection.close()

    def test_a_failure_during_the_final_swap_leaves_no_staged_file_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = tmp_path / "parselmouth.sqlite3"
        _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(_gzip_line(_REQUESTS_ROW), "etag-1")])

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("reroll.parselmouth_mapper.db.shutil.copy2", _boom)

        with pytest.raises(OSError, match="disk full"):
            open_parselmouth_database(db_path)

        assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# `parselmouth_mapper` (zero arguments, default cache path)
# --------------------------------------------------------------------------


class TestDefaultParselmouthMapper:
    def test_builds_a_mapper_at_the_default_cache_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _install_fake_urlopen(monkeypatch, [_FakeHTTPResponse(_gzip_line(_REQUESTS_ROW), "etag-1")])

        mapper = parselmouth_mapper()
        result = mapper(canonicalize_name("requests"), ())

        assert (tmp_path / ".cache" / "reroll" / "parselmouth.sqlite3").exists()
        assert not isinstance(result, str)
        assert result[0].conda_name == "requests"

        # `parselmouth_mapper` deliberately never exposes its connection for
        # closing (see its docstring): collecting it here, rather than
        # leaving the timing to whenever the garbage collector next runs,
        # keeps the resulting `ResourceWarning` contained to this test.
        del mapper
        with pytest.warns(ResourceWarning, match="unclosed database"):
            gc.collect()


# --------------------------------------------------------------------------
# Against the real published data (network)
# --------------------------------------------------------------------------


class TestParselmouthAgainstRealData:
    @pytest.mark.network
    def test_default_url_is_a_readable_relations_table(self, tmp_path: Path) -> None:
        dest = tmp_path / "relations.jsonl.gz"
        first = download_relations(DEFAULT_RELATIONS_URL, dest=dest)
        assert first.changed is True
        assert first.etag is not None
        first_rows = []
        for row in iter_relations(dest):
            first_rows.append(row)
            if len(first_rows) >= 5:
                break
        assert len(first_rows) == 5
        for row in first_rows:
            assert row.keys() == {
                "conda_name",
                "conda_filename",
                "pypi_name",
                "pypi_version",
            }
            parse_conda_filename(row["conda_filename"])

        # The endpoint actually honors `If-None-Match` the way
        # `open_parselmouth_database` assumes -- not just a fake in
        # `TestOpenParselmouthDatabase`. Reuses the ETag from the download
        # above instead of downloading the ~90MB table a second time.
        second = download_relations(DEFAULT_RELATIONS_URL, dest=dest, etag=first.etag)
        assert second.changed is False
        assert second.etag == first.etag
