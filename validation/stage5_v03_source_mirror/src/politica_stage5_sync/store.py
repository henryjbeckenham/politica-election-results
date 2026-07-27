from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Candidate, RunCheckpoint, RunMetrics
from .strategy import candidate_identity, canonical_json_hash


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonStateStore:
    """Atomic disposable state store for runner integration and restart tests."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            self._save(
                {
                    "runs": {},
                    "checkpoints": {},
                    "candidates": {},
                    "raw_captures": {},
                    "watermarks": {},
                }
            )

    def _load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name, dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def start_run(self, run_id: str, payload: dict[str, Any]) -> None:
        state = self._load()
        if run_id in state["runs"]:
            raise ValueError(f"run already exists: {run_id}")
        state["runs"][run_id] = {**payload, "status": "running", "started_at": _now()}
        self._save(state)

    def resume_run(self, run_id: str) -> None:
        state = self._load()
        run = state["runs"].get(run_id)
        if not run:
            raise ValueError(f"run does not exist: {run_id}")
        if run["status"] not in {"interrupted", "failed"}:
            raise ValueError("only interrupted or failed runs can be resumed")
        run["status"] = "running"
        run["resumed_at"] = _now()
        self._save(state)

    def finish_run(self, run_id: str, status: str, metrics: RunMetrics, error: str | None = None) -> None:
        state = self._load()
        run = state["runs"][run_id]
        run.update({"status": status, "finished_at": _now(), "metrics": metrics.as_dict(), "error": error})
        self._save(state)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._load()["runs"][run_id]

    def put_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        state = self._load()
        key = f"{checkpoint.run_id}:{checkpoint.entity_set}:{checkpoint.partition}"
        state["checkpoints"][key] = {
            "run_id": checkpoint.run_id,
            "entity_set": checkpoint.entity_set,
            "partition": checkpoint.partition,
            "cursor": checkpoint.cursor,
            "completed_pages": checkpoint.completed_pages,
            "candidate_count": checkpoint.candidate_count,
            "updated_at": checkpoint.updated_at.isoformat(),
            "configuration_hash": checkpoint.configuration_hash,
            "contract_hash": checkpoint.contract_hash,
        }
        self._save(state)

    def get_checkpoint(self, run_id: str, entity_set: str, partition: int = 0) -> dict[str, Any] | None:
        return self._load()["checkpoints"].get(f"{run_id}:{entity_set}:{partition}")

    def upsert_candidate(self, candidate: Candidate, item: dict[str, Any], run_id: str) -> str:
        state = self._load()
        identity = candidate_identity(candidate)
        existing = state["candidates"].get(identity)
        item_hash = canonical_json_hash(item)
        outcome = "created" if existing is None else ("unchanged" if existing["item_hash"] == item_hash else "changed")
        state["candidates"][identity] = {
            "entity_type": candidate.entity_type,
            "external_identifier": candidate.external_identifier,
            "source_url": candidate.source_url,
            "observed_fingerprint": candidate.observed_fingerprint,
            "item_hash": item_hash,
            "first_seen_run_id": run_id if existing is None else existing["first_seen_run_id"],
            "last_seen_run_id": run_id,
            "item": item,
        }
        self._save(state)
        return outcome

    def put_raw_capture(self, record: dict[str, Any]) -> bool:
        state = self._load()
        capture_identity = canonical_json_hash(
            {
                "requested_url": record["requested_url"],
                "status": record["status"],
                "response_sha256": record["response_sha256"],
            }
        )
        created = capture_identity not in state["raw_captures"]
        state["raw_captures"].setdefault(capture_identity, record)
        self._save(state)
        return created

    def commit_watermarks(self, watermarks: dict[str, dict[str, Any]]) -> None:
        state = self._load()
        state["watermarks"].update(watermarks)
        self._save(state)

    def commit_watermark(self, entity_set: str, watermark: dict[str, Any]) -> None:
        self.commit_watermarks({entity_set: watermark})

    def get_watermark(self, entity_set: str) -> dict[str, Any] | None:
        return self._load()["watermarks"].get(entity_set)

    def snapshot(self) -> dict[str, Any]:
        return self._load()
