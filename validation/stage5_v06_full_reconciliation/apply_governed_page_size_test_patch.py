from __future__ import annotations

import hashlib
import json
from pathlib import Path

TARGET = Path(__file__).with_name("test_full_reconciliation.py")
REPORT = Path(__file__).with_name("governed_page_size_test_patch.json")
PRE_SHA256 = "b0c387bc82e7256036f961d8b43e46bcbdb13873604a216d503b7ac3580b3bbf"
PRE_BYTES = 10171
POST_SHA256 = "a8f79319f13daaabaf23181f4d8a348620a41eb9e8f16c74b7dd24a522d471b0"
POST_BYTES = 10168
REPLACEMENTS = {
    'self.assertEqual(plans[-1]["end_page_exclusive"], 1318)': 'self.assertEqual(plans[-1]["end_page_exclusive"], 264)',
    'for page in range(1318)': 'for page in range(264)',
    'self.assertEqual(module.PAGE_SIZE, 100)': 'self.assertEqual(module.PAGE_SIZE, 500)',
    'self.assertEqual(module.REQUEST_CEILING, 13_000)': 'self.assertEqual(module.REQUEST_CEILING, 3_000)',
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    before = TARGET.read_bytes()
    if len(before) != PRE_BYTES or sha256(before) != PRE_SHA256:
        raise SystemExit("governed pre-page-size test source does not match the accepted patched test runtime")
    text = before.decode("utf-8")
    for old, new in REPLACEMENTS.items():
        if text.count(old) != 1 or new in text:
            raise SystemExit(f"governed page-size test patch anchor is absent or ambiguous: {old}")
        text = text.replace(old, new, 1)
    after = text.encode("utf-8")
    if len(after) != POST_BYTES or sha256(after) != POST_SHA256:
        raise SystemExit("governed post-page-size test source does not match the authorised output")
    TARGET.write_bytes(after)
    REPORT.write_text(
        json.dumps(
            {
                "status": "passed",
                "classification": "test expectation revision for accepted 500-row traversal configuration",
                "before": {"sha256": PRE_SHA256, "byte_count": PRE_BYTES},
                "after": {"sha256": POST_SHA256, "byte_count": POST_BYTES},
                "changes": {
                    "expected_page_size": 500,
                    "expected_request_ceiling": 3000,
                    "expected_title_page_count_for_131783": 264,
                },
                "test_scope_or_safety_requirement_weakened": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
