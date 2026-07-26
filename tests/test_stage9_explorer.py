import csv
import hashlib
import http.client
import io
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import uvicorn

from politica_erd.app.api import create_app
from politica_erd.app.config import AppSettings
from politica_erd.app.explorer import (
    DATASET_LABELS,
    ElectionExplorer,
    ExplorerFilters,
    ExportTooLargeError,
)
from politica_erd.build import PROJECT_ROOT
from tests.test_stage4_workflow import (
    CANDIDACY_ID,
    CHAMBER_ID,
    CONTEST_ID,
    ELECTION_ID,
    make_minimal_database,
)


ACTIVE_REVISION = "source_revision_stage9_active"
SUPERSEDED_REVISION = "source_revision_stage9_superseded"


def _uuid(index: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-5000-8000-{index:012d}")


def seed_explorer_fixture(path: Path) -> None:
    connection = duckdb.connect(str(path))
    now = datetime.now(timezone.utc)
    try:
        connection.execute(
            """INSERT INTO provenance.source_file
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                "source_file_stage9",
                "authority_aec",
                ELECTION_ID,
                None,
                "Stage 9 explorer fixture",
                "test_results",
                "house",
                "division",
                "published",
                "Read-only explorer test source",
            ],
        )
        for revision_id, revision_number, status, sha in (
            (ACTIVE_REVISION, 2, "active", "a" * 64),
            (SUPERSEDED_REVISION, 1, "superseded", "b" * 64),
        ):
            connection.execute(
                """INSERT INTO provenance.source_file_revision
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    revision_id,
                    "source_file_stage9",
                    revision_number,
                    None,
                    f"stage9-{revision_number}.csv",
                    f"data/raw/stage9-{revision_number}.csv",
                    "text/csv",
                    "utf-8",
                    ",",
                    None,
                    100,
                    1,
                    sha,
                    now,
                    now,
                    "final",
                    None,
                    SUPERSEDED_REVISION if revision_number == 2 else None,
                    status,
                ],
            )
        vote_rows = (
            (_uuid(9001), "votes", 1_000, None, "active", ACTIVE_REVISION),
            (_uuid(9002), "vote_share", None, "52.5", "active", ACTIVE_REVISION),
            (_uuid(9003), "swing", None, "1.2", "active", ACTIVE_REVISION),
            (_uuid(9004), "votes", 900, None, "superseded", SUPERSEDED_REVISION),
        )
        for result_id, measure, integer_value, decimal_value, status, revision in vote_rows:
            connection.execute(
                """INSERT INTO results.vote_result
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    result_id,
                    ELECTION_ID,
                    CONTEST_ID,
                    None,
                    "candidate",
                    CANDIDACY_ID,
                    None,
                    None,
                    None,
                    "first_preference",
                    "total",
                    measure,
                    integer_value,
                    decimal_value,
                    "reported",
                    "official_reported",
                    "final",
                    revision,
                    "stage9.csv#row=2",
                    None,
                    status,
                ],
            )
        connection.execute(
            """INSERT INTO results.participation_result
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                _uuid(9010),
                ELECTION_ID,
                CONTEST_ID,
                None,
                "total",
                "formal_votes",
                1_900,
                None,
                "reported",
                "official_reported",
                "final",
                ACTIVE_REVISION,
                "stage9.csv#row=3",
                None,
                "active",
            ],
        )
        connection.execute(
            """INSERT INTO results.contest_outcome
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                _uuid(9020),
                CONTEST_ID,
                CANDIDACY_ID,
                "elected",
                1,
                now,
                "final",
                ACTIVE_REVISION,
                "stage9.csv#row=4",
                "active",
            ],
        )
        active_round = _uuid(9030)
        superseded_round = _uuid(9031)
        connection.execute(
            """INSERT INTO "count".count_round
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                active_round,
                CONTEST_ID,
                1,
                "Count 1",
                "initial_count",
                "950",
                None,
                None,
                None,
                None,
                "final",
                ACTIVE_REVISION,
                "stage9.csv#row=5",
            ],
        )
        connection.execute(
            """INSERT INTO "count".count_round
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                superseded_round,
                CONTEST_ID,
                1,
                "Count 1",
                "initial_count",
                "900",
                None,
                None,
                None,
                None,
                "final",
                SUPERSEDED_REVISION,
                "stage9-old.csv#row=5",
            ],
        )
        for index, round_id, revision, total in (
            (9040, active_round, ACTIVE_REVISION, "1000"),
            (9041, superseded_round, SUPERSEDED_REVISION, "900"),
        ):
            connection.execute(
                """INSERT INTO "count".count_candidate_total
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    _uuid(index),
                    round_id,
                    CANDIDACY_ID,
                    int(total),
                    total,
                    total,
                    "continuing",
                    "reported",
                    revision,
                    "stage9.csv#row=6",
                ],
            )
        connection.execute(
            """INSERT INTO "count".preference_transfer
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                _uuid(9050),
                active_round,
                None,
                CANDIDACY_ID,
                100,
                "100",
                False,
                "reported",
                ACTIVE_REVISION,
                "stage9.csv#row=7",
            ],
        )
        for index, revision, status, rows in (
            (9060, ACTIVE_REVISION, "active", 1_234),
            (9061, SUPERSEDED_REVISION, "superseded", 1_100),
        ):
            connection.execute(
                """INSERT INTO ballot.ballot_dataset
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    _uuid(index),
                    CHAMBER_ID,
                    CONTEST_ID,
                    revision,
                    "contest",
                    "ordinary",
                    "anonymous_source_row",
                    "No elector identity retained.",
                    "0.2.0",
                    rows,
                    status,
                ],
            )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


