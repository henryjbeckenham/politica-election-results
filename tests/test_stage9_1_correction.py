import hashlib
import json
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import duckdb

from politica_erd.app.config import AppSettings
from politica_erd.app.service import JobService
from politica_erd.correct_tcp_measures import (
    EXPECTED_VOTE_SHARE_ROWS,
    apply_tcp_measure_correction,
    assess_tcp_measure_semantics,
    correct_active_release,
)
from politica_erd.ids import fact_id
from politica_erd.tcp_measures import (
    TcpReportedPercentage,
    classify_tcp_reported_percentages,
)
from politica_erd.validate import validate_database
from tests.test_stage4_workflow import add_all_official_house_candidates, make_minimal_database
from tests.test_stage5_workflow import stage_files


TCP_FILENAME = "HouseTcpByCandidateByVoteTypeDownload-31496.csv"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_tcp_database(root: Path) -> Path:
    base = root / "base.duckdb"
    make_minimal_database(base, official_event_id="31496")
    add_all_official_house_candidates(base)
    service = JobService(
        AppSettings(project_root=Path(__file__).resolve().parents[1], base_database=base, app_data=root / "build-app")
    )
    inspected = stage_files(service, (TCP_FILENAME,))
    service.queue_execution(inspected["job_id"])
    completed = service.execute_job(inspected["job_id"])
    if completed["state"] != "validated":
        raise AssertionError(completed)
    database = service.store.job_dir(inspected["job_id"]) / "work/database.duckdb"

    connection = duckdb.connect(str(database))
    try:
        rows = connection.execute(
            """SELECT vote_result_id, election_id, contest_id,
                      election_reporting_unit_id, subject_type, candidacy_id,
                      source_revision_id, source_locator, import_run_id
               FROM results.vote_result
               WHERE result_type='tcp' AND vote_type='total'
                 AND measure_type='vote_share' AND record_status='active'
               ORDER BY contest_id, candidacy_id"""
        ).fetchall()
        if len(rows) != EXPECTED_VOTE_SHARE_ROWS:
            raise AssertionError(f"Expected 34 synthetic legacy rows; found {len(rows)}")
        connection.execute("BEGIN TRANSACTION")
        for row in rows:
            (
                current_id,
                election_id,
                contest_id,
                reporting_unit_id,
                subject_type,
                candidacy_id,
                revision_id,
                locator,
                import_run_id,
            ) = row
            old_id = fact_id(
                "vote_result",
                (
                    election_id,
                    contest_id,
                    reporting_unit_id,
                    subject_type,
                    candidacy_id,
                    "tcp",
                    "total",
                    "swing",
                ),
                revision_id,
            )
            source_hash = connection.execute(
                """SELECT source_row_hash FROM provenance.row_lineage
                   WHERE target_schema='results' AND target_table='vote_result'
                     AND target_record_id=?""",
                [str(current_id)],
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM provenance.row_lineage WHERE target_schema='results' AND target_table='vote_result' AND target_record_id=?",
                [str(current_id)],
            )
            connection.execute(
                "UPDATE results.vote_result SET vote_result_id=?, measure_type='swing' WHERE vote_result_id=?",
                [old_id, current_id],
            )
            lineage_id = fact_id(
                "row_lineage",
                ["results", "vote_result", str(old_id), locator],
                revision_id,
            )
            connection.execute(
                "INSERT INTO provenance.row_lineage VALUES (?, 'results', 'vote_result', ?, ?, ?, ?, NULL, ?)",
                [lineage_id, str(old_id), revision_id, locator, import_run_id, source_hash],
            )
        connection.execute("COMMIT")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return database


