import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path

import duckdb
import yaml

from politica_erd.app.explorer import ElectionExplorer
from politica_erd.app.publication import FEEDS, FEED_VERSION, PublicationFilters, VisualisationFeedService
from politica_erd.build import PROJECT_ROOT
from tests.test_stage4_workflow import ELECTION_ID, make_minimal_database
from tests.test_stage9_explorer import ACTIVE_REVISION, seed_explorer_fixture
from tests.test_stage10_publication import seed_senate_group_fixture


SENATE_CONTEST = "contest_test_2026_senate_nsw"


def uid(value: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-5000-8000-{value:012d}")


def seed_senate_count_fixture(path: Path) -> None:
    connection = duckdb.connect(str(path))
    try:
        candidates = ((12001, "Candidate A", "A"), (12002, "Candidate B", "B"))
        for value, name, official_id in candidates:
            connection.execute(
                "INSERT INTO core.candidacy VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [uid(value), SENATE_CONTEST, None, "party_test", official_id, name, name, name, "Test Party", "TST", "not_incumbent", "accepted", "matched", "final", "active"],
            )
        for round_number, action, quota in ((1, "first_preferences", 1000), (2, "exclusion", 1000)):
            round_id = uid(12100 + round_number)
            connection.execute(
                'INSERT INTO "count".count_round VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [round_id, SENATE_CONTEST, round_number, f"Count {round_number}", action, quota, 1, "AEC DOP", None, "Test count", "final", ACTIVE_REVISION, f"dop.csv#count={round_number}"],
            )
            totals = (900, 600) if round_number == 1 else (1100, 0)
            statuses = ("continuing", "continuing") if round_number == 1 else ("elected", "excluded")
            for index, ((candidate_value, _, _), total, status) in enumerate(zip(candidates, totals, statuses)):
                connection.execute(
                    'INSERT INTO "count".count_candidate_total VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    [uid(12200 + round_number * 10 + index), round_id, uid(candidate_value), total, total, total, status, "reported", ACTIVE_REVISION, f"dop.csv#count={round_number}&candidate={index}"],
                )
        round_two = uid(12102)
        movements = ((uid(12001), 200, False), (uid(12002), -600, False), (None, 5, True))
        for index, (candidate_id, votes, exhausted) in enumerate(movements):
            connection.execute(
                'INSERT INTO "count".preference_transfer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [uid(12300 + index), round_two, None, candidate_id, abs(votes), votes, exhausted, "reported", ACTIVE_REVISION, f"dop.csv#movement={index}"],
            )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


class Stage135SenateVisualisationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "stage13_5.duckdb"
        make_minimal_database(cls.database)
        seed_explorer_fixture(cls.database)
        seed_senate_group_fixture(cls.database)
        seed_senate_count_fixture(cls.database)
        cls.database_sha256 = hashlib.sha256(cls.database.read_bytes()).hexdigest()
        cls.explorer = ElectionExplorer(lambda: cls.database, lambda _database: cls.root, app_version="1.3.6")
        cls.feeds = VisualisationFeedService(
            cls.explorer,
            lambda: {"release_id": "release_stage13_5_test", "database_sha256": cls.database_sha256, "application_version": "1.3.6", "schema_version": "0.2.0"},
            composition_contract_path=PROJECT_ROOT / "config" / "parliament_composition_48th.yml",
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_count_progress_and_movement_feeds_are_governed_and_read_only(self):
        self.assertEqual(FEED_VERSION, "1.8.0")
        self.assertIn("senate_count_movements", FEEDS)
        progress = self.feeds.build("senate_count_progress", PublicationFilters(election_id=ELECTION_ID, state="NSW"))
        progress_rows = json.loads(progress.json_bytes)["data"]
        self.assertEqual(len(progress_rows), 4)
        self.assertEqual({row["quota_value"] for row in progress_rows}, {1000.0})
        movements = self.feeds.build("senate_count_movements", PublicationFilters(election_id=ELECTION_ID, state="NSW"))
        movement_rows = json.loads(movements.json_bytes)["data"]
        self.assertEqual(len(movement_rows), 3)
        self.assertEqual(sum(row["exhausted"] for row in movement_rows), 1)
        self.assertEqual({row["round_number"] for row in movement_rows}, {2})
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), self.database_sha256)

    def test_contract_and_frontend_register_the_complete_senate_suite(self):
        contract = yaml.safe_load((PROJECT_ROOT / "config" / "visualisation_contract.yml").read_text(encoding="utf-8"))
        self.assertEqual(contract["contract_version"], "2.0.0")
        visualisations = {row["visualisation_id"]: row for row in contract["visualisations"]}
        expected = {
            "senate_state_delegations",
            "senate_count_animation",
            "senate_quota_progress",
            "senate_transfer_movements",
            "senate_candidate_milestones",
            "senate_elected_timeline",
        }
        self.assertTrue(expected.issubset(visualisations))
        self.assertIn("unique origin candidate", contract["capability_boundaries"]["senate_transfer_flows"])
        source = (PROJECT_ROOT / "visualisation/src/components/results.js").read_text(encoding="utf-8")
        senate = (PROJECT_ROOT / "visualisation/src/foundation/senate.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "visualisation/src/politica.css").read_text(encoding="utf-8")
        self.assertIn('id="pr-count-play"', source)
        self.assertIn('id="pr-count-round"', source)
        self.assertIn("senate_count_movements", source)
        self.assertIn("export function senateMilestones", senate)
        self.assertIn(".pr-transfer-bar.is-outflow", css)
        self.assertIn("prefers-reduced-motion", css)


if __name__ == "__main__":
    unittest.main()
