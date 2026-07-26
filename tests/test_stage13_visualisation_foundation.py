import hashlib
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import duckdb
import uvicorn

from politica_erd.app.api import create_app
from politica_erd.app.config import AppSettings
from politica_erd.app.publication import FEEDS
from politica_erd.build import PROJECT_ROOT
from tests.test_stage4_workflow import make_minimal_database
from tests.test_stage9_explorer import seed_explorer_fixture
from tests.test_stage10_publication import seed_senate_group_fixture
from tests.test_stage11_public_results import seed_all_state_senate_fixture


class Stage13VisualisationFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "stage13.duckdb"
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
            raise RuntimeError("Stage 13 HTTP test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        if cls.application.state.instance_lock.is_locked:
            cls.application.state.instance_lock.release()
        cls.temporary.cleanup()

    @classmethod
    def request(cls, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=60)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            return response.status, response_headers, body
        finally:
            connection.close()

    def test_contract_defines_routes_metrics_dependencies_and_boundaries(self):
        document = self.application.state.visualisation_contract.catalogue()
        self.assertEqual(document["application_version"], "1.8.0")
        self.assertEqual(document["contract_version"], "2.0.0")
        self.assertEqual(document["design_system_version"], "2.0.0")
        self.assertTrue(document["read_only"])
        self.assertEqual(document["release"]["database_sha256"], self.database_sha256)
        self.assertGreaterEqual(len(document["metrics"]), 7)
        self.assertTrue(
            all(item.get("definition") and item.get("calculation") for item in document["metrics"])
        )
        registered_feeds = {
            feed
            for item in document["visualisations"]
            for feed in item.get("required_feeds", [])
        }
        self.assertEqual(registered_feeds, set(FEEDS))
        self.assertIn("electorate_maps", document["capability_boundaries"])
        self.assertIn("full_senate_composition", document["capability_boundaries"])
        self.assertEqual(document["boundary_geometry"]["feature_count"], 150)
        self.assertEqual(
            document["boundary_geometry"]["source"]["publisher"],
            "Australian Electoral Commission",
        )

    def test_public_contract_endpoint_has_cors_etag_and_304(self):
        status, headers, body = self.request("/api/public/v1/visualisations")
        self.assertEqual(status, 200)
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertTrue(headers["etag"].startswith('"'))
        document = json.loads(body)
        self.assertEqual(document["release"]["database_sha256"], self.database_sha256)
        status, second_headers, second_body = self.request(
            "/api/public/v1/visualisations",
            {"If-None-Match": headers["etag"]},
        )
        self.assertEqual(status, 304)
        self.assertEqual(second_headers["etag"], headers["etag"])
        self.assertEqual(second_body, b"")

    def test_static_publication_contains_release_bound_visualisation_contract(self):
        built = self.application.state.website_publisher.build()
        release_root = Path(built["release_root"])
        contract = json.loads(
            (release_root / "data" / "visualisations.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (release_root / "publication-manifest.json").read_text(encoding="utf-8")
        )
        self.assertTrue(contract["static_publication"])
        self.assertEqual(contract["site_release_id"], built["site_release_id"])
        self.assertEqual(contract["release"]["database_sha256"], self.database_sha256)
        self.assertEqual(
            contract["contract_sha256"], manifest["visualisation_contract_sha256"]
        )
        boundary = contract["boundary_geometry"]
        geometry_path = (
            release_root / "data" / boundary["derived_geometry"]["public_asset_path"]
        )
        self.assertTrue(geometry_path.is_file())
        self.assertEqual(
            hashlib.sha256(geometry_path.read_bytes()).hexdigest(),
            manifest["boundary_geojson_sha256"],
        )
        self.assertEqual(
            boundary["contract_sha256"], manifest["boundary_contract_sha256"]
        )
        self.assertEqual(
            self.application.state.website_publisher.verify_release(release_root)["status"],
            "PASS",
        )

    def test_frontend_foundation_is_modular_accessible_and_self_contained(self):
        root = PROJECT_ROOT / "visualisation" / "src"
        modules = {
            path.name for path in (root / "foundation").glob("*") if path.is_file()
        }
        self.assertTrue(
            {"tokens.css", "dom.js", "format.js", "party.js", "registry.js", "url-state.js", "tooltip.js", "legend.js", "source-panel.js"}.issubset(modules)
        )
        source = (root / "components" / "results.js").read_text(encoding="utf-8")
        css = (root / "politica.css").read_text(encoding="utf-8")
        tokens = (root / "foundation" / "tokens.css").read_text(encoding="utf-8")
        self.assertIn('setAttribute("role", "tooltip")', (root / "foundation" / "tooltip.js").read_text(encoding="utf-8"))
        self.assertIn("aria-current", source)
        self.assertIn("data-route-section", source)
        self.assertIn("prefers-reduced-motion", css)
        self.assertNotIn("fonts.googleapis.com", source + css + tokens)
        self.assertNotIn("https://", source + css + tokens)


if __name__ == "__main__":
    unittest.main()
