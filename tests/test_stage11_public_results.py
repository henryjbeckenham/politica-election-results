import hashlib
import http.client
import json
import re
import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
import uuid
from pathlib import Path

import duckdb
import uvicorn

from politica_erd.app.api import create_app
from politica_erd.app.config import AppSettings
from politica_erd.build import PROJECT_ROOT
from tests.test_stage4_workflow import ELECTION_ID, make_minimal_database
from tests.test_stage9_explorer import ACTIVE_REVISION, seed_explorer_fixture
from tests.test_stage10_publication import seed_senate_group_fixture


STATE_NAMES = {
    "ACT": "Australian Capital Territory",
    "NSW": "New South Wales",
    "NT": "Northern Territory",
    "QLD": "Queensland",
    "SA": "South Australia",
    "TAS": "Tasmania",
    "VIC": "Victoria",
    "WA": "Western Australia",
}


def _stage11_uuid(kind: str, state: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"politica-stage11.1/{kind}/{state}")


def seed_all_state_senate_fixture(path: Path) -> None:
    """Reproduce the official register's full Senate contest names in every state."""
    connection = duckdb.connect(str(path))
    try:
        chamber_id = "election_chamber_test_2026_senate"
        for index, (state, contest_name) in enumerate(STATE_NAMES.items(), start=1):
            contest_id = f"contest_test_2026_senate_{state.lower()}"
            if state == "NSW":
                connection.execute(
                    "UPDATE core.contest SET contest_name=? WHERE contest_id=?",
                    [contest_name, contest_id],
                )
            else:
                connection.execute(
                    "INSERT INTO core.contest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        contest_id,
                        chamber_id,
                        None,
                        state,
                        contest_name,
                        2 if state in {"ACT", "NT"} else 6,
                        None,
                        "declared",
                        False,
                        None,
                        "final",
                        "active",
                    ],
                )
                group_id = _stage11_uuid("group", state)
                connection.execute(
                    "INSERT INTO core.ballot_group VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        group_id,
                        contest_id,
                        "A",
                        "A",
                        f"{state} Test Party",
                        "party_test",
                        None,
                        False,
                        "final",
                        "active",
                    ],
                )
                for measure, integer_value, decimal_value in (
                    ("votes", 100_000 + index, None),
                    ("vote_share", None, "40.0"),
                ):
                    connection.execute(
                        """INSERT INTO results.vote_result
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        [
                            _stage11_uuid(f"group-{measure}", state),
                            ELECTION_ID,
                            contest_id,
                            None,
                            "ballot_group",
                            None,
                            group_id,
                            None,
                            None,
                            "group_total",
                            "total",
                            measure,
                            integer_value,
                            decimal_value,
                            "reported",
                            "official_reported",
                            "final",
                            ACTIVE_REVISION,
                            f"stage11-all-states.csv#state={state}",
                            None,
                            "active",
                        ],
                    )

            candidacy_id = _stage11_uuid("candidacy", state)
            connection.execute(
                """INSERT INTO core.candidacy
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    candidacy_id,
                    contest_id,
                    None,
                    "party_test",
                    f"candidate-{state}",
                    f"{state} Test Senator",
                    f"{state} Test",
                    "Senator",
                    "Test Party",
                    "TST",
                    None,
                    "nominated",
                    "matched",
                    "final",
                    "active",
                ],
            )
            connection.execute(
                "INSERT INTO results.contest_outcome VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    _stage11_uuid("outcome", state),
                    contest_id,
                    candidacy_id,
                    "elected",
                    1,
                    None,
                    "final",
                    ACTIVE_REVISION,
                    f"stage11-all-states.csv#state={state}",
                    "active",
                ],
            )
            connection.execute(
                """INSERT INTO results.participation_result
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    _stage11_uuid("turnout", state),
                    ELECTION_ID,
                    contest_id,
                    None,
                    "total",
                    "turnout_percentage",
                    None,
                    "90.0",
                    "reported",
                    "official_reported",
                    "final",
                    ACTIVE_REVISION,
                    f"stage11-all-states.csv#state={state}",
                    None,
                    "active",
                ],
            )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


class Stage11PublicResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "stage11.duckdb"
        make_minimal_database(cls.database)
        seed_explorer_fixture(cls.database)
        seed_senate_group_fixture(cls.database)
        seed_all_state_senate_fixture(cls.database)
        connection = duckdb.connect(str(cls.database))
        try:
            connection.execute(
                "UPDATE sync.constituency SET state_territory='nsw' WHERE state_territory='NSW'"
            )
            connection.execute("CHECKPOINT")
        finally:
            connection.close()
        cls.original_sha256 = hashlib.sha256(cls.database.read_bytes()).hexdigest()
        cls.settings = AppSettings(
            project_root=PROJECT_ROOT,
            base_database=cls.database,
            app_data=cls.root / "app",
            explorer_max_export_rows=1_000_000,
            publication_max_rows=250_000,
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
            raise RuntimeError("Stage 11 HTTP test server did not start")

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
            return response.status, headers, payload
        finally:
            connection.close()

    def assert_database_unchanged(self):
        self.assertEqual(
            hashlib.sha256(self.database.read_bytes()).hexdigest(),
            self.original_sha256,
        )

    def test_results_redirect_site_and_compiled_assets_are_served(self):
        status, headers, _ = self.request("/results")
        self.assertEqual(status, 307)
        self.assertEqual(headers["location"], "/results/")
        status, _, page = self.request("/results/")
        self.assertEqual(status, 200)
        rendered = page.decode("utf-8")
        self.assertIn("Australian federal election results", rendered)
        assets = re.findall(r'(?:src|href)="(\./[^"?#]+)', rendered)
        self.assertTrue(assets)
        for asset in assets[:5]:
            status, _, payload = self.request("/results/" + asset.removeprefix("./"))
            self.assertEqual(status, 200, asset)
            self.assertTrue(payload, asset)
        self.assert_database_unchanged()

    def test_compiled_client_uses_only_fixed_public_get_feeds_and_no_external_font(self):
        results_root = PROJECT_ROOT / "src" / "politica_erd" / "app" / "results"
        compiled = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in results_root.rglob("*")
            if path.is_file() and path.suffix in {".html", ".js", ".css"}
        )
        self.assertIn("/api/public/v1/feeds", compiled)
        self.assertNotIn("/api/jobs", compiled)
        self.assertNotIn('method:"POST"', compiled)
        self.assertNotIn("fonts.googleapis.com", compiled)
        for feed_id in (
            "house_candidate_results",
            "house_seat_results",
            "house_party_summary",
            "senate_group_results",
            "turnout_informality",
            "declared_members",
            "senate_count_progress",
        ):
            self.assertIn(feed_id, compiled)
        self.assert_database_unchanged()

    def test_public_state_filter_canonicalises_historical_lowercase_constituency_codes(self):
        query = urllib.parse.urlencode({"election_id": ELECTION_ID, "state": "NSW"})
        status, _, payload = self.request(
            "/api/public/v1/feeds/house_seat_results.json?" + query
        )
        self.assertEqual(status, 200)
        body = json.loads(payload.decode("utf-8"))
        self.assertEqual(body["manifest"]["filters"]["state"], "NSW")
        self.assertEqual(len(body["data"]), 1)
        self.assertEqual(body["data"][0]["state"], "NSW")
        self.assert_database_unchanged()

    def test_all_senate_states_are_canonical_across_public_result_feeds(self):
        for state in STATE_NAMES:
            query = urllib.parse.urlencode({"election_id": ELECTION_ID, "state": state})
            for feed_id in (
                "senate_group_results",
                "declared_members",
                "turnout_informality",
            ):
                status, _, payload = self.request(
                    f"/api/public/v1/feeds/{feed_id}.json?{query}"
                )
                self.assertEqual(status, 200, f"{feed_id}/{state}")
                body = json.loads(payload.decode("utf-8"))
                matching = [row for row in body["data"] if row["state"] == state]
                self.assertTrue(matching, f"{feed_id}/{state}")
                if feed_id == "declared_members":
                    self.assertTrue(
                        any(row["chamber_id"] == "chamber_senate" for row in matching),
                        state,
                    )
        self.assert_database_unchanged()

    def test_operator_links_to_public_results_and_reports_application_1_3_1(self):
        status, _, page = self.request("/")
        self.assertEqual(status, 200)
        rendered = page.decode("utf-8")
        self.assertIn('href="/results/"', rendered)
        self.assertIn("Public results", rendered)
        status, _, payload = self.request("/api/explorer/catalogue")
        self.assertEqual(status, 200)
        catalogue = json.loads(payload.decode("utf-8"))
        self.assertEqual(catalogue["application_version"], "1.8.0")
        self.assertEqual(catalogue["database"]["schema_version"], "0.2.0")
        self.assert_database_unchanged()


if __name__ == "__main__":
    unittest.main()
