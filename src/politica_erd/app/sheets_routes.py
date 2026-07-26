"""FastAPI routes for controlled Grand Database reference synchronisation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Literal
from uuid import uuid4

import duckdb
import yaml
from fastapi import APIRouter, HTTPException, Request
from filelock import FileLock
from pydantic import AliasChoices, BaseModel, Field

from .sheets_sync import (
    ALLOWED_SHEETS,
    ApplyConfirmationError,
    GoogleSheetsReader,
    GoogleSheetsReferenceSynchronizer,
    SheetsSyncError,
    SyncContractError,
    google_sheets_configuration_status,
)
from .service import InvalidJobStateError
from .store import JobNotFoundError
from ..validate import validate_database

router = APIRouter(prefix="/api/sheets", tags=["google-sheets"])


class SheetsPreviewRequest(BaseModel):
    spreadsheet_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{10,200}$")
    tabs: tuple[Literal["People", "Parties", "Constituencies"], ...] = ALLOWED_SHEETS


class SheetsApplyRequest(BaseModel):
    preview_id: str = Field(
        pattern=r"^[a-f0-9]{32}$",
        validation_alias=AliasChoices("preview_id", "preview_token"),
    )
    job_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview_root(settings: Any) -> Path:
    path = settings.app_data / "sheets" / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _preview_path(settings: Any, token: str) -> Path:
    return _preview_root(settings) / f"{token}.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.stem + "-", suffix=".json.tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_locator(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _active_pointer(request: Request) -> dict[str, Any] | None:
    pointer_path = request.app.state.settings.releases_root / "active.json"
    try:
        document = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _publication_pointer(publication: dict[str, Any]) -> dict[str, Any]:
    """Build an absolute-path pointer document for bundle integrity checks."""

    return {
        "release_id": publication["release_id"],
        "path_base": "project_root",
        "database_path": str(publication["database_path"]),
        "sha256": publication["database_sha256"],
        "activated_at": publication["activated_at"],
        "release_root": str(publication["release_root"]),
        "release_manifest_path": str(publication["release_manifest_path"]),
        "artifact_manifest_sha256": publication["artifact_manifest_sha256"],
        "release_manifest_sha256": publication["release_manifest_sha256"],
    }


def _publication_is_active(
    request: Request, publication: dict[str, Any]
) -> bool:
    """Return true only when the active pointer names this exact bundle."""

    pointer = _active_pointer(request)
    if pointer is None:
        return False
    service = request.app.state.job_service
    try:
        active_database = service._resolve_pointer_path(
            pointer["database_path"], pointer
        ).resolve()
        publication_database = service._resolve_portable_path(
            publication["database_path"]
        ).resolve()
        active_manifest = service._resolve_pointer_path(
            pointer["release_manifest_path"], pointer
        ).resolve()
        publication_manifest = service._resolve_portable_path(
            publication["release_manifest_path"]
        ).resolve()
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        pointer.get("release_id") == publication.get("release_id")
        and active_database == publication_database
        and active_manifest == publication_manifest
        and pointer.get("sha256") == publication.get("database_sha256")
        and pointer.get("artifact_manifest_sha256")
        == publication.get("artifact_manifest_sha256")
        and pointer.get("release_manifest_sha256")
        == publication.get("release_manifest_sha256")
    )


def _verify_publication_bundle(
    request: Request, publication: dict[str, Any]
) -> tuple[Path, dict[str, Any]]:
    """Verify a frozen candidate independently of whether it is active."""

    service = request.app.state.job_service
    database = service._resolve_portable_path(publication["database_path"])
    manifest_path = service._resolve_portable_path(
        publication["release_manifest_path"]
    )
    if (
        not database.is_file()
        or not manifest_path.is_file()
        or _sha256_file(database) != publication.get("database_sha256")
        or _sha256_file(manifest_path)
        != publication.get("release_manifest_sha256")
    ):
        raise SheetsSyncError("The frozen reference release failed checksum verification.")
    pointer = _publication_pointer(
        {
            **publication,
            "database_path": str(database.resolve()),
            "release_root": str(
                service._resolve_portable_path(publication["release_root"]).resolve()
            ),
            "release_manifest_path": str(manifest_path.resolve()),
        }
    )
    service._verify_release_bundle(pointer, database)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SheetsSyncError("The frozen reference release manifest is unreadable.") from exc
    return database, manifest


def _quarantine_reference_release(
    request: Request,
    publication: dict[str, Any],
    *,
    reason: str,
) -> str | None:
    """Move an unactivated job-owned candidate out of the release namespace."""

    if _publication_is_active(request, publication):
        return None
    settings = request.app.state.settings
    service = request.app.state.job_service
    release_root = service._resolve_portable_path(publication["release_root"]).resolve()
    releases_root = settings.releases_root.resolve()
    try:
        relative = release_root.relative_to(releases_root)
    except ValueError:
        return None
    if len(relative.parts) != 1 or not release_root.is_dir():
        return None

    # Recheck under the activation lock so a concurrent successful activation
    # can never race with quarantine of its target.
    with FileLock(str(settings.releases_root / ".activation.lock")):
        if _publication_is_active(request, publication):
            return None
        quarantine_root = settings.releases_root / ".quarantine"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        destination = quarantine_root / (
            f"{release_root.name}-{reason}-{uuid4().hex[:12]}"
        )
        os.rename(release_root, destination)
    return service._portable_path(destination)


def _configured_workbook_id(settings: Any) -> str | None:
    environment_id = os.getenv("POLITICA_GRAND_DATABASE_ID")
    if environment_id:
        return environment_id
    try:
        contract = yaml.safe_load(
            (settings.project_root / "config" / "grand_sync_contract.yml").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, yaml.YAMLError):
        return None
    return contract.get("source", {}).get("workbook_id") if isinstance(contract, dict) else None


def _local_release_lifecycle(state: str, publication: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "state": state,
        "google_source_access": "read_only",
        "google_source_modified": False,
        "local_apply_sequence": [
            "write_isolated_working_snapshot",
            "copy_pinned_parquet_and_manifests_into_release_bundle",
            "validate_database_against_candidate_bundle_root",
            "write_checksummed_release_manifest",
            "freeze_new_immutable_self_contained_release_bundle",
            "activate_validated_local_release",
        ],
        "local_working_snapshot_written": state in {"applying", "activated"},
        "local_database_validated": state == "activated",
        "immutable_local_release_created": state == "activated",
        "self_contained_release_bundle_created": state == "activated",
        "checksummed_release_manifest_created": state == "activated",
        "local_release_activated": state == "activated",
        "canonical_base_database_modified": False,
    }
    if publication:
        result.update(
            {
                "release_id": publication.get("release_id"),
                "release_sha256": publication.get("database_sha256"),
                "release_manifest_sha256": publication.get(
                    "release_manifest_sha256"
                ),
                "artifact_manifest_sha256": publication.get(
                    "artifact_manifest_sha256"
                ),
                "activated_at": publication.get("activated_at"),
            }
        )
    return result


def _resolve_audit_path(locator: str, project_root: Path) -> Path:
    path = Path(locator)
    return path if path.is_absolute() else project_root / path


def _idempotent_application_response(
    preview: dict[str, Any], settings: Any
) -> dict[str, Any] | None:
    applications = preview.get("applications") or []
    state = preview.get("state")
    if not applications and state in {None, "ready"}:
        return None
    if not applications or state not in {None, "applied"}:
        failure = str(preview.get("failure") or "").strip()
        raise HTTPException(
            status_code=409,
            detail=(
                "This one-shot Sheets preview token has already been consumed. "
                + (failure + " " if failure else "")
                + "Create a new Google Sheets preview before attempting another apply."
            ),
        )
    audit_locator = applications[-1].get("audit_path")
    if not audit_locator:
        raise HTTPException(
            status_code=409,
            detail="The applied Sheets preview has no recoverable audit response.",
        )
    audit_path = _resolve_audit_path(str(audit_locator), settings.project_root)
    try:
        response = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="The applied Sheets preview audit response is unavailable.",
        ) from exc
    expected_token = preview.get("preview_token")
    if expected_token and response.get("preview_id") != expected_token:
        raise HTTPException(
            status_code=409,
            detail="The applied Sheets preview audit response does not match its token.",
        )
    response["idempotent_replay"] = True
    response["preview_token_state"] = "applied"
    return response


def _working_database(request: Request, job_id: str) -> Path:
    try:
        request.app.state.store.read(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Ingestion job {job_id!r} was not found.") from exc
    path = request.app.state.settings.jobs_root / job_id / "work" / "database.duckdb"
    if not path.is_file():
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ingestion job {job_id!r} does not yet have a working database. "
                "Stage the job before applying a reference sync."
            ),
        )
    return path


def _create_reference_job(request: Request, source_revision: str) -> tuple[str, Path]:
    service = request.app.state.job_service
    job = service.begin_job(
        name=f"Grand Database reference sync {source_revision[:12]}",
        authority_id=None,
        election_id=None,
    )
    job_id = job["job_id"]
    try:
        database, base_hash = service._copy_governed_database(job_id)
    except Exception as exc:
        service._fail_job(job_id, "reference_working_copy_failed", exc)
        raise

    def mark_reference_job(metadata: dict[str, Any]) -> None:
        metadata["mode"] = "reference_sync"
        metadata["state"] = "reviewing"
        metadata["execution"]["base_database_sha256"] = base_hash
        metadata["execution"]["source_revision_sha256"] = source_revision
        metadata["events"].append(
            {
                "at": _utc_now(),
                "type": "reference_working_copy_created",
                "message": "Created an isolated working copy for Grand Database reference review.",
            }
        )

    request.app.state.store.mutate(job_id, mark_reference_job)
    return job_id, database


def _flatten_preview(result: Any) -> tuple[dict[str, int], list[dict[str, Any]]]:
    summary = {"added": 0, "updated": 0, "missing": 0, "unchanged": 0}
    changes: list[dict[str, Any]] = []
    action_labels = {"add": "added", "update": "updated", "retain_local": "missing"}
    for sheet, diff in result.tables.items():
        summary["added"] += diff.added
        summary["updated"] += diff.updated
        summary["missing"] += diff.retained_local
        summary["unchanged"] += diff.unchanged
        for change in diff.changes:
            changes.append(
                {
                    "tab": sheet,
                    "id": change.key,
                    "primary_key": change.key,
                    "change": action_labels[change.action],
                    "changed_fields": list(change.changed_fields),
                    "source_row_hash": change.source_row_hash,
                    "local_row_hash": change.local_row_hash,
                    "history": {
                        key: value.isoformat() if hasattr(value, "isoformat") else value
                        for key, value in change.history.items()
                    },
                }
            )
    return summary, changes


def _activate_reference_snapshot(
    request: Request,
    job_id: str,
    database: Path,
    source_revision: str,
    selected_sheets: list[str],
    preview_token: str | None = None,
    checkpoint: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build, validate, freeze and CAS-activate a self-contained release bundle."""

    service = request.app.state.job_service
    job = request.app.state.store.read(job_id)
    base_release = job.get("execution", {}).get("base_release") or {}
    expected_generation = base_release.get("generation")
    if not expected_generation:
        raise SheetsSyncError(
            "The reference-sync job has no pinned base generation. Create a new preview."
        )

    approved_at = datetime.now(timezone.utc)
    release_id = f"release_reference_{job_id.replace('-', '')}"
    settings = request.app.state.settings
    settings.releases_root.mkdir(parents=True, exist_ok=True)
    release_name = (
        f"politica-reference-sync-{source_revision[:16]}-"
        f"{job_id.replace('-', '')}"
    )
    release_root = settings.releases_root / release_name
    if release_root.exists():
        raise SheetsSyncError(
            f"Immutable reference release already exists and will not be overwritten: {release_root}"
        )
    temporary = settings.releases_root / f".{release_name}.tmp-{uuid4().hex}"
    frozen = False
    try:
        temporary.mkdir(parents=False)
        candidate_database = (
            temporary / "data" / "database" / "politica_election_results.duckdb"
        )
        candidate_database.parent.mkdir(parents=True)
        shutil.copy2(database, candidate_database)

        artifact_source = service._release_artifact_source(job)
        pinned_root = (
            service._resolve_portable_path(base_release["artifact_root"])
            if base_release.get("artifact_root")
            else None
        )
        expected_artifacts = (
            base_release.get("artifact_files")
            if pinned_root is not None
            and artifact_source.resolve() == pinned_root.resolve()
            else None
        )
        service._copy_release_artifacts(
            source_root=artifact_source,
            release_root=temporary,
            database=candidate_database,
            expected_artifacts=expected_artifacts,
        )

        connection = service._connect(
            candidate_database, external_data_root=temporary
        )
        try:
            schema_version = connection.execute(
                "SELECT schema_version FROM control.schema_version "
                "ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()[0]
            connection.execute("BEGIN TRANSACTION")
            if connection.execute(
                "SELECT count(*) FROM control.database_release WHERE release_id=?",
                [release_id],
            ).fetchone()[0]:
                raise SheetsSyncError(
                    f"Immutable reference release metadata already exists: {release_id}"
                )
            connection.execute(
                """INSERT INTO control.database_release
                   (release_id, schema_version, release_status, release_started_at,
                    published_at, created_by, notes)
                   VALUES (?, ?, 'validated', ?, ?, ?, ?)""",
                [
                    release_id,
                    schema_version,
                    approved_at,
                    approved_at,
                    "Stage 3 Google Sheets reference synchronisation",
                    (
                        "Read-only Grand Database reference snapshot "
                        f"{source_revision}; tabs: {', '.join(selected_sheets)}."
                    ),
                ],
            )
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except duckdb.Error:
                pass
            raise
        finally:
            connection.close()

        validation = validate_database(candidate_database, temporary)
        if validation["status"] != "PASS":
            raise SheetsSyncError(
                "The self-contained reference release bundle failed validation and was not "
                "activated: "
                + json.dumps(validation.get("failures", []), ensure_ascii=False)
            )
        database_sha256 = _sha256_file(candidate_database)
        publication = {
            "job_id": job_id,
            "release_id": release_id,
            "release_type": "grand_database_reference_snapshot",
            "source_revision_sha256": source_revision,
            "selected_sheets": selected_sheets,
            "preview_token": preview_token,
            "base_generation": expected_generation,
            "activated_at": approved_at.isoformat(),
        }
        release_manifest = service._write_release_manifest(
            temporary,
            publication=publication,
            database_sha256=database_sha256,
            validation=validation,
            artifact_source=artifact_source,
        )
        release_manifest.update(
            {
                "release_type": publication["release_type"],
                "source_revision_sha256": source_revision,
                "selected_sheets": selected_sheets,
                "preview_token": preview_token,
                "base_generation": expected_generation,
                "activated_at": publication["activated_at"],
                "self_contained": True,
            }
        )
        manifest_candidate = temporary / "release_manifest.json"
        _write_json(manifest_candidate, release_manifest)

        # A same-filesystem directory rename exposes either the complete
        # validated unit or no final candidate at all. The fixed job-owned
        # destination must be absent; existing releases are never overwritten.
        if release_root.exists():
            raise SheetsSyncError(
                "Immutable reference release already exists and will not be "
                f"overwritten: {release_root}"
            )
        os.rename(temporary, release_root)
        frozen = True

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
                "database_sha256": database_sha256,
                "artifact_file_count": release_manifest["artifact_file_count"],
                "artifact_size_bytes": release_manifest["artifact_size_bytes"],
                "artifact_manifest_sha256": release_manifest[
                    "artifact_manifest_sha256"
                ],
                "release_manifest_sha256": _sha256_file(release_manifest_path),
                "release_validation": validation,
                "validation_status": validation["status"],
                "self_contained": True,
            }
        )
        try:
            if checkpoint is not None:
                checkpoint(publication, validation)
            service._activate_release(
                publication, expected_generation=expected_generation
            )
        except Exception:
            _quarantine_reference_release(
                request, publication, reason="activation-failed"
            )
            raise
        return publication, validation
    finally:
        if not frozen:
            shutil.rmtree(temporary, ignore_errors=True)


