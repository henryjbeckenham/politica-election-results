import csv
import hashlib
import json
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from politica_erd.app.config import AppSettings
from politica_erd.app.detection import AdapterCatalogue
from politica_erd.app.service import InvalidJobStateError, JobService
from politica_erd.app.transformers import get_transformer, transformer_catalogue
from politica_erd.build import PROJECT_ROOT, apply_migrations
from politica_erd.ids import candidacy_id
from politica_erd.validate import resolve_default_database_path, validate_database


ELECTION_ID = "election_fed_2025_05_03_general"
CHAMBER_ID = "election_chamber_test_2026_house"
CONTEST_ID = "contest_test_2026_house_101"
CANDIDACY_ID = uuid.UUID("00000000-0000-5000-8000-000000000101")
HEADERS = [
    "StateAb",
    "DivisionID",
    "DivisionNm",
    "PartyAb",
    "PartyNm",
    "CandidateID",
    "Surname",
    "GivenNm",
    "OrdinaryVotes",
    "AbsentVotes",
    "ProvisionalVotes",
    "PrePollVotes",
    "PostalVotes",
    "TotalVotes",
    "Swing",
]


def make_minimal_database(path: Path, *, official_event_id: str = "99999") -> None:
    connection = duckdb.connect(str(path))
    now = datetime.now(timezone.utc)
    try:
        apply_migrations(connection, PROJECT_ROOT)
        connection.execute(
            "INSERT INTO control.schema_version VALUES (?, ?, ?, ?, ?, ?)",
            ["0.2.0", "stage4_test", now, "0" * 64, True, "Stage 4 isolated test"],
        )
        connection.execute(
            "INSERT INTO control.database_release VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "release_0_2_0_aec_2025",
                "0.2.0",
                "validated",
                now,
                now,
                "Stage 4 tests",
                "Validated baseline test fixture",
            ],
        )
        connection.execute(
            "INSERT INTO sync.person VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "person_test",
                "Test Candidate",
                "Test Candidate",
                "Test",
                "Candidate",
                None,
                None,
                "Australia",
                True,
                "active",
                "audited",
                "1" * 64,
                now,
            ],
        )
        connection.execute(
            "INSERT INTO sync.party VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "party_test",
                "Test Party",
                "Test",
                "TST",
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
                "2" * 64,
                now,
            ],
        )
        constituency_values = [
            "constituency_test",
            "Test Division",
            "electoral_division",
            "Federal",
            "House",
            "NSW",
            "Australia",
            "Test election",
            None,
            None,
            None,
            None,
            None,
            None,
            "test",
            "test!A2",
            "official",
            "active",
            "audited",
            now,
            "Stage 4 tests",
            None,
            None,
            "101",
            "verified",
            "3" * 64,
            now,
        ]
        connection.execute(
            "INSERT INTO sync.constituency VALUES (" + ",".join("?" for _ in constituency_values) + ")",
            constituency_values,
        )
        connection.execute(
            "INSERT INTO core.election VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ELECTION_ID,
                official_event_id,
                "Test federal election",
                "2025-05-03",
                2025,
                "jurisdiction_aus_federal",
                "authority_aec",
                "election_type_general",
                "final",
                "declared",
                None,
                "active",
                now,
                now,
            ],
        )
        connection.execute(
            "INSERT INTO core.election_chamber VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [CHAMBER_ID, ELECTION_ID, "chamber_house", None, 1, True, "final", "active"],
        )
        connection.execute(
            "INSERT INTO core.contest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                CONTEST_ID,
                CHAMBER_ID,
                "constituency_test",
                "101",
                "Test Division",
                1,
                None,
                "declared",
                False,
                None,
                "final",
                "active",
            ],
        )
        connection.execute(
            "INSERT INTO core.candidacy VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                CANDIDACY_ID,
                CONTEST_ID,
                "person_test",
                "party_test",
                "501",
                "Test Candidate",
                "Test",
                "Candidate",
                "Test Party",
                "TST",
                None,
                "accepted",
                "matched",
                "final",
                "active",
            ],
        )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


def write_result_csv(path: Path, *, ordinary_votes: int = 100) -> bytes:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        writer.writerow(
            [
                "NSW",
                "101",
                "Test Division",
                "TST",
                "Test Party",
                "501",
                "Candidate",
                "Test",
                str(ordinary_votes),
                "10",
                "2",
                "20",
                "8",
                str(ordinary_votes + 40),
                "1.25",
            ]
        )
    return path.read_bytes()


