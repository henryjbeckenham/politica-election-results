import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path

import duckdb
import yaml

from politica_erd.app.explorer import ElectionExplorer
from politica_erd.app.publication import (
    FEED_VERSION,
    PublicationFilters,
    VisualisationFeedService,
)
from politica_erd.build import PROJECT_ROOT
from tests.test_stage4_workflow import (
    CANDIDACY_ID,
    CONTEST_ID,
    ELECTION_ID,
    make_minimal_database,
)
from tests.test_stage9_explorer import ACTIVE_REVISION, seed_explorer_fixture


class Stage132ElectorateResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "electorates.duckdb"
        make_minimal_database(cls.database)
        seed_explorer_fixture(cls.database)
        cls.opponent_id = uuid.UUID("00000000-0000-5000-8000-000000013202")
        connection = duckdb.connect(str(cls.database))
        try:
            connection.execute(
                "UPDATE core.candidacy SET incumbent_status='not_incumbent' WHERE candidacy_id=?",
                [CANDIDACY_ID],
            )
            connection.execute(
                """INSERT INTO core.candidacy VALUES
                   (?, ?, NULL, NULL, '502', 'Former Member', 'Former', 'Member',
                    'Former Party', 'OLD', 'incumbent', 'accepted', 'unmatched',
                    'final', 'active')""",
                [cls.opponent_id, CONTEST_ID],
            )
            for index, candidacy, votes in (
                (13201, CANDIDACY_ID, 1_050),
                (13202, cls.opponent_id, 850),
            ):
                connection.execute(
                    """INSERT INTO results.vote_result VALUES
                       (?, ?, ?, NULL, 'candidate', ?, NULL, NULL, NULL, 'tcp',
                        'total', 'votes', ?, NULL, 'reported', 'official_reported',
                        'final', ?, ?, NULL, 'active')""",
                    [
                        uuid.UUID(f"00000000-0000-5000-8000-{index:012d}"),
                        ELECTION_ID,
                        CONTEST_ID,
                        candidacy,
                        votes,
                        ACTIVE_REVISION,
                        f"stage13-2.csv#row={index}",
                    ],
                )
            participation = (
                (13210, "enrolment", 2_100, None),
                (13211, "informal_votes", 200, None),
                (13212, "total_votes", 2_100, None),
                (13213, "turnout", 2_100, None),
                (13214, "turnout_percentage", None, "100.0"),
                (13215, "informality_percentage", None, "9.5238"),
            )
            for index, measure, integer_value, decimal_value in participation:
                connection.execute(
                    """INSERT INTO results.participation_result VALUES
                       (?, ?, ?, NULL, 'total', ?, ?, ?, 'reported',
                        'official_reported', 'final', ?, ?, NULL, 'active')""",
                    [
                        uuid.UUID(f"00000000-0000-5000-8000-{index:012d}"),
                        ELECTION_ID,
                        CONTEST_ID,
                        measure,
                        integer_value,
                        decimal_value,
                        ACTIVE_REVISION,
                        f"stage13-2-participation.csv#row={index}",
                    ],
                )
            connection.execute("CHECKPOINT")
        finally:
            connection.close()
        cls.database_sha256 = hashlib.sha256(cls.database.read_bytes()).hexdigest()
        cls.explorer = ElectionExplorer(
            lambda: cls.database,
            lambda _database: cls.root,
            app_version="1.3.6",
        )
        cls.feeds = VisualisationFeedService(
            cls.explorer,
            lambda: {
                "release_id": "release_stage13_2_test",
                "database_sha256": cls.database_sha256,
                "application_version": "1.3.6",
                "schema_version": "0.2.0",
            },
            composition_contract_path=(
                PROJECT_ROOT / "config" / "parliament_composition_48th.yml"
            ),
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_seat_feed_publishes_status_participation_and_count_metadata(self):
        self.assertEqual(FEED_VERSION, "1.8.0")
        result = self.feeds.build(
            "house_seat_results",
            PublicationFilters(election_id=ELECTION_ID, contest_id=CONTEST_ID),
        )
        self.assertEqual(result.row_count, 1)
        row = json.loads(result.json_bytes)["data"][0]
        self.assertEqual(row["seat_change_type"], "gained")
        self.assertEqual(row["seat_change_label"], "Gained from Former Party")
        self.assertEqual(row["defeated_incumbent_party_name"], "Former Party")
        self.assertEqual(row["enrolment"], 2_100)
        self.assertEqual(row["formal_votes"], 1_900)
        self.assertEqual(row["informal_votes"], 200)
        self.assertEqual(row["votes_counted"], 2_100)
        self.assertEqual(row["turnout_percentage"], 100.0)
        self.assertAlmostEqual(row["informality_percentage"], 9.5238)
        self.assertEqual(row["counted_percentage_of_enrolment"], 100.0)

    def test_candidate_feed_exposes_incumbency_without_mutating_release(self):
        result = self.feeds.build(
            "house_candidate_results",
            PublicationFilters(election_id=ELECTION_ID, contest_id=CONTEST_ID),
        )
        rows = json.loads(result.json_bytes)["data"]
        tcp = [row for row in rows if row["result_type"] == "tcp"]
        self.assertEqual(len(tcp), 2)
        self.assertEqual(
            {row["subject_name"]: row["incumbent_status"] for row in tcp},
            {"Test Candidate": "not_incumbent", "Former Member": "incumbent"},
        )
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), self.database_sha256)

    def test_contract_and_frontend_register_full_electorate_cards(self):
        contract = yaml.safe_load(
            (PROJECT_ROOT / "config" / "visualisation_contract.yml").read_text(
                encoding="utf-8"
            )
        )
        registered = {
            item["visualisation_id"]: item for item in contract["visualisations"]
        }
        electorate = registered["house_electorate_results"]
        self.assertEqual(contract["contract_version"], "2.0.0")
        self.assertEqual(electorate["component"], "electorate-result-cards")
        self.assertEqual(
            set(electorate["metrics"]),
            {
                "winning_margin",
                "electorate_primary_vote",
                "electorate_tcp",
                "electorate_tpp",
                "electorate_count_metadata",
            },
        )
        results = (
            PROJECT_ROOT / "visualisation/src/components/results.js"
        ).read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "visualisation/src/politica.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="pr-seat-cards"', results)
        self.assertIn("Two-candidate preferred", results)
        self.assertIn("Two-party preferred", results)
        self.assertIn("Show primary votes", results)
        self.assertIn("Count metadata", results)
        self.assertIn(".pr-result-card", css)
        self.assertIn(".pr-primary-expander", css)


if __name__ == "__main__":
    unittest.main()
