import hashlib
import json
import mimetypes
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb

from politica_erd.app.config import AppSettings
from politica_erd.app.explorer import ElectionExplorer
from politica_erd.app.publication import PublicationFilters, VisualisationFeedService
from politica_erd.app.service import JobService
from politica_erd.build import PROJECT_ROOT
from politica_erd.complete_senate_groups import (
    EXPECTED_ACTIVE_FACTS,
    EXPECTED_NATIONAL_GROUPS,
    EXPECTED_STATE_GROUPS,
    OFFICIAL_SOURCES,
    assess_senate_group_completion,
    complete_active_release,
)
from politica_erd.validate import validate_database
from tests.test_stage7_workflow import bootstrap_official
from tests.test_stage8_workflow import add_ballot_structure


OFFICIAL_ROOT = PROJECT_ROOT / "data/raw/aec/2025_federal/31496/final"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _register_group_sources_without_transform(root: Path, base: Path) -> Path:
    service = JobService(
        AppSettings(
            project_root=PROJECT_ROOT,
            base_database=base,
            app_data=root / "source-registration-app",
        )
    )
    job = service.begin_job(
        name="Register legacy 2025 group sources",
        authority_id="authority_aec",
        election_id="election_fed_2025_05_03_general",
    )
    uploads = []
    for index, filename in enumerate(OFFICIAL_SOURCES, start=1):
        source = OFFICIAL_ROOT / filename
        destination = service.store.job_dir(job["job_id"]) / "uploads" / filename
        shutil.copy2(source, destination)
        uploads.append(
            {
                "upload_id": f"legacy-{index}",
                "original_name": filename,
                "stored_name": filename,
                "size_bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
                "content_type": mimetypes.guess_type(filename)[0] or "text/csv",
            }
        )
    inspected = service.finalise_uploads(job["job_id"], uploads)
    if inspected["state"] != "ready":
        raise AssertionError(inspected)
    database, _ = service._copy_governed_database(job["job_id"])
    import_run_id = str(uuid.uuid4())
    connection = service._connect(database)
    try:
        service._register_run_and_sources(connection, inspected, import_run_id)
        connection.execute(
            """UPDATE provenance.import_run
               SET import_status='published', completed_at=? WHERE import_run_id=?""",
            [datetime.now(timezone.utc), import_run_id],
        )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return database


class Stage113SenateGroupCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        ballot_base = add_ballot_structure(
            cls.root,
            bootstrap_official(cls.root),
        )
        registered = _register_group_sources_without_transform(cls.root, ballot_base)
        # Keep the fixture in a release-shaped directory so its external-artifact
        # root is the temporary test release, never the developer's real project.
        cls.legacy = (
            cls.root
            / "legacy-release"
            / "data"
            / "database"
            / "politica_election_results.duckdb"
        )
        cls.legacy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(registered, cls.legacy)
        cls.legacy_sha256 = _sha256(cls.legacy)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_completion_reuses_registered_sources_and_activates_validated_release(self):
        settings = AppSettings(
            project_root=PROJECT_ROOT,
            base_database=self.legacy,
            app_data=self.root / "completion-app",
        )
        validation = {
            "status": "PASS",
            "stage": "stage_11_3_test",
            "failures": [],
        }
        with patch(
            "politica_erd.validate.validate_database",
            return_value=validation,
        ):
            result = complete_active_release(settings)
        self.assertEqual(result["status"], "COMPLETED_AND_ACTIVATED")
        self.assertEqual(_sha256(self.legacy), self.legacy_sha256)
        self.assertEqual(result["before"]["status"], "EMPTY")
        self.assertEqual(result["after"]["status"], "COMPLETE")
        self.assertEqual(
            result["after"]["active_group_total_facts"], EXPECTED_ACTIVE_FACTS
        )
        self.assertEqual(result["after"]["state_group_rows"], EXPECTED_STATE_GROUPS)
        self.assertEqual(
            result["after"]["national_group_rows"], EXPECTED_NATIONAL_GROUPS
        )
        self.assertEqual(result["after"]["state_contests"], 8)

        active = JobService(settings).governed_database()
        connection = duckdb.connect(str(active), read_only=True)
        try:
            assessment = assess_senate_group_completion(connection)
            revisions = connection.execute(
                """SELECT count(DISTINCT revision.source_revision_id),
                          count(DISTINCT revision.sha256)
                   FROM provenance.source_file_revision revision
                   WHERE lower(revision.sha256) IN (?, ?)""",
                sorted(OFFICIAL_SOURCES.values()),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(assessment["status"], "COMPLETE")
        self.assertEqual(revisions, (2, 2))

        explorer = ElectionExplorer(
            lambda: active,
            lambda _: active.parents[2],
            app_version="1.1.3",
        )
        feeds = VisualisationFeedService(
            explorer,
            lambda: {
                "release_id": result["release_id"],
                "database_sha256": result["database_sha256"],
                "application_version": "1.1.3",
                "schema_version": "0.2.0",
            },
        )
        published = feeds.build(
            "senate_group_results",
            PublicationFilters(election_id="election_fed_2025_05_03_general"),
        )
        rows = json.loads(published.json_bytes)["data"]
        self.assertEqual(len(rows), EXPECTED_STATE_GROUPS)
        self.assertEqual({row["state"] for row in rows}, {
            "ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"
        })
        self.assertEqual({row["subject_type"] for row in rows}, {"source_group"})

        # This reduced fixture intentionally lacks the full Stage 2 totals, but
        # the validator must still add all 1,872 richer facts to the official
        # 213,328-row baseline rather than treating same-revision facts as old.
        validation_report = validate_database(active)
        count_failure = next(
            failure
            for failure in validation_report["failures"]
            if failure["check"] == "stage_2_expected_counts"
        )
        self.assertEqual(count_failure["expected"]["vote_results"], 215_200)

        unchanged = complete_active_release(settings)
        self.assertEqual(unchanged["status"], "NO_CHANGE")


if __name__ == "__main__":
    unittest.main()