def add_all_official_house_candidates(path: Path) -> None:
    source = (
        PROJECT_ROOT
        / "data/raw/aec/2025_federal/31496/final/HouseCandidatesDownload-31496.csv"
    )
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        headers = next(reader)
        rows = [dict(zip(headers, values, strict=True)) for values in reader if values]
    divisions = {}
    for row in rows:
        divisions[row["DivisionID"]] = row["DivisionNm"]
    connection = duckdb.connect(str(path))
    try:
        connection.execute("DELETE FROM core.candidacy")
        connection.execute("DELETE FROM core.contest")
        contest_rows = [
            (
                f"contest_test_house_{division_id}",
                CHAMBER_ID,
                None,
                division_id,
                division_name,
                1,
                None,
                "declared",
                False,
                None,
                "final",
                "active",
            )
            for division_id, division_name in sorted(divisions.items())
        ]
        connection.executemany(
            "INSERT INTO core.contest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            contest_rows,
        )
        candidacy_rows = []
        for row in rows:
            contest = f"contest_test_house_{row['DivisionID']}"
            candidacy_rows.append(
                (
                    candidacy_id(contest, row["CandidateID"]),
                    contest,
                    None,
                    None,
                    row["CandidateID"],
                    " ".join([row["GivenNm"], row["Surname"]]).strip(),
                    row["GivenNm"],
                    row["Surname"],
                    row["PartyNm"],
                    row["PartyAb"],
                    None,
                    "accepted",
                    "matched",
                    "final",
                    "active",
                )
            )
        connection.executemany(
            "INSERT INTO core.candidacy VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            candidacy_rows,
        )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


