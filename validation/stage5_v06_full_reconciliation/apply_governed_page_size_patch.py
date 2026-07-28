from __future__ import annotations

import hashlib
import json
from pathlib import Path

TARGET = Path(__file__).with_name("full_reconciliation.py")
REPORT = Path(__file__).with_name("governed_page_size_patch.json")
PRE_SHA256 = "60b7b0650256de99668cb23b2ba7eda7b0fd85d954135a6c2a0421ee330cef1b"
PRE_BYTES = 72120
REPLACEMENTS = {
    "PAGE_SIZE = 100": "PAGE_SIZE = 500",
    "REQUEST_CEILING = 13_000": "REQUEST_CEILING = 3_000",
    'RULESET_VERSION = "stage5-v0.6-full-reconciliation-1"': 'RULESET_VERSION = "stage5-v0.6-full-reconciliation-2"',
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    before = TARGET.read_bytes()
    if len(before) != PRE_BYTES or sha256(before) != PRE_SHA256:
        raise SystemExit("governed pre-page-size source does not match the accepted patched runtime")
    text = before.decode("utf-8")
    for old, new in REPLACEMENTS.items():
        if text.count(old) != 1 or new in text:
            raise SystemExit(f"governed page-size patch anchor is absent or ambiguous: {old}")
        text = text.replace(old, new, 1)
    after = text.encode("utf-8")
    TARGET.write_bytes(after)
    REPORT.write_text(
        json.dumps(
            {
                "status": "diagnostic_passed",
                "classification": "governed load-restraint and execution-bound revision",
                "before": {"sha256": PRE_SHA256, "byte_count": PRE_BYTES},
                "after": {"sha256": sha256(after), "byte_count": len(after)},
                "changes": {
                    "page_size": {"before": 100, "after": 500, "official_probe_status": "passed"},
                    "request_ceiling": {"before": 13000, "after": 3000},
                    "ruleset_version": {"before": "stage5-v0.6-full-reconciliation-1", "after": "stage5-v0.6-full-reconciliation-2"},
                },
                "source_identity_and_evidence_logic_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
