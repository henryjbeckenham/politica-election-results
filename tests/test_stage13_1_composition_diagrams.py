import collections
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import duckdb
import yaml

from politica_erd.app.explorer import ElectionExplorer
from politica_erd.app.config import discover_project_root
from politica_erd.app.publication import (
    FEEDS,
    FEED_VERSION,
    PublicationFilters,
    VisualisationFeedService,
)
from politica_erd.build import PROJECT_ROOT
from tests.test_stage4_workflow import ELECTION_ID, make_minimal_database
from tests.test_stage9_explorer import seed_explorer_fixture


class Stage131CompositionDiagramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "composition.duckdb"
        make_minimal_database(cls.database)
        seed_explorer_fixture(cls.database)
        connection = duckdb.connect(str(cls.database))
        try:
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
                "release_id": "release_stage13_1_test",
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

    def test_snapshot_is_complete_reconciled_and_officially_sourced(self):
        path = PROJECT_ROOT / "config" / "parliament_composition_48th.yml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(document["contract_version"], "1.0.0")
        self.assertEqual(document["parliament_number"], 48)
        self.assertEqual(str(document["snapshot_as_at"]), "2026-05-14")
        self.assertEqual(document["seat_count"], 76)
        self.assertEqual(len(document["senators"]), 76)
        self.assertEqual(
            collections.Counter(row["state"] for row in document["senators"]),
            {"ACT": 2, "NSW": 12, "NT": 2, "QLD": 12, "SA": 12, "TAS": 12, "VIC": 12, "WA": 12},
        )
        self.assertEqual(
            collections.Counter(row["party"] for row in document["senators"]),
            {"ALP": 30, "LP": 21, "AG": 10, "ON": 4, "NATS": 3, "LNP": 2, "IND": 2, "AV": 1, "CLP": 1, "JLN": 1, "UAP": 1},
        )
        self.assertEqual(len({row["person_id"] for row in document["senators"]}), 76)
        self.assertEqual(document["source_authority"], "Parliament of Australia")
        self.assertTrue(document["source_url"].startswith("https://www.aph.gov.au/"))

    def test_non_editable_install_resolves_the_operator_project_not_site_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Politica_Election_Results_Database"
            nested = root / "tests" / "fixtures"
            nested.mkdir(parents=True)
            (root / "config").mkdir()
            (root / "data").mkdir()
            (root / "pyproject.toml").write_text(
                '[project]\nname = "politica-election-results-database"\n',
                encoding="utf-8",
            )
            simulated_site_packages = (
                Path(temporary) / ".venv/lib/python3.12/site-packages"
            )
            simulated_site_packages.mkdir(parents=True)
            with mock.patch.dict(
                os.environ, {"POLITICA_PROJECT_ROOT": ""}, clear=False
            ):
                self.assertEqual(
                    discover_project_root(nested, simulated_site_packages),
                    root.resolve(),
                )
            environment = os.environ.copy()
            environment["POLITICA_PROJECT_ROOT"] = str(root)
            environment["PYTHONPATH"] = os.pathsep.join(
                value for value in sys.path if value
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from politica_erd.build import PROJECT_ROOT; print(PROJECT_ROOT)",
                ],
                cwd=simulated_site_packages.parent,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(completed.stdout.strip(), str(root.resolve()))

    def test_fixed_feed_publishes_all_76_members_and_state_filters(self):
        self.assertEqual(FEED_VERSION, "1.8.0")
        self.assertIn("senate_composition", FEEDS)
        full = self.feeds.build(
            "senate_composition",
            PublicationFilters(election_id=ELECTION_ID),
        )
        document = json.loads(full.json_bytes)
        self.assertEqual(full.row_count, 76)
        self.assertEqual(len(document["data"]), 76)
        self.assertEqual(
            {row["bloc"] for row in document["data"]},
            {"government", "opposition", "crossbench"},
        )
        self.assertEqual(
            document["manifest"]["supplemental_contract"]["contract_sha256"],
            self.feeds.composition_contract_sha256,
        )
        act = self.feeds.build(
            "senate_composition",
            PublicationFilters(election_id=ELECTION_ID, state="ACT"),
        )
        self.assertEqual(act.row_count, 2)
        self.assertEqual(
            {row["state"] for row in json.loads(act.json_bytes)["data"]},
            {"ACT"},
        )
        self.assertEqual(
            hashlib.sha256(self.database.read_bytes()).hexdigest(),
            self.database_sha256,
        )

    def test_contract_and_frontend_register_both_chambers(self):
        contract = yaml.safe_load(
            (PROJECT_ROOT / "config" / "visualisation_contract.yml").read_text(
                encoding="utf-8"
            )
        )
        registered = {
            item["visualisation_id"]: item for item in contract["visualisations"]
        }
        self.assertEqual(contract["contract_version"], "2.0.0")
        self.assertEqual(registered["house_composition"]["component"], "semicircle-chamber")
        self.assertEqual(registered["senate_composition"]["component"], "semicircle-chamber")
        self.assertEqual(registered["senate_composition"]["status"], "available")
        chamber = (
            PROJECT_ROOT / "visualisation/src/foundation/chamber.js"
        ).read_text(encoding="utf-8")
        results = (
            PROJECT_ROOT / "visualisation/src/components/results.js"
        ).read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "visualisation/src/politica.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("semicircleLayout", chamber)
        self.assertIn('id="pr-house-chamber"', results)
        self.assertIn('id="pr-senate-chamber"', results)
        self.assertIn('aria-pressed', results)
        self.assertIn(".pr-chamber-seat", css)
        self.assertIn("prefers-reduced-motion", css)


if __name__ == "__main__":
    unittest.main()
