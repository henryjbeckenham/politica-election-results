import csv
import hashlib
import json
import tempfile
import unittest
import zipfile
import stat
from pathlib import Path
from unittest.mock import patch

import duckdb
from openpyxl import Workbook

from politica_erd.app.config import AppSettings
from politica_erd.app.detection import AdapterCatalogue
from politica_erd.app.readers import InputInspectionError, inspect_upload, iter_dataset_rows
from politica_erd.app.service import BUILTIN_AEC_2025, JobService
from politica_erd.app.sheets_sync import (
    ApplyConfirmationError,
    GoogleSheetsReferenceSynchronizer,
)
from politica_erd.build import PROJECT_ROOT, build


AEC_CANDIDATE_HEADERS = [
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


def inspect(path: Path, original_name: str):
    return inspect_upload(
        path,
        "upload-test",
        original_name,
        preview_rows=20,
        max_archive_bytes=50 * 1024**2,
        max_archive_members=100,
        max_xlsx_member_bytes=20 * 1024**2,
    )


class Stage3ReaderTests(unittest.TestCase):
    def test_csv_inspection_detection_and_streaming(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "HouseCandidatesDownload-31496.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(AEC_CANDIDATE_HEADERS)
                writer.writerow(
                    ["NSW", "105", "Bennelong", "ALP", "Australian Labor Party", "1", "Doe", "Jane", "N", "N"]
                )
            datasets, ignored = inspect(path, path.name)
            self.assertEqual(ignored, [])
            self.assertEqual(len(datasets), 1)
            self.assertEqual(datasets[0]["headers"], AEC_CANDIDATE_HEADERS)
            catalogue = AdapterCatalogue(PROJECT_ROOT / "config/adapters")
            detection = catalogue.detect(path.name, datasets[0]["headers"], "authority_aec")
            self.assertEqual(detection["status"], "matched")
            self.assertEqual(detection["selection"]["dataset_key"], "house_candidates")
            rows = list(iter_dataset_rows(path, datasets[0]))
            self.assertEqual(rows[0][0], 2)
            self.assertEqual(rows[0][1]["DivisionNm"], "Bennelong")

    def test_xlsx_inspection_exposes_each_worksheet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate-release.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Candidates"
            sheet.append(AEC_CANDIDATE_HEADERS)
            sheet.append(
                ["ACT", "318", "Bean", "ALP", "Australian Labor Party", "2", "Doe", "John", "N", "N"]
            )
            workbook.save(path)
            datasets, ignored = inspect(path, path.name)
            self.assertEqual(ignored, [])
            self.assertEqual(len(datasets), 1)
            self.assertEqual(datasets[0]["format"], "xlsx")
            self.assertEqual(datasets[0]["sheet"], "Candidates")
            self.assertEqual(datasets[0]["preview"][0]["DivisionID"], "318")

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../escape.csv", "a,b\n1,2\n")
            with self.assertRaises(InputInspectionError):
                inspect(path, path.name)

    def test_zip_symbolic_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "symlink.zip"
            link = zipfile.ZipInfo("link.csv")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(link, "target.csv")
            with self.assertRaises(InputInspectionError):
                inspect(path, path.name)

    def test_zip_member_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "many-files.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for index in range(101):
                    archive.writestr(f"ignored-{index}.txt", "x")
            with self.assertRaises(InputInspectionError):
                inspect(path, path.name)

    def test_zip_expanded_size_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("data.csv", "a,b\n1,2\n")
            with self.assertRaises(InputInspectionError):
                inspect_upload(
                    path,
                    "upload-test",
                    path.name,
                    preview_rows=20,
                    max_archive_bytes=4,
                    max_archive_members=100,
                    max_xlsx_member_bytes=20 * 1024**2,
                )

    def test_zip_compression_ratio_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compressed.zip"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("data.csv", b"0" * 1024 * 1024)
            with self.assertRaises(InputInspectionError):
                inspect(path, path.name)

    def test_xlsx_internal_expansion_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.xlsx"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
                package.writestr("[Content_Types].xml", "x" * 200)
            with patch("politica_erd.app.readers.MAX_XLSX_EXPANDED_BYTES", 100):
                with self.assertRaises(InputInspectionError):
                    inspect(path, path.name)


class _SnapshotReader:
    credential_descriptor = {"source": "test", "oauth_scopes": []}


class Stage3SheetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        legacy = json.loads(
            (PROJECT_ROOT / "data/snapshots/grand_database_2026-07-16.json").read_text(
                encoding="utf-8"
            )
        )
        cls.snapshot = {
            "spreadsheet_id": legacy["source_workbook_id"],
            "spreadsheet_properties": {"title": "Politica Grand Database"},
            "captured_at": "2026-07-16T00:00:00+00:00",
            "credential": _SnapshotReader.credential_descriptor,
            "tables": legacy["tables"],
        }

    def test_snapshot_matches_validated_stage2_references(self):
        database = PROJECT_ROOT / "data/database/politica_election_results.duckdb"
        connection = duckdb.connect(str(database), read_only=True)
        try:
            synchronizer = GoogleSheetsReferenceSynchronizer(
                connection, PROJECT_ROOT, reader=_SnapshotReader()
            )
            result = synchronizer.run(snapshot=self.snapshot)
        finally:
            connection.close()
        self.assertFalse(result.applied)
        self.assertEqual(
            {
                sheet: (diff.added, diff.updated, diff.retained_local)
                for sheet, diff in result.tables.items()
            },
            {
                "People": (0, 0, 0),
                "Parties": (0, 0, 0),
                "Constituencies": (0, 0, 0),
            },
        )

    def test_apply_requires_the_exact_reviewed_revision(self):
        database = PROJECT_ROOT / "data/database/politica_election_results.duckdb"
        connection = duckdb.connect(str(database), read_only=True)
        try:
            synchronizer = GoogleSheetsReferenceSynchronizer(
                connection, PROJECT_ROOT, reader=_SnapshotReader()
            )
            with self.assertRaises(ApplyConfirmationError):
                synchronizer.run(
                    apply=True,
                    expected_source_revision_sha256="0" * 64,
                    snapshot=self.snapshot,
                )
        finally:
            connection.close()


class Stage3ServiceTests(unittest.TestCase):
    def test_unknown_schema_is_staged_in_quarantine_and_cannot_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_database = root / "base.duckdb"
            build(base_database, PROJECT_ROOT)
            settings = AppSettings(
                project_root=PROJECT_ROOT,
                base_database=base_database,
                app_data=root / "app",
            )
            service = JobService(settings)
            job = service.begin_job(
                name="Mystery source", authority_id="authority_aec", election_id=None
            )
            payload = b"unknown_a,unknown_b\nfirst,1\nsecond,2\n"
            stored_name = "mystery.csv"
            upload_path = service.store.job_dir(job["job_id"]) / "uploads" / stored_name
            upload_path.write_bytes(payload)
            inspected = service.finalise_uploads(
                job["job_id"],
                [
                    {
                        "upload_id": "mystery-upload",
                        "original_name": stored_name,
                        "stored_name": stored_name,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            self.assertEqual(inspected["state"], "ready")
            self.assertEqual(
                inspected["datasets"][0]["detection"]["selection"]["adapter_id"],
                "stage3_quarantine",
            )
            service.queue_execution(job["job_id"])
            completed = service.execute_job(job["job_id"])
            self.assertEqual(completed["state"], "staged")
            self.assertFalse(completed["execution"]["canonical_complete"])
            working = service.store.job_dir(job["job_id"]) / "work/database.duckdb"
            connection = duckdb.connect(str(working), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM staging.source_record WHERE mapping_status='quarantined'"
                    ).fetchone()[0],
                    2,
                )
            finally:
                connection.close()
            report = service.validate_job(job["job_id"])
            self.assertEqual(report["status"], "FAIL")
            self.assertGreater(report["blocker_count"], 0)

    def test_jobs_are_external_and_work_on_a_checksum_verified_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_database = root / "base.duckdb"
            build(base_database, PROJECT_ROOT)
            settings = AppSettings(
                project_root=PROJECT_ROOT,
                base_database=base_database,
                app_data=root / "app",
            )
            service = JobService(settings)
            job = service.begin_job(
                name="Test ingestion", authority_id="authority_aec", election_id=None
            )
            working, source_hash = service._copy_governed_database(job["job_id"])
            self.assertTrue(working.is_file())
            self.assertNotEqual(working, base_database)
            self.assertEqual(source_hash, hashlib.sha256(base_database.read_bytes()).hexdigest())
            connection = duckdb.connect(str(base_database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM core.election").fetchone()[0],
                    0,
                )
            finally:
                connection.close()

    def test_governed_aec_reproduction_is_an_explicit_job_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = AppSettings(
                project_root=PROJECT_ROOT,
                base_database=PROJECT_ROOT / "data/database/politica_election_results.duckdb",
                app_data=root / "app",
            )
            service = JobService(settings)
            job = service.begin_reproduce_2025()
            self.assertEqual(job["mode"], BUILTIN_AEC_2025)
            self.assertEqual(job["state"], "ready")
            self.assertEqual(job["execution"]["builtin_pipeline"], BUILTIN_AEC_2025)


if __name__ == "__main__":
    unittest.main()
