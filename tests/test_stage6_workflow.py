import csv
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import duckdb

from politica_erd.app.aec_bootstrap import AecBootstrapError
from politica_erd.app.config import AppSettings
from politica_erd.app.detection import AdapterCatalogue
from politica_erd.app.service import InvalidJobStateError, JobService
from politica_erd.build import (
    PROJECT_ROOT,
    refresh_data_dictionary,
    seed_controlled_values,
    seed_relationship_contract,
)
from politica_erd.validate import validate_database

from tests.test_stage4_workflow import make_minimal_database, write_result_csv


HOUSE_HEADERS = [
    "StateAb",
    "DivisionID",
    "DivisionNm",
    "PartyAb",
    "PartyNm",
    "CandidateID",
    "Surname",
    "GivenNm",
    "Elected",
    "HistoricElected",
]
SENATE_HEADERS = [
    "StateAb",
    "PartyAb",
    "PartyNm",
    "CandidateID",
    "Surname",
    "GivenNm",
    "Elected",
    "HistoricElected",
]
OFFICIAL_ROOT = PROJECT_ROOT / "data/raw/aec/2025_federal/31496/final"


def make_registration_base(path: Path) -> None:
    make_minimal_database(path, official_event_id="31496")
    connection = duckdb.connect(str(path))
    try:
        connection.execute("DELETE FROM core.candidacy")
        connection.execute("DELETE FROM core.contest_constituency_snapshot")
        connection.execute("DELETE FROM core.contest")
        connection.execute("DELETE FROM core.election_key_date")
        connection.execute("DELETE FROM core.election_chamber")
        connection.execute("DELETE FROM core.election")
        seed_controlled_values(connection, PROJECT_ROOT)
        seed_relationship_contract(connection, PROJECT_ROOT, "0.2.0")
        refresh_data_dictionary(connection, "0.2.0")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


def write_candidate_csv(
    path: Path,
    *,
    event: str,
    chamber: str,
    rows: list[list[object]],
    preamble_event: str | None = None,
) -> bytes:
    headers = HOUSE_HEADERS if chamber == "house" else SENATE_HEADERS
    title = "House of Representatives" if chamber == "house" else "Senate"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                f"2030 Federal Election {title} Candidates "
                f"[Event:{preamble_event or event} Phase:FinalResults]"
            ]
        )
        writer.writerow(headers)
        writer.writerows(rows)
    return path.read_bytes()


def create_bootstrap_job(
    service: JobService,
    *,
    event: str = "55555",
    house_rows: list[list[object]] | None = None,
    senate_rows: list[list[object]] | None = None,
    filename_event: str | None = None,
    preamble_event: str | None = None,
) -> dict:
    job = service.begin_aec_election_bootstrap(
        election_name="2030 Australian federal election",
        official_event_id=event,
        election_date="2030-05-18",
        election_type_code="general",
        publication_phase="final",
        contest_status="declared",
        senate_state_vacancies=6,
        senate_territory_vacancies=2,
    )
    uploads = []
    cases = []
    if house_rows is not None:
        cases.append(("house", house_rows))
    if senate_rows is not None:
        cases.append(("senate", senate_rows))
    for index, (chamber, rows) in enumerate(cases, start=1):
        stem = "HouseCandidatesDownload" if chamber == "house" else "SenateCandidatesDownload"
        filename = f"{stem}-{filename_event or event}.csv"
        path = service.store.job_dir(job["job_id"]) / "uploads" / filename
        payload = write_candidate_csv(
            path,
            event=event,
            chamber=chamber,
            rows=rows,
            preamble_event=preamble_event,
        )
        uploads.append(
            {
                "upload_id": f"candidate-{index}",
                "original_name": filename,
                "stored_name": filename,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "content_type": "text/csv",
            }
        )
    return service.finalise_uploads(job["job_id"], uploads)


