from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout
from pydantic import BaseModel, Field

from .config import AppSettings
from .aec_bootstrap import AecBootstrapError, MODE as AEC_ELECTION_BOOTSTRAP
from .detection import DatasetSelectionError
from .explorer import (
    ElectionExplorer,
    ExplorerError,
    ExplorerFilters,
    ExportTooLargeError,
)
from .publication import (
    PublicationError,
    PublicationFilters,
    PublicationTooLargeError,
    VisualisationFeedService,
    safe_feed_filename,
)
from .visualisations import VisualisationContractError, VisualisationContractService
from .models import (
    DatasetSelectionRequest,
    MappingResolutionRequest,
    PublishRequest,
    ValidationRequest,
)
from .readers import InputInspectionError, inspect_upload
from .service import (
    APP_VERSION,
    InvalidJobStateError,
    JobService,
    MappingResolutionError,
)
from .sheets_routes import reconcile_interrupted_sheets_syncs, router as sheets_router
from .store import JobConflictError, JobNotFoundError, JobStore, utc_now
from .transformers import get_transformer, transformer_catalogue
from ..static_site import StaticWebsitePublisher, WebsitePublicationError


class FrontendJobConfiguration(BaseModel):
    authority_id: str | None = None
    election_id: str | None = None
    publication_phase: str = Field(default="final", pattern=r"^final$")
    source_url: str | None = None
    operator_note: str | None = None
    adapter_id: str | None = None


class ReproduceRequest(BaseModel):
    name: str | None = Field(default=None, max_length=300)


class FrontendPublishRequest(BaseModel):
    approved_by: str = Field(default="Local operator", min_length=1, max_length=200)
    snapshot_name: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4_000)
    release_note: str | None = Field(default=None, max_length=4_000)
    job_id: str | None = None


class WebsiteBuildRequest(BaseModel):
    election_id: str | None = Field(default=None, max_length=200)


def _safe_filename(filename: str | None) -> str:
    name = (filename or "upload").replace("\\", "/").split("/")[-1].strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=422, detail="Upload filename is invalid")
    return name


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _save_upload(upload: UploadFile, destination: Path, maximum_bytes: int) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > maximum_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds the configured {maximum_bytes:,}-byte limit",
                    )
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
        await upload.close()
    return {
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "content_type": upload.content_type,
    }


def _public_job(job: dict) -> dict:
    state = job["state"]
    status_alias = {
        "mapping_review": "mapping_required",
        "format_review": "needs_review",
        "executing": "running",
        "publishing": "running",
        "staged": "completed",
        "interrupted": "failed",
    }.get(state, state)
    results = job.get("execution", {}).get("dataset_results", {})
    total = max(len(job.get("datasets", [])), 1)
    completed = len(job.get("execution", {}).get("completed_dataset_ids", []))
    progress = {
        "uploading": 5,
        "inspecting": 12,
        "format_review": 22,
        "mapping_review": 28,
        "ready": 32,
        "queued": 35,
        "staged": 92,
        "validated": 100,
        "validation_failed": 100,
        "publishing": 98,
        "published": 100,
        "cancelled": 0,
    }.get(state, 35 + round(55 * completed / total) if state == "executing" else 0)
    phase = (
        "register"
        if progress < 40
        else "stage"
        if progress < 70
        else "transform"
        if progress < 95
        else "validate"
    )
    unresolved = sum(issue["status"] == "unresolved" for issue in job.get("mapping_issues", []))
    validation_failures = (job.get("validation") or {}).get("blocker_count", 0)
    rows = sum(item.get("staged_rows", 0) for item in results.values())
    if job.get("mode") == "reproduce_aec_2025":
        report = job.get("execution", {}).get("builtin_report") or {}
        rows = report.get("staged_source_rows", rows)
    elif job.get("mode") == AEC_ELECTION_BOOTSTRAP:
        rows = (job.get("bootstrap_preview") or {}).get("total_candidates", rows)
    return {
        **job,
        "id": job["job_id"],
        "status": status_alias,
        "source": job.get("authority_id") or job.get("mode"),
        "rows": rows,
        "row_count": rows,
        "progress_percent": progress,
        "phase": phase,
        "progress_message": job.get("events", [{}])[-1].get("message"),
        "log_lines": [event.get("message", "") for event in job.get("events", [])],
        "unresolved_mappings": unresolved,
        "validation_failures": validation_failures,
        "canonical_capable": bool(
            job.get("mode")
            in {"aec_election_bootstrap", "reproduce_aec_2025", "reference_sync"}
            or job.get("execution", {}).get("canonical_complete")
        ),
        "ingestion_scope": (
            "governed_canonical_pipeline"
            if job.get("mode") == "reproduce_aec_2025"
            else "reference_snapshot"
            if job.get("mode") == "reference_sync"
            else "governed_new_election_bootstrap"
            if job.get("mode") == "aec_election_bootstrap"
            else (
                "governed_individual_transformer"
                if job.get("execution", {}).get("canonical_complete")
                else "staging_only_until_transformer_registered"
            )
        ),
    }