class Stage91CorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_directory = tempfile.TemporaryDirectory()
        cls.fixture_root = Path(cls.fixture_directory.name)
        cls.legacy_database = _legacy_tcp_database(cls.fixture_root)

    @classmethod
    def tearDownClass(cls):
        cls.fixture_directory.cleanup()

    def test_pair_classifier_distinguishes_shares_swings_and_ambiguity(self):
        self.assertEqual(
            classify_tcp_reported_percentages(
                (
                    TcpReportedPercentage(Decimal("56.19"), 57916),
                    TcpReportedPercentage(Decimal("43.81"), 45147),
                ),
                context="Farrer",
            ),
            "vote_share",
        )
        self.assertEqual(
            classify_tcp_reported_percentages(
                (
                    TcpReportedPercentage(Decimal("7.16"), 60000),
                    TcpReportedPercentage(Decimal("-7.16"), 40000),
                ),
                context="Adelaide",
            ),
            "swing",
        )
        with self.assertRaisesRegex(ValueError, "ambiguous TCP percentage pair"):
            classify_tcp_reported_percentages(
                (
                    TcpReportedPercentage(Decimal("12.3"), 600),
                    TcpReportedPercentage(Decimal("8.4"), 400),
                ),
                context="Invalid",
            )

    def test_existing_2025_rows_are_corrected_with_history_and_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "candidate.duckdb"
            shutil.copy2(self.legacy_database, database)
            before_sha = _sha256(database)
            before_validation = validate_database(database)
            self.assertTrue(
                any(
                    failure.get("check") == "tcp_percentage_semantics"
                    for failure in before_validation["failures"]
                )
            )
            connection = duckdb.connect(str(database))
            try:
                before = assess_tcp_measure_semantics(connection)
                self.assertEqual(before["mismatch_count"], 34)
                report = apply_tcp_measure_correction(
                    connection,
                    base_database_sha256=before_sha,
                    release_id="release_test_stage9_1",
                )
                self.assertEqual(report["status"], "CORRECTED")
                self.assertEqual(report["superseded_observations"], 34)
                self.assertEqual(report["inserted_observations"], 34)
                self.assertEqual(report["active_vote_result_count_before"], report["active_vote_result_count_after"])
                self.assertEqual(
                    report["historical_vote_result_count_after"],
                    report["historical_vote_result_count_before"] + 34,
                )
                after = assess_tcp_measure_semantics(connection)
                self.assertEqual(after["mismatch_count"], 0)
                self.assertEqual(
                    connection.execute(
                        """SELECT measure_type, count(*) FROM results.vote_result
                           WHERE result_type='tcp' AND vote_type='total'
                             AND measure_type IN ('swing', 'vote_share')
                             AND record_status='active'
                           GROUP BY measure_type ORDER BY measure_type"""
                    ).fetchall(),
                    [("swing", 266), ("vote_share", 34)],
                )
                farrer = connection.execute(
                    """SELECT result.decimal_value, result.measure_type
                       FROM results.vote_result result
                       JOIN core.contest contest USING (contest_id)
                       WHERE contest.contest_name='Farrer' AND result.result_type='tcp'
                         AND result.vote_type='total' AND result.measure_type='vote_share'
                         AND result.record_status='active'
                       ORDER BY result.decimal_value DESC"""
                ).fetchall()
                self.assertEqual(farrer, [(Decimal("56.190000000000"), "vote_share"), (Decimal("43.810000000000"), "vote_share")])
                second = apply_tcp_measure_correction(
                    connection,
                    base_database_sha256=_sha256(database),
                    release_id="release_test_stage9_1_second",
                )
                self.assertEqual(second["status"], "NO_CHANGE")
            finally:
                connection.close()
            after_validation = validate_database(database)
            self.assertFalse(
                any(
                    failure.get("check") == "tcp_percentage_semantics"
                    for failure in after_validation["failures"]
                )
            )

    def test_publication_preserves_base_and_activates_validated_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            shutil.copy2(self.legacy_database, base)
            base_sha = _sha256(base)
            settings = AppSettings(
                project_root=root,
                base_database=base,
                app_data=root / "app",
            )
            validation = {
                "status": "PASS",
                "stage": "stage_9_1_test",
                "failures": [],
            }
            with patch(
                "politica_erd.correct_tcp_measures.validate_database",
                return_value=validation,
            ):
                publication = correct_active_release(settings)
            self.assertEqual(publication["status"], "CORRECTED_AND_ACTIVATED")
            self.assertEqual(_sha256(base), base_sha)
            pointer = json.loads((settings.releases_root / "active.json").read_text())
            self.assertEqual(pointer["release_id"], publication["release_id"])
            governed = JobService(settings).governed_database()
            self.assertNotEqual(governed.resolve(), base.resolve())
            connection = duckdb.connect(str(governed), read_only=True)
            try:
                self.assertEqual(assess_tcp_measure_semantics(connection)["mismatch_count"], 0)
            finally:
                connection.close()
            self.assertTrue(
                (
                    governed.parents[2]
                    / "data/manifests/tcp_measure_correction_0_9_1.json"
                ).is_file()
            )
            second = correct_active_release(settings)
            self.assertEqual(second["status"], "NO_CHANGE")


if __name__ == "__main__":
    unittest.main()
