import hashlib
import json
import unittest
from pathlib import Path

import yaml

from politica_erd.build import PROJECT_ROOT
from politica_erd.app.detection import AdapterCatalogue
from politica_erd.install_2022_release import ELECTION_2022


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Stage142HistoricalIngestionTests(unittest.TestCase):
    def test_all_45_official_aec_sources_are_present_and_checksum_pinned(self):
        manifest = json.loads(
            (PROJECT_ROOT / "data/manifests/aec_2022_sources.json").read_text(
                encoding="utf-8"
            )
        )
        checksum_contract = yaml.safe_load(
            (PROJECT_ROOT / "config/source_checksums_2022.yml").read_text(
                encoding="utf-8"
            )
        )["sha256_by_source_key"]
        self.assertEqual(manifest["election_id"], ELECTION_2022)
        self.assertEqual(manifest["event_id"], "27966")
        self.assertEqual(manifest["source_count"], 45)
        self.assertEqual(set(checksum_contract), {row["key"] for row in manifest["sources"]})
        self.assertEqual(
            manifest["total_size_bytes"],
            sum(row["size_bytes"] for row in manifest["sources"]),
        )
        for row in manifest["sources"]:
            source = PROJECT_ROOT / row["path"]
            self.assertTrue(source.is_file(), source)
            self.assertTrue(row["url"].startswith("https://results.aec.gov.au/27966/"))
            self.assertEqual(source.stat().st_size, row["size_bytes"])
            self.assertEqual(sha256(source), row["sha256"])
            self.assertEqual(row["sha256"], checksum_contract[row["key"]])

    def test_prevalidated_delta_contains_the_complete_relational_import(self):
        manifest_path = PROJECT_ROOT / "data/manifests/aec_2022_delta_tables.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        core = dict(manifest)
        recorded = core.pop("manifest_sha256")
        observed = hashlib.sha256(
            json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(recorded, observed)
        self.assertEqual(manifest["election_id"], ELECTION_2022)
        self.assertEqual(manifest["table_count"], 54)
        tables = {
            f"{row['schema']}.{row['table']}": row for row in manifest["tables"]
        }
        expected_counts = {
            "core.contest": 159,
            "core.candidacy": 1624,
            "geography.election_reporting_unit": 8638,
            "results.vote_result": 230488,
            "results.participation_result": 1908,
            "results.contest_outcome": 191,
            "count.count_round": 2670,
            "count.count_candidate_total": 115892,
            "count.preference_transfer": 19904,
            "ballot.ballot_dataset": 8,
            "provenance.source_file_revision": 45,
            "provenance.row_lineage": 384179,
        }
        for table, expected in expected_counts.items():
            self.assertEqual(tables[table]["row_count"], expected, table)
        for row in manifest["tables"]:
            shard = PROJECT_ROOT / row["path"]
            self.assertTrue(shard.is_file(), shard)
            self.assertEqual(shard.stat().st_size, row["size_bytes"])
            self.assertEqual(sha256(shard), row["sha256"])

    def test_import_report_reconciles_complete_house_senate_and_ballot_data(self):
        report = json.loads(
            (PROJECT_ROOT / "dist/stage_14_2_2022_import_report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["validation"]["blocker_count"], 0)
        self.assertEqual(report["source_count"], 45)
        self.assertEqual(report["table_counts"]["contests"], 159)
        self.assertEqual(report["table_counts"]["candidacies"], 1624)
        self.assertEqual(report["table_counts"]["formal_ballots"], 15_040_658)
        self.assertEqual(
            report["table_counts"]["formal_ballot_preferences"], 101_100_266
        )

    def test_2022_map_contract_uses_the_151_official_aec_divisions(self):
        contract = yaml.safe_load(
            (PROJECT_ROOT / "config/electorate_boundaries_2022.yml").read_text(
                encoding="utf-8"
            )
        )
        geometry_path = PROJECT_ROOT / contract["derived_geometry"]["source_path"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        self.assertEqual(contract["election_id"], ELECTION_2022)
        self.assertEqual(contract["feature_count"], 151)
        self.assertEqual(len(geometry["features"]), 151)
        self.assertEqual(sha256(geometry_path), contract["derived_geometry"]["sha256"])

    def test_frontend_registers_election_selection_and_election_scoped_assets(self):
        results = (PROJECT_ROOT / "visualisation/src/components/results.js").read_text(
            encoding="utf-8"
        )
        dom = (PROJECT_ROOT / "visualisation/src/foundation/dom.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="pr-global-election"', results)
        self.assertIn("loadElection", results)
        self.assertIn("/feeds/${election}${feedId}", dom)
        self.assertIn("/visualisations${suffix}", dom)

    def test_batch_2022_adapter_does_not_shadow_interactive_import_detection(self):
        config = yaml.safe_load(
            (PROJECT_ROOT / "config/adapters/aec_2022.yml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(config["catalogue_visibility"], "batch_only")
        catalogue = AdapterCatalogue(PROJECT_ROOT / "config/adapters")
        detection = catalogue.detect(
            "HouseCandidatesDownload-31496.csv",
            [
                "StateAb",
                "DivisionID",
                "DivisionNm",
                "PartyAb",
                "PartyNm",
                "CandidateID",
                "Surname",
                "GivenNm",
                "Elected",
            ],
            "authority_aec",
        )
        self.assertEqual(detection["status"], "matched")
        self.assertEqual(
            detection["selection"]["adapter_id"], "adapter_aec_2025_v1"
        )


if __name__ == "__main__":
    unittest.main()
