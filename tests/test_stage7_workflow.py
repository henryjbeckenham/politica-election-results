import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import duckdb

from politica_erd.app.config import AppSettings
from politica_erd.app.aec_senate_summaries import _validate_participation
from politica_erd.app.detection import AdapterCatalogue
from politica_erd.app.service import JobService
from politica_erd.app.transformers import get_transformer
from politica_erd.build import PROJECT_ROOT
from tests.test_stage6_workflow import make_registration_base


OFFICIAL_ROOT = PROJECT_ROOT / "data/raw/aec/2025_federal/31496/final"
FILENAMES = (
    "SenateFirstPrefsByStateByVoteTypeDownload-31496.csv",
    "SenateFirstPrefsByDivisionByVoteTypeDownload-31496.csv",
    "SenateSenatorsElectedDownload-31496.csv",
    "GeneralEnrolmentByStateDownload-31496.csv",
    "SenateInformalByStateDownload-31496.csv",
    "SenateTurnoutByStateDownload-31496.csv",
    "SenateVotesCountedByStateDownload-31496.csv",
    "SenateInformalByDivisionDownload-31496.csv",
    "SenateTurnoutByDivisionDownload-31496.csv",
    "SenateVotesCountedByDivisionDownload-31496.csv",
)
EXPECTED_SHA256 = {
    "SenateFirstPrefsByStateByVoteTypeDownload-31496.csv": "f80100e04ac3f57783bd8d052f9bd6706f7deb3d9224ef4b6965045695f6583b",
    "SenateFirstPrefsByDivisionByVoteTypeDownload-31496.csv": "7b770c8539ea802713f25b7586e8ee23ca436dd1e487886c2d724af03669e5fd",
    "SenateSenatorsElectedDownload-31496.csv": "e1c24bfd933feb8b23ea6e451d07e39f70f7af59a1cf991c0e1e1ced42071c01",
    "GeneralEnrolmentByStateDownload-31496.csv": "5ad4631b7283944829185b41522fea98c8cf6c6ac540c8f21ee9e0b8d52dd60b",
    "SenateInformalByStateDownload-31496.csv": "f2f356b903cb1f7f03078162cdabf93244c03e099f49ff2a44c07a62f7cfb7c0",
    "SenateTurnoutByStateDownload-31496.csv": "a23862f197d2fa231ccad3745c9194d82c95f4e9650022687f99e66c4d6b312a",
    "SenateVotesCountedByStateDownload-31496.csv": "8484018813173f027d2530cc7c1a7dcc3d54046d39abbe2621d69af511e48215",
    "SenateInformalByDivisionDownload-31496.csv": "ecb43bf26a451cf143880dfea9b28735aa0007a54d23aea80638e4dec2232ee7",
    "SenateTurnoutByDivisionDownload-31496.csv": "d6649177a9c3a7c381954be7abd954dbd06a935887a32127ee7b3c92dab16100",
    "SenateVotesCountedByDivisionDownload-31496.csv": "145eaee14bac61487ad411d68830667b755d736ed6eaeaf4042d0387e14d2818",
}


def upload_files(service: JobService, job: dict, filenames: tuple[str, ...]) -> dict:
    uploads = []
    for index, filename in enumerate(filenames, start=1):
        destination = service.store.job_dir(job["job_id"]) / "uploads" / filename
        shutil.copy2(OFFICIAL_ROOT / filename, destination)
        payload = destination.read_bytes()
        uploads.append({
            "upload_id": f"official-{index}", "original_name": filename,
            "stored_name": filename, "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "content_type": "text/csv",
        })
    return service.finalise_uploads(job["job_id"], uploads)


def bootstrap_official(root: Path) -> Path:
    base = root / "registration_base.duckdb"
    make_registration_base(base)
    service = JobService(AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "bootstrap_app"))
    job = service.begin_aec_election_bootstrap(
        election_name="2025 Australian federal election", official_event_id="31496",
        election_date="2025-05-03", election_type_code="general",
        publication_phase="final", contest_status="declared",
    )
    inspected = upload_files(
        service, job,
        ("HouseCandidatesDownload-31496.csv", "SenateCandidatesDownload-31496.csv"),
    )
    service.queue_execution(inspected["job_id"])
    completed = service.execute_job(inspected["job_id"])
    if completed["state"] != "validated":
        raise AssertionError(completed)
    return service.store.job_dir(inspected["job_id"]) / "work/database.duckdb"