def _public_issue(issue: dict) -> dict:
    canonical_id = issue.get("canonical_id")
    return {
        **issue,
        "id": issue["issue_id"],
        "field_name": issue.get("field"),
        "context": {"datasets": issue.get("dataset_ids", []), "occurrences": issue.get("occurrences")},
        "resolution": {
            "canonical_id": canonical_id,
            "label": canonical_id if canonical_id else (
                "Not applicable" if issue.get("resolution_type") == "not_applicable" else None
            ),
        },
    }


def _public_validation(report: dict | None, job: dict | None = None) -> dict:
    if not report:
        return {
            "status": "PENDING",
            "checks": [],
            "passed": 0,
            "warnings": 0,
            "failed": 0,
            "total": 0,
            "open_mappings": sum(
                issue["status"] == "unresolved" for issue in (job or {}).get("mapping_issues", [])
            ),
            "included_jobs": 1 if job else 0,
            "can_publish": False,
        }
    checks = [
        {
            **item,
            "id": item.get("rule_id"),
            "name": item.get("name") or item.get("rule_id", "Validation check").replace("_", " ").title(),
            "description": item.get("description") or item.get("message"),
            "blocking": item.get("blocking", item.get("severity") in {"blocker", "blocking"}),
        }
        for item in report.get("checks", [])
    ]
    failed = report.get("failed", report.get("blocker_count", 0))
    warnings = report.get("warnings", report.get("warning_count", 0))
    passed = report.get("passed", sum(item.get("status") in {"pass", "passed"} for item in checks))
    open_mappings = sum(
        issue["status"] == "unresolved" for issue in (job or {}).get("mapping_issues", [])
    )
    return {
        **report,
        "checks": checks,
        "passed": passed,
        "warnings": warnings,
        "failed": failed,
        "total": report.get("total", report.get("rules_executed", len(checks))),
        "open_mappings": open_mappings,
        "included_jobs": 1,
        "can_publish": bool(
            job
            and job.get("state") == "validated"
            and not failed
            and not open_mappings
        ),
    }


