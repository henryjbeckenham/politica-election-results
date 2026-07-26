import csv
import hashlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
import zipfile

import duckdb

from politica_erd.app.config import AppSettings
from politica_erd.app.detection import AdapterCatalogue
from politica_erd.app.service import JobService
from politica_erd.app.transformers import get_transformer
from politica_erd.build import PROJECT_ROOT
from tests.test_stage7_workflow import bootstrap_official


OFFICIAL_ROOT = PROJECT_ROOT / "data/raw/aec/2025_federal/31496/final"
FILES = {
    "SenateFirstPrefsByGroupByVoteTypeDownload-31496.csv": (
        "1b527253ec43188f4d923349344ad3ed3dcb10e588f87daa888fe211c0c31ced"
    ),
    "SenateFirstPrefsByStateByGroupByVoteTypeDownload-31496.csv": (
        "084c7f88e18f62db0b1a18099b081c7ae12680240435a5f4c9aa14e92b29efd5"
    ),
    "SenateDopDownload-31496.zip": (
        "a86b591b422218fd9b2f43f4adcf061007b731f0d740a614e1cfa3cf64392b1e"
    ),
    "aec-senate-formalpreferences-31496-ACT.zip": (
        "320b42fd26a45d8719efc9c54c5cc9f3d8b4370d95365b109fd2087beff1c28a"
    ),
}


def upload_files(service: JobService, job: dict, filenames: tuple[str, ...]) -> dict:
    uploads = []
    for index, filename in enumerate(filenames, start=1):
        destination = service.store.job_dir(job["job_id"]) / "uploads" / filename
        shutil.copy2(OFFICIAL_ROOT / filename, destination)
        payload = destination.read_bytes()
        uploads.append(
            {
                "upload_id": f"stage8-{index}",
                "original_name": filename,
                "stored_name": filename,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "content_type": "application/zip" if filename.endswith(".zip") else "text/csv",
            }
        )
    return service.finalise_uploads(job["job_id"], uploads)


def add_ballot_structure(root: Path, base: Path) -> Path:
    service = JobService(
        AppSettings(
            project_root=PROJECT_ROOT,
            base_database=base,
            app_data=root / "ballot_structure_app",
        )
    )
    job = service.begin_job(
        name="Create official Senate ballot structure",
        authority_id="authority_aec",
        election_id="election_fed_2025_05_03_general",
    )
    inspected = upload_files(
        service,
        job,
        ("SenateFirstPrefsByStateByVoteTypeDownload-31496.csv",),
    )
    service.queue_execution(inspected["job_id"])
    completed = service.execute_job(inspected["job_id"])
    if completed["state"] != "validated":
        raise AssertionError(completed)
    return service.store.job_dir(inspected["job_id"]) / "work/database.duckdb"


def revised_zip(source: Path, destination: Path, comment: bytes) -> bytes:
    shutil.copy2(source, destination)
    with zipfile.ZipFile(destination, "a") as archive:
        archive.comment = comment
    return destination.read_bytes()