class Stage7WorkflowTests(unittest.TestCase):
    def test_registered_generic_senate_routes(self):
        keys = {
            "senate_first_preferences_state", "senate_first_preferences_division",
            "senate_elected", "enrolment_state", "senate_participation",
            "senate_participation_division",
        }
        for key in keys:
            self.assertIsNotNone(get_transformer("adapter_aec_2025_v1", key))
        catalogue = AdapterCatalogue(PROJECT_ROOT / "config/adapters")
        for filename in FILENAMES:
            lines = (OFFICIAL_ROOT / filename).read_text(encoding="utf-8-sig").splitlines()
            headers = lines[1].split(",")
            detected = catalogue.detect(filename.replace("31496", "99999"), headers, "authority_aec")
            self.assertEqual(detected["status"], "matched", filename)
            self.assertEqual(detected["selection"]["mapping_entities"], [], filename)
            self.assertIsNotNone(
                get_transformer(
                    detected["selection"]["adapter_id"],
                    detected["selection"]["dataset_key"],
                ),
                filename,
            )

    def test_official_sources_are_checksum_pinned(self):
        for filename, expected in EXPECTED_SHA256.items():
            self.assertEqual(hashlib.sha256((OFFICIAL_ROOT / filename).read_bytes()).hexdigest(), expected)

    def test_invalid_senate_participation_arithmetic_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "do not reconcile"):
            _validate_participation(
                "SenateInformalByStateDownload",
                {
                    "FormalVotes": "100",
                    "InformalVotes": "5",
                    "TotalVotes": "104",
                    "InformalPercent": "4.81",
                },
                "synthetic#row=3",
            )

    def test_all_ten_complete_official_files_execute_canonically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = bootstrap_official(root)
            base_hash = hashlib.sha256(base.read_bytes()).hexdigest()
            service = JobService(AppSettings(project_root=PROJECT_ROOT, base_database=base, app_data=root / "stage7_app"))
            job = service.begin_job(name="Official 2025 Senate summary group", authority_id="authority_aec",
                                    election_id="election_fed_2025_05_03_general")
            inspected = upload_files(service, job, FILENAMES)
            self.assertTrue(all(item["detection"]["canonical_capable"] for item in inspected["datasets"]))
            service.queue_execution(inspected["job_id"])
            completed = service.execute_job(inspected["job_id"])
            self.assertEqual(completed["state"], "validated", completed.get("error"))
            self.assertEqual(completed["validation"]["ruleset_version"], "stage7_v1")
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), base_hash)
            results = completed["execution"]["dataset_results"]
            self.assertEqual(sum(item["staged_rows"] for item in results.values()), 11824)
            database = service.store.job_dir(inspected["job_id"]) / "work/database.duckdb"
            connection = duckdb.connect(str(database), read_only=True)
            try:
                self.assertEqual(connection.execute("SELECT count(*) FROM results.vote_result WHERE record_status='active'").fetchone()[0], 67812)
                self.assertEqual(connection.execute("SELECT count(*) FROM results.participation_result WHERE record_status='active'").fetchone()[0], 1746)
                self.assertEqual(connection.execute("SELECT count(*) FROM results.contest_outcome WHERE record_status='active'").fetchone()[0], 40)
                self.assertEqual(connection.execute("SELECT count(*) FROM results.elected_member").fetchone()[0], 40)
                self.assertEqual(connection.execute("SELECT count(*) FROM core.ballot_group").fetchone()[0], 125)
                self.assertEqual(connection.execute("SELECT count(*) FROM core.ballot_group_membership").fetchone()[0], 330)
                self.assertEqual(connection.execute("SELECT count(*) FROM geography.election_reporting_unit WHERE source_reporting_unit_type='division'").fetchone()[0], 150)
            finally:
                connection.close()

            revision_service = JobService(
                AppSettings(project_root=PROJECT_ROOT, base_database=database, app_data=root / "revision_app")
            )
            revision_job = revision_service.begin_job(
                name="Revised Senate state first preferences",
                authority_id="authority_aec",
                election_id="election_fed_2025_05_03_general",
            )
            filename = FILENAMES[0]
            destination = revision_service.store.job_dir(revision_job["job_id"]) / "uploads" / filename
            payload = (OFFICIAL_ROOT / filename).read_bytes().replace(b"  [Event", b"   [Event", 1)
            destination.write_bytes(payload)
            revised = revision_service.finalise_uploads(
                revision_job["job_id"],
                [{
                    "upload_id": "revised-state-fp", "original_name": filename,
                    "stored_name": filename, "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(), "content_type": "text/csv",
                }],
            )
            revision_service.queue_execution(revised["job_id"])
            revised_completed = revision_service.execute_job(revised["job_id"])
            self.assertEqual(revised_completed["state"], "validated")
            revised_database = revision_service.store.job_dir(revised["job_id"]) / "work/database.duckdb"
            connection = duckdb.connect(str(revised_database), read_only=True)
            try:
                self.assertEqual(connection.execute("SELECT count(*) FROM results.vote_result WHERE record_status='active'").fetchone()[0], 67812)
                self.assertEqual(connection.execute("SELECT count(*) FROM results.vote_result WHERE record_status='superseded'").fetchone()[0], 2688)
                self.assertEqual(connection.execute("SELECT count(*) FROM core.ballot_group").fetchone()[0], 125)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
