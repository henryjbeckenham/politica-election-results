import csv
import hashlib
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path

import duckdb

from politica_erd.app.explorer import ElectionExplorer
from politica_erd.app.publication import PublicationFilters, VisualisationFeedService
from politica_erd.verify_publication import verify_senate_group_publication
from tests.test_stage4_workflow import ELECTION_ID, make_minimal_database
from tests.test_stage9_explorer import ACTIVE_REVISION, seed_explorer_fixture
from tests.test_stage10_publication import seed_senate_group_fixture
from tests.test_stage11_public_results import STATE_NAMES, seed_all_state_senate_fixture


SENATE_CHAMBER_ID = "election_chamber_test_2026_senate_fallback"
SENATE_CONTEST_ID = "contest_test_2026_senate_nsw_fallback"


def _uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"politica-stage11.2/{label}")


def seed_legacy_party_total_fixture(path: Path, *, add_group_total: bool = False) -> None:
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            "INSERT INTO core.election_chamber VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                SENATE_CHAMBER_ID,
                ELECTION_ID,
                "chamber_senate",
                None,
                6,
                True,
                "final",
                "active",
            ],
        )
        connection.execute(
            "INSERT INTO core.contest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                SENATE_CONTEST_ID,
                SENATE_CHAMBER_ID,
                None,
                "NSW",
                "New South Wales",
                6,
                None,
                "declared",
                False,
                None,
                "final",
                "active",
            ],
        )
        for measure, integer_value, decimal_value in (
            ("votes", 1_234_567, None),
            ("vote_share", None, "41.25"),
        ):
            connection.execute(
                """INSERT INTO results.vote_result
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    _uuid(f"party-total-{measure}"),
                    ELECTION_ID,
                    SENATE_CONTEST_ID,
                    None,
                    "party",
                    None,
                    None,
                    "party_test",
                    None,
                    "party_total",
                    "total",
                    measure,
                    integer_value,
                    decimal_value,
                    "reported",
                    "official_reported",
                    "final",
                    ACTIVE_REVISION,
                    "official-2025-party-total.csv#row=2",
                    None,
                    "active",
                ],
            )
        if add_group_total:
            group_id = _uuid("group")
            connection.execute(
                "INSERT INTO core.ballot_group VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    group_id,
                    SENATE_CONTEST_ID,
                    "A",
                    "A",
                    "Governed Group",
                    "party_test",
                    None,
                    False,
                    "final",
                    "active",
                ],
            )
            connection.execute(
                """INSERT INTO results.vote_result
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    _uuid("group-total-votes"),
                    ELECTION_ID,
                    SENATE_CONTEST_ID,
                    None,
                    "ballot_group",
                    None,
                    group_id,
                    None,
                    None,
                    "group_total",
                    "total",
                    "votes",
                    2_000_000,
                    None,
                    "reported",
                    "official_reported",
                    "final",
                    ACTIVE_REVISION,
                    "official-2025-group-total.csv#row=2",
                    None,
                    "active",
                ],
            )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


class Stage112SenateGroupFallbackTests(unittest.TestCase):
    def _service(self, database: Path) -> VisualisationFeedService:
        digest = hashlib.sha256(database.read_bytes()).hexdigest()
        explorer = ElectionExplorer(
            lambda: database,
            lambda _: database.parent,
            app_version="1.1.2",
        )
        return VisualisationFeedService(
            explorer,
            lambda: {
                "release_id": "release_stage11_2_test",
                "database_sha256": digest,
                "application_version": "1.1.2",
                "schema_version": "0.2.0",
            },
        )

    def _database(self, directory: str, *, add_group_total: bool = False) -> Path:
        database = Path(directory) / "stage11_2.duckdb"
        make_minimal_database(database)
        seed_explorer_fixture(database)
        seed_legacy_party_total_fixture(database, add_group_total=add_group_total)
        return database

    def test_legacy_party_totals_populate_the_fixed_group_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            original_sha256 = hashlib.sha256(database.read_bytes()).hexdigest()
            publication = self._service(database).build(
                "senate_group_results",
                PublicationFilters(election_id=ELECTION_ID, state="NSW"),
            )
            self.assertEqual(publication.row_count, 1)
            document = json.loads(publication.json_bytes)
            row = document["data"][0]
            self.assertEqual(row["state"], "NSW")
            self.assertEqual(row["result_type"], "group_total")
            self.assertEqual(row["subject_type"], "party")
            self.assertEqual(row["party_id"], "party_test")
            self.assertEqual(row["votes"], 1_234_567)
            exported = list(
                csv.DictReader(io.StringIO(publication.csv_bytes.decode("utf-8-sig")))
            )
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0]["state"], "NSW")
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), original_sha256)

    def test_governed_group_totals_take_precedence_per_state(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory, add_group_total=True)
            original_sha256 = hashlib.sha256(database.read_bytes()).hexdigest()
            publication = self._service(database).build(
                "senate_group_results",
                PublicationFilters(election_id=ELECTION_ID, state="NSW"),
            )
            self.assertEqual(publication.row_count, 1)
            document = json.loads(publication.json_bytes)
            row = document["data"][0]
            self.assertEqual(row["subject_type"], "ballot_group")
            self.assertEqual(row["votes"], 2_000_000)
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), original_sha256)

    def test_active_release_verifier_requires_all_eight_jurisdictions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "stage11_2_all_states.duckdb"
            make_minimal_database(database)
            seed_explorer_fixture(database)
            seed_senate_group_fixture(database)
            seed_all_state_senate_fixture(database)
            report = verify_senate_group_publication(database)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(set(report["senate_group_rows_by_state"]), set(STATE_NAMES))
            self.assertTrue(all(report["senate_group_rows_by_state"].values()))


if __name__ == "__main__":
    unittest.main()