class Stage8WorkflowTests(unittest.TestCase):
    def test_stage8_routes_detect_generic_event_files_and_are_checksum_pinned(self):
        for filename, expected in FILES.items():
            self.assertEqual(
                hashlib.sha256((OFFICIAL_ROOT / filename).read_bytes()).hexdigest(),
                expected,
            )
        catalogue = AdapterCatalogue(PROJECT_ROOT / "config/adapters")
        cases: list[tuple[str, list[str], str]] = []
        for filename, key in (
            (
                "SenateFirstPrefsByGroupByVoteTypeDownload-31496.csv",
                "senate_group_preferences_national",
            ),
            (
                "SenateFirstPrefsByStateByGroupByVoteTypeDownload-31496.csv",
                "senate_group_preferences_state",
            ),
        ):
            with (OFFICIAL_ROOT / filename).open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                next(handle)
                headers = next(csv.reader(handle))
            cases.append((filename.replace("31496", "99999"), headers, key))
        for archive_name, key in (
            ("SenateDopDownload-31496.zip", "senate_distribution"),
            (
                "aec-senate-formalpreferences-31496-ACT.zip",
                "senate_formal_preferences",
            ),
        ):
            with zipfile.ZipFile(OFFICIAL_ROOT / archive_name) as archive:
                member = archive.namelist()[0]
                with archive.open(member) as raw:
                    headers = [
                        value.strip()
                        for value in next(
                            csv.reader(
                                io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                            )
                        )
                    ]
            cases.append((Path(member).name.replace("31496", "99999"), headers, key))
        for filename, headers, key in cases:
            detected = catalogue.detect(filename, headers, "authority_aec")
            self.assertEqual(detected["status"], "matched", filename)
            self.assertEqual(detected["selection"]["dataset_key"], key, filename)
            self.assertIsNotNone(
                get_transformer(
                    detected["selection"]["adapter_id"],
                    detected["selection"]["dataset_key"],
                )
            )

    def test_partial_dop_member_is_rejected_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = bootstrap_official(root)
            service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=base,
                    app_data=root / "partial_app",
                )
            )
            job = service.begin_job(
                name="Invalid partial ACT DOP",
                authority_id="authority_aec",
                election_id="election_fed_2025_05_03_general",
            )
            filename = "SenateStateDOPDownload-31496-ACT.csv"
            destination = service.store.job_dir(job["job_id"]) / "uploads" / filename
            with zipfile.ZipFile(OFFICIAL_ROOT / "SenateDopDownload-31496.zip") as archive:
                source = archive.read(filename).splitlines(keepends=True)
            payload = b"".join(source[:20])
            destination.write_bytes(payload)
            inspected = service.finalise_uploads(
                job["job_id"],
                [
                    {
                        "upload_id": "partial",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            service.queue_execution(inspected["job_id"])
            with self.assertRaisesRegex(ValueError, "incomplete|contiguous"):
                service.execute_job(inspected["job_id"])
            database = service.store.job_dir(inspected["job_id"]) / "work/database.duckdb"
            connection = duckdb.connect(str(database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        'SELECT count(*) FROM "count".count_round'
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        'SELECT count(*) FROM "count".count_candidate_total'
                    ).fetchone()[0],
                    0,
                )
            finally:
                connection.close()

    def test_individual_dop_member_cannot_pass_the_complete_archive_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = add_ballot_structure(root, bootstrap_official(root))
            service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=base,
                    app_data=root / "individual_dop_app",
                )
            )
            job = service.begin_job(
                name="Complete ACT DOP member without its governed outer archive",
                authority_id="authority_aec",
                election_id="election_fed_2025_05_03_general",
            )
            filename = "SenateStateDOPDownload-31496-ACT.csv"
            destination = service.store.job_dir(job["job_id"]) / "uploads" / filename
            with zipfile.ZipFile(OFFICIAL_ROOT / "SenateDopDownload-31496.zip") as archive:
                payload = archive.read(filename)
            destination.write_bytes(payload)
            inspected = service.finalise_uploads(
                job["job_id"],
                [
                    {
                        "upload_id": "individual-dop",
                        "original_name": filename,
                        "stored_name": filename,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            service.queue_execution(inspected["job_id"])
            completed = service.execute_job(inspected["job_id"])
            self.assertEqual(completed["state"], "validation_failed")
            archive_check = next(
                check
                for check in completed["validation"]["checks"]
                if check["rule_id"] == "stage8_complete_dop_archive"
            )
            self.assertEqual(archive_check["status"], "failed")
            self.assertEqual(archive_check["observed"], [filename])

    def test_complete_official_stage8_group_dop_and_formal_routes_with_revisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registered = bootstrap_official(root)
            base = add_ballot_structure(root, registered)
            base_hash = hashlib.sha256(base.read_bytes()).hexdigest()
            service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=base,
                    app_data=root / "stage8_app",
                )
            )
            job = service.begin_job(
                name="Official 2025 Stage 8 Senate group",
                authority_id="authority_aec",
                election_id="election_fed_2025_05_03_general",
            )
            inspected = upload_files(service, job, tuple(FILES))
            self.assertEqual(len(inspected["datasets"]), 11)
            self.assertTrue(
                all(
                    dataset["detection"]["canonical_capable"]
                    for dataset in inspected["datasets"]
                )
            )
            service.queue_execution(inspected["job_id"])
            completed = service.execute_job(inspected["job_id"])
            self.assertEqual(completed["state"], "validated")
            self.assertEqual(completed["validation"]["ruleset_version"], "stage8_v1")
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), base_hash)
            results = completed["execution"]["dataset_results"].values()
            self.assertEqual(sum(result["staged_rows"] for result in results), 67640)
            self.assertEqual(sum(result["source_rows"] for result in results), 361113)

            database = service.store.job_dir(inspected["job_id"]) / "work/database.duckdb"
            work_root = database.parent
            connection = duckdb.connect(str(database), read_only=True)
            connection.execute(f"SET file_search_path='{work_root}'")
            try:
                self.assertEqual(
                    connection.execute(
                        """SELECT value_set_name, value_code
                           FROM control.controlled_value
                           WHERE (value_set_name, value_code) IN (
                             ('reporting_unit_type', 'national_total'),
                             ('subject_type', 'source_group')
                           ) ORDER BY value_set_name, value_code"""
                    ).fetchall(),
                    [
                        ("reporting_unit_type", "national_total"),
                        ("subject_type", "source_group"),
                    ],
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT count(*) FROM results.vote_result
                           WHERE result_type='group_total' AND record_status='active'"""
                    ).fetchone()[0],
                    1872,
                )
                self.assertEqual(
                    connection.execute(
                        'SELECT count(*) FROM "count".count_round'
                    ).fetchone()[0],
                    1259,
                )
                self.assertEqual(
                    connection.execute(
                        'SELECT count(*) FROM "count".count_candidate_total'
                    ).fetchone()[0],
                    64965,
                )
                self.assertEqual(
                    connection.execute(
                        'SELECT count(*) FROM "count".preference_transfer'
                    ).fetchone()[0],
                    11633,
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT count(*) FROM "count".preference_transfer
                           WHERE exhausted"""
                    ).fetchone()[0],
                    418,
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT round_number, action_type, transfer_value > 0
                           FROM "count".count_round
                           WHERE starts_with(
                             source_locator,
                             'SenateStateDOPDownload-31496-ACT.csv#row='
                           ) AND round_number IN (2, 3)
                           ORDER BY round_number"""
                    ).fetchall(),
                    [
                        (2, "surplus_distribution", True),
                        (3, "exclusion", True),
                    ],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*), sum(preference_count) FROM ballot.ballot"
                    ).fetchone(),
                    (293474, 2166773),
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT min(source_row_number), max(source_row_number)
                           FROM read_parquet(
                             'data/parquet/aec_31496/formal_preferences/state=ACT/**/*.parquet',
                             hive_partitioning=false, union_by_name=true
                           )"""
                    ).fetchone(),
                    (1, 293474),
                )
            finally:
                connection.close()

            # The generic release copier must produce a self-contained unit:
            # its copied DuckDB views resolve only against copied artifacts.
            release_root = root / "self_contained_release"
            release_database = (
                release_root / "data/database/politica_election_results.duckdb"
            )
            release_database.parent.mkdir(parents=True)
            shutil.copy2(database, release_database)
            service._copy_release_artifacts(
                source_root=work_root,
                release_root=release_root,
                database=release_database,
            )
            connection = duckdb.connect(str(release_database), read_only=True)
            connection.execute(
                "SET file_search_path='" + str(release_root).replace("'", "''") + "'"
            )
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*), sum(preference_count) FROM ballot.ballot"
                    ).fetchone(),
                    (293474, 2166773),
                )
            finally:
                connection.close()
            self.assertTrue(
                any((release_root / "data/parquet").rglob("*.parquet"))
            )
            self.assertTrue(
                (release_root / "data/manifests/formal_preferences_stage8.json").is_file()
            )

            # A corrected national aggregate replaces only its prior facts and
            # is cross-checked against the still-active state aggregates.
            group_service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=database,
                    app_data=root / "group_revision_app",
                )
            )
            group_job = group_service.begin_job(
                name="Corrected 2025 national Senate group aggregates",
                authority_id="authority_aec",
                election_id="election_fed_2025_05_03_general",
            )
            group_name = "SenateFirstPrefsByGroupByVoteTypeDownload-31496.csv"
            group_destination = (
                group_service.store.job_dir(group_job["job_id"])
                / "uploads"
                / group_name
            )
            payload = (OFFICIAL_ROOT / group_name).read_bytes() + b"\n"
            group_destination.write_bytes(payload)
            revised = group_service.finalise_uploads(
                group_job["job_id"],
                [
                    {
                        "upload_id": "revised-group",
                        "original_name": group_name,
                        "stored_name": group_name,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "text/csv",
                    }
                ],
            )
            group_service.queue_execution(revised["job_id"])
            revised_group = group_service.execute_job(revised["job_id"])
            self.assertEqual(revised_group["state"], "validated")
            reconciliation = next(
                check
                for check in revised_group["validation"]["checks"]
                if check["rule_id"] == "stage8_group_aggregate_reconciliation"
            )
            self.assertEqual(reconciliation["status"], "passed")
            group_database = (
                group_service.store.job_dir(revised["job_id"])
                / "work/database.duckdb"
            )
            connection = duckdb.connect(str(group_database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        """SELECT record_status, count(*) FROM results.vote_result
                           WHERE result_type='group_total'
                           GROUP BY record_status ORDER BY record_status"""
                    ).fetchall(),
                    [("active", 1872), ("superseded", 396)],
                )
            finally:
                connection.close()

            # A corrected DOP package retains old count rows but makes only the
            # new source revision current.
            dop_service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=database,
                    app_data=root / "dop_revision_app",
                )
            )
            dop_job = dop_service.begin_job(
                name="Corrected 2025 Senate DOP",
                authority_id="authority_aec",
                election_id="election_fed_2025_05_03_general",
            )
            dop_name = "SenateDopDownload-31496.zip"
            dop_destination = (
                dop_service.store.job_dir(dop_job["job_id"]) / "uploads" / dop_name
            )
            payload = revised_zip(
                OFFICIAL_ROOT / dop_name,
                dop_destination,
                b"corrected authority package metadata",
            )
            revised = dop_service.finalise_uploads(
                dop_job["job_id"],
                [
                    {
                        "upload_id": "revised-dop",
                        "original_name": dop_name,
                        "stored_name": dop_name,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "application/zip",
                    }
                ],
            )
            dop_service.queue_execution(revised["job_id"])
            revised_dop = dop_service.execute_job(revised["job_id"])
            self.assertEqual(revised_dop["state"], "validated")
            dop_database = (
                dop_service.store.job_dir(revised["job_id"]) / "work/database.duckdb"
            )
            connection = duckdb.connect(str(dop_database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        'SELECT count(*) FROM "count".count_candidate_total'
                    ).fetchone()[0],
                    129930,
                )
                self.assertEqual(
                    connection.execute(
                        """SELECT count(*) FROM "count".count_candidate_total total
                           JOIN "count".count_round round USING (count_round_id)
                           JOIN provenance.source_file_revision revision
                             ON revision.source_revision_id=round.source_revision_id
                           WHERE revision.record_status='active'"""
                    ).fetchone()[0],
                    64965,
                )
            finally:
                connection.close()

            # A corrected formal archive retains the old partition and exposes
            # only the new active dataset through the ballot views.
            formal_service = JobService(
                AppSettings(
                    project_root=PROJECT_ROOT,
                    base_database=database,
                    app_data=root / "formal_revision_app",
                )
            )
            formal_job = formal_service.begin_job(
                name="Corrected 2025 ACT formal preferences",
                authority_id="authority_aec",
                election_id="election_fed_2025_05_03_general",
            )
            formal_name = "aec-senate-formalpreferences-31496-ACT.zip"
            formal_destination = (
                formal_service.store.job_dir(formal_job["job_id"])
                / "uploads"
                / formal_name
            )
            payload = revised_zip(
                OFFICIAL_ROOT / formal_name,
                formal_destination,
                b"corrected authority package metadata",
            )
            revised = formal_service.finalise_uploads(
                formal_job["job_id"],
                [
                    {
                        "upload_id": "revised-formal",
                        "original_name": formal_name,
                        "stored_name": formal_name,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "content_type": "application/zip",
                    }
                ],
            )
            formal_service.queue_execution(revised["job_id"])
            revised_formal = formal_service.execute_job(revised["job_id"])
            self.assertEqual(revised_formal["state"], "validated")
            formal_database = (
                formal_service.store.job_dir(revised["job_id"])
                / "work/database.duckdb"
            )
            formal_root = formal_database.parent
            connection = duckdb.connect(str(formal_database), read_only=True)
            connection.execute(f"SET file_search_path='{formal_root}'")
            try:
                self.assertEqual(
                    connection.execute(
                        """SELECT record_status, count(*) FROM ballot.ballot_dataset
                           GROUP BY record_status ORDER BY record_status"""
                    ).fetchall(),
                    [("active", 1), ("superseded", 1)],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*), sum(preference_count) FROM ballot.ballot"
                    ).fetchone(),
                    (293474, 2166773),
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