class Stage4WorkflowTests(unittest.TestCase):
    def test_transformer_is_registered_for_generic_aec_event_filename(self):
        registered = get_transformer(
            "adapter_aec_2025_v1", "house_first_preferences_by_vote_type"
        )
        self.assertIsNotNone(registered)
        self.assertIn(
            "house_first_preferences_by_vote_type",
            {item["dataset_key"] for item in transformer_catalogue()},
        )
        detection = AdapterCatalogue(PROJECT_ROOT / "config/adapters").detect(
            "HouseFirstPrefsByCandidateByVoteTypeDownload-99999.csv",
            HEADERS,
            "authority_aec",
        )
        self.assertEqual(detection["status"], "matched")
        self.assertEqual(detection["selection"]["mapping_entities"], [])

    def test_cli_default_resolves_and_verifies_active_release_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "data/app/releases/release-test/data/database/active.duckdb"
            database.parent.mkdir(parents=True)
            database.write_bytes(b"immutable-test-database")
            pointer = root / "data/app/releases/active.json"
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(
                json.dumps(
                    {
                        "path_base": "project_root",
                        "database_path": database.relative_to(root).as_posix(),
                        "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(resolve_default_database_path(root), database.resolve())
            database.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                resolve_default_database_path(root)

    def test_full_reproduction_reference_overlay_restores_pinned_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base)
            settings = AppSettings(
                project_root=PROJECT_ROOT,
                base_database=base,
                app_data=root / "app",
            )
            service = JobService(settings)
            job = service.begin_reproduce_2025("Reference preservation test")
            work = service.store.job_dir(job["job_id"]) / "work/database.duckdb"
            work.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base, work)
            connection = duckdb.connect(str(work))
            try:
                connection.execute("DELETE FROM sync.person")
                connection.execute("DELETE FROM sync.party")
                connection.execute("DELETE FROM sync.constituency")
                connection.execute("CHECKPOINT")
            finally:
                connection.close()
            counts = service._preserve_governed_reference_snapshot(job["job_id"], work)
            self.assertEqual(counts, {"people": 1, "parties": 1, "constituencies": 1})

    def test_individual_aec_result_file_executes_and_auto_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base)
            service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=base,
                    app_data=root / "app",
                )
            )
            job = service.begin_job(
                name="Individual AEC result revision",
                authority_id="authority_aec",
                election_id=ELECTION_ID,
            )
            filename = "HouseFirstPrefsByCandidateByVoteTypeDownload-99999.csv"
            upload_path = service.store.job_dir(job["job_id"]) / "uploads" / filename
            payload = write_result_csv(upload_path)
            inspected = service.finalise_uploads(
                job["job_id"],
                [
                    {
                        "upload_id": "individual-result",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            self.assertEqual(inspected["state"], "ready")
            self.assertTrue(inspected["datasets"][0]["detection"]["canonical_capable"])
            service.queue_execution(job["job_id"])
            completed = service.execute_job(job["job_id"])
            self.assertEqual(completed["state"], "validated")
            self.assertEqual(completed["validation"]["status"], "PASS")
            result = next(iter(completed["execution"]["dataset_results"].values()))
            self.assertEqual(result["staged_rows"], 1)
            self.assertEqual(result["inserted_rows"], 7)
            working = service.store.job_dir(job["job_id"]) / "work/database.duckdb"
            connection = duckdb.connect(str(working), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM results.vote_result WHERE record_status='active'"
                    ).fetchone()[0],
                    7,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM provenance.row_lineage WHERE target_table='vote_result'"
                    ).fetchone()[0],
                    7,
                )
                revision = connection.execute(
                    "SELECT sha256, revision_number FROM provenance.source_file_revision"
                ).fetchone()
                self.assertEqual(revision, (hashlib.sha256(payload).hexdigest(), 1))
            finally:
                connection.close()
            base_connection = duckdb.connect(str(base), read_only=True)
            try:
                self.assertEqual(
                    base_connection.execute("SELECT count(*) FROM results.vote_result").fetchone()[0],
                    0,
                )
            finally:
                base_connection.close()

            release_root = root / "release-copy"
            candidate = release_root / "data/database/database.duckdb"
            candidate.parent.mkdir(parents=True)
            shutil.copy2(working, candidate)
            service._copy_job_uploads_to_release(candidate, completed, release_root)
            copied_source = (
                release_root
                / "data/raw/operator_uploads"
                / job["job_id"]
                / filename
            )
            self.assertEqual(copied_source.read_bytes(), payload)
            copied_connection = duckdb.connect(str(candidate), read_only=True)
            try:
                self.assertEqual(
                    copied_connection.execute(
                        "SELECT archive_path FROM provenance.source_file_revision"
                    ).fetchone()[0],
                    copied_source.relative_to(release_root).as_posix(),
                )
            finally:
                copied_connection.close()

    def test_partial_candidate_file_is_rejected_before_canonical_insertion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base)
            connection = duckdb.connect(str(base))
            try:
                connection.execute(
                    "INSERT INTO core.candidacy VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        uuid.UUID("00000000-0000-5000-8000-000000000102"),
                        CONTEST_ID,
                        None,
                        None,
                        "502",
                        "Second Candidate",
                        "Second",
                        "Candidate",
                        None,
                        None,
                        None,
                        "accepted",
                        "matched",
                        "final",
                        "active",
                    ],
                )
                connection.execute("CHECKPOINT")
            finally:
                connection.close()
            service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=base,
                    app_data=root / "app",
                )
            )
            job = service.begin_job(
                name="Incomplete candidate file",
                authority_id="authority_aec",
                election_id=ELECTION_ID,
            )
            filename = "HouseFirstPrefsByCandidateByVoteTypeDownload-99999.csv"
            upload_path = service.store.job_dir(job["job_id"]) / "uploads" / filename
            payload = write_result_csv(upload_path)
            service.finalise_uploads(
                job["job_id"],
                [
                    {
                        "upload_id": "partial-file",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            service.queue_execution(job["job_id"])
            with self.assertRaisesRegex(ValueError, "not a complete House candidate"):
                service.execute_job(job["job_id"])
            self.assertEqual(service.store.read(job["job_id"])["state"], "failed")

    def test_source_event_number_must_match_selected_election(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base)
            service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=base,
                    app_data=root / "app",
                )
            )
            job = service.begin_job(
                name="Wrong event",
                authority_id="authority_aec",
                election_id=ELECTION_ID,
            )
            filename = "HouseFirstPrefsByCandidateByVoteTypeDownload-88888.csv"
            upload_path = service.store.job_dir(job["job_id"]) / "uploads" / filename
            payload = write_result_csv(upload_path)
            service.finalise_uploads(
                job["job_id"],
                [
                    {
                        "upload_id": "wrong-event",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            service.queue_execution(job["job_id"])
            with self.assertRaisesRegex(ValueError, "does not match the selected election"):
                service.execute_job(job["job_id"])

    def test_complete_official_2025_file_uses_the_individual_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base, official_event_id="31496")
            add_all_official_house_candidates(base)
            service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=base,
                    app_data=root / "app",
                )
            )
            job = service.begin_job(
                name="Complete official AEC House first preferences",
                authority_id="authority_aec",
                election_id=ELECTION_ID,
            )
            filename = "HouseFirstPrefsByCandidateByVoteTypeDownload-31496.csv"
            official = (
                PROJECT_ROOT / "data/raw/aec/2025_federal/31496/final" / filename
            )
            upload_path = service.store.job_dir(job["job_id"]) / "uploads" / filename
            shutil.copy2(official, upload_path)
            payload = upload_path.read_bytes()
            inspected = service.finalise_uploads(
                job["job_id"],
                [
                    {
                        "upload_id": "official-complete-file",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            self.assertTrue(inspected["datasets"][0]["detection"]["canonical_capable"])
            service.queue_execution(job["job_id"])
            completed = service.execute_job(job["job_id"])
            self.assertEqual(completed["state"], "validated")
            result = next(iter(completed["execution"]["dataset_results"].values()))
            self.assertEqual(result["staged_rows"], 1276)
            self.assertEqual(result["inserted_rows"], 7882)
            self.assertIn("ignored 150 source informal-summary rows", result["transform_notes"])

    def test_revised_source_supersedes_old_facts_without_active_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base)
            filename = "HouseFirstPrefsByCandidateByVoteTypeDownload-99999.csv"

            first_service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=base,
                    app_data=root / "first-app",
                )
            )
            first_job = first_service.begin_job(
                name="First revision",
                authority_id="authority_aec",
                election_id=ELECTION_ID,
            )
            first_path = (
                first_service.store.job_dir(first_job["job_id"]) / "uploads" / filename
            )
            first_payload = write_result_csv(first_path, ordinary_votes=100)
            first_service.finalise_uploads(
                first_job["job_id"],
                [
                    {
                        "upload_id": "first-revision",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(first_payload),
                        "sha256": hashlib.sha256(first_payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            first_service.queue_execution(first_job["job_id"])
            first_service.execute_job(first_job["job_id"])
            first_database = (
                first_service.store.job_dir(first_job["job_id"]) / "work/database.duckdb"
            )

            second_service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=first_database,
                    app_data=root / "second-app",
                )
            )
            duplicate = second_service.duplicate_source_revisions(
                hashlib.sha256(first_payload).hexdigest()
            )
            self.assertEqual(len(duplicate), 1)
            self.assertEqual(duplicate[0]["revision_number"], 1)

            duplicate_job = second_service.begin_job(
                name="Exact duplicate",
                authority_id="authority_aec",
                election_id=ELECTION_ID,
            )
            duplicate_path = (
                second_service.store.job_dir(duplicate_job["job_id"])
                / "uploads"
                / filename
            )
            duplicate_path.write_bytes(first_payload)
            second_service.finalise_uploads(
                duplicate_job["job_id"],
                [
                    {
                        "upload_id": "duplicate-revision",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(first_payload),
                        "sha256": hashlib.sha256(first_payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            second_service.queue_execution(duplicate_job["job_id"])
            with self.assertRaisesRegex(InvalidJobStateError, "Exact duplicate source bytes"):
                second_service.execute_job(duplicate_job["job_id"])
            self.assertFalse(
                (
                    second_service.store.job_dir(duplicate_job["job_id"])
                    / "work/database.duckdb"
                ).exists()
            )

            second_job = second_service.begin_job(
                name="Second revision",
                authority_id="authority_aec",
                election_id=ELECTION_ID,
            )
            second_path = (
                second_service.store.job_dir(second_job["job_id"]) / "uploads" / filename
            )
            second_payload = write_result_csv(second_path, ordinary_votes=101)
            second_service.finalise_uploads(
                second_job["job_id"],
                [
                    {
                        "upload_id": "second-revision",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(second_payload),
                        "sha256": hashlib.sha256(second_payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            second_service.queue_execution(second_job["job_id"])
            completed = second_service.execute_job(second_job["job_id"])
            self.assertEqual(completed["state"], "validated")
            second_database = (
                second_service.store.job_dir(second_job["job_id"])
                / "work/database.duckdb"
            )
            connection = duckdb.connect(str(second_database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        """SELECT record_status, count(*) FROM results.vote_result
                           GROUP BY record_status ORDER BY record_status"""
                    ).fetchall(),
                    [("active", 7), ("superseded", 7)],
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT revision_number, record_status
                           FROM provenance.source_file_revision
                           ORDER BY revision_number"""
                    ).fetchall(),
                    [(1, "superseded"), (2, "active")],
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT integer_value FROM results.vote_result
                           WHERE record_status='active' AND vote_type='ordinary'
                             AND measure_type='votes'"""
                    ).fetchone()[0],
                    101,
                )
            finally:
                connection.close()
            validation = validate_database(second_database, PROJECT_ROOT)
            self.assertEqual(validation["vote_result_count"], 7)
            self.assertEqual(validation["superseded_vote_result_count"], 7)
            self.assertEqual(validation["stage"], "stage_2_2025_federal")
            self.assertEqual(validation["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
