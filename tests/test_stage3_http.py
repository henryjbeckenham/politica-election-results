import hashlib
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import uvicorn

from politica_erd.app.api import create_app
from politica_erd.app.config import AppSettings
from politica_erd.build import PROJECT_ROOT


class Stage3HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.app_data = Path(cls.temporary.name) / "app"
        cls.base_database = (
            PROJECT_ROOT / "data/database/politica_election_results.duckdb"
        )
        cls.base_hash = hashlib.sha256(cls.base_database.read_bytes()).hexdigest()
        settings = AppSettings(
            project_root=PROJECT_ROOT,
            base_database=cls.base_database,
            app_data=cls.app_data,
        )
        application = create_app(settings)
        cls.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cls.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        cls.socket.bind(("127.0.0.1", 0))
        cls.socket.listen(128)
        cls.port = cls.socket.getsockname()[1]
        cls.server = uvicorn.Server(
            uvicorn.Config(
                application,
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
            raise RuntimeError("Stage 3 HTTP test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        cls.temporary.cleanup()

    @classmethod
    def request(cls, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=30)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            content_type = response.getheader("Content-Type", "")
            parsed = (
                json.loads(payload.decode("utf-8"))
                if "application/json" in content_type
                else payload.decode("utf-8")
            )
            return response.status, parsed
        finally:
            connection.close()

    def test_http_upload_configuration_and_error_contract(self):
        status_code, status = self.request("GET", "/api/status")
        self.assertEqual(status_code, 200)
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["validation"]["passed"], 27)

        status_code, html = self.request("GET", "/")
        self.assertEqual(status_code, 200)
        self.assertIn("Politica", html)

        boundary = "----politica-stage3-test-boundary"
        csv_payload = b"unknown_a,unknown_b\nfirst,1\nsecond,2\n"
        multipart = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="mystery.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode("ascii") + csv_payload + f"\r\n--{boundary}--\r\n".encode("ascii")
        status_code, detected = self.request(
            "POST",
            "/api/imports/detect",
            multipart,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        self.assertEqual(status_code, 200)
        upload_id = detected["upload_id"]
        self.assertTrue((self.app_data / "pending_uploads" / upload_id).is_dir())

        configuration = {
            "authority_id": "authority_aec",
            "election_id": None,
            "publication_phase": "final",
            "source_url": "https://example.invalid/mystery.csv",
            "operator_note": "HTTP integration test",
            "adapter_id": "adapter_aec_2025_v1",
        }
        status_code, created = self.request(
            "POST",
            f"/api/imports/{upload_id}/jobs",
            json.dumps(configuration).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status_code, 200)
        job = created["job"]
        self.assertEqual(job["configuration"]["publication_phase"], "final")
        self.assertEqual(job["configuration"]["source_url"], configuration["source_url"])
        self.assertEqual(job["configuration"]["operator_note"], "HTTP integration test")
        self.assertEqual(
            job["configuration"]["requested_adapter_id"], "adapter_aec_2025_v1"
        )
        self.assertFalse((self.app_data / "pending_uploads" / upload_id).exists())

        dataset_id = job["datasets"][0]["dataset_id"]
        status_code, error = self.request(
            "PUT",
            f"/api/jobs/{job['job_id']}/datasets/{dataset_id}",
            json.dumps({"adapter_id": "missing", "dataset_key": "missing"}).encode(
                "utf-8"
            ),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status_code, 422)
        self.assertIn("Unknown adapter", error["detail"])
        self.assertEqual(
            hashlib.sha256(self.base_database.read_bytes()).hexdigest(), self.base_hash
        )


if __name__ == "__main__":
    unittest.main()
