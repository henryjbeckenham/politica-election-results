"""Shared immutable-release checks for the staged historical-election installers."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

import duckdb

from .app.service import JobService, _sha256_file


ElectionCounts = Callable[[duckdb.DuckDBPyConnection, str], dict[str, int]]


def copy_database_snapshot(
    source: Path,
    destination: Path,
    *,
    error_type: type[Exception] = RuntimeError,
) -> str:
    """Copy and physically materialize an immutable DuckDB base before writing it."""

    if not source.is_file():
        raise error_type("The immutable base database is missing.")
    if destination.exists():
        raise error_type("The candidate database path is already occupied.")
    source_sha256 = _sha256_file(source)
    try:
        shutil.copy2(source, destination)
        descriptor = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise error_type("The candidate database copy could not be made durable.") from exc
    if (
        destination.stat().st_size != source.stat().st_size
        or _sha256_file(destination) != source_sha256
        or _sha256_file(source) != source_sha256
    ):
        raise error_type("The durable candidate database copy failed checksum verification.")
    return source_sha256


def verify_historical_candidate_database(
    service: JobService,
    database: Path,
    release_root: Path,
    *,
    election_id: str,
    expected_counts: dict[str, int],
    expected_active_elections: int,
    election_counts: ElectionCounts,
    error_type: type[Exception] = RuntimeError,
) -> None:
    """Prove that a writer's committed stage is durable after the connection closes."""

    wal = Path(str(database) + ".wal")
    if wal.exists():
        raise error_type("The candidate database still has an uncheckpointed WAL file.")
    if not database.is_file():
        raise error_type("The candidate database disappeared before validation.")
    try:
        descriptor = os.open(database, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(database.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise error_type("The candidate database could not be synchronized to disk.") from exc

    connection = service._connect(
        database, read_only=True, external_data_root=release_root
    )
    try:
        observed_counts = election_counts(connection, election_id)
        active_elections = connection.execute(
            "SELECT count(*) FROM core.election WHERE record_status='active'"
        ).fetchone()[0]
    except duckdb.Error as exc:
        raise error_type(
            "The checkpointed candidate database cannot be reopened for validation."
        ) from exc
    finally:
        connection.close()
    if observed_counts != expected_counts:
        raise error_type(
            "The checkpointed candidate database does not contain its declared election."
        )
    if active_elections != expected_active_elections:
        raise error_type(
            "The checkpointed candidate has an unexpected active-election count."
        )


def verify_historical_release_bundle(
    service: JobService,
    release_root: Path,
    release_id: str,
    *,
    stage_manifest_name: str,
    election_id: str,
    election_validation_key: str,
    expected_active_elections: int,
    election_counts: ElectionCounts,
    error_type: type[Exception] = RuntimeError,
) -> dict:
    """Reopen a sealed release and prove that its stage payload is really present.

    The general database validator intentionally accepts any governed election set. A
    historical installer therefore needs this additional election-specific check before
    it may recover or activate a release directory left by an interrupted process.
    """

    manifest_path = release_root / "release_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise error_type("The retained release manifest cannot be read.") from exc
    if manifest.get("release_id") != release_id:
        raise error_type("An unrelated release already occupies the target path.")
    if manifest.get("validation", {}).get("status") != "PASS":
        raise error_type("The retained release manifest is not passing.")

    database_value = manifest.get("database_path")
    if not isinstance(database_value, str) or not database_value.strip():
        raise error_type("The retained release manifest has no database path.")
    database = (release_root / database_value).resolve()
    try:
        database.relative_to(release_root.resolve())
    except ValueError as exc:
        raise error_type("The retained release database path escapes its release root.") from exc
    if not database.is_file():
        raise error_type("The retained release database is missing.")
    observed_database_sha256 = _sha256_file(database)
    if observed_database_sha256 != manifest.get("database_sha256"):
        raise error_type("The retained release database failed checksum verification.")

    stage_manifest_path = release_root / "data" / "manifests" / stage_manifest_name
    try:
        stage_manifest = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise error_type("The retained stage release manifest cannot be read.") from exc
    validation = stage_manifest.get("validation", {})
    expected_counts = validation.get(election_validation_key)
    if stage_manifest.get("status") != "PASS" or validation.get("status") != "PASS":
        raise error_type("The retained stage release manifest is not passing.")
    if not isinstance(expected_counts, dict) or not expected_counts.get("contests"):
        raise error_type("The retained stage release manifest has no election contract.")

    connection = service._connect(database, read_only=True, external_data_root=release_root)
    try:
        observed_counts = election_counts(connection, election_id)
        active_elections = connection.execute(
            "SELECT count(*) FROM core.election WHERE record_status='active'"
        ).fetchone()[0]
    except duckdb.Error as exc:
        raise error_type("The retained release database cannot be validated.") from exc
    finally:
        connection.close()
    if observed_counts != expected_counts:
        raise error_type(
            "The retained release database does not contain its declared historical election."
        )
    if active_elections != expected_active_elections:
        raise error_type(
            "The retained release database has an unexpected active-election count."
        )

    return {
        "job_id": manifest["job_id"],
        "release_id": release_id,
        "release_root": service._portable_path(release_root),
        "database_path": service._portable_path(database),
        "release_manifest_path": service._portable_path(manifest_path),
        "database_size_bytes": database.stat().st_size,
        "database_sha256": observed_database_sha256,
        "artifact_file_count": manifest["artifact_file_count"],
        "artifact_size_bytes": manifest["artifact_size_bytes"],
        "artifact_manifest_sha256": manifest["artifact_manifest_sha256"],
        "release_manifest_sha256": _sha256_file(manifest_path),
        "release_validation": manifest["validation"],
        "stage_validation": validation,
    }
