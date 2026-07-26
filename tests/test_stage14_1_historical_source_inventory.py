from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from politica_erd.historical_sources import (  # noqa: E402
    acquisition_plan,
    election_for_year,
    election_id,
    inventory_rows,
    load_catalogue,
    validate_catalogue,
)


class HistoricalSourceInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogue = load_catalogue(PROJECT_ROOT / "config" / "source_catalogue_historical.yml")

    def test_catalogue_is_complete_and_valid(self) -> None:
        report = validate_catalogue(self.catalogue)
        self.assertEqual("PASS", report["status"], report["failures"])
        self.assertEqual(47, report["election_count"])
        self.assertEqual(1901, report["first_year"])
        self.assertEqual(2022, report["last_year"])

    def test_modern_primary_source_counts_match_governed_profiles(self) -> None:
        expected = {2022: 45, 2019: 45, 2016: 46, 2013: 47, 2010: 47, 2007: 47, 2004: 39}
        report = validate_catalogue(self.catalogue)
        self.assertEqual(expected, report["modern_primary_source_counts"])

    def test_2022_plan_contains_exact_aec_locations(self) -> None:
        records = acquisition_plan(self.catalogue, 2022, include_corroboration=False)
        by_key = {record.source_key: record for record in records}
        self.assertEqual(45, len(records))
        self.assertEqual(
            "https://results.aec.gov.au/27966/Website/Downloads/HouseCandidatesDownload-27966.csv",
            by_key["house_candidates"].url,
        )
        self.assertEqual(
            "https://results.aec.gov.au/27966/Website/External/aec-senate-formalpreferences-27966-NSW.zip",
            by_key["senate_formal_preferences_nsw"].url,
        )

    def test_2013_plan_uses_group_ticket_and_btl_sources(self) -> None:
        records = acquisition_plan(self.catalogue, 2013, include_corroboration=False)
        by_key = {record.source_key: record for record in records}
        self.assertEqual(47, len(records))
        self.assertIn("senate_group_voting_tickets", by_key)
        self.assertIn("senate_btl_preferences_wa", by_key)
        self.assertNotIn("senate_formal_preferences_wa", by_key)

    def test_2016_plan_includes_formal_preference_candidate_information(self) -> None:
        records = acquisition_plan(self.catalogue, 2016, include_corroboration=False)
        by_key = {record.source_key: record for record in records}
        self.assertEqual(46, len(records))
        self.assertEqual(
            "https://results.aec.gov.au/20499/Website/External/aec-senate-candidateinformation-20499.zip",
            by_key["senate_formal_candidate_information"].url,
        )

    def test_legacy_archives_are_exact_and_reused(self) -> None:
        plans = {
            year: acquisition_plan(self.catalogue, year, include_corroboration=False)
            for year in (1993, 1996, 1998, 2001)
        }
        shared = "https://www.aec.gov.au/About_AEC/Publications/statistics/files/aec-1993-1996-1998-election-statistics.zip"
        self.assertTrue(all(plans[year][0].url == shared for year in (1993, 1996, 1998)))
        self.assertEqual(
            "https://www.aec.gov.au/About_AEC/Publications/statistics/files/aec-2001-election-statistics.zip",
            plans[2001][0].url,
        )

    def test_1901_plan_uses_official_parliamentary_api(self) -> None:
        election = election_for_year(self.catalogue, 1901)
        self.assertEqual("election_fed_1901_03_29_general", election_id(election))
        records = acquisition_plan(self.catalogue, 1901, include_corroboration=False)
        by_key = {record.source_key: record for record in records}
        self.assertEqual(
            "https://handbookapi.aph.gov.au/api/Elections/Election?electionId=202",
            by_key["aph_election"].url,
        )
        self.assertIn("aph_house_division_statistics", by_key)
        self.assertIn("aph_senate_by_candidate", by_key)
        self.assertTrue(all(record.authority.startswith("Parliament of Australia") for record in records))

    def test_house_only_years_exclude_senate_api_sources(self) -> None:
        for year in (1929, 1954):
            records = acquisition_plan(self.catalogue, year, include_corroboration=False)
            self.assertFalse(any(record.chamber == "senate" for record in records))

    def test_inventory_has_one_download_location_row_per_election(self) -> None:
        rows = inventory_rows(self.catalogue)
        self.assertEqual(47, len(rows))
        self.assertEqual(2022, rows[0]["year"])
        self.assertEqual(1901, rows[-1]["year"])
        self.assertTrue(all(row["aph_election_api"].startswith("https://") for row in rows))
        self.assertTrue(rows[0]["aec_house_download_page"].endswith("27966-Csv.htm"))
        self.assertTrue(rows[7]["aec_archive_url"].endswith("aec-2001-election-statistics.zip"))


if __name__ == "__main__":
    unittest.main()
