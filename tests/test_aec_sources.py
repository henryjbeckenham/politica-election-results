import hashlib
import json
import unittest
from pathlib import Path

from politica_erd.adapters.registry import detect
from politica_erd.aec import normalise_label, read_aec_csv, read_aec_zip
from politica_erd.build import PROJECT_ROOT


class AecSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (PROJECT_ROOT / "data/manifests/aec_2025_sources.json").read_text(encoding="utf-8")
        )

    def test_manifest_files_are_immutable(self):
        self.assertEqual(self.manifest["source_count"], 45)
        for source in self.manifest["sources"]:
            path = PROJECT_ROOT / source["path"]
            self.assertTrue(path.is_file(), path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source["sha256"])

    def test_formal_preference_archives_reconcile_to_published_formal_votes(self):
        formal_rows = {
            source["state"]: source["row_count"]
            for source in self.manifest["sources"]
            if source["family"] == "formal_preferences"
        }
        self.assertEqual(
            formal_rows,
            {
                "ACT": 293474,
                "NSW": 4986832,
                "NT": 106807,
                "QLD": 3224436,
                "SA": 1164072,
                "TAS": 371790,
                "VIC": 4101762,
                "WA": 1622016,
            },
        )
        self.assertEqual(sum(formal_rows.values()), 15871189)

    def test_registered_csv_schemas_are_detected_once(self):
        for source in self.manifest["sources"]:
            if not source["file"].endswith(".csv"):
                continue
            matches = detect(PROJECT_ROOT / source["path"], "authority_aec")
            self.assertEqual(len(matches), 1, (source["key"], matches))

    def test_all_senate_distribution_candidate_rows_match(self):
        raw = PROJECT_ROOT / "data/raw/aec/2025_federal/31496/final"
        candidates = {
            (row.data["StateAb"], normalise_label(f"{row.data['GivenNm']} {row.data['Surname']}"))
            for row in read_aec_csv(raw / "SenateCandidatesDownload-31496.csv")
        }
        matched = sum(
            1
            for row in read_aec_zip(raw / "SenateDopDownload-31496.zip")
            if (row.data["State"], normalise_label(f"{row.data['GivenNm']} {row.data['Surname']}"))
            in candidates
        )
        self.assertEqual(matched, 64965)


if __name__ == "__main__":
    unittest.main()
