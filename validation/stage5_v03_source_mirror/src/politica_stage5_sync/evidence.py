from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .http import HttpResponse


class EvidenceWriter:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "request_manifest.jsonl"

    def retain(self, label: str, response: HttpResponse, *, request_parameters: dict[str, Any]) -> dict[str, Any]:
        stem = f"{label}_{response.sha256[:12]}"
        body_path = self.root / f"{stem}.body"
        headers_path = self.root / f"{stem}.headers.json"
        if not body_path.exists():
            body_path.write_bytes(response.body)
        if not headers_path.exists():
            headers_path.write_text(json.dumps(response.headers, indent=2), encoding="utf-8")
        record = {
            "label": label,
            "requested_url": response.requested_url,
            "final_url": response.final_url,
            "status": response.status,
            "content_type": response.content_type,
            "byte_count": len(response.body),
            "response_sha256": response.sha256,
            "attempt": response.attempt,
            "duration_ms": response.duration_ms,
            "request_parameters": request_parameters,
            "body_file": body_path.name,
            "headers_file": headers_path.name,
        }
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return record
