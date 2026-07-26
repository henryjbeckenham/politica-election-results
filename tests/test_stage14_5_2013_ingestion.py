import hashlib
import json
import unittest
from pathlib import Path

import duckdb
import yaml

from politica_erd.build import PROJECT_ROOT
from politica_erd.app.detection import AdapterCatalogue
from politica_erd.import_2013 import ELECTION_ID


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Stage145HistoricalIngestionTests(unittest.TestCase):
    def test_all_47_official_aec_sources_are_present_and_checksum_pinned(self):
        manifest = json.loads(
            (PROJECT_ROOT / "data/manifests/aec_2013_sources.json").read_text(
                encoding="utf-8"
            )
        )
        checksum_contract = yaml.safe_load(
            (PROJECT_ROOT / "config/source_checksums_2013.yml").read_text(
                encoding="utf-8"
            )
        )["sha256_by_source_key"]
        self.assertEqual(manifest["election_id"], ELECTION_ID)
        self.assertEqual(manifest["event_id"], "17496")
        self.assertEqual(manifest["source_count"], 47)
        self.assertEqual(set(checksum_contract), {row["key"] for row in manifest["sources"]})
        self.assertEqual(
            manifest["total_size_bytes"],
            sum(row["size_bytes"] for row in manifest["sources"]),
        )
        for row in manifest["sources"]:
            source = PROJECT_ROOT / row["path"]
            self.assertTrue(source.is_file(), source)
            self.assertTrue(row["url"].startswith("https://results.aec.gov.au/17496/"))
            self.assertEqual(source.stat().st_size, row["size_bytes"])
            self.assertEqual(sha256(source), row["sha256"])
            self.assertEqual(row["sha256"], checksum_contract[row["key"]])

    def test_prevalidated_delta_contains_the_complete_relational_import(self):
        manifest_path = PROJECT_ROOT / "data/manifests/aec_2013_delta_tables.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        core = dict(manifest)
        recorded = core.pop("manifest_sha256")
        observed = hashlib.sha256(
            json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(recorded, observed)
        self.assertEqual(manifest["election_id"], ELECTION_ID)
        self.assertEqual(manifest["table_count"], 56)
        tables = {
            f"{row['schema']}.{row['table']}": row for row in manifest["tables"]
        }
        expected_counts = {
            "core.contest": 158,
            "core.candidacy": 1717,
            "geography.election_reporting_unit": 9304,
            "results.participation_result": 1896,
            "results.contest_outcome": 190,
            "count.count_round": 2525,
            "count.count_candidate_total": 131071,
            "count.preference_transfer": 14633,
            "ballot.ballot_dataset": 8,
            "ballot.group_voting_ticket": 265,
            "ballot.group_voting_ticket_preference": 20492,
            "provenance.source_file_revision": 47,
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
            (PROJECT_ROOT / "dist/stage_14_5_2013_import_report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["validation"]["blocker_count"], 0)
        self.assertEqual(report["source_count"], 47)
        self.assertEqual(report["table_counts"]["contests"], 158)
        self.assertEqual(report["table_counts"]["candidacies"], 1717)
        self.assertEqual(report["table_counts"]["formal_ballots"], 471_030)
        self.assertEqual(
            report["table_counts"]["formal_ballot_preferences"], 33_704_176
        )

    def test_pre_reform_ballots_and_group_tickets_reconcile(self):
        report = json.loads(
            (PROJECT_ROOT / "dist/stage_14_5_2013_import_report.json").read_text(
                encoding="utf-8"
            )
        )
        formal = report["formal_preferences"]
        self.assertEqual(formal["ballot_count"], 471_030)
        self.assertEqual(formal["preference_count"], 33_704_176)
        self.assertEqual(formal["above_the_line_ballot_count"], 0)
        self.assertEqual(formal["below_the_line_ballot_count"], 471_030)
        self.assertEqual(formal["group_ticket_vote_count"], 12_941_989)
        self.assertEqual(formal["formal_vote_count"], 13_413_019)
        self.assertEqual(formal["file_count"], 95)
        self.assertEqual(report["table_counts"]["group_voting_tickets"], 265)
        self.assertEqual(
            report["table_counts"]["group_voting_ticket_preferences"], 20_492
        )

    def test_complete_senate_count_and_voided_wa_result_are_retained(self):
        report = json.loads(
            (PROJECT_ROOT / "dist/stage_14_5_2013_import_report.json").read_text(
                encoding="utf-8"
            )
        )
        senate_rows = report["senate_count_rows"]["candidate_totals"]
        self.assertEqual(senate_rows, 122_305)
        self.assertEqual(report["table_counts"]["outcomes"], 190)
        contests = PROJECT_ROOT / "data/stage14_5/tables/core/contest.parquet"
        outcomes = (
            PROJECT_ROOT
            / "data/stage14_5/tables/results/contest_outcome.parquet"
        )
        connection = duckdb.connect()
        try:
            observed = connection.execute(
                """SELECT c.contest_status, o.publication_status, count(*)
                   FROM read_parquet(?) c
                   JOIN read_parquet(?) o USING (contest_id)
                   WHERE c.official_contest_id='WA'
                   GROUP BY c.contest_status, o.publication_status""",
                [str(contests), str(outcomes)],
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(observed, [("void", "superseded", 6)])

    def test_2013_map_contract_uses_the_150_official_aec_divisions(self):
        contract = yaml.safe_load(
            (PROJECT_ROOT / "config/electorate_boundaries_2013.yml").read_text(
                encoding="utf-8"
            )
        )
        geometry_path = PROJECT_ROOT / contract["derived_geometry"]["source_path"]
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        self.assertEqual(contract["election_id"], ELECTION_ID)
        self.assertEqual(contract["feature_count"], 150)
        self.assertEqual(len(geometry["features"]), 150)
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
        self.assertIn("election_fed_2013_09_07_general", results)
        self.assertIn("Western Australia's six outcomes are marked as later voided", results)
        self.assertIn("/feeds/${election}${feedId}", dom)
        self.assertIn("/visualisations${suffix}", dom)

    def test_batch_2013_adapter_does_not_shadow_interactive_import_detection(self):
        config = yaml.safe_load(
            (PROJECT_ROOT / "config/adapters/aec_2013.yml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(config["catalogue_visibility"], "batch_only")
        self.assertEqual(config["election"]["election_id"], ELECTION_ID)
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
