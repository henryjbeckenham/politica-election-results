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
import shutil
from pathlib import Path

import uvicorn
import duckdb

from politica_erd.app.api import create_app
from politica_erd.app.config import AppSettings
from politica_erd.app.publication import (
    FEEDS,
    PublicationFilters,
    PublicationTooLargeError,
    VisualisationFeedService,
)
from politica_erd.app.service import JobService
from politica_erd.build import PROJECT_ROOT
from tests.test_stage4_workflow import CONTEST_ID, ELECTION_ID, make_minimal_database
from tests.test_stage9_explorer import ACTIVE_REVISION, seed_explorer_fixture


def seed_senate_group_fixture(path: Path) -> None:
    connection = duckdb.connect(str(path))
    try:
        chamber_id = "election_chamber_test_2026_senate"
        contest_id = "contest_test_2026_senate_nsw"
        group_id = uuid.UUID("00000000-0000-5000-8000-000000010001")
        connection.execute(
            "INSERT INTO core.election_chamber VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [chamber_id, ELECTION_ID, "chamber_senate", None, 6, True, "final", "active"],
        )
        connection.execute(
            "INSERT INTO core.contest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                contest_id,
                chamber_id,
                None,
                "NSW",
                "NSW",
                6,
                None,
                "declared",
                False,
                None,
                "final",
                "active",
            ],
        )
        connection.execute(
            "INSERT INTO core.ballot_group VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [group_id, contest_id, "A", "A", "Test Party", "party_test", None, False, "final", "active"],
        )
        for index, measure, integer_value, decimal_value in (
            (10010, "votes", 500_000, None),
            (10011, "vote_share", None, "50.0"),
        ):
            connection.execute(
                """INSERT INTO results.vote_result
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    uuid.UUID(f"00000000-0000-5000-8000-{index:012d}"),
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
                    "stage10-senate.csv#row=2",
                    None,
                    "active",
                ],
            )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


class Stage10PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "stage10.duckdb"
        make_minimal_database(cls.database)
        seed_explorer_fixture(cls.database)
        seed_senate_group_fixture(cls.database)
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
            raise RuntimeError("Stage 10 HTTP test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        if cls.application.state.instance_lock.is_locked:
            cls.application.state.instance_lock.release()
        cls.temporary.cleanup()

    @classmethod
    def request(cls, path, *, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=60)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            if "application/json" in response_headers.get("content-type", "") and payload:
                body = json.loads(payload.decode("utf-8"))
            else:
                body = payload
            return response.status, response_headers, body
        finally:
            connection.close()

    def assert_database_unchanged(self):
        self.assertEqual(
            hashlib.sha256(self.database.read_bytes()).hexdigest(),
            self.original_sha256,
        )

    def test_catalogue_exposes_versioned_fixed_feed_contracts(self):
        status, headers, body = self.request("/api/public/v1/feeds")
        self.assertEqual(status, 200)
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertEqual(body["api_version"], "v1")
        self.assertEqual(body["feed_version"], "1.8.0")
        self.assertTrue(body["read_only"])
        self.assertEqual(body["default_election_id"], ELECTION_ID)
        self.assertEqual({item["feed_id"] for item in body["feeds"]}, set(FEEDS))
        self.assertEqual(
            body["release"]["database_sha256"], self.original_sha256
        )
        house = next(
            item for item in body["feeds"] if item["feed_id"] == "house_candidate_results"
        )
        self.assertIn("party_id", {field["name"] for field in house["fields"]})
        self.assertEqual(house["formats"], ["json", "csv", "manifest"])
        status, operator_headers, _ = self.request("/api/status")
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", operator_headers)
        self.assert_database_unchanged()

    def test_feed_identity_uses_the_verified_active_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.duckdb"
            make_minimal_database(base)
            active = root / "immutable/data/database/politica.duckdb"
            active.parent.mkdir(parents=True)
            shutil.copy2(base, active)
            digest = hashlib.sha256(active.read_bytes()).hexdigest()
            settings = AppSettings(
                project_root=PROJECT_ROOT,
                base_database=base,
                app_data=root / "app",
            )
            settings.releases_root.mkdir(parents=True)
            (settings.releases_root / "active.json").write_text(
                json.dumps(
                    {
                        "release_id": "release_stage10_pointer_test",
                        "path_base": "absolute",
                        "database_path": str(active),
                        "sha256": digest,
                        "activated_at": "2026-07-19T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            service = JobService(settings)
            identity = service.governed_release_identity()
            self.assertEqual(identity["release_id"], "release_stage10_pointer_test")
            self.assertEqual(identity["database_sha256"], digest)
            self.assertEqual(identity["application_version"], "1.8.0")
            self.assertEqual(service.governed_database().resolve(), active.resolve())

    def test_json_csv_and_manifest_are_release_bound_and_deterministic(self):
        query = urllib.parse.urlencode(
            {"election_id": ELECTION_ID, "state": "NSW", "contest_id": CONTEST_ID}
        )
        json_path = "/api/public/v1/feeds/house_candidate_results.json?" + query
        status, headers, body = self.request(json_path)
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-politica-row-count"], "1")
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertEqual(body["manifest"]["feed_id"], "house_candidate_results")
        self.assertEqual(body["manifest"]["row_count"], 1)
        self.assertEqual(body["manifest"]["release"]["database_sha256"], self.original_sha256)
        self.assertEqual(body["manifest"]["source_revision_ids"], [ACTIVE_REVISION])
        self.assertEqual(body["data"][0]["votes"], 1_000)
        self.assertEqual(body["data"][0]["party_id"], "party_test")
        canonical_data = json.dumps(
            body["data"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(canonical_data).hexdigest(),
            body["manifest"]["data_sha256"],
        )

        status, cached_headers, payload = self.request(
            json_path, headers={"If-None-Match": headers["etag"]}
        )
        self.assertEqual(status, 304)
        self.assertEqual(payload, b"")
        self.assertEqual(cached_headers["etag"], headers["etag"])

        csv_path = "/api/public/v1/feeds/house_candidate_results.csv?" + query
        status, csv_headers, payload = self.request(csv_path)
        self.assertEqual(status, 200)
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_feed_id"], "house_candidate_results")
        self.assertEqual(
            rows[0]["_publication_id"], body["manifest"]["publication_id"]
        )
        self.assertEqual(rows[0]["_database_sha256"], self.original_sha256)
        self.assertEqual(rows[0]["source_revision_id"], ACTIVE_REVISION)
        self.assertIn("attachment; filename=", csv_headers["content-disposition"])

        manifest_path = "/api/public/v1/feeds/house_candidate_results/manifest.json?" + query
        status, _, manifest = self.request(manifest_path)
        self.assertEqual(status, 200)
        self.assertEqual(manifest, body["manifest"])
        self.assert_database_unchanged()

    def test_visualisation_presets_calculate_party_and_member_outputs(self):
        query = urllib.parse.urlencode({"election_id": ELECTION_ID})
        status, _, summary = self.request(
            "/api/public/v1/feeds/house_party_summary.json?" + query
        )
        self.assertEqual(status, 200)
        self.assertEqual(summary["manifest"]["row_count"], 1)
        party = summary["data"][0]
        self.assertEqual(party["party_id"], "party_test")
        self.assertEqual(party["first_preference_votes"], 1_000)
        self.assertEqual(party["first_preference_vote_share"], 100.0)
        self.assertEqual(party["declared_seats"], 1)

        status, _, members = self.request(
            "/api/public/v1/feeds/declared_members.json?" + query
        )
        self.assertEqual(status, 200)
        member = members["data"][0]
        self.assertEqual(member["person_id"], "person_test")
        self.assertEqual(member["party_id"], "party_test")

        status, _, seats = self.request(
            "/api/public/v1/feeds/house_seat_results.json?" + query
        )
        self.assertEqual(status, 200)
        self.assertEqual(seats["data"][0]["candidacy_id"], str(member["candidacy_id"]))

        status, _, senate = self.request(
            "/api/public/v1/feeds/senate_group_results.json?" + query
        )
        self.assertEqual(status, 200)
        self.assertEqual(senate["manifest"]["row_count"], 1)
        self.assertEqual(senate["data"][0]["party_id"], "party_test")
        self.assertEqual(senate["data"][0]["votes"], 500_000)
        self.assert_database_unchanged()

    def test_every_registered_feed_executes_and_rejects_unregistered_inputs(self):
        for feed_id in FEEDS:
            query = urllib.parse.urlencode({"election_id": ELECTION_ID})
            status, _, body = self.request(
                f"/api/public/v1/feeds/{feed_id}.json?{query}"
            )
            self.assertEqual(status, 200, feed_id)
            self.assertEqual(body["manifest"]["feed_id"], feed_id)
            self.assertTrue(body["manifest"]["read_only"])

        status, _, _ = self.request("/api/public/v1/feeds/arbitrary_sql.json")
        self.assertEqual(status, 422)
        hostile = urllib.parse.urlencode({"election_id": "' OR 1=1 --"})
        status, _, _ = self.request(
            "/api/public/v1/feeds/declared_members.json?" + hostile
        )
        self.assertEqual(status, 422)
        status, _, _ = self.request(
            "/api/public/v1/feeds/declared_members.json?state=INVALID"
        )
        self.assertEqual(status, 422)
        self.assert_database_unchanged()

    def test_feed_row_limit_blocks_unbounded_publication(self):
        limited = VisualisationFeedService(
            self.application.state.explorer,
            self.application.state.job_service.governed_release_identity,
            max_rows=0,
        )
        with self.assertRaisesRegex(PublicationTooLargeError, "narrow the state"):
            limited.build(
                "house_candidate_results",
                PublicationFilters(election_id=ELECTION_ID),
            )
        self.assert_database_unchanged()


if __name__ == "__main__":
    unittest.main()
