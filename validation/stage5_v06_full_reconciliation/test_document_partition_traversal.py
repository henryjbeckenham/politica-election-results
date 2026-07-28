from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PATH = Path(__file__).with_name("document_partition_traversal.py")
SPEC = importlib.util.spec_from_file_location("stage5_v06_document_partition_traversal", PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class DocumentPartitionTraversalTests(unittest.TestCase):
    def test_identity_is_order_independent(self):
        left = {field: None for field in module.IDENTITY_FIELDS}
        left.update({"titleId": "F2021L00001", "start": "2021-01-01T00:00:00", "format": "Pdf"})
        right = dict(reversed(list(left.items())))
        self.assertEqual(module.observation_identity(left), module.observation_identity(right))

    def test_order_key_places_null_first(self):
        fields = ["titleId", "start"]
        rows = [
            {"titleId": "F", "start": "2021"},
            {"titleId": "F", "start": None},
        ]
        self.assertEqual(sorted(rows, key=lambda row: module.order_key(row, fields)), [rows[1], rows[0]])

    def test_safe_url_uses_tested_order_limit(self):
        url = module.safe_url(
            "startswith(titleId,'F2021')",
            module.IDENTITY_FIELDS,
            ["titleId", "start", "rectificationVersionNumber", "type", "format"],
            0,
        )
        self.assertIn("api.prod.legislation.gov.au", url)
        with self.assertRaises(RuntimeError):
            module.safe_url(
                "startswith(titleId,'F2021')",
                module.IDENTITY_FIELDS,
                ["a", "b", "c", "d", "e", "f"],
                0,
            )

    def test_validate_leaf(self):
        leaf = {
            "partition_index": 0,
            "prefix": "F2021",
            "filter_expression": "startswith(titleId,'F2021')",
            "document_count": 1000,
            "page_count": 10,
            "page_size": 100,
            "select_fields": module.IDENTITY_FIELDS,
            "order_fields": ["titleId", "start", "rectificationVersionNumber", "type", "format"],
        }
        plan = {
            "status": "passed",
            "select_fields": leaf["select_fields"],
            "order_fields": leaf["order_fields"],
            "leaves": [leaf],
        }
        self.assertEqual(module.validate_leaf(plan, 0), leaf)
        leaf["page_count"] = 101
        with self.assertRaises(RuntimeError):
            module.validate_leaf(plan, 0)

    def test_request_index_is_deterministic(self):
        leaves = [
            {"page_count": 3},
            {"page_count": 5},
            {"page_count": 2},
        ]
        plan_request_count = 150
        partition_index = 2
        base = plan_request_count + sum(row["page_count"] for row in leaves[:partition_index])
        self.assertEqual(base, 158)

    def test_runtime_limits(self):
        self.assertEqual(module.PAGE_SIZE, 100)
        self.assertEqual(module.MAX_PAGES_PER_PARTITION, 100)
        self.assertEqual(module.MAX_ATTEMPTS, 4)
        self.assertEqual(module.RULESET_VERSION, "stage5-v0.6-document-prefix-traversal-1")


if __name__ == "__main__":
    unittest.main()
