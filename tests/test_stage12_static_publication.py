import hashlib
import http.client
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

import duckdb
import uvicorn

from politica_erd.app.api import create_app
from politica_erd.app.config import AppSettings
from politica_erd.build import PROJECT_ROOT
from politica_erd.static_site import STATIC_MODE_CALL, WebsitePublicationError
from tests.test_stage4_workflow import make_minimal_database
from tests.test_stage9_explorer import seed_explorer_fixture
from tests.test_stage10_publication import seed_senate_group_fixture
from tests.test_stage11_public_results import seed_all_state_senate_fixture


class Stage12StaticPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "stage12.duckdb"
        make_minimal_database(cls.database)
        seed_explorer_fixture(cls.database)
        seed_senate_group_fixture(cls.database)
        seed_all_state_senate_fixture(cls.database)
        connection = duckdb.connect(str(cls.database))
        try:
            connection.execute("CHECKPOINT")
        finally:
            connection.close()
        cls.database_sha256 = hashlib.sha256(cls.database.read_bytes()).hexdigest()
        cls.settings = AppSettings(
            project_root=PROJECT_ROOT,
            base_database=cls.database,
            app_data=cls.root / "app",
            explorer_max_export_rows=1_000_000,
            publication_max_rows=250_000,
        )
        cls.application = create_app(cls.settings)
        cls.publisher = cls.application.state.website_publisher
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
            raise RuntimeError("Stage 12 HTTP test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        if cls.application.state.instance_lock.is_locked:
            cls.application.state.instance_lock.release()
        cls.temporary.cleanup()

    @classmethod
    def request(cls, path, *, method="GET", document=None):
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=60)
        try:
            payload = None
            headers = {}
            if document is not None:
                payload = json.dumps(document).encode("utf-8")
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            body = response.read()
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            return response.status, response_headers, body
        finally:
            connection.close()

    @classmethod
    def build(cls):
        return cls.publisher.build()

    def assert_database_unchanged(self):
        self.assertEqual(
            hashlib.sha256(self.database.read_bytes()).hexdigest(),
            self.database_sha256,
        )

    def test_build_is_static_complete_release_bound_and_deterministic(self):
        first = self.build()
        self.assertEqual(first["status"], "READY_TO_DEPLOY")
        self.assertEqual(first["application_version"], "1.8.0")
        self.assertEqual(first["database_sha256"], self.database_sha256)
        self.assertEqual(first["feed_count"], 9)
        release_root = Path(first["release_root"])
        self.assertIn(STATIC_MODE_CALL, (release_root / "index.html").read_text())
        catalogue = json.loads(
            (release_root / "data/catalogue.json").read_text(encoding="utf-8")
        )
        self.assertTrue(catalogue["static_publication"])
        self.assertEqual(catalogue["site_release_id"], first["site_release_id"])
        for feed in catalogue["feeds"]:
            self.assertTrue(feed["urls"]["json"].startswith("data/feeds/"))
            for suffix in ("json", "csv", "manifest.json"):
                expected = (
                    release_root
                    / "data/feeds"
                    / catalogue["default_election_id"]
                    / f"{feed['feed_id']}.{suffix}"
                )
                self.assertTrue(expected.is_file(), expected)

        with zipfile.ZipFile(first["export_zip"]) as archive:
            names = archive.namelist()
            self.assertIn("index.html", names)
            self.assertIn("publication-manifest.json", names)
            self.assertFalse(
                any(
                    name.endswith((".duckdb", ".env", ".pem", ".key"))
                    for name in names
                )
            )
        second = self.build()
        self.assertTrue(second["reused_existing_release"])
        self.assertEqual(second["site_release_id"], first["site_release_id"])
        self.assertEqual(second["export_sha256"], first["export_sha256"])
        Path(second["export_zip"]).write_bytes(b"changed export")
        rebuilt = self.build()
        self.assertTrue(rebuilt["reused_existing_release"])
        self.assertEqual(rebuilt["export_sha256"], first["export_sha256"])
        self.assertEqual(
            rebuilt["verification"]["release_root"],
            str(Path(rebuilt["release_root"]).resolve()),
        )
        self.assert_database_unchanged()

    def test_manifest_verifier_blocks_changed_or_extra_files(self):
        built = self.build()
        tampered = self.root / "tampered-site"
        shutil.copytree(built["release_root"], tampered)
        readme = tampered / "README.txt"
        readme.write_text(readme.read_text() + "changed\n", encoding="utf-8")
        with self.assertRaisesRegex(WebsitePublicationError, "checksum"):
            self.publisher.verify_release(tampered)
        shutil.rmtree(tampered)
        shutil.copytree(built["release_root"], tampered)
        (tampered / ".env").write_text("SECRET=blocked\n", encoding="utf-8")
        with self.assertRaisesRegex(WebsitePublicationError, "inventory"):
            self.publisher.verify_release(tampered)
        self.assert_database_unchanged()

    def test_operator_build_status_preview_and_download_routes(self):
        status, _, page = self.request("/")
        self.assertEqual(status, 200)
        rendered = page.decode("utf-8")
        self.assertIn("Website publication", rendered)
        self.assertIn('data-route="website"', rendered)

        status, _, payload = self.request("/api/site-publication/status")
        self.assertEqual(status, 200)
        current = json.loads(payload)
        self.assertIn(current["status"], {"NOT_BUILT", "READY_TO_DEPLOY"})

        status, _, payload = self.request(
            "/api/site-publication/build", method="POST", document={}
        )
        self.assertEqual(status, 200)
        built = json.loads(payload)
        self.assertEqual(built["status"], "READY_TO_DEPLOY")

        status, headers, page = self.request("/site-preview/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn(STATIC_MODE_CALL, page.decode("utf-8"))
        status, headers, package = self.request("/api/site-publication/download")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/zip")
        self.assertEqual(hashlib.sha256(package).hexdigest(), built["export_sha256"])
        self.assert_database_unchanged()

    def test_status_marks_package_stale_when_active_database_changes(self):
        built = self.build()
        self.assertEqual(built["database_sha256"], self.database_sha256)
        status = self.publisher.status()
        self.assertTrue(status["matches_active_database"])
        self.assertEqual(status["verification"]["status"], "PASS")
        self.assertEqual(status["verification"]["feed_count"], 9)
        self.assert_database_unchanged()


if __name__ == "__main__":
    unittest.main()