def _pending_root(settings: AppSettings) -> Path:
    path = settings.app_data / "pending_uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json_atomic(path: Path, document: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix="metadata-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or AppSettings.from_environment()
    settings.ensure_directories()
    instance_lock = FileLock(str(settings.app_data / ".application-instance.lock"))
    try:
        instance_lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError(
            "Another Politica application instance is already using this app-data directory."
        ) from exc
    try:
        store = JobStore(settings.jobs_root)
        service = JobService(settings, store)
    except Exception:
        instance_lock.release()
        raise
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if instance_lock.is_locked:
                instance_lock.release()

    app = FastAPI(
        title="Politica Election Results Operator",
        version=APP_VERSION,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.job_service = service
    app.state.instance_lock = instance_lock
    explorer = ElectionExplorer(
        service.governed_database,
        service._database_external_root,
        app_version=APP_VERSION,
        max_export_rows=settings.explorer_max_export_rows,
    )
    app.state.explorer = explorer
    publication_feeds = VisualisationFeedService(
        explorer,
        service.governed_release_identity,
        max_rows=settings.publication_max_rows,
        composition_contract_path=(
            settings.project_root / "config" / "parliament_composition_48th.yml"
        ),
    )
    app.state.publication_feeds = publication_feeds
    visualisation_contract = VisualisationContractService(
        settings.project_root / "config" / "visualisation_contract.yml",
        publication_feeds,
        service.governed_release_identity,
        app_version=APP_VERSION,
    )
    app.state.visualisation_contract = visualisation_contract
    website_publisher = StaticWebsitePublisher(
        settings,
        publication_feeds,
        visualisation_contract,
        service.governed_database,
        service.governed_release_identity,
    )
    app.state.website_publisher = website_publisher
    try:
        reconcile_interrupted_sheets_syncs(app)
    except Exception:
        instance_lock.release()
        raise

    @app.exception_handler(JobNotFoundError)
    async def job_not_found(_request: Request, exc: JobNotFoundError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": f"Job not found: {exc.args[0]}"})

    @app.exception_handler(JobConflictError)
    async def job_conflict(_request: Request, exc: JobConflictError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InputInspectionError)
    async def invalid_input(_request: Request, exc: InputInspectionError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(AecBootstrapError)
    async def invalid_aec_bootstrap(_request: Request, exc: AecBootstrapError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(DatasetSelectionError)
    async def invalid_dataset_selection(_request: Request, exc: DatasetSelectionError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ExplorerError)
    async def explorer_error(_request: Request, exc: ExplorerError):
        from fastapi.responses import JSONResponse

        status_code = 413 if isinstance(exc, ExportTooLargeError) else 422
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    @app.exception_handler(VisualisationContractError)
    async def visualisation_contract_error(
        _request: Request, exc: VisualisationContractError
    ):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(PublicationError)
    async def publication_error(_request: Request, exc: PublicationError):
        from fastapi.responses import JSONResponse

        status_code = 413 if isinstance(exc, PublicationTooLargeError) else 422
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    @app.exception_handler(WebsitePublicationError)
    async def website_publication_error(_request: Request, exc: WebsitePublicationError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/api/health")
    def health() -> dict:
        return service.health()

    @app.get("/api/status")
    def status() -> dict:
        return service.status()

    @app.get("/api/reference-options")
    def reference_options() -> dict:
        return service.reference_options()

    @app.get("/api/explorer/catalogue")
    def explorer_catalogue() -> dict:
        return explorer.catalogue()

    @app.get("/api/explorer/query")
    def explorer_query(
        dataset: str = Query(default="results", pattern=r"^(results|outcomes|participation|count_rounds|count_totals|ballot_datasets|contests)$"),
        election_id: str | None = Query(default=None, max_length=200),
        chamber_id: str | None = Query(default=None, max_length=200),
        state: str | None = Query(default=None, pattern=r"^(ACT|NSW|NT|QLD|SA|TAS|VIC|WA)$"),
        contest_id: str | None = Query(default=None, max_length=200),
        result_type: str | None = Query(default=None, max_length=100),
        vote_type: str | None = Query(default=None, max_length=100),
        reporting_level: str | None = Query(
            default=None,
            pattern=r"^(contest|state|division|polling_place|all)$",
        ),
        q: str | None = Query(default=None, max_length=200),
        page: int = Query(default=1, ge=1, le=1_000_000),
        page_size: int = Query(default=50, ge=1, le=250),
    ) -> dict:
        return explorer.query(
            dataset,
            ExplorerFilters(
                election_id=election_id,
                chamber_id=chamber_id,
                state=state,
                contest_id=contest_id,
                result_type=result_type,
                vote_type=vote_type,
                reporting_level=reporting_level,
                q=q,
            ),
            page=page,
            page_size=page_size,
        )

    @app.get("/api/explorer/export.csv")
    def explorer_export(
        dataset: str = Query(default="results", pattern=r"^(results|outcomes|participation|count_rounds|count_totals|ballot_datasets|contests)$"),
        election_id: str | None = Query(default=None, max_length=200),
        chamber_id: str | None = Query(default=None, max_length=200),
        state: str | None = Query(default=None, pattern=r"^(ACT|NSW|NT|QLD|SA|TAS|VIC|WA)$"),
        contest_id: str | None = Query(default=None, max_length=200),
        result_type: str | None = Query(default=None, max_length=100),
        vote_type: str | None = Query(default=None, max_length=100),
        reporting_level: str | None = Query(
            default=None,
            pattern=r"^(contest|state|division|polling_place|all)$",
        ),
        q: str | None = Query(default=None, max_length=200),
    ) -> StreamingResponse:
        filename, row_count, metadata, content = explorer.export(
            dataset,
            ExplorerFilters(
                election_id=election_id,
                chamber_id=chamber_id,
                state=state,
                contest_id=contest_id,
                result_type=result_type,
                vote_type=vote_type,
                reporting_level=reporting_level,
                q=q,
            ),
        )
        return StreamingResponse(
            content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Politica-Release-ID": str(metadata.get("release_id") or ""),
                "X-Politica-Schema-Version": str(metadata.get("schema_version") or ""),
                "X-Politica-Row-Count": str(row_count),
            },
        )

    def public_response(
        request: Request,
        content: bytes,
        *,
        media_type: str,
        release_id: str | None = None,
        publication_id: str | None = None,
        row_count: int | None = None,
        filename: str | None = None,
    ) -> Response:
        digest = hashlib.sha256(content).hexdigest()
        etag = f'"{digest}"'
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": (
                "ETag, X-Politica-Release-ID, X-Politica-Publication-ID, "
                "X-Politica-Row-Count, X-Politica-Payload-SHA256"
            ),
            "Cache-Control": "public, no-cache",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
            "X-Politica-Release-ID": str(release_id or ""),
            "X-Politica-Publication-ID": str(publication_id or ""),
            "X-Politica-Payload-SHA256": digest,
        }
        if row_count is not None:
            headers["X-Politica-Row-Count"] = str(row_count)
        if filename:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=content, media_type=media_type, headers=headers)

    @app.get("/api/public/v1/feeds")
    def public_feed_catalogue(request: Request) -> Response:
        document = publication_feeds.catalogue()
        content = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return public_response(
            request,
            content,
            media_type="application/json; charset=utf-8",
            release_id=document.get("release", {}).get("release_id"),
        )

    @app.get("/api/public/v1/visualisations")
    def public_visualisation_catalogue(
        request: Request,
        election_id: str | None = Query(default=None, max_length=200),
    ) -> Response:
        document = visualisation_contract.catalogue(election_id)
        content = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return public_response(
            request,
            content,
            media_type="application/json; charset=utf-8",
            release_id=document.get("release", {}).get("release_id"),
        )

    @app.get("/api/public/v1/feeds/{feed_id}.json")
    def public_feed_json(
        request: Request,
        feed_id: str,
        election_id: str | None = Query(default=None, max_length=200),
        state: str | None = Query(
            default=None, pattern=r"^(ACT|NSW|NT|QLD|SA|TAS|VIC|WA)$"
        ),
        contest_id: str | None = Query(default=None, max_length=200),
    ) -> Response:
        representation = publication_feeds.build(
            feed_id, PublicationFilters(election_id, state, contest_id)
        )
        return public_response(
            request,
            representation.json_bytes,
            media_type="application/json; charset=utf-8",
            release_id=representation.manifest["release"].get("release_id"),
            publication_id=representation.publication_id,
            row_count=representation.row_count,
        )

    @app.get("/api/public/v1/feeds/{feed_id}.csv")
    def public_feed_csv(
        request: Request,
        feed_id: str,
        election_id: str | None = Query(default=None, max_length=200),
        state: str | None = Query(
            default=None, pattern=r"^(ACT|NSW|NT|QLD|SA|TAS|VIC|WA)$"
        ),
        contest_id: str | None = Query(default=None, max_length=200),
    ) -> Response:
        representation = publication_feeds.build(
            feed_id, PublicationFilters(election_id, state, contest_id)
        )
        return public_response(
            request,
            representation.csv_bytes,
            media_type="text/csv; charset=utf-8",
            release_id=representation.manifest["release"].get("release_id"),
            publication_id=representation.publication_id,
            row_count=representation.row_count,
            filename=safe_feed_filename(feed_id, representation.publication_id),
        )

    @app.get("/api/public/v1/feeds/{feed_id}/manifest.json")
    def public_feed_manifest(
        request: Request,
        feed_id: str,
        election_id: str | None = Query(default=None, max_length=200),
        state: str | None = Query(
            default=None, pattern=r"^(ACT|NSW|NT|QLD|SA|TAS|VIC|WA)$"
        ),
        contest_id: str | None = Query(default=None, max_length=200),
    ) -> Response:
        representation = publication_feeds.build(
            feed_id, PublicationFilters(election_id, state, contest_id)
        )
        content = json.dumps(
            representation.manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return public_response(
            request,
            content,
            media_type="application/json; charset=utf-8",
            release_id=representation.manifest["release"].get("release_id"),
            publication_id=representation.publication_id,
            row_count=representation.row_count,
        )

    @app.get("/api/site-publication/status")
    def website_publication_status() -> dict:
        return website_publisher.status()

    @app.post("/api/site-publication/build")
    def build_website_publication(body: WebsiteBuildRequest | None = None) -> dict:
        return website_publisher.build(body.election_id if body else None)

    @app.get("/api/site-publication/download")
    def download_website_publication() -> FileResponse:
        export = website_publisher.active_export()
        return FileResponse(
            export,
            media_type="application/zip",
            filename=export.name,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/site-preview", include_in_schema=False)
    def website_preview_redirect() -> RedirectResponse:
        return RedirectResponse(url="/site-preview/", status_code=307)

    @app.get("/site-preview/{asset_path:path}", include_in_schema=False)
    def website_preview(asset_path: str = "") -> FileResponse:
        try:
            path = website_publisher.active_file(asset_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Website preview file not found") from exc
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.get("/api/adapters")
    def adapters() -> dict:
        catalogue = service.adapters.catalogue()
        for adapter in catalogue:
            for dataset in adapter["datasets"]:
                dataset["canonical_capable"] = bool(
                    get_transformer(adapter["adapter_id"], dataset["dataset_key"])
                )
            adapter["canonical_capable"] = any(
                dataset["canonical_capable"] for dataset in adapter["datasets"]
            )
        return {
            "adapters": catalogue,
            "canonical_transformers": transformer_catalogue(),
            "builtin_canonical_routes": [
                "aec_election_bootstrap",
                "reproduce_aec_2025",
                "reference_sync",
            ],
        }

    @app.post("/api/imports/detect")
    async def detect_import(file: UploadFile = File(...)) -> dict:
        upload_id = uuid.uuid4().hex
        directory = _pending_root(settings) / upload_id
        directory.mkdir()
        original_name = _safe_filename(file.filename)
        stored_name = f"source{Path(original_name).suffix.lower()}"
        path = directory / stored_name
        saved = await _save_upload(file, path, settings.max_upload_bytes)
        try:
            duplicate_revisions = service.duplicate_source_revisions(saved["sha256"])
            datasets, ignored = inspect_upload(
                path,
                upload_id,
                original_name,
                preview_rows=settings.preview_rows,
                max_archive_bytes=settings.max_archive_bytes,
                max_archive_members=settings.max_archive_members,
                max_xlsx_member_bytes=settings.max_xlsx_member_bytes,
            )
            for dataset in datasets:
                dataset["detection"] = service.adapters.detect(
                    dataset["virtual_name"], dataset["headers"], None
                )
            selection = next(
                (
                    dataset["detection"]["selection"]
                    for dataset in datasets
                    if dataset["detection"].get("selection")
                ),
                None,
            )
            canonical_capable = bool(
                selection
                and get_transformer(selection["adapter_id"], selection["dataset_key"])
            )
            primary = datasets[0]
            document = {
                "upload_id": upload_id,
                "original_name": original_name,
                "stored_name": stored_name,
                **saved,
                "datasets": datasets,
                "ignored_archive_members": ignored,
                "created_at": utc_now(),
            }
            _write_json_atomic(directory / "metadata.json", document)
            warnings = []
            if len(datasets) > 1:
                warnings.append(
                    f"The upload contains {len(datasets)} datasets; all will be reviewed in the job."
                )
            if duplicate_revisions:
                warnings.append(
                    "This exact file is already registered as source revision "
                    f"{duplicate_revisions[0]['source_revision_id']}. Duplicate bytes cannot "
                    "create another ingestion release."
                )
            if not selection:
                warnings.append("No single registered adapter matched both filename and headers.")
            elif not canonical_capable:
                warnings.append(
                    "This format can be preserved, previewed and staged, but no individual-file "
                    "canonical transformer is installed. Publication remains locked; use the "
                    "governed AEC 2025 reproduction route for the complete supported import."
                )
            if duplicate_revisions:
                shutil.rmtree(directory, ignore_errors=True)
            return {
                "upload_id": upload_id,
                "file": {"id": upload_id, "name": original_name, **saved},
                "detection": {
                    **(selection or {}),
                    "adapter_name": (selection or {}).get("adapter_id"),
                    "dataset": (selection or {}).get("dataset_key"),
                    "confidence": 1.0 if selection else 0.0,
                    "canonical_capable": canonical_capable,
                    "execution_mode": (
                        "duplicate_source"
                        if duplicate_revisions
                        else "canonical_transform"
                        if canonical_capable
                        else "staging_only"
                    ),
                    "duplicate_source": bool(duplicate_revisions),
                    "duplicate_revisions": duplicate_revisions,
                    "warnings": warnings,
                    "encoding": primary.get("encoding"),
                    "delimiter": primary.get("delimiter"),
                },
                "preview": {
                    "columns": primary["headers"],
                    "rows": primary["preview"],
                    "sheet": primary.get("sheet") or primary["virtual_name"],
                },
                "stats": {
                    "row_count": primary.get("row_count"),
                    "column_count": len(primary["headers"]),
                    "file_count": len(datasets),
                },
            }
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    @app.post("/api/imports/{upload_id}/jobs")
    def create_job_from_import(upload_id: str, body: FrontendJobConfiguration) -> dict:
        pending = _pending_root(settings) / upload_id
        try:
            document = json.loads((pending / "metadata.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=404, detail="Pending upload was not found") from exc
        job = service.begin_job(
            name=document["original_name"],
            authority_id=body.authority_id,
            election_id=body.election_id,
            publication_phase=body.publication_phase,
            source_url=body.source_url,
            operator_note=body.operator_note,
            requested_adapter_id=body.adapter_id,
        )
        source = pending / document["stored_name"]
        stored_name = f"{upload_id}-{_safe_filename(document['original_name'])}"
        destination = store.job_dir(job["job_id"]) / "uploads" / stored_name
        shutil.copy2(source, destination)
        if _sha256_path(destination) != document["sha256"]:
            raise HTTPException(status_code=500, detail="Durable upload copy failed checksum verification")
        upload_record = {
            "upload_id": upload_id,
            "original_name": document["original_name"],
            "stored_name": stored_name,
            "size_bytes": document["size_bytes"],
            "sha256": document["sha256"],
            "content_type": document.get("content_type"),
        }
        completed = service.finalise_uploads(job["job_id"], [upload_record])
        shutil.rmtree(pending)
        return {"job": _public_job(completed)}

    @app.post("/api/jobs")
    async def create_job(
        files: list[UploadFile] = File(...),
        name: str | None = Form(default=None),
        authority_id: str | None = Form(default=None),
        election_id: str | None = Form(default=None),
        publication_phase: str = Form(default="final", pattern=r"^final$"),
        source_url: str | None = Form(default=None),
        operator_note: str | None = Form(default=None),
        adapter_id: str | None = Form(default=None),
    ) -> dict:
        job = service.begin_job(
            name=name,
            authority_id=authority_id,
            election_id=election_id,
            publication_phase=publication_phase,
            source_url=source_url,
            operator_note=operator_note,
            requested_adapter_id=adapter_id,
        )
        uploads = []
        for upload in files:
            upload_id = uuid.uuid4().hex
            original_name = _safe_filename(upload.filename)
            stored_name = f"{upload_id}-{original_name}"
            destination = store.job_dir(job["job_id"]) / "uploads" / stored_name
            saved = await _save_upload(upload, destination, settings.max_upload_bytes)
            uploads.append(
                {
                    "upload_id": upload_id,
                    "original_name": original_name,
                    "stored_name": stored_name,
                    **saved,
                }
            )
        return {"job": _public_job(service.finalise_uploads(job["job_id"], uploads))}

    @app.post("/api/jobs/reproduce-2025")
    def reproduce_2025(body: ReproduceRequest | None = None) -> dict:
        return {"job": _public_job(service.begin_reproduce_2025(body.name if body else None))}

    @app.post("/api/jobs/bootstrap-aec-election")
    async def bootstrap_aec_election(
        files: list[UploadFile] = File(...),
        election_name: str = Form(..., min_length=1, max_length=300),
        official_event_id: str = Form(..., pattern=r"^\d+$"),
        election_date: str = Form(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
        election_type_code: str = Form(default="general"),
        publication_phase: str = Form(default="final"),
        contest_status: str = Form(default="nominations_closed"),
        senate_state_vacancies: int = Form(default=6, ge=1, le=24),
        senate_territory_vacancies: int = Form(default=2, ge=1, le=12),
        senate_whole_chamber: bool = Form(default=False),
        source_url: str | None = Form(default=None),
        operator_note: str | None = Form(default=None, max_length=4_000),
    ) -> dict:
        job = service.begin_aec_election_bootstrap(
            election_name=election_name,
            official_event_id=official_event_id,
            election_date=election_date,
            election_type_code=election_type_code,
            publication_phase=publication_phase,
            contest_status=contest_status,
            senate_state_vacancies=senate_state_vacancies,
            senate_territory_vacancies=senate_territory_vacancies,
            senate_whole_chamber=senate_whole_chamber,
            source_url=source_url,
            operator_note=operator_note,
        )
        uploads = []
        try:
            for upload in files:
                upload_id = uuid.uuid4().hex
                original_name = _safe_filename(upload.filename)
                stored_name = f"{upload_id}-{original_name}"
                destination = store.job_dir(job["job_id"]) / "uploads" / stored_name
                saved = await _save_upload(upload, destination, settings.max_upload_bytes)
                uploads.append(
                    {
                        "upload_id": upload_id,
                        "original_name": original_name,
                        "stored_name": stored_name,
                        **saved,
                    }
                )
            inspected = service.finalise_uploads(job["job_id"], uploads)
            return {"job": _public_job(inspected), "preview": inspected["bootstrap_preview"]}
        except Exception:
            # The failed job and immutable bytes remain available for audit; no governed
            # database or active release has been modified.
            raise

    @app.get("/api/jobs")
    def list_jobs() -> dict:
        return {"jobs": [_public_job(job) for job in store.list()]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return {"job": _public_job(store.read(job_id))}

    @app.put("/api/jobs/{job_id}/datasets/{dataset_id}")
    def select_dataset(job_id: str, dataset_id: str, body: DatasetSelectionRequest) -> dict:
        try:
            job = service.select_dataset(job_id, dataset_id, body.adapter_id, body.dataset_key)
            return {"job": _public_job(job)}
        except (DatasetSelectionError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def start_execution(job_id: str, background_tasks: BackgroundTasks, wait: bool) -> dict:
        service.queue_execution(job_id)
        if wait:
            return {"job": _public_job(service.execute_job(job_id))}
        background_tasks.add_task(service.execute_job, job_id)
        return {"job": _public_job(store.read(job_id))}

    @app.post("/api/jobs/{job_id}/run")
    def run_job(
        job_id: str, background_tasks: BackgroundTasks, wait: bool = Query(default=False)
    ) -> dict:
        return start_execution(job_id, background_tasks, wait)

    @app.post("/api/jobs/{job_id}/execute")
    def execute_job(
        job_id: str, background_tasks: BackgroundTasks, wait: bool = Query(default=False)
    ) -> dict:
        return start_execution(job_id, background_tasks, wait)

    @app.post("/api/jobs/{job_id}/resume")
    def resume_job(
        job_id: str, background_tasks: BackgroundTasks, wait: bool = Query(default=False)
    ) -> dict:
        return start_execution(job_id, background_tasks, wait)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict:
        return {"job": _public_job(service.cancel_job(job_id))}

    @app.get("/api/jobs/{job_id}/mappings")
    def mappings(job_id: str) -> dict:
        job = store.read(job_id)
        issues = [_public_issue(issue) for issue in job["mapping_issues"]]
        resolved = sum(issue["status"] == "resolved" for issue in issues)
        return {
            "issues": issues,
            "summary": {"open": len(issues) - resolved, "resolved": resolved, "total": len(issues)},
        }

    def resolve(job_id: str, mapping_id: str, body: MappingResolutionRequest) -> dict:
        try:
            job = service.resolve_mapping(job_id, mapping_id, body.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Mapping issue was not found") from exc
        except MappingResolutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        issue = next(item for item in job["mapping_issues"] if item["issue_id"] == mapping_id)
        return {"issue": _public_issue(issue), "job": _public_job(job)}

    @app.patch("/api/jobs/{job_id}/mappings/{mapping_id}")
    def patch_mapping(job_id: str, mapping_id: str, body: MappingResolutionRequest) -> dict:
        return resolve(job_id, mapping_id, body)

    @app.put("/api/jobs/{job_id}/mappings/{mapping_id}")
    def put_mapping(job_id: str, mapping_id: str, body: MappingResolutionRequest) -> dict:
        return resolve(job_id, mapping_id, body)

    @app.get("/api/canonical/{entity_type}")
    def canonical_search(
        entity_type: str, q: str | None = None, limit: int = Query(default=100, ge=1, le=500)
    ) -> list[dict]:
        try:
            return service.canonical_options(entity_type, q, limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}/validation")
    def get_job_validation(job_id: str) -> dict:
        job = store.read(job_id)
        return {"validation": _public_validation(job.get("validation"), job)}

    @app.post("/api/jobs/{job_id}/validate")
    def validate_job(job_id: str, body: ValidationRequest | None = None) -> dict:
        report = service.validate_job(
            job_id, requested_by=body.requested_by if body else "Local operator"
        )
        return {"validation": _public_validation(report, store.read(job_id))}

    @app.get("/api/validation")
    def current_validation() -> dict:
        return {"validation": _public_validation(service.current_release_validation())}

    @app.post("/api/validation/run")
    def rerun_current_validation() -> dict:
        # The current governed release is immutable; return its stored governed
        # evidence rather than writing a new run into it from a GET-like UI action.
        return {"validation": _public_validation(service.current_release_validation())}

    def publish(job_id: str, body: FrontendPublishRequest) -> dict:
        publication = service.publish_job(
            job_id,
            approved_by=body.approved_by,
            snapshot_name=body.snapshot_name,
            notes=body.notes or body.release_note,
        )
        return {"publication": publication}

    @app.post("/api/jobs/{job_id}/publish")
    def publish_job(job_id: str, body: FrontendPublishRequest) -> dict:
        return publish(job_id, body)

    @app.get("/api/publications")
    def publications() -> dict:
        return service.publications()

    @app.post("/api/publications")
    def publish_latest(body: FrontendPublishRequest) -> dict:
        job_id = body.job_id
        if not job_id:
            job_id = next(
                (job["job_id"] for job in store.list() if job["state"] == "validated"), None
            )
        if not job_id:
            raise HTTPException(
                status_code=409,
                detail="The current database is already governed; select a validated unpublished job.",
            )
        return publish(job_id, body)

    app.include_router(sheets_router)

    results_root = Path(__file__).parent / "results"
    if results_root.is_dir():

        @app.get("/results", include_in_schema=False)
        def public_results_redirect() -> RedirectResponse:
            return RedirectResponse(url="/results/", status_code=307)

        app.mount(
            "/results",
            StaticFiles(directory=results_root, html=True),
            name="public-results",
        )

    static_root = Path(__file__).parent / "static"
    if static_root.is_dir():
        app.mount("/static", StaticFiles(directory=static_root), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(static_root / "index.html")

    return app


app = create_app()