class Stage9ExplorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "stage9.duckdb"
        make_minimal_database(cls.database)
        seed_explorer_fixture(cls.database)
        cls.original_sha256 = hashlib.sha256(cls.database.read_bytes()).hexdigest()
        cls.settings = AppSettings(
            project_root=PROJECT_ROOT,
            base_database=cls.database,
            app_data=cls.root / "app",
            explorer_max_export_rows=1_000_000,
        )
        cls.application = create_app(cls.settings)
        cls.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cls.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        cls.socket.bind(("127.0.0.1", 0))
        cls.socket.listen(128)
        cls.port = cls.socket.getsockname()[1]
        cls.server = uvicorn.Server(
            uvicorn.Config(
                cls.application,
                host="127.0.0.1",
                port=cls.port,
                log_level="critical",
                lifespan="off",
            )
        )
        cls.thread = threading.Thread(
            target=cls.server.run,
            kwargs={"sockets": [cls.socket]},
            daemon=True,
        )
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not cls.server.started:
            raise RuntimeError("Stage 9 HTTP test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        if cls.application.state.instance_lock.is_locked:
            cls.application.state.instance_lock.release()
        cls.temporary.cleanup()

    @classmethod
    def request(cls, path):
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=60)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            payload = response.read()
            headers = {key.lower(): value for key, value in response.getheaders()}
            if "application/json" in headers.get("content-type", ""):
                body = json.loads(payload.decode("utf-8"))
            else:
                body = payload
            return response.status, headers, body
        finally:
            connection.close()

    def assert_database_unchanged(self):
        self.assertEqual(
            hashlib.sha256(self.database.read_bytes()).hexdigest(),
            self.original_sha256,
        )

    def test_catalogue_and_interface_expose_distinct_application_and_schema_versions(self):
        status, _, catalogue = self.request("/api/explorer/catalogue")
        self.assertEqual(status, 200)
        self.assertEqual(catalogue["application_version"], "1.8.0")
        self.assertEqual(catalogue["database"]["schema_version"], "0.2.0")
        self.assertEqual(catalogue["counts"]["results"], 3)
        self.assertEqual(catalogue["counts"]["formal_ballots"], 1_234)
        self.assertEqual(
            {item["dataset"] for item in catalogue["datasets"]},
            set(DATASET_LABELS),
        )
        status, _, page = self.request("/")
        self.assertEqual(status, 200)
        rendered = page.decode("utf-8")
        self.assertIn("Explore &amp; export", rendered)
        self.assertIn('id="app-version"', rendered)
        self.assertIn('id="db-version"', rendered)
        self.assert_database_unchanged()

    def test_every_curated_dataset_uses_only_current_rows(self):
        expected = {
            "results": 1,
            "outcomes": 1,
            "participation": 1,
            "count_rounds": 1,
            "count_totals": 1,
            "ballot_datasets": 1,
            "contests": 1,
        }
        for dataset, count in expected.items():
            query = urllib.parse.urlencode(
                {"dataset": dataset, "election_id": ELECTION_ID, "page_size": 250}
            )
            status, _, body = self.request("/api/explorer/query?" + query)
            self.assertEqual(status, 200, dataset)
            self.assertEqual(body["total_rows"], count, dataset)
            self.assertTrue(body["read_only"], dataset)
        self.assert_database_unchanged()

    def test_results_filtering_pagination_and_parameter_safety(self):
        query = urllib.parse.urlencode(
            {
                "dataset": "results",
                "election_id": ELECTION_ID,
                "chamber_id": "chamber_house",
                "state": "NSW",
                "contest_id": CONTEST_ID,
                "result_type": "first_preference",
                "vote_type": "total",
                "reporting_level": "contest",
                "q": "Test Candidate",
                "page": 1,
                "page_size": 1,
            }
        )
        status, _, body = self.request("/api/explorer/query?" + query)
        self.assertEqual(status, 200)
        self.assertEqual(body["total_rows"], 1)
        self.assertEqual(body["rows"][0]["votes"], 1_000)
        self.assertEqual(body["rows"][0]["vote_share"], 52.5)
        self.assertEqual(body["rows"][0]["swing"], 1.2)
        hostile = urllib.parse.urlencode(
            {"dataset": "results", "q": "' OR 1=1 --", "reporting_level": "contest"}
        )
        status, _, body = self.request("/api/explorer/query?" + hostile)
        self.assertEqual(status, 200)
        self.assertEqual(body["total_rows"], 0)
        status, _, _ = self.request("/api/explorer/query?dataset=arbitrary_sql")
        self.assertEqual(status, 422)
        status, _, _ = self.request("/api/explorer/query?dataset=results&page_size=251")
        self.assertEqual(status, 422)
        self.assert_database_unchanged()

    def test_csv_export_is_filtered_provenance_bearing_and_read_only(self):
        query = urllib.parse.urlencode(
            {
                "dataset": "results",
                "election_id": ELECTION_ID,
                "contest_id": CONTEST_ID,
                "reporting_level": "contest",
            }
        )
        status, headers, payload = self.request("/api/explorer/export.csv?" + query)
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-politica-row-count"], "1")
        self.assertEqual(headers["x-politica-schema-version"], "0.2.0")
        self.assertIn("release_0_2_0_aec_2025", headers["x-politica-release-id"])
        self.assertIn("attachment; filename=", headers["content-disposition"])
        text = payload.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["subject_name"], "Test Candidate")
        self.assertEqual(rows[0]["votes"], "1000")
        self.assertEqual(rows[0]["source_revision_id"], ACTIVE_REVISION)
        self.assert_database_unchanged()

    def test_export_row_limit_blocks_unbounded_download(self):
        explorer = ElectionExplorer(
            lambda: self.database,
            lambda database: database.parent,
            app_version="1.0.0",
            max_export_rows=0,
        )
        with self.assertRaisesRegex(ExportTooLargeError, "narrow the filters"):
            explorer.export(
                "results",
                ExplorerFilters(
                    election_id=ELECTION_ID,
                    reporting_level="contest",
                ),
            )
        self.assert_database_unchanged()


if __name__ == "__main__":
    unittest.main()
