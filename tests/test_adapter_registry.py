import csv
import tempfile
import unittest
from pathlib import Path

from politica_erd.adapters.registry import detect


class AdapterRegistryTests(unittest.TestCase):
    def test_aec_candidate_signature(self):
        headers = [
            "StateAb", "DivisionID", "DivisionNm", "PartyAb", "PartyNm",
            "CandidateID", "Surname", "GivenNm", "Elected", "HistoricElected",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "HouseCandidatesDownload-31496.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerow(headers)
            matches = detect(path, "authority_aec")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].dataset_key, "house_candidates")

    def test_unknown_schema_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "HouseCandidatesDownload-31496.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerow(["unexpected", "columns"])
            self.assertEqual(detect(path, "authority_aec"), [])


if __name__ == "__main__":
    unittest.main()