def _publication_from_reference_manifest(
    request: Request, release_root: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Reconstruct the activation descriptor for a frozen reference bundle."""

    service = request.app.state.job_service
    manifest_path = release_root / "release_manifest.json"
    database_relative = Path(
        manifest.get(
            "database_path", "data/database/politica_election_results.duckdb"
        )
    )
    if database_relative.is_absolute() or ".." in database_relative.parts:
        raise SheetsSyncError("Reference release manifest has an unsafe database path.")
    database = (release_root / database_relative).resolve()
    if not database.is_file() or not manifest_path.is_file():
        raise SheetsSyncError("Reference release bundle is incomplete.")
    database_sha256 = str(manifest.get("database_sha256") or "")
    if not database_sha256 or _sha256_file(database) != database_sha256:
        raise SheetsSyncError("Reference release database checksum does not match its manifest.")
    publication = {
        "job_id": manifest["job_id"],
        "release_id": manifest["release_id"],
        "release_type": manifest.get(
            "release_type", "grand_database_reference_snapshot"
        ),
        "source_revision_sha256": manifest["source_revision_sha256"],
        "selected_sheets": list(manifest.get("selected_sheets") or []),
        "preview_token": manifest.get("preview_token"),
        "base_generation": manifest["base_generation"],
        "activated_at": manifest.get("activated_at") or manifest["created_at"],
        "release_root": service._portable_path(release_root),
        "database_path": service._portable_path(database),
        "release_manifest_path": service._portable_path(manifest_path),
        "database_size_bytes": database.stat().st_size,
        "database_sha256": database_sha256,
        "artifact_file_count": int(manifest["artifact_file_count"]),
        "artifact_size_bytes": int(manifest["artifact_size_bytes"]),
        "artifact_manifest_sha256": manifest["artifact_manifest_sha256"],
        "release_manifest_sha256": _sha256_file(manifest_path),
        "release_validation": manifest.get("validation") or {},
        "validation_status": (manifest.get("validation") or {}).get("status"),
        "self_contained": bool(manifest.get("self_contained")),
    }
    _verify_publication_bundle(request, publication)
    return publication


def _discover_reference_candidates(
    request: Request, preview: dict[str, Any]
) -> list[dict[str, Any]]:
    """Find frozen candidates even when the pre-activation checkpoint was lost."""

    settings = request.app.state.settings
    discovered: list[dict[str, Any]] = []
    for release_root in sorted(
        settings.releases_root.glob("politica-reference-sync-*")
    ):
        if not release_root.is_dir():
            continue
        manifest_path = release_root / "release_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                manifest.get("release_type")
                != "grand_database_reference_snapshot"
                or manifest.get("job_id") != preview.get("job_id")
                or manifest.get("source_revision_sha256")
                != preview.get("source_revision_sha256")
                or manifest.get("preview_token") != preview.get("preview_token")
            ):
                continue
            discovered.append(
                _publication_from_reference_manifest(
                    request, release_root.resolve(), manifest
                )
            )
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            SheetsSyncError,
        ):
            continue
    return discovered


def _cleanup_reference_temporaries(
    request: Request, preview: dict[str, Any]
) -> int:
    """Remove only abandoned, hidden build dirs owned by this preview's job."""

    job_id = str(preview.get("job_id") or "").replace("-", "")
    source_revision = str(preview.get("source_revision_sha256") or "")
    if not job_id or len(source_revision) < 16:
        return 0
    release_name = f"politica-reference-sync-{source_revision[:16]}-{job_id}"
    removed = 0
    for temporary in request.app.state.settings.releases_root.glob(
        f".{release_name}.tmp-*"
    ):
        if temporary.is_dir() and not temporary.is_symlink():
            shutil.rmtree(temporary)
            removed += 1
        elif temporary.is_file() or temporary.is_symlink():
            temporary.unlink()
            removed += 1
    return removed


def _fail_reference_preview(
    preview_path: Path,
    preview: dict[str, Any],
    message: str,
    *,
    quarantined_release: str | None = None,
) -> None:
    preview["state"] = "failed"
    preview["failed_at"] = _utc_now()
    preview["failure"] = message
    if quarantined_release:
        preview["quarantined_release"] = quarantined_release
    _write_json(preview_path, preview)


def _recover_interrupted_preview(
    request: Request, preview_path: Path, preview: dict[str, Any]
) -> dict[str, Any] | None:
    """Resolve every durable crash window around reference activation."""

    publication = preview.get("candidate_publication")
    if not isinstance(publication, dict):
        removed_temporaries = _cleanup_reference_temporaries(request, preview)
        candidates = _discover_reference_candidates(request, preview)
        active = next(
            (
                candidate
                for candidate in candidates
                if _publication_is_active(request, candidate)
            ),
            None,
        )
        # Current code checkpoints atomically before pointer activation, so an
        # active pre-checkpoint candidate is evidence of an older/incomplete
        # record. Never touch the active target; leave an explicit diagnosis.
        if active is not None:
            _fail_reference_preview(
                preview_path,
                preview,
                (
                    "The reference release is active, but its pre-activation preview "
                    "checkpoint is missing. The active bundle was preserved; operator "
                    "reconciliation is required."
                ),
            )
            return None
        quarantined: list[str] = []
        for candidate in candidates:
            locator = _quarantine_reference_release(
                request, candidate, reason="pre-checkpoint-crash"
            )
            if locator:
                quarantined.append(locator)
        if candidates:
            _fail_reference_preview(
                preview_path,
                preview,
                (
                    "The application stopped after freezing the reference bundle but "
                    "before its activation checkpoint. The unactivated candidate was "
                    "quarantined. Create a new Google Sheets preview."
                ),
                quarantined_release=quarantined[-1] if quarantined else None,
            )
        elif preview.get("state") == "applying":
            cleanup_note = (
                f" Removed {removed_temporaries} abandoned temporary bundle(s)."
                if removed_temporaries
                else ""
            )
            _fail_reference_preview(
                preview_path,
                preview,
                (
                    "The application stopped before a complete reference release "
                    "candidate was checkpointed."
                    + cleanup_note
                    + " Create a new Google Sheets preview."
                ),
            )
        return None

    if _publication_is_active(request, publication):
        return _reconcile_activated_preview(request, preview_path, preview)
    if preview.get("quarantined_release"):
        return None

    service = request.app.state.job_service
    try:
        _verify_publication_bundle(request, publication)
        expected_generation = publication.get("base_generation")
        if not expected_generation:
            raise InvalidJobStateError(
                "The frozen reference candidate has no pinned base generation."
            )
        if service.current_release_generation() != expected_generation:
            raise InvalidJobStateError(
                "The governed release changed before the frozen reference candidate "
                "could be activated."
            )
        service._activate_release(
            publication, expected_generation=expected_generation
        )
    except Exception as exc:
        if _publication_is_active(request, publication):
            return _reconcile_activated_preview(request, preview_path, preview)
        locator = _quarantine_reference_release(
            request, publication, reason="recovery-failed"
        )
        _fail_reference_preview(
            preview_path,
            preview,
            (
                f"Could not safely resume the frozen reference release: {exc} "
                "The unactivated candidate was quarantined. Create a new Google "
                "Sheets preview."
            ),
            quarantined_release=locator,
        )
        return None
    return _reconcile_activated_preview(request, preview_path, preview)


def _local_status(database: Path) -> dict[str, Any]:
    if not database.is_file():
        return {
            "available": False,
            "database": str(database),
            "message": "The local DuckDB database has not been built.",
        }
    connection = duckdb.connect(str(database), read_only=True)
    try:
        counts = {
            "People": connection.execute("SELECT count(*) FROM sync.person").fetchone()[0],
            "Parties": connection.execute("SELECT count(*) FROM sync.party").fetchone()[0],
            "Constituencies": connection.execute(
                "SELECT count(*) FROM sync.constituency"
            ).fetchone()[0],
        }
        timestamps = connection.execute(
            """
            SELECT max(value) FROM (
                SELECT max(grand_synced_at) AS value FROM sync.person
                UNION ALL SELECT max(grand_synced_at) FROM sync.party
                UNION ALL SELECT max(grand_synced_at) FROM sync.constituency
            )
            """
        ).fetchone()[0]
        row_hashes: list[str] = []
        for target, key in (
            ("sync.person", "person_id"),
            ("sync.party", "party_id"),
            ("sync.constituency", "constituency_id"),
        ):
            row_hashes.extend(
                row[0]
                for row in connection.execute(
                    f"SELECT source_row_hash FROM {target} ORDER BY {key}"
                ).fetchall()
            )
        checksum = hashlib.sha256("\n".join(row_hashes).encode("utf-8")).hexdigest()
    except duckdb.Error as exc:
        return {
            "available": False,
            "database": str(database),
            "message": f"The local database is not ready for reference sync: {exc}",
        }
    finally:
        connection.close()
    return {
        "available": True,
        "database": str(database),
        "counts": counts,
        "last_synced_at": timestamps.isoformat() if timestamps else None,
        "captured_at": timestamps.isoformat() if timestamps else None,
        "workbook_checksum": checksum,
        "checksum": checksum,
    }


def _complete_reference_application(
    request: Request,
    preview_path: Path,
    preview: dict[str, Any],
    apply_result: dict[str, Any],
    publication: dict[str, Any],
    validation: dict[str, Any],
    *,
    recovered_after_activation: bool = False,
) -> dict[str, Any]:
    """Durably finish audit, token and job checkpoints after pointer activation."""

    settings = request.app.state.settings
    job_id = preview["job_id"]
    job_directory = settings.jobs_root / job_id
    response = dict(apply_result)
    response.update(
        {
            "ok": True,
            "id": preview["preview_token"],
            "preview_id": preview["preview_token"],
            "preview_token": preview["preview_token"],
            "preview_token_state": "applied",
            "idempotent_replay": recovered_after_activation,
            "recovered_after_activation": recovered_after_activation,
            "job_id": job_id,
            "spreadsheet_id_pinned": True,
            "publication": publication,
            "validation": validation,
            "local_release_lifecycle": _local_release_lifecycle(
                "activated", publication
            ),
        }
    )
    response["local_snapshot"] = _local_status(
        request.app.state.job_service.governed_database()
    )
    response["snapshot"] = response["local_snapshot"]
    response["spreadsheet_id"] = apply_result["spreadsheet_id"]
    audit_path = job_directory / "work" / f"{apply_result['audit_id']}.json"
    response["audit_path"] = _audit_locator(audit_path, settings.project_root)
    _write_json(audit_path, response)

    applied_at = apply_result.get("applied_at") or _utc_now()
    applied_record = {
        "job_id": job_id,
        "applied_at": applied_at,
        "audit_id": apply_result["audit_id"],
        "audit_path": response["audit_path"],
        "release_id": publication["release_id"],
        "database_sha256": publication["database_sha256"],
        "release_manifest_sha256": publication["release_manifest_sha256"],
    }
    preview["state"] = "applied"
    preview["applied_at"] = applied_at
    preview["applications"] = [applied_record]
    preview.pop("failure", None)
    preview.pop("failed_at", None)
    _write_json(preview_path, preview)

    def record_sync(metadata: dict[str, Any]) -> None:
        records = metadata.setdefault("reference_syncs", [])
        if not any(item.get("audit_id") == applied_record["audit_id"] for item in records):
            records.append(applied_record)
        metadata["state"] = "published"
        metadata["execution"]["canonical_complete"] = True
        metadata["publication"] = publication
        metadata["reference_snapshot"] = {
            "source_revision_sha256": apply_result["source_revision_sha256"],
            "spreadsheet_id": apply_result["spreadsheet_id"],
            "synced_at": applied_at,
        }
        if not any(
            event.get("type") == "reference_snapshot_activated"
            for event in metadata["events"]
        ):
            metadata["events"].append(
                {
                    "at": _utc_now(),
                    "type": "reference_snapshot_activated",
                    "message": (
                        "Wrote and validated the self-contained local reference bundle, "
                        "then froze and activated its immutable local release."
                    ),
                }
            )

    request.app.state.store.mutate(job_id, record_sync)
    return response


def _reconcile_activated_preview(
    request: Request, preview_path: Path, preview: dict[str, Any]
) -> dict[str, Any] | None:
    """Complete a token/job checkpoint when activation beat process shutdown."""

    publication = preview.get("candidate_publication")
    apply_result = preview.get("apply_result")
    if not isinstance(publication, dict) or not isinstance(apply_result, dict):
        return None
    settings = request.app.state.settings
    pointer_path = settings.releases_root / "active.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        if pointer.get("release_id") != publication.get("release_id"):
            return None
        service = request.app.state.job_service
        database = service._resolve_pointer_path(pointer["database_path"], pointer)
        expected_database = service._resolve_portable_path(publication["database_path"])
        if (
            database.resolve() != expected_database.resolve()
            or pointer.get("sha256") != publication.get("database_sha256")
            or _sha256_file(database) != publication.get("database_sha256")
        ):
            return None
        manifest = service._resolve_pointer_path(
            pointer["release_manifest_path"], pointer
        )
        expected_manifest = service._resolve_portable_path(
            publication["release_manifest_path"]
        )
        if (
            manifest.resolve() != expected_manifest.resolve()
            or pointer.get("release_manifest_sha256")
            != publication.get("release_manifest_sha256")
            or _sha256_file(manifest) != publication.get("release_manifest_sha256")
            or pointer.get("artifact_manifest_sha256")
            != publication.get("artifact_manifest_sha256")
        ):
            return None
        manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            manifest_document.get("job_id") != preview.get("job_id")
            or manifest_document.get("source_revision_sha256")
            != preview.get("source_revision_sha256")
            or manifest_document.get("preview_token")
            != preview.get("preview_token")
        ):
            return None
        service._verify_release_bundle(pointer, database)
        release_root = service._resolve_portable_path(publication["release_root"])
        validation = validate_database(database, release_root)
        if validation["status"] != "PASS":
            return None
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        duckdb.Error,
    ):
        return None
    return _complete_reference_application(
        request,
        preview_path,
        preview,
        apply_result,
        publication,
        validation,
        recovered_after_activation=True,
    )


