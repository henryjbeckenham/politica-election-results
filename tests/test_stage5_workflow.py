import csv
import hashlib
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from politica_erd.app.config import AppSettings
from politica_erd.app.detection import AdapterCatalogue
from politica_erd.app.service import JobService
from politica_erd.app.transformers import get_transformer, transformer_catalogue
from politica_erd.build import PROJECT_ROOT

from tests.test_stage4_workflow import (
    ELECTION_ID,
    add_all_official_house_candidates,
    make_minimal_database,
)


OFFICIAL_ROOT = PROJECT_ROOT / "data/raw/aec/2025_federal/31496/final"
STAGE5_FILENAMES = (
    "HouseTcpByCandidateByVoteTypeDownload-31496.csv",
    "HouseTppByDivisionDownload-31496.csv",
    "HouseMembersElectedDownload-31496.csv",
    "GeneralEnrolmentByDivisionDownload-31496.csv",
    "HouseInformalByDivisionDownload-31496.csv",
    "HouseTurnoutByDivisionDownload-31496.csv",
    "HouseVotesCountedByDivisionDownload-31496.csv",
)
EXPECTED_SHA256 = {
    "HouseTcpByCandidateByVoteTypeDownload-31496.csv": "870d7d326df7f59cf3c0638045eb0a42722f39b2b40b1b7e475d31a15bd221b7",
    "HouseTppByDivisionDownload-31496.csv": "6f48350be330f9d3424137f94b8f936dee47840f1eb2731606ce5ce673ebdde4",
    "HouseMembersElectedDownload-31496.csv": "2bb8da54d136fcb93b2b775d59f0d234998583966e00d35645f4df8e4d38b32c",
    "GeneralEnrolmentByDivisionDownload-31496.csv": "19bc25f89362e14a910b2ed4857459b5daa63c3acba8582a0b9c132fe491c4f3",
    "HouseInformalByDivisionDownload-31496.csv": "52d6201910a2401fbbf3ecb3f87c089aaff268b19ed9f5b463489fcf322078de",
    "HouseTurnoutByDivisionDownload-31496.csv": "37ae6598a2b4a54f6a8ad83ee787a1842d73ce82b9a405db3d00d765c34c1526",
    "HouseVotesCountedByDivisionDownload-31496.csv": "9a622f83749ef03b0d78171c1d8e93e0e0cd26c4d7dc1a42b2cdfc792f27c612",
}


def add_tpp_parties(path: Path) -> None:
    connection = duckdb.connect(str(path))
    try:
        for party_id, name, abbreviation in (
            ("party_alp", "Australian Labor Party", "ALP"),
            ("party_coalition", "Liberal/National Coalition", "L/NP"),
        ):
            connection.execute(
                "INSERT OR IGNORE INTO sync.party VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    party_id,
                    name,
                    name,
                    abbreviation,
                    None,
                    None,
                    None,
                    "Federal",
                    "Australia",
                    True,
                    None,
                    None,
                    "active",
                    "audited",
                    hashlib.sha256(party_id.encode()).hexdigest(),
                    datetime.now(timezone.utc),
                ],
            )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


def stage_files(service: JobService, filenames: tuple[str, ...]) -> dict:
    job = service.begin_job(
        name="Stage 5 official House summaries",
        authority_id="authority_aec",
        election_id=ELECTION_ID,
    )
    uploads = []
    for index, filename in enumerate(filenames, start=1):
        source = OFFICIAL_ROOT / filename
        destination = service.store.job_dir(job["job_id"]) / "uploads" / filename
        shutil.copy2(source, destination)
        payload = destination.read_bytes()
        uploads.append(
            {
                "upload_id": f"official-stage5-{index}",
                "original_name": filename,
                "stored_name": filename,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "content_type": "text/csv",
            }
        )
    inspected = service.finalise_uploads(job["job_id"], uploads)
    return inspected


def write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> bytes:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return path.read_bytes()


def execute_synthetic(
    *,
    base: Path,
    app_data: Path,
    filename: str,
    headers: list[str],
    rows: list[list[object]],
) -> tuple[dict, Path, bytes]:
    service = JobService(
        AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=app_data)
    )
    job = service.begin_job(
        name=filename,
        authority_id="authority_aec",
        election_id=ELECTION_ID,
    )
    upload = service.store.job_dir(job["job_id"]) / "uploads" / filename
    payload = write_csv(upload, headers, rows)
    inspected = service.finalise_uploads(
        job["job_id"],
        [
            {
                "upload_id": "synthetic-source",
                "original_name": filename,
                "stored_name": filename,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "content_type": "text/csv",
            }
        ],
    )
    assert inspected["datasets"][0]["detection"]["canonical_capable"]
    service.queue_execution(job["job_id"])
    completed = service.execute_job(job["job_id"])
    return completed, service.store.job_dir(job["job_id"]) / "work/database.duckdb", payload


