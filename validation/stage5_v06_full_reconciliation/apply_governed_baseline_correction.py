from __future__ import annotations

import hashlib
import json
from pathlib import Path

TARGET = Path(__file__).with_name("full_reconciliation.py")
REPORT = Path(__file__).with_name("governed_baseline_correction.json")
OLD_SHA256 = "55e5e6b287514fe81877e54b5da1fa1b3c0b794e04d0e378fbd80cf59a98b740"
OLD_BYTES = 63625
NEW_SHA256 = "3f464ea89beefd1af0cb18a82a6f6e7ab6b0a2249183e055c8fe79e92f8f501a"
NEW_BYTES = 63626
OLD_TEXT = 'ACCEPTED_NORMALIZED_OPENAPI_SHA256 = "6487d878f2aa36b19de6be60ab99b290ca1958a77b1377d50691d7f32a345b2"'
NEW_TEXT = 'ACCEPTED_NORMALIZED_OPENAPI_SHA256 = "6487d878f2aa36b19de6be60ab99b290ca1958a77b1377d50691d7f32a345b2a"'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    before = TARGET.read_bytes()
    if len(before) != OLD_BYTES or digest(before) != OLD_SHA256:
        raise SystemExit("governed pre-correction source does not match the accepted retained input")
    text = before.decode("utf-8")
    if text.count(OLD_TEXT) != 1 or NEW_TEXT in text:
        raise SystemExit("governed baseline hash target is absent or ambiguous")
    after = text.replace(OLD_TEXT, NEW_TEXT, 1).encode("utf-8")
    if len(after) != NEW_BYTES or digest(after) != NEW_SHA256:
        raise SystemExit("governed post-correction source does not match the authorised output")
    TARGET.write_bytes(after)
    REPORT.write_text(
        json.dumps(
            {
                "status": "passed",
                "classification": "governance-recording defect correction, not source-contract drift",
                "field": "accepted normalised OpenAPI SHA-256",
                "before": {"sha256": OLD_SHA256, "byte_count": OLD_BYTES},
                "after": {"sha256": NEW_SHA256, "byte_count": NEW_BYTES},
                "incorrect_value": OLD_TEXT.rsplit('"', 2)[1],
                "correct_value": NEW_TEXT.rsplit('"', 2)[1],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