def _repair_applied_preview_job_checkpoint(
    request: Request, preview_path: Path, preview: dict[str, Any]
) -> dict[str, Any] | None:
    """Repair audit/token success that preceded the final job metadata write."""

    applications = preview.get("applications") or []
    if preview.get("state") != "applied" or not applications:
        return None
    record = applications[-1]
    audit_locator = record.get("audit_path")
    if (
        not audit_locator
        or not preview.get("job_id")
        or not hasattr(request.app.state, "store")
        or not hasattr(request.app.state, "job_service")
    ):
        return None
    settings = request.app.state.settings
    audit_path = _resolve_audit_path(str(audit_locator), settings.project_root)
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        job = request.app.state.store.read(preview["job_id"])
    except (OSError, KeyError, json.JSONDecodeError, JobNotFoundError):
        return None
    audit_id = audit.get("audit_id")
    publication = audit.get("publication")
    if (
        audit.get("preview_id") != preview.get("preview_token")
        or not audit_id
        or not isinstance(publication, dict)
    ):
        return None
    job_recorded = any(
        item.get("audit_id") == audit_id
        and item.get("release_id") == publication.get("release_id")
        for item in job.get("reference_syncs", [])
    )
    job_publication = job.get("publication") or {}
    if (
        job_recorded
        and job.get("execution", {}).get("canonical_complete")
        and job.get("state") == "published"
        and job_publication.get("release_id") == publication.get("release_id")
    ):
        return None
    try:
        database, _ = _verify_publication_bundle(request, publication)
        release_root = request.app.state.job_service._resolve_portable_path(
            publication["release_root"]
        )
        validation = validate_database(database, release_root)
    except Exception:
        return None
    if validation.get("status") != "PASS":
        return None
    return _complete_reference_application(
        request,
        preview_path,
        preview,
        audit,
        publication,
        validation,
        recovered_after_activation=True,
    )


