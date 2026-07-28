from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PATH = Path(__file__).with_name("document_partition_plan.py")
SPEC = importlib.util.spec_from_file_location("stage5_v06_document_partition_plan", PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class DocumentPartitionPlanTests(unittest.TestCase):
    def test_next_char_groups(self):
        values = ["F2021A001", "F2021A002", "F2021B001"]
        self.assertEqual(
            module.next_char_groups(values, "F2021"),
            {"A": ["F2021A001", "F2021A002"], "B": ["F2021B001"]},
        )

    def test_prefix_tree_splits_and_reconciles(self):
        values = ["F2021A001", "F2021A002", "F2021B001"]
        counts = {"F2021": 12, "F2021A": 7, "F2021B": 5}
        leaves, nodes = module.build_prefix_tree(
            title_ids=values,
            root_prefixes=["F2021"],
            count_provider=lambda prefix: counts[prefix],
            max_rows=10,
        )
        self.assertEqual([row["prefix"] for row in leaves], ["F2021A", "F2021B"])
        self.assertEqual(sum(row["document_count"] for row in leaves), 12)
        self.assertEqual(len(nodes), 3)

    def test_prefix_tree_rejects_child_coverage_gap(self):
        values = ["F2021A001", "F2021B001"]
        counts = {"F2021": 12, "F2021A": 7, "F2021B": 4}
        with self.assertRaises(RuntimeError):
            module.build_prefix_tree(
                title_ids=values,
                root_prefixes=["F2021"],
                count_provider=lambda prefix: counts[prefix],
                max_rows=10,
            )

    def test_safe_count_url_uses_only_approved_host(self):
        url = module.safe_count_url("startswith(titleId,'F2021')")
        self.assertTrue(url.startswith("https://api.prod.legislation.gov.au/v1/Documents/$count?"))

    def test_page_size_and_order_limits(self):
        self.assertEqual(module.PAGE_SIZE, 100)
        self.assertLessEqual(len(module.ORDER_FIELDS), 5)
        self.assertEqual(module.REQUEST_CEILING, 500)


if __name__ == "__main__":
    unittest.main()
