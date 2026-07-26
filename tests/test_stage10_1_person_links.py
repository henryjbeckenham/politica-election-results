import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb

from politica_erd.app.config import AppSettings
from politica_erd.app.service import JobService
from politica_erd.correct_person_links import (
    TARGETS,
    PersonLinkCorrectionError,
    apply_person_link_correction,
    assess_person_identity_links,
    correct_active_release,
)
from politica_erd.ids import deterministic_uuid
from politica_erd.import_2025 import ReferenceMatcher
from tests.test_stage4_workflow import CHAMBER_ID, make_minimal_database


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _person_link_database(root: Path) -> Path:
    database = root / "person-links.duckdb"
    make_minimal_database(database, official_event_id="31496")
    now = datetime.now(timezone.utc)
    connection = duckdb.connect(str(database))
    try:
        for person in (
            ("person_anne_stanley", "Anne Stanley", "Anne", "Stanley", "a" * 64),
            ("person_luke_gosling", "Luke Gosling", "Luke", "Gosling", "b" * 64),
        ):
            connection.execute(
                """INSERT INTO sync.person VALUES
                   (?, ?, ?, ?, ?, NULL, NULL, 'Australia', TRUE,
                    'active', 'audited', ?, ?)""",
                [person[0], person[1], person[1], person[2], person[3], person[4], now],
            )
        for index, target in enumerate(TARGETS, start=1):
            candidacy = deterministic_uuid(
                "stage10_1_test_candidacy", target["official_candidate_id"]
            )
            outcome = deterministic_uuid(
                "stage10_1_test_outcome", target["official_candidate_id"]
            )
            member = deterministic_uuid(
                "stage10_1_test_member", target["official_candidate_id"]
            )
            connection.execute(
                """INSERT INTO core.contest VALUES
                   (?, ?, NULL, ?, ?, 1, NULL, 'declared', FALSE, NULL, 'final', 'active')""",
                [
                    target["contest_id"],
                    CHAMBER_ID,
                    target["contest_id"].rsplit("_", 1)[-1],
                    target["contest_name"],
                ],
            )
            connection.execute(
                """INSERT INTO core.candidacy VALUES
                   (?, ?, NULL, 'party_test', ?, ?, ?, ?, 'Labor', 'ALP',
                    'incumbent', 'accepted', 'unmatched', 'final', 'active')""",
                [
                    candidacy,
                    target["contest_id"],
                    target["official_candidate_id"],
                    f"{target['ballot_given_names']} {target['ballot_family_name']}",
                    target["ballot_given_names"],
                    target["ballot_family_name"],
                ],
            )
            connection.execute(
                """INSERT INTO results.contest_outcome VALUES
                   (?, ?, ?, 'elected', 1, NULL, 'final', ?, ?, 'active')""",
                [
                    outcome,
                    target["contest_id"],
                    candidacy,
                    f"source_revision_stage10_1_{index}",
                    f"HouseMembersElectedDownload-31496.csv#row={index}",
                ],
            )
            connection.execute(
                """INSERT INTO results.elected_member VALUES
                   (?, ?, 'election_fed_2025_05_03_general', ?, ?, NULL, 1, 'not_applicable')""",
                [member, outcome, target["contest_id"], candidacy],
            )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return database


class Stage101PersonLinkTests(unittest.TestCase):
    def test_first_given_and_family_fallback_is_unique_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database = _person_link_database(Path(directory))
            connection = duckdb.connect(str(database))
            try:
                matcher = ReferenceMatcher(connection)
                self.assertEqual(
                    matcher.person("Anne Maree", "STANLEY"),
                    ("person_anne_stanley", "matched"),
                )
                now = datetime.now(timezone.utc)
                connection.execute(
                    """INSERT INTO sync.person VALUES
                       ('person_anne_marie_stanley', 'Anne Marie Stanley',
                        'Anne Marie Stanley', 'Anne Marie', 'Stanley', NULL, NULL,
                        'Australia', TRUE, 'active', 'audited', ?, ?)""",
                    ["c" * 64, now],
                )
                matcher = ReferenceMatcher(connection)
                self.assertEqual(
                    matcher.person("Anne Maree", "STANLEY"),
                    (None, "conflict"),
                )
            finally:
                connection.close()

    def test_correction_links_candidacies_and_elected_members_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            database = _person_link_database(Path(directory))
            before_sha = _sha256(database)
            connection = duckdb.connect(str(database))
            try:
                before = assess_person_identity_links(connection)
                self.assertEqual(before["pending_target_count"], 2)
                report = apply_person_link_correction(
                    connection,
                    base_database_sha256=before_sha,
                    release_id="release_test_stage10_1",
                )
                self.assertEqual(report["status"], "CORRECTED")
                self.assertEqual(report["corrected_target_count"], 2)
                after = assess_person_identity_links(connection)
                self.assertEqual(after["pending_target_count"], 0)
                self.assertEqual(
                    {
                        (item["contest_name"], item["canonical_person_id"])
                        for item in after["targets"]
                    },
                    {
                        ("Werriwa", "person_anne_stanley"),
                        ("Solomon", "person_luke_gosling"),
                    },
                )
                second = apply_person_link_correction(
                    connection,
                    base_database_sha256=_sha256(database),
                    release_id="release_test_stage10_1_second",
                )
                self.assertEqual(second["status"], "NO_CHANGE")
            finally:
                connection.close()

    def test_existing_conflicting_person_link_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            database = _person_link_database(Path(directory))
            connection = duckdb.connect(str(database))
            try:
                connection.execute(
                    """UPDATE core.candidacy SET person_id='person_test'
                       WHERE official_candidate_id='41328'"""
                )
                with self.assertRaisesRegex(PersonLinkCorrectionError, "Refused to replace"):
                    assess_person_identity_links(connection)
            finally:
                connection.close()

    def test_publication_preserves_base_and_activates_validated_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _person_link_database(root)
            base_sha = _sha256(base)
            settings = AppSettings(
                project_root=root,
                base_database=base,
                app_data=root / "app",
            )
            validation = {
                "status": "PASS",
                "stage": "stage_10_1_test",
                "failures": [],
            }
            with patch(
                "politica_erd.correct_person_links.validate_database",
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
                self.assertEqual(
                    assess_person_identity_links(connection)["pending_target_count"], 0
                )
            finally:
                connection.close()
            self.assertTrue(
                (
                    governed.parents[2]
                    / "data/manifests/person_link_correction_1_0_1.json"
                ).is_file()
            )
            second = correct_active_release(settings)
            self.assertEqual(second["status"], "NO_CHANGE")


if __name__ == "__main__":
    unittest.main()