def reconcile_interrupted_sheets_syncs(app: Any) -> list[str]:
    """Startup recovery for pointer activation preceding token/job checkpoints."""

    request = SimpleNamespace(app=app)
    recovered: list[str] = []
    for preview_path in sorted(_preview_root(app.state.settings).glob("*.json")):
        with FileLock(str(preview_path) + ".lock"):
            try:
                preview = json.loads(preview_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                if preview.get("state") == "applied":
                    job_id = preview.get("job_id")
                    if not job_id:
                        continue
                    with app.state.job_service._job_operation_lock(job_id):
                        response = _repair_applied_preview_job_checkpoint(
                            request, preview_path, preview
                        )
                elif preview.get("state") in {"applying", "failed"}:
                    job_id = preview.get("job_id")
                    if not job_id:
                        continue
                    with app.state.job_service._job_operation_lock(job_id):
                        response = _recover_interrupted_preview(
                            request, preview_path, preview
                        )
                else:
                    continue
            except Exception:
                continue
            if response is not None:
                recovered.append(preview["preview_token"])
    return recovered


@router.get("/status")
def sheets_status(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    connection = google_sheets_configuration_status()
    pinned_spreadsheet_id = _configured_workbook_id(settings)
    if not pinned_spreadsheet_id:
        connection.update(
            {
                "available": False,
                "reason": "spreadsheet_id_not_pinned",
                "message": "Grand Database spreadsheet ID is not pinned in configuration.",
            }
        )
    connection["connected"] = connection["available"]
    connection["spreadsheet_id"] = pinned_spreadsheet_id
    connection["spreadsheet_id_pinned"] = bool(pinned_spreadsheet_id)
    governed = request.app.state.job_service.governed_database()
    return {
        "ok": connection["available"],
        "connection": connection,
        "spreadsheet_id": pinned_spreadsheet_id,
        "spreadsheet_id_pinned": bool(pinned_spreadsheet_id),
        "allowed_tabs": list(ALLOWED_SHEETS),
        "local_snapshot": _local_status(governed),
        "sync_policy": {
            "google_access": "read_only",
            "spreadsheet_id_policy": "pinned_only",
            "apply_target": "isolated_job_working_snapshot",
            "apply_steps": [
                "write_reviewed_reference_rows_to_isolated_working_snapshot",
                "copy_pinned_parquet_and_manifests_into_candidate_bundle",
                "validate_database_against_candidate_bundle_root",
                "write_checksummed_release_manifest",
                "freeze_new_immutable_self_contained_release_bundle",
                "activate_validated_local_release",
            ],
            "preview_token_policy": "one_shot_with_idempotent_success_replay",
            "existing_release_policy": "never_overwrite",
            "source_missing_rows": "retain_locally",
            "canonical_base_database_modified": False,
            "election_results_write_back": False,
        },
    }


@router.post("/preview")
def preview_sheets_sync(body: SheetsPreviewRequest, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    governed = request.app.state.job_service.governed_database()
    if not governed.is_file():
        raise HTTPException(status_code=409, detail="The governed database is unavailable.")
    if not body.tabs:
        raise HTTPException(status_code=422, detail="Select at least one reference tab.")

    try:
        reader = GoogleSheetsReader()
        connection = duckdb.connect(str(governed), read_only=True)
        try:
            source_reader = GoogleSheetsReferenceSynchronizer(
                connection,
                settings.project_root,
                reader=reader,
                workbook_id=body.spreadsheet_id,
            )
            snapshot = source_reader.fetch_snapshot()
        finally:
            connection.close()
    except SyncContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SheetsSyncError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=409, detail=f"Could not inspect the local database: {exc}") from exc

    source_revision = snapshot.get("source_revision_sha256")
    if not isinstance(source_revision, str) or len(source_revision) != 64:
        raise HTTPException(
            status_code=503,
            detail="The Grand Database snapshot did not provide a valid source revision.",
        )
    try:
        job_id, database = _create_reference_job(request, source_revision)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Could not create an isolated reference-sync working copy: {exc}",
        ) from exc

    # The reviewed diff is calculated against the exact pinned working copy
    # that apply will later mutate. A concurrent activation can no longer bind
    # the token to a different base release.
    try:
        with request.app.state.job_service._job_operation_lock(job_id):
            connection = request.app.state.job_service._connect(
                database, read_only=True
            )
            try:
                synchronizer = GoogleSheetsReferenceSynchronizer(
                    connection,
                    settings.project_root,
                    reader=reader,
                    workbook_id=body.spreadsheet_id,
                )
                result = synchronizer.run(
                    snapshot=snapshot, selected_sheets=body.tabs
                )
            finally:
                connection.close()
    except Exception as exc:
        request.app.state.job_service._fail_job(
            job_id, "reference_preview_failed", exc
        )
        if isinstance(exc, SyncContractError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if isinstance(exc, SheetsSyncError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, duckdb.Error):
            raise HTTPException(
                status_code=409,
                detail=f"Could not inspect the pinned reference database: {exc}",
            ) from exc
        raise

    token = uuid4().hex
    summary, changes = _flatten_preview(result)
    response = result.as_dict()
    response.update(
        {
            "id": token,
            "preview_id": token,
            "preview_token": token,
            "job_id": job_id,
            "summary": summary,
            "changes": changes,
            "spreadsheet_id_pinned": True,
            "preview_token_state": "ready",
            "apply_plan": _local_release_lifecycle("planned"),
        }
    )
    _write_json(
        _preview_path(settings, token),
        {
            "schema_version": 1,
            "preview_token": token,
            "state": "ready",
            "one_shot": True,
            "created_at": _utc_now(),
            "job_id": job_id,
            "spreadsheet_id": result.spreadsheet_id,
            "source_revision_sha256": result.source_revision_sha256,
            "selected_sheets": list(result.selected_sheets),
            "snapshot": snapshot,
            "preview_result": response,
            "applications": [],
        },
    )
    return response


@router.post("/sync")
def apply_sheets_sync(body: SheetsApplyRequest, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    preview_path = _preview_path(settings, body.preview_id)
    preview_lock = FileLock(str(preview_path) + ".lock")
    with preview_lock:
        if not preview_path.is_file():
            raise HTTPException(status_code=404, detail="The Sheets preview token was not found.")
        try:
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=409, detail="The stored Sheets preview is unreadable.") from exc

        bound_job_id = preview.get("job_id")
        if body.job_id is not None and bound_job_id and body.job_id != bound_job_id:
            raise HTTPException(
                status_code=409,
                detail=f"This preview is bound to reference-sync job {bound_job_id!r}.",
            )
        if preview.get("state") == "applied":
            if bound_job_id and hasattr(
                request.app.state, "job_service"
            ):
                with request.app.state.job_service._job_operation_lock(
                    bound_job_id
                ):
                    repaired = _repair_applied_preview_job_checkpoint(
                        request, preview_path, preview
                    )
            else:
                repaired = None
            if repaired is not None:
                return repaired
        if preview.get("state") in {"applying", "failed"}:
            if not bound_job_id:
                raise HTTPException(
                    status_code=409,
                    detail="The consumed Sheets preview has no recoverable job binding.",
                )
            with request.app.state.job_service._job_operation_lock(
                bound_job_id
            ):
                reconciled = _recover_interrupted_preview(
                    request, preview_path, preview
                )
            if reconciled is not None:
                return reconciled
        replay = _idempotent_application_response(preview, settings)
        if replay is not None:
            return replay
        if preview.get("state", "ready") != "ready":
            raise HTTPException(
                status_code=409,
                detail=(
                    "This one-shot Sheets preview token has already been consumed. "
                    "Create a new preview before attempting another apply."
                ),
            )

        required = {
            "job_id", "spreadsheet_id", "source_revision_sha256", "selected_sheets", "snapshot"
        }
        missing = sorted(required - set(preview))
        if missing:
            raise HTTPException(
                status_code=409,
                detail=f"The stored Sheets preview is incomplete; missing {missing!r}.",
            )
        job_id = preview["job_id"]
        if body.job_id is not None and body.job_id != job_id:
            raise HTTPException(
                status_code=409,
                detail=f"This preview is bound to reference-sync job {job_id!r}.",
            )
        job_directory = settings.jobs_root / job_id
        token_committed = False
        try:
            with request.app.state.job_service._job_operation_lock(job_id):
                try:
                    job = request.app.state.store.read(job_id)
                except JobNotFoundError as exc:
                    raise HTTPException(
                        status_code=404,
                        detail="The preview's reference-sync job was not found.",
                    ) from exc
                if job.get("mode") != "reference_sync":
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The preview is not bound to a reference-sync job."
                        ),
                    )
                database = _working_database(request, job_id)
                preview["state"] = "applying"
                preview["apply_started_at"] = _utc_now()
                _write_json(preview_path, preview)
                connection = duckdb.connect(str(database))
                try:
                    # The persisted, reviewed snapshot avoids a second network
                    # read; the exact revision token protects approved contents.
                    offline_reader = GoogleSheetsReader(
                        service=object(),
                        credential_descriptor=preview.get("snapshot", {}).get(
                            "credential", {"source": "persisted_preview"}
                        ),
                    )
                    synchronizer = GoogleSheetsReferenceSynchronizer(
                        connection,
                        settings.project_root,
                        reader=offline_reader,
                        workbook_id=preview["spreadsheet_id"],
                    )
                    result = synchronizer.run(
                        apply=True,
                        expected_source_revision_sha256=preview["source_revision_sha256"],
                        snapshot=preview["snapshot"],
                        selected_sheets=preview["selected_sheets"],
                    )
                finally:
                    connection.close()
                apply_result = result.as_dict()
                audit_path = job_directory / "work" / f"{result.audit_id}.json"

                def checkpoint(
                    candidate_publication: dict[str, Any],
                    candidate_validation: dict[str, Any],
                ) -> None:
                    preview["candidate_publication"] = candidate_publication
                    preview["candidate_validation"] = candidate_validation
                    preview["apply_result"] = apply_result
                    preview["audit_path"] = _audit_locator(
                        audit_path, settings.project_root
                    )
                    preview["activation_checkpointed_at"] = _utc_now()
                    _write_json(preview_path, preview)

                publication, validation = _activate_reference_snapshot(
                    request,
                    job_id,
                    database,
                    result.source_revision_sha256,
                    list(result.selected_sheets),
                    preview_token=preview["preview_token"],
                    checkpoint=checkpoint,
                )
                response = _complete_reference_application(
                    request,
                    preview_path,
                    preview,
                    apply_result,
                    publication,
                    validation,
                )
                token_committed = True
                return response
        except Exception as exc:
            try:
                with request.app.state.job_service._job_operation_lock(job_id):
                    reconciled = _recover_interrupted_preview(
                        request, preview_path, preview
                    )
            except Exception:
                reconciled = None
            if reconciled is not None:
                return reconciled
            token_committed = token_committed or preview.get("state") == "applied"
            if not token_committed:
                _fail_reference_preview(
                    preview_path,
                    preview,
                    f"{type(exc).__name__}: {exc}",
                    quarantined_release=preview.get("quarantined_release"),
                )
            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, ApplyConfirmationError):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if isinstance(exc, SheetsSyncError):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if isinstance(exc, InvalidJobStateError):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{exc} Create a new Google Sheets preview before applying again."
                    ),
                ) from exc
            if isinstance(exc, duckdb.Error):
                raise HTTPException(
                    status_code=409, detail=f"Could not update the job database: {exc}"
                ) from exc
            raise


__all__ = ["reconcile_interrupted_sheets_syncs", "router"]