class Stage5WorkflowTests(unittest.TestCase):
    def test_stage5_transformers_are_registered_and_generic_event_names_detect(self):
        expected = {
            "house_first_preferences_by_vote_type",
            "house_tcp_by_vote_type",
            "house_tpp_division",
            "house_elected",
            "enrolment_division",
            "house_participation",
        }
        self.assertTrue(
            expected.issubset(
                {item["dataset_key"] for item in transformer_catalogue()}
            )
        )
        for dataset_key in expected:
            self.assertIsNotNone(get_transformer("adapter_aec_2025_v1", dataset_key))

        catalogue = AdapterCatalogue(PROJECT_ROOT / "config/adapters")
        cases = {
            "HouseTcpByCandidateByVoteTypeDownload-99999.csv": [
                "DivisionID", "CandidateID", "OrdinaryVotes", "AbsentVotes",
                "ProvisionalVotes", "PrePollVotes", "PostalVotes", "TotalVotes",
            ],
            "HouseTppByDivisionDownload-99999.csv": [
                "DivisionID", "StateAb", "PartyAb", "Australian Labor Party Votes",
                "Australian Labor Party Percentage", "Liberal/National Coalition Votes",
                "Liberal/National Coalition Percentage", "TotalVotes", "Swing",
            ],
            "HouseMembersElectedDownload-99999.csv": [
                "DivisionID", "DivisionNm", "CandidateID", "GivenNm", "Surname", "PartyNm",
            ],
            "GeneralEnrolmentByDivisionDownload-99999.csv": ["DivisionID", "Enrolment"],
            "HouseInformalByDivisionDownload-99999.csv": ["DivisionID"],
            "HouseTurnoutByDivisionDownload-99999.csv": ["DivisionID"],
            "HouseVotesCountedByDivisionDownload-99999.csv": ["DivisionID"],
        }
        for filename, headers in cases.items():
            detected = catalogue.detect(filename, headers, "authority_aec")
            self.assertEqual(detected["status"], "matched", filename)
            self.assertEqual(detected["selection"]["mapping_entities"], [], filename)

    def test_official_stage5_sources_match_inventory_checksums(self):
        for filename, expected in EXPECTED_SHA256.items():
            self.assertEqual(
                hashlib.sha256((OFFICIAL_ROOT / filename).read_bytes()).hexdigest(),
                expected,
                filename,
            )

    def test_all_seven_complete_official_files_use_canonical_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base, official_event_id="31496")
            add_all_official_house_candidates(base)
            add_tpp_parties(base)
            service = JobService(
                AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "app")
            )
            inspected = stage_files(service, STAGE5_FILENAMES)
            self.assertEqual(inspected["state"], "ready")
            self.assertEqual(len(inspected["datasets"]), 7)
            self.assertTrue(
                all(item["detection"]["canonical_capable"] for item in inspected["datasets"])
            )
            service.queue_execution(inspected["job_id"])
            completed = service.execute_job(inspected["job_id"])
            self.assertEqual(completed["state"], "validated")
            results = completed["execution"]["dataset_results"]
            self.assertEqual(sum(item["staged_rows"] for item in results.values()), 1200)
            self.assertEqual(sum(item["inserted_rows"] for item in results.values()), 4950)
            database = service.store.job_dir(inspected["job_id"]) / "work/database.duckdb"
            connection = duckdb.connect(str(database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM results.vote_result WHERE record_status='active'"
                    ).fetchone()[0],
                    2850,
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT measure_type, count(*) FROM results.vote_result
                           WHERE record_status='active' AND result_type='tcp'
                             AND vote_type='total' AND measure_type IN ('swing', 'vote_share')
                           GROUP BY measure_type ORDER BY measure_type"""
                    ).fetchall(),
                    [("swing", 266), ("vote_share", 34)],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM results.participation_result WHERE record_status='active'"
                    ).fetchone()[0],
                    1800,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM results.contest_outcome WHERE record_status='active'"
                    ).fetchone()[0],
                    150,
                )
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM results.elected_member").fetchone()[0],
                    150,
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT count(*) FROM provenance.row_lineage
                           WHERE target_table IN ('vote_result', 'participation_result',
                                                  'contest_outcome', 'elected_member')"""
                    ).fetchone()[0],
                    4950,
                )
            finally:
                connection.close()

    def test_tcp_requires_exactly_two_candidates_in_every_contest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base, official_event_id="99999")
            headers = [
                "StateAb", "DivisionID", "DivisionNm", "CandidateID", "Surname", "GivenNm",
                "OrdinaryVotes", "AbsentVotes", "ProvisionalVotes", "PrePollVotes", "PostalVotes",
                "TotalVotes", "Swing",
            ]
            with self.assertRaisesRegex(ValueError, "exactly two TCP candidates"):
                execute_synthetic(
                    base=base,
                    app_data=root / "app",
                    filename="HouseTcpByCandidateByVoteTypeDownload-99999.csv",
                    headers=headers,
                    rows=[["NSW", "101", "Test Division", "501", "Candidate", "Test", 100, 10, 2, 20, 8, 140, 50]],
                )

    def test_participation_revision_supersedes_prior_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base, official_event_id="99999")
            filename = "HouseTurnoutByDivisionDownload-99999.csv"
            headers = [
                "DivisionID", "DivisionNm", "StateAb", "Enrolment", "Turnout",
                "TurnoutPercentage", "TurnoutSwing",
            ]
            first, first_database, _ = execute_synthetic(
                base=base,
                app_data=root / "first-app",
                filename=filename,
                headers=headers,
                rows=[["101", "Test Division", "NSW", 200, 150, 75, 0]],
            )
            self.assertEqual(first["state"], "validated")
            second, second_database, _ = execute_synthetic(
                base=first_database,
                app_data=root / "second-app",
                filename=filename,
                headers=headers,
                rows=[["101", "Test Division", "NSW", 200, 152, 76, 1]],
            )
            self.assertEqual(second["state"], "validated")
            connection = duckdb.connect(str(second_database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        """SELECT record_status, count(*) FROM results.participation_result
                           GROUP BY record_status ORDER BY record_status"""
                    ).fetchall(),
                    [("active", 2), ("superseded", 2)],
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT integer_value FROM results.participation_result
                           WHERE record_status='active' AND measure_type='turnout'"""
                    ).fetchone()[0],
                    152,
                )
            finally:
                connection.close()

    def test_stage5_event_number_must_match_selected_election(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base, official_event_id="99999")
            with self.assertRaisesRegex(ValueError, "does not match the selected election"):
                execute_synthetic(
                    base=base,
                    app_data=root / "app",
                    filename="HouseTurnoutByDivisionDownload-88888.csv",
                    headers=[
                        "DivisionID", "DivisionNm", "StateAb", "Enrolment", "Turnout",
                        "TurnoutPercentage", "TurnoutSwing",
                    ],
                    rows=[["101", "Test Division", "NSW", 200, 150, 75, 0]],
                )

    def test_elected_outcome_revision_preserves_old_outcome_not_old_current_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base, official_event_id="99999")
            filename = "HouseMembersElectedDownload-99999.csv"
            headers = [
                "DivisionID", "DivisionNm", "StateAb", "CandidateID", "GivenNm", "Surname",
                "PartyNm", "PartyAb",
            ]
            first, first_database, _ = execute_synthetic(
                base=base,
                app_data=root / "first-app",
                filename=filename,
                headers=headers,
                rows=[["101", "Test Division", "NSW", "501", "Test", "Candidate", "Test Party", "TST"]],
            )
            self.assertEqual(first["state"], "validated")
            second, second_database, _ = execute_synthetic(
                base=first_database,
                app_data=root / "second-app",
                filename=filename,
                headers=headers,
                rows=[["101", "Test Division", "NSW", "501", "Test", "Candidate", "Test Party revised label", "TST"]],
            )
            self.assertEqual(second["state"], "validated")
            connection = duckdb.connect(str(second_database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        """SELECT record_status, count(*) FROM results.contest_outcome
                           GROUP BY record_status ORDER BY record_status"""
                    ).fetchall(),
                    [("active", 1), ("superseded", 1)],
                )
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM results.elected_member").fetchone()[0],
                    1,
                )
            finally:
                connection.close()

    def test_invalid_participation_reconciliation_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base, official_event_id="99999")
            with self.assertRaisesRegex(ValueError, "does not equal TotalVotes"):
                execute_synthetic(
                    base=base,
                    app_data=root / "app",
                    filename="HouseInformalByDivisionDownload-99999.csv",
                    headers=[
                        "DivisionID", "DivisionNm", "StateAb", "FormalVotes", "InformalVotes",
                        "TotalVotes", "InformalPercent", "InformalSwing",
                    ],
                    rows=[["101", "Test Division", "NSW", 100, 10, 999, 9.09, 0]],
                )


if __name__ == "__main__":
    unittest.main()