class Stage6WorkflowTests(unittest.TestCase):
    def test_candidate_adapters_accept_generic_numeric_event_names(self):
        catalogue = AdapterCatalogue(PROJECT_ROOT / "config/adapters")
        for filename, headers, dataset_key in (
            ("HouseCandidatesDownload-55555.csv", HOUSE_HEADERS, "house_candidates"),
            ("SenateCandidatesDownload-55555.csv", SENATE_HEADERS, "senate_candidates"),
        ):
            result = catalogue.detect(filename, headers, "authority_aec")
            self.assertEqual(result["status"], "matched")
            self.assertEqual(result["selection"]["dataset_key"], dataset_key)
            self.assertEqual(result["selection"]["mapping_entities"], [])

    def test_preview_is_read_only_and_reports_reference_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_registration_base(base)
            before = hashlib.sha256(base.read_bytes()).hexdigest()
            service = JobService(
                AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "app")
            )
            inspected = create_bootstrap_job(
                service,
                house_rows=[
                    ["NSW", "101", "Test Division", "TST", "Test Party", "501", "Candidate", "Test", "N", "N"]
                ],
            )
            self.assertEqual(inspected["state"], "ready")
            self.assertEqual(inspected["mode"], "aec_election_bootstrap")
            self.assertEqual(inspected["bootstrap_preview"]["total_contests"], 1)
            self.assertEqual(inspected["bootstrap_preview"]["total_candidates"], 1)
            self.assertEqual(
                inspected["bootstrap_preview"]["reference_matches"]["people_matched"], 1
            )
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), before)
            self.assertFalse(
                (service.store.job_dir(inspected["job_id"]) / "work/database.duckdb").exists()
            )

    def test_complete_official_2025_candidate_register_executes_at_full_scale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_registration_base(base)
            base_hash = hashlib.sha256(base.read_bytes()).hexdigest()
            service = JobService(
                AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "app")
            )
            job = service.begin_aec_election_bootstrap(
                election_name="2025 Australian federal election",
                official_event_id="31496",
                election_date="2025-05-03",
                election_type_code="general",
                publication_phase="final",
                contest_status="declared",
            )
            uploads = []
            for index, filename in enumerate(
                ("HouseCandidatesDownload-31496.csv", "SenateCandidatesDownload-31496.csv"),
                start=1,
            ):
                source = OFFICIAL_ROOT / filename
                destination = service.store.job_dir(job["job_id"]) / "uploads" / filename
                shutil.copy2(source, destination)
                payload = destination.read_bytes()
                uploads.append(
                    {
                        "upload_id": f"official-candidates-{index}",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                )
            inspected = service.finalise_uploads(job["job_id"], uploads)
            preview = inspected["bootstrap_preview"]
            self.assertEqual(inspected["state"], "ready")
            self.assertEqual(preview["house_contests"], 150)
            self.assertEqual(preview["senate_contests"], 8)
            self.assertEqual(preview["house_candidates"], 1126)
            self.assertEqual(preview["senate_candidates"], 330)
            self.assertEqual(preview["total_candidates"], 1456)
            self.assertFalse(
                (service.store.job_dir(inspected["job_id"]) / "work/database.duckdb").exists()
            )
            service.queue_execution(inspected["job_id"])
            completed = service.execute_job(inspected["job_id"])
            self.assertEqual(completed["state"], "validated")
            self.assertEqual(completed["execution"]["bootstrap_report"]["total_contests"], 158)
            self.assertEqual(completed["execution"]["bootstrap_report"]["total_candidates"], 1456)
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), base_hash)

    def test_house_and_senate_bootstrap_validates_without_changing_references_or_base(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_registration_base(base)
            base_hash = hashlib.sha256(base.read_bytes()).hexdigest()
            service = JobService(
                AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "app")
            )
            inspected = create_bootstrap_job(
                service,
                house_rows=[
                    ["NSW", "101", "Test Division", "TST", "Test Party", "501", "Candidate", "Test", "N", "Y"],
                    ["VIC", "202", "New Division", "NEW", "New Party", "502", "Person", "New", "N", "N"],
                ],
                senate_rows=[
                    ["NSW", "TST", "Test Party", "601", "Senator", "Sample", "N", "N"],
                    ["ACT", "IND", "Independent", "602", "Citizen", "Example", "N", "N"],
                ],
            )
            service.queue_execution(inspected["job_id"])
            completed = service.execute_job(inspected["job_id"])
            self.assertEqual(completed["state"], "validated")
            self.assertEqual(completed["validation"]["status"], "PASS")
            self.assertEqual(completed["validation"]["ruleset_version"], "stage6_v1")
            report = completed["execution"]["bootstrap_report"]
            self.assertEqual(report["total_contests"], 4)
            self.assertEqual(report["total_candidates"], 4)
            self.assertEqual(report["reference_counts_before"], report["reference_counts_after"])
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), base_hash)

            working = service.store.job_dir(inspected["job_id"]) / "work/database.duckdb"
            cli_validation = validate_database(working, root)
            self.assertEqual(cli_validation["status"], "PASS", cli_validation["failures"])
            self.assertEqual(cli_validation["stage"], "governed_elections")
            connection = duckdb.connect(str(working), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT official_event_id FROM core.election WHERE election_id=?",
                        [inspected["election_id"]],
                    ).fetchone()[0],
                    "55555",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM core.election_chamber WHERE election_id=?",
                        [inspected["election_id"]],
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT count(*) FROM core.contest contest
                           JOIN core.election_chamber chamber USING (election_chamber_id)
                           WHERE chamber.election_id=?""",
                        [inspected["election_id"]],
                    ).fetchone()[0],
                    4,
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT count(*) FROM core.candidacy candidacy
                           JOIN core.contest contest USING (contest_id)
                           JOIN core.election_chamber chamber USING (election_chamber_id)
                           WHERE chamber.election_id=?""",
                        [inspected["election_id"]],
                    ).fetchone()[0],
                    4,
                )
                self.assertGreaterEqual(
                    connection.execute(
                        "SELECT count(*) FROM provenance.row_lineage WHERE import_run_id=?",
                        [completed["execution"]["import_run_id"]],
                    ).fetchone()[0],
                    4,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM core.candidacy WHERE person_id IS NULL"
                    ).fetchone()[0],
                    3,
                )
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM sync.person").fetchone()[0], 1
                )
            finally:
                connection.close()
            release_root = root / "release-copy"
            release_database = release_root / "data/database/database.duckdb"
            release_database.parent.mkdir(parents=True)
            shutil.copy2(working, release_database)
            service._copy_job_uploads_to_release(release_database, completed, release_root)
            archived = list(
                (release_root / "data/raw/operator_uploads" / inspected["job_id"]).glob("*.csv")
            )
            self.assertEqual(len(archived), 2)
            archived_connection = duckdb.connect(str(release_database), read_only=True)
            try:
                self.assertEqual(
                    archived_connection.execute(
                        """SELECT count(*) FROM provenance.source_file_revision
                           WHERE archive_path LIKE 'data/raw/operator_uploads/%'"""
                    ).fetchone()[0],
                    2,
                )
            finally:
                archived_connection.close()
            base_connection = duckdb.connect(str(base), read_only=True)
            try:
                self.assertEqual(base_connection.execute("SELECT count(*) FROM core.election").fetchone()[0], 0)
            finally:
                base_connection.close()

    def test_existing_house_result_route_targets_a_newly_registered_election(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_registration_base(base)
            bootstrap_service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=base,
                    app_data=root / "bootstrap-app",
                )
            )
            inspected = create_bootstrap_job(
                bootstrap_service,
                house_rows=[
                    ["NSW", "101", "Test Division", "TST", "Test Party", "501", "Candidate", "Test", "N", "N"]
                ],
            )
            bootstrap_service.queue_execution(inspected["job_id"])
            registered = bootstrap_service.execute_job(inspected["job_id"])
            self.assertEqual(registered["state"], "validated")
            registered_database = (
                bootstrap_service.store.job_dir(inspected["job_id"])
                / "work/database.duckdb"
            )

            result_service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=registered_database,
                    app_data=root / "result-app",
                )
            )
            result_job = result_service.begin_job(
                name="New election House first preferences",
                authority_id="authority_aec",
                election_id=inspected["election_id"],
            )
            filename = "HouseFirstPrefsByCandidateByVoteTypeDownload-55555.csv"
            upload_path = result_service.store.job_dir(result_job["job_id"]) / "uploads" / filename
            payload = write_result_csv(upload_path)
            prepared = result_service.finalise_uploads(
                result_job["job_id"],
                [
                    {
                        "upload_id": "new-election-result",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            result_service.queue_execution(prepared["job_id"])
            completed = result_service.execute_job(prepared["job_id"])
            self.assertEqual(completed["state"], "validated")
            result_database = (
                result_service.store.job_dir(prepared["job_id"])
                / "work/database.duckdb"
            )
            connection = duckdb.connect(str(result_database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM results.vote_result WHERE election_id=? AND record_status='active'",
                        [inspected["election_id"]],
                    ).fetchone()[0],
                    7,
                )
            finally:
                connection.close()

    def test_duplicate_event_is_rejected_during_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base, official_event_id="55555")
            service = JobService(
                AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "app")
            )
            with self.assertRaisesRegex(AecBootstrapError, "already registered"):
                create_bootstrap_job(
                    service,
                    house_rows=[
                        ["NSW", "101", "Test Division", "TST", "Test Party", "501", "Candidate", "Test", "N", "N"]
                    ],
                )

    def test_filename_and_preamble_event_must_match_configuration(self):
        for filename_event, preamble_event, expected in (
            ("44444", "44444", "not the configured event"),
            ("55555", "44444", "source preamble names event"),
        ):
            with self.subTest(filename_event=filename_event, preamble_event=preamble_event):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    base = root / "base.duckdb"
                    make_registration_base(base)
                    service = JobService(
                        AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "app")
                    )
                    with self.assertRaisesRegex(AecBootstrapError, expected):
                        create_bootstrap_job(
                            service,
                            filename_event=filename_event,
                            preamble_event=preamble_event,
                            house_rows=[
                                ["NSW", "101", "Test Division", "TST", "Test Party", "501", "Candidate", "Test", "N", "N"]
                            ],
                        )

    def test_invalid_candidate_set_rolls_back_every_canonical_insert(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_registration_base(base)
            service = JobService(
                AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "app")
            )
            with self.assertRaisesRegex(AecBootstrapError, "duplicated"):
                create_bootstrap_job(
                    service,
                    house_rows=[
                        ["NSW", "101", "Test Division", "TST", "Test Party", "501", "Candidate", "Test", "N", "N"],
                        ["NSW", "101", "Test Division", "TST", "Test Party", "501", "Duplicate", "Test", "N", "N"],
                    ],
                )
            connection = duckdb.connect(str(base), read_only=True)
            try:
                self.assertEqual(connection.execute("SELECT count(*) FROM core.election").fetchone()[0], 0)
            finally:
                connection.close()

    def test_candidate_bytes_cannot_change_between_preview_and_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_registration_base(base)
            base_hash = hashlib.sha256(base.read_bytes()).hexdigest()
            service = JobService(
                AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "app")
            )
            inspected = create_bootstrap_job(
                service,
                house_rows=[
                    ["NSW", "101", "Test Division", "TST", "Test Party", "501", "Candidate", "Test", "N", "N"]
                ],
            )
            upload = inspected["uploads"][0]
            source = service.store.job_dir(inspected["job_id"]) / "uploads" / upload["stored_name"]
            source.write_bytes(source.read_bytes() + b"\n")
            service.queue_execution(inspected["job_id"])
            with self.assertRaisesRegex(InvalidJobStateError, "checksum verification failed"):
                service.execute_job(inspected["job_id"])
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), base_hash)
            self.assertFalse(
                (service.store.job_dir(inspected["job_id"]) / "work/database.duckdb").exists()
            )


if __name__ == "__main__":
    unittest.main()
