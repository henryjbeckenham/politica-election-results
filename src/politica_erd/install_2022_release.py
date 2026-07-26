"""Create and activate the immutable Stage 14.2 multi-election release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
from filelock import FileLock

from .app.config import AppSettings
from .app.service import JobService, _sha256_file
from .formal_preferences import _replace_ballot_views
from .historical_release import (
    copy_database_snapshot,
    verify_historical_candidate_database,
    verify_historical_release_bundle,
)
from .validate import validate_database


ELECTION_2022 = "election_fed_2022_05_21_general"
ELECTION_2025 = "election_fed_2025_05_03_general"
STAGE_VERSION = "14.2"
APPLICATION_VERSION = "1.4.0"


class HistoricalReleaseError(RuntimeError):
    pass


def _election_counts(
    connection: duckdb.DuckDBPyConnection, election_id: str
) -> dict[str, int]:
    return {
        "contests": connection.execute(
            """SELECT count(*) FROM core.contest c
               JOIN core.election_chamber ec USING (election_chamber_id)
               WHERE ec.election_id=?""",
            [election_id],
        ).fetchone()[0],
        "candidacies": connection.execute(
            """SELECT count(*) FROM core.candidacy ca
               JOIN core.contest c USING (contest_id)
               JOIN core.election_chamber ec USING (election_chamber_id)
               WHERE ec.election_id=?""",
            [election_id],
        ).fetchone()[0],
        "vote_results": connection.execute(
            "SELECT count(*) FROM results.vote_result WHERE election_id=?",
            [election_id],
        ).fetchone()[0],
        "participation_results": connection.execute(
            "SELECT count(*) FROM results.participation_result WHERE election_id=?",
            [election_id],
        ).fetchone()[0],
        "outcomes": connection.execute(
            """SELECT count(*) FROM results.contest_outcome o
               JOIN core.contest c USING (contest_id)
               JOIN core.election_chamber ec USING (election_chamber_id)
               WHERE ec.election_id=?""",
            [election_id],
        ).fetchone()[0],
        "count_rounds": connection.execute(
            """SELECT count(*) FROM \"count\".count_round r
               JOIN core.contest c USING (contest_id)
               JOIN core.election_chamber ec USING (election_chamber_id)
               WHERE ec.election_id=?""",
            [election_id],
        ).fetchone()[0],
        "count_candidate_totals": connection.execute(
            """SELECT count(*) FROM \"count\".count_candidate_total t
               JOIN \"count\".count_round r USING (count_round_id)
               JOIN core.contest c USING (contest_id)
               JOIN core.election_chamber ec USING (election_chamber_id)
               WHERE ec.election_id=?""",
            [election_id],
        ).fetchone()[0],
        "ballot_datasets": connection.execute(
            """SELECT count(*) FROM ballot.ballot_dataset d
               JOIN core.election_chamber ec USING (election_chamber_id)
               WHERE ec.election_id=?""",
            [election_id],
        ).fetchone()[0],
        "formal_ballots": connection.execute(
            """SELECT count(*) FROM ballot.ballot b
               JOIN ballot.ballot_dataset d USING (ballot_dataset_id)
               JOIN core.election_chamber ec USING (election_chamber_id)
               WHERE ec.election_id=?""",
            [election_id],
        ).fetchone()[0],
        "source_revisions": connection.execute(
            """SELECT count(*) FROM provenance.source_file_revision r
               JOIN provenance.source_file f USING (source_file_id)
               WHERE f.election_id=?""",
            [election_id],
        ).fetchone()[0],
    }


def _source_identity(connection: duckdb.DuckDBPyConnection, election_id: str) -> str:
    rows = connection.execute(
        """SELECT r.source_revision_id, r.sha256
           FROM provenance.source_file_revision r
           JOIN provenance.source_file f USING (source_file_id)
           WHERE f.election_id=? ORDER BY r.source_revision_id""",
        [election_id],
    ).fetchall()
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _copy_2022_artifacts(project_root: Path, release_root: Path) -> None:
    source_parquet = project_root / "data" / "parquet" / "aec_2022"
    source_raw = project_root / "data" / "raw" / "aec" / "2022_federal"
    if not source_parquet.is_dir() or not any(source_parquet.rglob("*.parquet")):
        raise HistoricalReleaseError("The verified 2022 formal-ballot Parquet set is missing.")
    if not source_raw.is_dir() or not any(source_raw.rglob("*")):
        raise HistoricalReleaseError("The governed 2022 raw-source set is missing.")
    destination_parquet = release_root / "data" / "parquet" / "aec_2022"
    destination_raw = release_root / "data" / "raw" / "aec" / "2022_federal"
    shutil.copytree(
        source_parquet,
        destination_parquet,
        copy_function=shutil.copy2,
        dirs_exist_ok=True,
    )
    shutil.copytree(
        source_raw,
        destination_raw,
        copy_function=shutil.copy2,
        dirs_exist_ok=True,
    )
    destination_manifests = release_root / "data" / "manifests"
    destination_manifests.mkdir(parents=True, exist_ok=True)
    for source in sorted((project_root / "data" / "manifests").glob("aec_2022*")):
        if source.is_file():
            shutil.copy2(source, destination_manifests / source.name)


def _verified_manifest_files(
    project_root: Path,
    manifest: dict,
    rows: list[dict],
    *,
    label: str,
) -> None:
    for row in rows:
        value = row.get("path")
        if not isinstance(value, str) or not value:
            raise HistoricalReleaseError(f"A {label} manifest path is missing.")
        path = (project_root / value).resolve()
        try:
            path.relative_to(project_root)
        except ValueError as exc:
            raise HistoricalReleaseError(
                f"A {label} manifest path escapes the project root."
            ) from exc
        observed_size = path.stat().st_size if path.is_file() else None
        observed_sha256 = _sha256_file(path) if path.is_file() else None
        expected_size = row.get("size_bytes")
        if (
            (expected_size is not None and observed_size != expected_size)
            or observed_sha256 != row.get("sha256")
        ):
            raise HistoricalReleaseError(
                f"A packaged {label} file failed checksum verification: {value}; "
                f"size {observed_size!r} != {expected_size!r}; "
                f"SHA-256 {observed_sha256!r} != {row.get('sha256')!r}."
            )


def _verify_packaged_2022_artifacts(project_root: Path) -> None:
    manifest_root = project_root / "data" / "manifests"
    source_manifest = json.loads(
        (manifest_root / "aec_2022_sources.json").read_text(encoding="utf-8")
    )
    if (
        source_manifest.get("election_id") != ELECTION_2022
        or source_manifest.get("source_count") != 45
        or len(source_manifest.get("sources") or []) != 45
    ):
        raise HistoricalReleaseError("The governed 2022 source manifest is incomplete.")
    _verified_manifest_files(
        project_root,
        source_manifest,
        source_manifest["sources"],
        label="AEC 2022 source",
    )

    formal_manifest = json.loads(
        (manifest_root / "aec_2022_formal_preferences.json").read_text(
            encoding="utf-8"
        )
    )
    expected_formal = {
        "state_count": 8,
        "ballot_count": 15_040_658,
        "preference_count": 101_100_266,
        "file_count": 35,
    }
    if {key: formal_manifest.get(key) for key in expected_formal} != expected_formal:
        raise HistoricalReleaseError(
            "The packaged 2022 formal-preference manifest does not reconcile."
        )
    _verified_manifest_files(
        project_root,
        formal_manifest,
        formal_manifest.get("files") or [],
        label="2022 formal-preference Parquet",
    )

    fact_manifest = json.loads(
        (manifest_root / "aec_2022_parquet.json").read_text(encoding="utf-8")
    )
    partitions = fact_manifest.get("partitions") or []
    if fact_manifest.get("partition_count") != len(partitions) or len(partitions) != 30:
        raise HistoricalReleaseError("The packaged 2022 fact-Parquet manifest is incomplete.")
    _verified_manifest_files(
        project_root,
        fact_manifest,
        partitions,
        label="2022 fact Parquet",
    )


def _merge_delta(
    connection: duckdb.DuckDBPyConnection,
    delta_manifest: dict,
    project_root: Path,
    release_root: Path,
    release_id: str,
) -> dict:
    schema_order = {
        name: index
        for index, name in enumerate(
            (
                "control", "sync", "core", "geography", "provenance", "staging",
                "results", "count", "ballot", "audit", "derived", "publish",
            )
        )
    }
    tables = list(delta_manifest["tables"])
    tables.sort(
        key=lambda row: (
            schema_order.get(row["schema"], 999), row["schema"], row["table"]
        )
    )
    inserted: dict[str, int] = {}
    connection.execute("BEGIN TRANSACTION")
    try:
        for entry in tables:
            schema, table = entry["schema"], entry["table"]
            source = (project_root / entry["path"]).resolve()
            try:
                source.relative_to(project_root)
            except ValueError as exc:
                raise HistoricalReleaseError("A delta-table path escapes the project root.") from exc
            if (
                not source.is_file()
                or source.stat().st_size != entry["size_bytes"]
                or _sha256_file(source) != entry["sha256"]
            ):
                raise HistoricalReleaseError(
                    f"The packaged delta table failed checksum verification: {schema}.{table}"
                )
            before = connection.execute(
                f'SELECT count(*) FROM "{schema}"."{table}"'
            ).fetchone()[0]
            escaped = str(source).replace("'", "''")
            connection.execute(
                f'INSERT OR IGNORE INTO "{schema}"."{table}" '
                f"SELECT * FROM read_parquet('{escaped}')"
            )
            after = connection.execute(
                f'SELECT count(*) FROM "{schema}"."{table}"'
            ).fetchone()[0]
            inserted[f"{schema}.{table}"] = after - before
        now = datetime.now(timezone.utc)
        schema_version = connection.execute(
            "SELECT schema_version FROM control.schema_version ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            """INSERT OR IGNORE INTO control.database_release VALUES
               (?, ?, 'validated', ?, ?, 'Politica Stage 14.2 updater', ?)""",
            [
                release_id,
                schema_version,
                now,
                now,
                (
                    "Added the complete governed 2022 federal election alongside the "
                    "unchanged 2025 election, with election-specific publication feeds."
                ),
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    _replace_ballot_views(
        SimpleNamespace(connection=connection), release_root / "data" / "parquet"
    )
    connection.execute("PRAGMA enable_checkpoint_on_shutdown")
    connection.execute("PRAGMA force_checkpoint")
    return inserted


def _stage14_2_checks(
    connection: duckdb.DuckDBPyConnection,
    base_2025_counts: dict[str, int],
    base_2025_sources: str,
) -> dict:
    expected_2022 = {
        "contests": 159,
        "candidacies": 1624,
        "vote_results": 230488,
        "participation_results": 1908,
        "outcomes": 191,
        "count_rounds": 2670,
        "count_candidate_totals": 115892,
        "ballot_datasets": 8,
        "formal_ballots": 15040658,
        "source_revisions": 45,
    }
    observed_2022 = _election_counts(connection, ELECTION_2022)
    observed_2025 = _election_counts(connection, ELECTION_2025)
    checks = {
        "active_election_count": (
            connection.execute(
                "SELECT count(*) FROM core.election WHERE record_status='active'"
            ).fetchone()[0],
            2,
        ),
        "2022_counts": (observed_2022, expected_2022),
        "2025_counts_preserved": (observed_2025, base_2025_counts),
        "2025_source_identity_preserved": (
            _source_identity(connection, ELECTION_2025),
            base_2025_sources,
        ),
        "historical_constituencies_matched": (
            connection.execute(
                """SELECT count(*) FROM core.contest_constituency_snapshot s
                   JOIN core.contest c USING (contest_id)
                   JOIN core.election_chamber ec USING (election_chamber_id)
                   WHERE ec.election_id=? AND s.match_status<>'matched'
                     AND s.constituency_type='federal_lower_house_division'""",
                [ELECTION_2022],
            ).fetchone()[0],
            0,
        ),
    }
    failures = [
        {"check": name, "observed": observed, "expected": expected}
        for name, (observed, expected) in checks.items()
        if observed != expected
    ]
    return {
        "status": "PASS" if not failures else "FAIL",
        "stage": "stage_14_2_2022_multi_election_release",
        "checks": len(checks),
        "failures": failures,
        "election_2022": observed_2022,
        "election_2025": observed_2025,
    }


def _existing_publication(
    service: JobService, release_root: Path, release_id: str
) -> dict:
    publication = verify_historical_release_bundle(
        service,
        release_root,
        release_id,
        stage_manifest_name="stage_14_2_release.json",
        election_id=ELECTION_2022,
        election_validation_key="election_2022",
        expected_active_elections=2,
        election_counts=_election_counts,
        error_type=HistoricalReleaseError,
    )
    publication["activated_at"] = datetime.now(timezone.utc).isoformat()
    return publication


def install_2022_release(settings: AppSettings | None = None) -> dict:
    settings = settings or AppSettings.from_environment()
    service = JobService(settings)
    project_root = settings.project_root.resolve()
    delta_manifest_path = project_root / "data" / "manifests" / "aec_2022_delta_tables.json"
    import_report_path = project_root / "dist" / "stage_14_2_2022_import_report.json"
    if not delta_manifest_path.is_file() or not import_report_path.is_file():
        raise HistoricalReleaseError("The prevalidated Stage 14.2 import assets are incomplete.")
    delta_manifest = json.loads(delta_manifest_path.read_text(encoding="utf-8"))
    manifest_core = dict(delta_manifest)
    recorded_manifest_sha256 = manifest_core.pop("manifest_sha256", None)
    observed_manifest_sha256 = hashlib.sha256(
        json.dumps(manifest_core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if recorded_manifest_sha256 != observed_manifest_sha256:
        raise HistoricalReleaseError("The 2022 delta-table manifest failed checksum verification.")
    import_report = json.loads(import_report_path.read_text(encoding="utf-8"))
    if import_report.get("status") != "PASS":
        raise HistoricalReleaseError("The packaged 2022 import report is not passing.")
    _verify_packaged_2022_artifacts(project_root)

    with FileLock(str(settings.releases_root / ".stage14_2.lock")):
        base = service.capture_base_release(include_artifacts=True)
        base_database = service._resolve_portable_path(base["database_path"])
        base_connection = service._connect(base_database, read_only=True)
        try:
            installed = base_connection.execute(
                "SELECT count(*) FROM core.election WHERE election_id=?",
                [ELECTION_2022],
            ).fetchone()[0]
            if installed:
                return {
                    "status": "ALREADY_INSTALLED",
                    "database_path": service._portable_path(base_database),
                    "database_sha256": base["database_sha256"],
                    "election_2022": _election_counts(base_connection, ELECTION_2022),
                }
            base_2025_counts = _election_counts(base_connection, ELECTION_2025)
            base_2025_sources = _source_identity(base_connection, ELECTION_2025)
        finally:
            base_connection.close()

        delta_sha256 = recorded_manifest_sha256
        token = hashlib.sha256(
            f"{base['database_sha256']}|{delta_sha256}|{APPLICATION_VERSION}".encode("utf-8")
        ).hexdigest()[:16]
        job_id = f"stage14_2_2022_ingestion_{token}"
        release_id = f"release_1_4_0_2022_{token}"
        release_root = settings.releases_root / f"politica-stage14-2-{token}"
        temporary = settings.releases_root / (
            f".politica-stage14-2-{token}.tmp-{uuid.uuid4().hex}"
        )
        if release_root.exists():
            publication = _existing_publication(service, release_root, release_id)
            service._activate_release(publication, expected_generation=base["generation"])
            publication["status"] = "RECOVERED_AND_ACTIVATED"
            return publication

        try:
            temporary.mkdir(parents=False)
            candidate_database = (
                temporary / "data" / "database" / "politica_election_results.duckdb"
            )
            candidate_database.parent.mkdir(parents=True)
            copy_database_snapshot(
                base_database,
                candidate_database,
                error_type=HistoricalReleaseError,
            )
            artifact_source = service._resolve_portable_path(base["artifact_root"])
            service._copy_release_artifacts(
                source_root=artifact_source,
                release_root=temporary,
                database=candidate_database,
                expected_artifacts=base.get("artifact_files"),
            )
            _copy_2022_artifacts(project_root, temporary)
            candidate = service._connect(
                candidate_database, external_data_root=temporary
            )
            try:
                inserted = _merge_delta(
                    candidate, delta_manifest, project_root, temporary, release_id
                )
                stage_validation = _stage14_2_checks(
                    candidate, base_2025_counts, base_2025_sources
                )
            finally:
                candidate.close()
            verify_historical_candidate_database(
                service,
                candidate_database,
                temporary,
                election_id=ELECTION_2022,
                expected_counts=stage_validation["election_2022"],
                expected_active_elections=2,
                election_counts=_election_counts,
                error_type=HistoricalReleaseError,
            )
            if stage_validation["status"] != "PASS":
                raise HistoricalReleaseError(
                    "The merged multi-election release failed Stage 14.2 reconciliation: "
                    + json.dumps(stage_validation["failures"], ensure_ascii=False)
                )
            if _sha256_file(base_database) != base["database_sha256"]:
                raise HistoricalReleaseError("The immutable 2025 base release changed during installation.")

            stage_manifest = {
                "stage": STAGE_VERSION,
                "application_version": APPLICATION_VERSION,
                "status": "PASS",
                "base_release_id": base.get("release_id"),
                "base_database_sha256": base["database_sha256"],
                "delta_database_sha256": delta_sha256,
                "inserted_rows_by_table": inserted,
                "validation": stage_validation,
                "source_manifest_sha256": import_report["source_manifest_sha256"],
            }
            manifest_path = temporary / "data" / "manifests" / "stage_14_2_release.json"
            manifest_path.write_text(
                json.dumps(stage_manifest, indent=2) + "\n", encoding="utf-8"
            )
            release_validation = validate_database(candidate_database, temporary)
            if release_validation["status"] != "PASS":
                raise HistoricalReleaseError(
                    "The merged release failed the full database validator: "
                    + json.dumps(release_validation["failures"], ensure_ascii=False)
                )
            release_sha256 = _sha256_file(candidate_database)
            publication = {"job_id": job_id, "release_id": release_id}
            release_manifest = service._write_release_manifest(
                temporary,
                publication=publication,
                database_sha256=release_sha256,
                validation=release_validation,
                artifact_source=project_root,
            )
            os.replace(temporary, release_root)
            release_database = (
                release_root / "data" / "database" / "politica_election_results.duckdb"
            )
            release_manifest_path = release_root / "release_manifest.json"
            publication.update(
                {
                    "release_root": service._portable_path(release_root),
                    "database_path": service._portable_path(release_database),
                    "release_manifest_path": service._portable_path(release_manifest_path),
                    "database_size_bytes": release_database.stat().st_size,
                    "database_sha256": release_sha256,
                    "artifact_file_count": release_manifest["artifact_file_count"],
                    "artifact_size_bytes": release_manifest["artifact_size_bytes"],
                    "artifact_manifest_sha256": release_manifest["artifact_manifest_sha256"],
                    "release_manifest_sha256": _sha256_file(release_manifest_path),
                    "release_validation": release_validation,
                    "stage_validation": stage_validation,
                    "activated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            publication.update(_existing_publication(service, release_root, release_id))
            publication["stage_validation"] = stage_validation
            service._activate_release(
                publication, expected_generation=base["generation"]
            )
            publication["status"] = "INSTALLED_AND_ACTIVATED"
            return publication
        except Exception:
            if temporary.is_dir() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install the prevalidated Stage 14.2 2022 multi-election release."
    )
    parser.parse_args()
    print(json.dumps(install_2022_release(), indent=2))


if __name__ == "__main__":
    main()
