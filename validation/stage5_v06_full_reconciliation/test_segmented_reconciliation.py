from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("segmented_reconciliation.py")
SPEC = importlib.util.spec_from_file_location("stage5_v06_segmented_reconciliation", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeRuntime:
    PAGE_SIZE = 100
    APPROVED_HOST = "api.prod.legislation.gov.au"
    RULESET_VERSION = "stage5-v0.6-full-reconciliation-1"


class SegmentedReconciliationTests(unittest.TestCase):
    def test_page_count_uses_governed_page_size(self):
        self.assertEqual(module.page_count_for(131790, 100), 1318)
        self.assertEqual(module.page_count_for(24, 100), 1)
        self.assertEqual(module.page_count_for(555, 100), 6)

    def test_complete_page_sequence(self):
        rows = [{"page": value} for value in range(4)]
        self.assertEqual(
            module.validate_page_sequence(rows, allow_partial=False, planned_pages=4),
            4,
        )

    def test_partial_page_sequence(self):
        rows = [{"page": value} for value in range(3)]
        self.assertEqual(
            module.validate_page_sequence(rows, allow_partial=True, planned_pages=5),
            3,
        )

    def test_page_gap_is_rejected(self):
        with self.assertRaises(RuntimeError):
            module.validate_page_sequence(
                [{"page": 0}, {"page": 2}],
                allow_partial=True,
                planned_pages=3,
            )

    def test_duplicate_page_is_rejected(self):
        with self.assertRaises(RuntimeError):
            module.validate_page_sequence(
                [{"page": 0}, {"page": 0}],
                allow_partial=True,
                planned_pages=2,
            )

    def test_seed_rejects_final_watermark_and_disappearance_advancement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "collections" / "Titles").mkdir(parents=True)
            (root / "reconciliation_configuration.json").write_text(
                json.dumps(
                    {
                        "page_size": 100,
                        "approved_host": FakeRuntime.APPROVED_HOST,
                        "ruleset_version": FakeRuntime.RULESET_VERSION,
                        "max_concurrency": 1,
                    }
                )
            )
            checkpoint = root / "collections" / "Titles" / "checkpoint.json"
            checkpoint.write_text(
                json.dumps(
                    {
                        "final_watermark_committed": False,
                        "disappearance_threshold_advanced": False,
                    }
                )
            )
            module.assert_seed_compatible(FakeRuntime, root)
            checkpoint.write_text(
                json.dumps(
                    {
                        "final_watermark_committed": True,
                        "disappearance_threshold_advanced": False,
                    }
                )
            )
            with self.assertRaises(RuntimeError):
                module.assert_seed_compatible(FakeRuntime, root)
            checkpoint.write_text(
                json.dumps(
                    {
                        "final_watermark_committed": False,
                        "disappearance_threshold_advanced": True,
                    }
                )
            )
            with self.assertRaises(RuntimeError):
                module.assert_seed_compatible(FakeRuntime, root)

    def test_request_indices_must_be_unique(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "collections" / "Titles").mkdir(parents=True)
            (root / "request_manifest.jsonl").write_text(
                '{"request_index": 0}\n'
            )
            (root / "collections" / "Titles" / "page_manifest.jsonl").write_text(
                '{"request_index": 0, "page": 0}\n'
            )
            with self.assertRaises(RuntimeError):
                module.next_request_index(root)

    def test_request_index_continues_after_retained_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "collections" / "Titles").mkdir(parents=True)
            (root / "request_manifest.jsonl").write_text(
                '{"request_index": 0}\n{"request_index": 1}\n'
            )
            (root / "collections" / "Titles" / "page_manifest.jsonl").write_text(
                '{"request_index": 2, "page": 0}\n'
            )
            self.assertEqual(module.next_request_index(root), 3)


if __name__ == "__main__":
    unittest.main()
