from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from filelock import FileLock


class JobNotFoundError(KeyError):
    pass


class JobConflictError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Small, durable JSON job store with process-safe writes."""

    def __init__(self, jobs_root: Path):
        self.jobs_root = jobs_root
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / job_id

    def metadata_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def create(self, metadata: dict) -> dict:
        job_id = metadata["job_id"]
        directory = self.job_dir(job_id)
        try:
            directory.mkdir(parents=False)
        except FileExistsError as exc:
            raise JobConflictError(f"Job {job_id} already exists") from exc
        (directory / "uploads").mkdir()
        (directory / "work").mkdir()
        self._write_unlocked(job_id, metadata)
        return metadata

    @contextmanager
    def _lock(self, job_id: str, *, shared: bool = False) -> Iterator[None]:
        directory = self.job_dir(job_id)
        if not directory.is_dir():
            raise JobNotFoundError(job_id)
        lock_path = directory / ".lock"
        # FileLock uses platform-native locking on Windows and POSIX. Reads are
        # deliberately exclusive too: job documents are small and this keeps
        # the durability contract identical on every supported desktop.
        del shared
        with FileLock(str(lock_path)):
            yield

    def read(self, job_id: str) -> dict:
        with self._lock(job_id, shared=True):
            return self._read_unlocked(job_id)

    def list(self) -> list[dict]:
        jobs: list[dict] = []
        for path in sorted(self.jobs_root.glob("*/job.json")):
            try:
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True)

    def mutate(self, job_id: str, mutation: Callable[[dict], None]) -> dict:
        with self._lock(job_id):
            metadata = self._read_unlocked(job_id)
            mutation(metadata)
            metadata["updated_at"] = utc_now()
            self._write_unlocked(job_id, metadata)
            return metadata

    def _read_unlocked(self, job_id: str) -> dict:
        try:
            return json.loads(self.metadata_path(job_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise JobNotFoundError(job_id) from exc

    def _write_unlocked(self, job_id: str, metadata: dict) -> None:
        destination = self.metadata_path(job_id)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="job-", suffix=".json.tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(metadata, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
