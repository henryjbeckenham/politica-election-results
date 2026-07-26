import tempfile
import unittest
from pathlib import Path

import duckdb

from politica_erd.build import PROJECT_ROOT, build
from politica_erd.validate import validate_database


class SchemaTests(unittest.TestCase):
    def test_empty_schema_builds_and_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.duckdb"
            manifest = build(database, PROJECT_ROOT)
            report = validate_database(database)
            self.assertEqual(report["status"], "PASS", report["failures"])
            self.assertEqual(manifest["election_count"], 0)
            self.assertTrue(manifest["stage_1_empty_schema"])
            self.assertGreaterEqual(manifest["relationship_count"], 40)
            self.assertEqual(manifest["grand_sync_counts"]["constituencies"], 591)

    def test_seeded_authorities(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.duckdb"
            build(database, PROJECT_ROOT)
            connection = duckdb.connect(str(database), read_only=True)
            try:
                authorities = connection.execute("SELECT count(*) FROM control.electoral_authority").fetchone()[0]
                self.assertEqual(authorities, 9)
                self.assertEqual(connection.execute("SELECT count(*) FROM core.election").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM sync.person").fetchone()[0], 171)
                self.assertEqual(connection.execute("SELECT count(*) FROM sync.party").fetchone()[0], 20)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
