from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def validate_disposable_database_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("database URL must use postgres or postgresql")
    hostname = (parsed.hostname or "").lower()
    database = (parsed.path or "").lower()
    combined = f"{hostname}{database}"
    forbidden = ("prod", "production", "politica-live", "primary")
    if any(marker in combined for marker in forbidden):
        raise ValueError("production-like database target rejected")


def run_psql_package(database_url: str, sql_files: list[Path], evidence_dir: Path) -> dict:
    validate_disposable_database_url(database_url)
    psql = shutil.which("psql")
    if not psql:
        raise RuntimeError("psql is not installed in this execution environment")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([psql, database_url, "-Atc", "SHOW server_version_num"], capture_output=True, text=True)
    if version.returncode != 0:
        raise RuntimeError(version.stderr.strip())
    if not version.stdout.strip().startswith("18"):
        raise RuntimeError(f"PostgreSQL 18.x required, found {version.stdout.strip()}")
    results = []
    for sql_file in sql_files:
        completed = subprocess.run([psql, database_url, "-v", "ON_ERROR_STOP=1", "-f", str(sql_file)], capture_output=True, text=True)
        (evidence_dir / f"{sql_file.name}.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (evidence_dir / f"{sql_file.name}.stderr.log").write_text(completed.stderr, encoding="utf-8")
        results.append({"file": str(sql_file), "exit_code": completed.returncode})
        if completed.returncode != 0:
            raise RuntimeError(f"psql failed for {sql_file.name}")
    return {"server_version_num": version.stdout.strip(), "files": results, "production_target": False}
