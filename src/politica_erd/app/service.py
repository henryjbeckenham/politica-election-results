from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import duckdb
from filelock import FileLock, Timeout

from ..ids import source_revision_id as governed_source_revision_id
from .config import AppSettings
from .detection import AdapterCatalogue, DatasetSelectionError
from .readers import InputInspectionError, inspect_upload, iter_dataset_rows
from .references import ReferenceMatcher, map_row, merge_issue
from .store import JobConflictError, JobNotFoundError, JobStore, utc_now
from .transformers import TransformContext, get_transformer, transformer_catalogue


APP_VERSION = "1.8.0"
BUILTIN_AEC_2025 = "reproduce_aec_2025"
AEC_ELECTION_BOOTSTRAP = "aec_election_bootstrap"
STAGE7_DATASET_KEYS = frozenset(
    {
        "senate_first_preferences_state",
        "senate_first_preferences_division",
        "senate_elected",
        "enrolment_state",
        "senate_participation",
        "senate_participation_division",
    }
)
STAGE8_DATASET_KEYS = frozenset(
    {
        "senate_group_preferences_national",
        "senate_group_preferences_state",
        "senate_distribution",
        "senate_formal_preferences",
    }
)
BULK_EXTERNAL_DATASET_KEYS = frozenset({"senate_formal_preferences"})


class InvalidJobStateError(JobConflictError):
    pass


class MappingResolutionError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _uuid(*parts: object) -> str:
    name = "\x1f".join(str(part) for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://politica.example/stage3/{name}"))


def _dataset_id(job_id: str, dataset: dict) -> str:
    digest = hashlib.sha256(
        "\x1f".join(
            [
                job_id,
                dataset["upload_id"],
                dataset["virtual_name"],
                dataset.get("member") or "",
                dataset.get("sheet") or "",
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"dataset_{digest[:24]}"


class JobService:
    def __init__(self, settings: AppSettings, store: JobStore | None = None):
        self.settings = settings
        self.settings.ensure_directories()
        self.store = store or JobStore(settings.jobs_root)
        self.adapters = AdapterCatalogue(settings.project_root / "config/adapters")
        self.instance_id = str(uuid.uuid4())
        self._active_pointer_signature: tuple[int, int] | None = None
        self._active_database_signature: tuple[str, str, int, int] | None = None
        self._active_database_path: Path | None = None
        self._active_bundle_files: tuple[tuple[str, int, int, int], ...] | None = None
        self._artifact_inventory_cache: dict[
            str, tuple[tuple[tuple[str, int, int, int], ...], list[dict]]
        ] = {}
        self.recover_interrupted_jobs()

    def _portable_path(self, path: Path) -> str:
        """Use a project-root-relative path when the artifact is inside the project."""

        resolved = path.resolve()
        try:
            return resolved.relative_to(self.settings.project_root.resolve()).as_posix()
        except ValueError:
            return str(resolved)

    def _resolve_portable_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (self.settings.project_root / path).resolve()

    def _pointer_path(self, path: Path) -> tuple[str, str]:
        resolved = path.resolve()
        for base_name, base in (
            ("project_root", self.settings.project_root.resolve()),
            ("releases_root", self.settings.releases_root.resolve()),
        ):
            try:
                return base_name, resolved.relative_to(base).as_posix()
            except ValueError:
                continue
        return "absolute", str(resolved)

    def _resolve_pointer_path(self, value: str, pointer: dict) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        base_name = pointer.get("path_base", "project_root")
        if base_name == "releases_root":
            return (self.settings.releases_root / path).resolve()
        if base_name == "project_root":
            return (self.settings.project_root / path).resolve()
        raise ValueError(f"Unsupported active-pointer path base: {base_name!r}")

    @contextmanager
    def _job_operation_lock(self, job_id: str) -> Iterator[None]:
        directory = self.store.job_dir(job_id)
        if not directory.is_dir():
            raise JobNotFoundError(job_id)
        with FileLock(str(directory / ".operation.lock")):
            yield

    def _database_external_root(self, database: Path) -> Path:
        """Return the root against which portable ``data/parquet`` views resolve."""

        resolved = database.resolve()
        if resolved.parent.name == "database" and resolved.parent.parent.name == "data":
            return resolved.parent.parent.parent
        if (resolved.parent / "data" / "parquet").is_dir():
            return resolved.parent
        return self.settings.project_root.resolve()

    def _connect(
        self,
        database: Path,
        *,
        read_only: bool = False,
        external_data_root: Path | None = None,
    ) -> duckdb.DuckDBPyConnection:
        connection = duckdb.connect(str(database), read_only=read_only)
        root = (external_data_root or self._database_external_root(database)).resolve()
        escaped_root = str(root).replace("'", "''")
        connection.execute(f"SET file_search_path='{escaped_root}'")
        return connection

    def recover_interrupted_jobs(self) -> list[str]:
        """Make abandoned execution/publication work recoverable after a restart."""

        recovered: list[str] = []
        for document in self.store.list():
            job_id = document.get("job_id")
            state = document.get("state")
            if not job_id or state not in {"queued", "executing", "publishing"}:
                continue

            if state == "publishing":
                for temporary in self.settings.releases_root.glob(
                    f".politica-election-results-*-{job_id[:8]}.tmp-*"
                ):
                    if temporary.is_dir() and not temporary.is_symlink():
                        shutil.rmtree(temporary, ignore_errors=True)
                    else:
                        temporary.unlink(missing_ok=True)
                publication = self._reconcile_activated_publication(document)
                active_pointer_claims_job = self._active_pointer_claims_job(job_id)
                quarantined = (
                    []
                    if publication is not None or active_pointer_claims_job
                    else self._quarantine_unactivated_releases(job_id)
                )

                def recover_publication(metadata: dict) -> None:
                    if metadata.get("state") != "publishing":
                        return
                    if publication is not None:
                        metadata["publication"] = publication
                        metadata["state"] = "published"
                        metadata["last_error"] = None
                        metadata["events"].append(
                            {
                                "at": utc_now(),
                                "type": "publication_reconciled",
                                "message": (
                                    "Recovered the already-activated immutable release after "
                                    "the application stopped before its job checkpoint was saved."
                                ),
                            }
                        )
                    else:
                        metadata["state"] = (
                            "publishing" if active_pointer_claims_job else "validated"
                        )
                        metadata["last_error"] = (
                            (
                                "The active pointer identifies this job's release, but its job "
                                "checkpoint could not be reconstructed. The active bundle was "
                                "preserved for manual recovery."
                            )
                            if active_pointer_claims_job
                            else (
                                "Publication stopped before an activated release could be "
                                "reconciled. The validated job is safe to publish again."
                            )
                            + (
                                f" Quarantined {len(quarantined)} finalized but unactivated "
                                "release candidate(s)."
                                if quarantined
                                else ""
                            )
                        )
                        metadata["events"].append(
                            {
                                "at": utc_now(),
                                "type": (
                                    "publication_reconciliation_required"
                                    if active_pointer_claims_job
                                    else "publication_interrupted"
                                ),
                                "message": metadata["last_error"],
                            }
                        )

                updated = self.store.mutate(job_id, recover_publication)
                if updated.get("state") in {"validated", "published"}:
                    recovered.append(job_id)
                continue

            def recover(metadata: dict) -> None:
                if metadata.get("state") not in {"queued", "executing"}:
                    return
                previous = metadata["state"]
                metadata["state"] = "interrupted"
                metadata["last_error"] = (
                    "The local application stopped before this job reached its next durable "
                    "checkpoint. Resume will reuse committed database checkpoints."
                )
                metadata["events"].append(
                    {
                        "at": utc_now(),
                        "type": "execution_interrupted",
                        "message": (
                            f"Recovered abandoned {previous} state after application restart; "
                            "the job is ready to resume."
                        ),
                    }
                )

            updated = self.store.mutate(job_id, recover)
            if updated.get("state") == "interrupted":
                recovered.append(job_id)
        return recovered

    def _active_pointer_claims_job(self, job_id: str) -> bool:
        pointer_path = self.settings.releases_root / "active.json"
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return pointer.get("release_id") == f"release_stage3_{job_id.replace('-', '')}"

    def _quarantine_unactivated_releases(self, job_id: str) -> list[str]:
        """Move finalized, non-active candidates for one job out of the release namespace."""

        if self._active_pointer_claims_job(job_id):
            return []
        quarantine_root = self.settings.releases_root / ".quarantine"
        moved: list[str] = []
        for candidate in self.settings.releases_root.glob(
            f"politica-election-results-*-{job_id[:8]}"
        ):
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            manifest_path = candidate / "release_manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("job_id") != job_id:
                continue
            quarantine_root.mkdir(parents=True, exist_ok=True)
            destination = quarantine_root / f"{candidate.name}-{uuid.uuid4().hex}"
            os.replace(candidate, destination)
            moved.append(str(destination))
        return moved

    def _reconcile_activated_publication(self, job: dict) -> dict | None:
        """Rebuild a publication checkpoint when activation beat the JSON job write."""

        pointer_path = self.settings.releases_root / "active.json"
        expected_release_id = f"release_stage3_{job['job_id'].replace('-', '')}"
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            if pointer.get("release_id") != expected_release_id:
                return None
            database = self._resolve_pointer_path(pointer["database_path"], pointer)
            if not database.is_file() or _sha256_file(database) != pointer["sha256"]:
                return None
            self._verify_release_bundle(pointer, database)
            manifest_value = pointer.get("release_manifest_path")
            if manifest_value:
                manifest_path = self._resolve_pointer_path(manifest_value, pointer)
            else:
                release_root_value = pointer.get("release_root")
                release_root = (
                    self._resolve_pointer_path(release_root_value, pointer)
                    if release_root_value
                    else self._database_external_root(database)
                )
                manifest_path = release_root / "release_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                manifest.get("release_id") != expected_release_id
                or manifest.get("job_id") != job["job_id"]
                or manifest.get("database_sha256") != pointer["sha256"]
                or (
                    pointer.get("artifact_manifest_sha256")
                    and manifest.get("artifact_manifest_sha256")
                    != pointer["artifact_manifest_sha256"]
                )
            ):
                return None

            source_revisions: list[str] = []
            connection = self._connect(database, read_only=True)
            try:
                import_run_id = job.get("execution", {}).get("import_run_id")
                if import_run_id:
                    source_revisions = [
                        row[0]
                        for row in connection.execute(
                            """SELECT DISTINCT source_revision_id
                               FROM provenance.import_run_input
                               WHERE import_run_id=? ORDER BY source_revision_id""",
                            [import_run_id],
                        ).fetchall()
                    ]
                snapshot_hash = hashlib.sha256(
                    json.dumps(
                        {
                            "job_id": job["job_id"],
                            "validation_run_id": job["validation"]["validation_run_id"],
                            "source_revisions": source_revisions,
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                snapshot_id = _uuid("publication_snapshot", job["job_id"], snapshot_hash)
                release = connection.execute(
                    """SELECT schema_version, created_by, published_at
                       FROM control.database_release WHERE release_id=?""",
                    [expected_release_id],
                ).fetchone()
                snapshot = connection.execute(
                    """SELECT snapshot_hash, approved_by, approved_at
                       FROM publish.publication_snapshot
                       WHERE publication_snapshot_id=? AND approval_status='approved'""",
                    [snapshot_id],
                ).fetchone()
            finally:
                connection.close()
            if release is None or snapshot is None or snapshot[0] != snapshot_hash:
                return None

            release_root_value = pointer.get("release_root")
            release_root = (
                self._resolve_pointer_path(release_root_value, pointer)
                if release_root_value
                else manifest_path.parent
            )
            return {
                "job_id": job["job_id"],
                "release_id": expected_release_id,
                "publication_snapshot_id": snapshot_id,
                "snapshot_hash": snapshot_hash,
                "schema_version": release[0],
                "approved_by": snapshot[1] or release[1],
                "approved_at": snapshot[2].isoformat() if snapshot[2] else None,
                "source_revision_count": len(source_revisions),
                "release_root": self._portable_path(release_root),
                "database_path": self._portable_path(database),
                "release_manifest_path": self._portable_path(manifest_path),
                "database_size_bytes": database.stat().st_size,
                "database_sha256": pointer["sha256"],
                "artifact_file_count": manifest.get("artifact_file_count", 0),
                "artifact_size_bytes": manifest.get("artifact_size_bytes", 0),
                "artifact_manifest_sha256": manifest.get("artifact_manifest_sha256"),
                "release_manifest_sha256": pointer.get("release_manifest_sha256"),
                "release_validation": manifest.get("validation"),
                "activated_at": pointer.get("activated_at"),
                "recovered_after_restart": True,
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, duckdb.Error):
            return None

    @staticmethod
    def _metadata_signature(paths: list[tuple[str, Path]]) -> tuple[tuple[str, int, int, int], ...]:
        records = []
        for label, path in paths:
            stat = path.stat()
            records.append((label, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return tuple(records)

    def _artifact_inventory(self, root: Path) -> list[dict]:
        root = root.resolve()
        paths = sorted(
            path
            for relative in ("data/parquet", "data/manifests")
            for path in (root / relative).rglob("*")
            if path.is_file()
        )
        labelled = [(path.relative_to(root).as_posix(), path) for path in paths]
        signature = self._metadata_signature(labelled)
        cached = self._artifact_inventory_cache.get(str(root))
        if cached and cached[0] == signature:
            return [dict(entry) for entry in cached[1]]
        entries = [
            {
                "path": label,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
            for label, path in labelled
        ]
        post_hash_signature = self._metadata_signature(labelled)
        if post_hash_signature != signature:
            raise RuntimeError("External artifacts changed while their inventory was being hashed")
        self._artifact_inventory_cache[str(root)] = (post_hash_signature, entries)
        return [dict(entry) for entry in entries]

    def _verify_artifact_inventory(self, root: Path, expected: list[dict]) -> None:
        observed = self._artifact_inventory(root)
        normalise = lambda entries: sorted(
            (entry["path"], int(entry["size_bytes"]), entry["sha256"])
            for entry in entries
        )
        if normalise(observed) != normalise(expected):
            raise RuntimeError(
                "The job's pinned external artifact set changed after its working database "
                "was created. Start a new job from the current governed release."
            )

    @staticmethod
    def _pointer_generation(pointer: dict) -> str:
        payload = json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "pointer:" + hashlib.sha256(payload).hexdigest()

    def current_release_generation(self) -> str:
        """Return the compare-and-swap token for the currently governed release."""

        governed = self.governed_database().resolve()
        pointer_path = self.settings.releases_root / "active.json"
        if pointer_path.is_file():
            try:
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                database = self._resolve_pointer_path(pointer["database_path"], pointer)
                if database.resolve() == governed:
                    return self._pointer_generation(pointer)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        return "base:" + _sha256_file(governed) if governed.is_file() else "base:missing"

    def capture_base_release(self, *, include_artifacts: bool = True) -> dict:
        """Pin the exact governed database, artifacts and activation generation for a job."""

        database = self.governed_database().resolve()
        database_sha256 = _sha256_file(database)
        pointer_path = self.settings.releases_root / "active.json"
        pointer: dict | None = None
        if pointer_path.is_file():
            try:
                candidate = json.loads(pointer_path.read_text(encoding="utf-8"))
                candidate_database = self._resolve_pointer_path(
                    candidate["database_path"], candidate
                )
                if (
                    candidate_database.resolve() == database
                    and candidate.get("sha256") == database_sha256
                ):
                    pointer = candidate
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pointer = None
        generation = (
            self._pointer_generation(pointer)
            if pointer is not None
            else f"base:{database_sha256}"
        )
        artifact_root = self._database_external_root(database).resolve()
        descriptor = {
            "database_path": self._portable_path(database),
            "database_sha256": database_sha256,
            "artifact_root": self._portable_path(artifact_root),
            "release_id": pointer.get("release_id") if pointer else None,
            "generation": generation,
            "captured_at": utc_now(),
        }
        if include_artifacts:
            entries: list[dict] | None = None
            if pointer and pointer.get("release_manifest_path"):
                manifest_path = self._resolve_pointer_path(
                    pointer["release_manifest_path"], pointer
                )
                manifest_bytes = manifest_path.read_bytes()
                expected_manifest_sha256 = pointer.get("release_manifest_sha256")
                if (
                    not expected_manifest_sha256
                    or hashlib.sha256(manifest_bytes).hexdigest()
                    != expected_manifest_sha256
                ):
                    raise RuntimeError(
                        "The governed release manifest changed while the base release was "
                        "being captured"
                    )
                manifest = json.loads(manifest_bytes.decode("utf-8"))
                entries = [
                    dict(entry)
                    for entry in manifest.get("files", [])
                    if entry.get("path", "").startswith(("data/parquet/", "data/manifests/"))
                ]
            descriptor["artifact_files"] = (
                entries if entries is not None else self._artifact_inventory(artifact_root)
            )
            descriptor["artifact_inventory_sha256"] = hashlib.sha256(
                json.dumps(
                    descriptor["artifact_files"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        return descriptor

    def _bundle_files_unchanged(self) -> bool:
        if self._active_bundle_files is None:
            return True
        try:
            for path_value, size, mtime_ns, ctime_ns in self._active_bundle_files:
                stat = Path(path_value).stat()
                if (
                    stat.st_size != size
                    or stat.st_mtime_ns != mtime_ns
                    or stat.st_ctime_ns != ctime_ns
                ):
                    return False
            return True
        except OSError:
            return False

    def _verify_release_bundle(
        self, pointer: dict, database: Path
    ) -> tuple[tuple[str, int, int, int], ...] | None:
        manifest_value = pointer.get("release_manifest_path")
        expected_aggregate = pointer.get("artifact_manifest_sha256")
        if not manifest_value or not expected_aggregate:
            return None
        manifest_path = self._resolve_pointer_path(manifest_value, pointer)
        release_root_value = pointer.get("release_root")
        release_root = (
            self._resolve_pointer_path(release_root_value, pointer)
            if release_root_value
            else manifest_path.parent
        ).resolve()
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        expected_manifest_sha256 = pointer.get("release_manifest_sha256")
        if (
            not expected_manifest_sha256
            or hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256
            or manifest.get("release_id") != pointer.get("release_id")
        ):
            raise ValueError("Release manifest identity does not match the active pointer")
        manifest_database = (release_root / manifest.get("database_path", "")).resolve()
        if manifest_database != database.resolve():
            raise ValueError("Release manifest database path does not match the active pointer")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("Release manifest has no file inventory")
        aggregate = hashlib.sha256(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if (
            aggregate != expected_aggregate
            or manifest.get("artifact_manifest_sha256") != expected_aggregate
            or manifest.get("database_sha256") != pointer.get("sha256")
        ):
            raise ValueError("Release manifest digest does not match the active pointer")
        labelled_paths: list[tuple[str, Path]] = [("release_manifest.json", manifest_path)]
        database_seen = False
        for entry in entries:
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Release manifest contains an unsafe artifact path")
            artifact = (release_root / relative).resolve()
            try:
                artifact.relative_to(release_root)
            except ValueError as exc:
                raise ValueError("Release artifact escapes its immutable release root") from exc
            stat = artifact.stat()
            if stat.st_size != int(entry["size_bytes"]) or _sha256_file(artifact) != entry["sha256"]:
                raise ValueError(f"Release artifact failed integrity verification: {entry['path']}")
            labelled_paths.append((entry["path"], artifact))
            if artifact == database.resolve():
                database_seen = entry["sha256"] == pointer["sha256"]
        if not database_seen:
            raise ValueError("The active database is not bound to the release artifact inventory")
        return self._metadata_signature(labelled_paths)

    def governed_database(self) -> Path:
        pointer = self.settings.releases_root / "active.json"
        if pointer.is_file():
            try:
                pointer_stat = pointer.stat()
                pointer_signature = (pointer_stat.st_mtime_ns, pointer_stat.st_size)
                if (
                    pointer_signature == self._active_pointer_signature
                    and self._active_database_path is not None
                    and self._active_database_signature is not None
                ):
                    candidate_stat = self._active_database_path.stat()
                    cached_path, _cached_hash, cached_size, cached_mtime = (
                        self._active_database_signature
                    )
                    if (
                        str(self._active_database_path) == cached_path
                        and candidate_stat.st_size == cached_size
                        and candidate_stat.st_mtime_ns == cached_mtime
                        and self._bundle_files_unchanged()
                    ):
                        return self._active_database_path
                document = json.loads(pointer.read_text(encoding="utf-8"))
                candidate = self._resolve_pointer_path(document["database_path"], document)
                candidate_stat = candidate.stat()
                if _sha256_file(candidate) == document["sha256"]:
                    bundle_files = self._verify_release_bundle(document, candidate)
                    self._active_pointer_signature = pointer_signature
                    self._active_database_signature = (
                        str(candidate),
                        document["sha256"],
                        candidate_stat.st_size,
                        candidate_stat.st_mtime_ns,
                    )
                    self._active_database_path = candidate
                    self._active_bundle_files = bundle_files
                    return candidate
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        self._active_pointer_signature = None
        self._active_database_signature = None
        self._active_database_path = None
        self._active_bundle_files = None
        return self.settings.base_database

    def governed_release_identity(self) -> dict:
        """Return the verified immutable-release identity used by public feeds."""
        database = self.governed_database().resolve()
        pointer_path = self.settings.releases_root / "active.json"
        pointer: dict = {}
        if pointer_path.is_file():
            try:
                candidate = json.loads(pointer_path.read_text(encoding="utf-8"))
                if self._resolve_pointer_path(candidate["database_path"], candidate).resolve() == database:
                    pointer = candidate
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pointer = {}

        connection = self._connect(database, read_only=True)
        try:
            schema = connection.execute(
                "SELECT schema_version FROM control.schema_version ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
            release = connection.execute(
                """SELECT release_id, release_status, published_at
                   FROM control.database_release
                   ORDER BY published_at DESC NULLS LAST, release_started_at DESC LIMIT 1"""
            ).fetchone()
        finally:
            connection.close()

        database_sha256 = pointer.get("sha256")
        if not database_sha256:
            cached = self._active_database_signature
            database_sha256 = (
                cached[1]
                if cached is not None and Path(cached[0]).resolve() == database
                else _sha256_file(database)
            )
        return {
            "release_id": pointer.get("release_id") or (release[0] if release else None),
            "release_status": release[1] if release else None,
            "activated_at": pointer.get("activated_at"),
            "published_at": (
                release[2].isoformat() if release and release[2] is not None else None
            ),
            "database_sha256": database_sha256,
            "release_manifest_sha256": pointer.get("release_manifest_sha256"),
            "artifact_manifest_sha256": pointer.get("artifact_manifest_sha256"),
            "schema_version": schema[0] if schema else None,
            "application_version": APP_VERSION,
        }

    def health(self) -> dict:
        governed = self.governed_database()
        status = "ok" if governed.is_file() else "unavailable"
        result = {
            "status": status,
            "app_version": APP_VERSION,
            "governed_database": str(governed),
            "governed_database_exists": governed.is_file(),
            "job_count": len(self.store.list()),
        }
        if governed.is_file():
            connection = self._connect(governed, read_only=True)
            try:
                result["schema_version"] = connection.execute(
                    "SELECT schema_version FROM control.schema_version ORDER BY applied_at DESC LIMIT 1"
                ).fetchone()[0]
            finally:
                connection.close()
        return result

    def status(self) -> dict:
        database = self.governed_database()
        if not database.is_file():
            return {"status": "unavailable", "ok": False, "database_path": str(database)}
        connection = self._connect(database, read_only=True)
        try:
            schema_version = connection.execute(
                "SELECT schema_version FROM control.schema_version ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()[0]
            release = connection.execute(
                """SELECT release_id, release_status, published_at, notes
                   FROM control.database_release
                   ORDER BY published_at DESC NULLS LAST, release_started_at DESC LIMIT 1"""
            ).fetchone()
            election = connection.execute(
                "SELECT election_name FROM core.election ORDER BY election_date DESC LIMIT 1"
            ).fetchone()
            sources = connection.execute(
                "SELECT count(*) FROM provenance.source_file_revision"
            ).fetchone()[0]
            facts = connection.execute(
                """SELECT
                       (SELECT count(*) FROM results.vote_result
                         WHERE record_status='active') +
                       (SELECT count(*) FROM results.participation_result
                         WHERE record_status='active') +
                       (SELECT count(*) FROM results.contest_outcome
                         WHERE record_status='active')"""
            ).fetchone()[0]
            validation = connection.execute(
                """SELECT validation_run_id, completed_at, rules_executed, blocker_count,
                          warning_count, validation_status
                   FROM audit.validation_run ORDER BY completed_at DESC NULLS LAST LIMIT 1"""
            ).fetchone()
        finally:
            connection.close()
        open_mappings = sum(
            issue["status"] == "unresolved"
            for job in self.store.list()
            for issue in job.get("mapping_issues", [])
        )
        # warning_count is the number of warning issue rows, not failed rules.
        # A run with zero blockers therefore passed every blocking check.
        passed_checks = max(0, validation[2] - validation[3]) if validation else None
        return {
            "status": "ok",
            "ok": True,
            "app_version": APP_VERSION,
            "database": {
                "path": str(database),
                "schema_version": schema_version,
                "size_bytes": database.stat().st_size,
            },
            "metrics": {
                "official_sources": sources,
                "result_facts": facts,
                "open_mappings": open_mappings,
            },
            "release": {
                "release_id": release[0] if release else None,
                "status": release[1] if release else None,
                "validated_at": release[2].isoformat() if release and release[2] else None,
                "notes": release[3] if release else None,
                "election_name": election[0] if election else None,
            },
            "validation": {
                "validation_run_id": str(validation[0]) if validation else None,
                "completed_at": validation[1].isoformat() if validation and validation[1] else None,
                "passed": passed_checks,
                "total": validation[2] if validation else None,
                "failed": validation[3] if validation else None,
                "warnings": validation[4] if validation else None,
                "status": validation[5] if validation else None,
            },
            "mappings": {"open": open_mappings},
            "capabilities": {
                "individual_uploads": "registered_transformers_only",
                "read_only_explorer": True,
                "csv_exports": "fixed_parameterised_datasets",
                "canonical_routes": [
                    AEC_ELECTION_BOOTSTRAP,
                    BUILTIN_AEC_2025,
                    "reference_sync",
                    "house_first_preferences_by_vote_type",
                    "house_tcp_by_vote_type",
                    "house_tpp_division",
                    "house_elected",
                    "enrolment_division",
                    "house_participation",
                ],
                "registered_dataset_transformer_count": len(transformer_catalogue()),
            },
        }

    def reference_options(self) -> dict:
        database = self.governed_database()
        connection = self._connect(database, read_only=True)
        try:
            elections = [
                {
                    "election_id": row[0],
                    "election_name": row[1],
                    "election_date": str(row[2]),
                    "authority_id": row[3],
                }
                for row in connection.execute(
                    """SELECT election_id, election_name, election_date, authority_id
                       FROM core.election ORDER BY election_date DESC"""
                ).fetchall()
            ]
            authorities = [
                {"authority_id": row[0], "authority_code": row[1], "authority_name": row[2]}
                for row in connection.execute(
                    """SELECT authority_id, authority_code, authority_name
                       FROM control.electoral_authority WHERE active ORDER BY authority_name"""
                ).fetchall()
            ]
            return {"elections": elections, "authorities": authorities}
        finally:
            connection.close()

    def duplicate_source_revisions(
        self, sha256: str, database: Path | None = None
    ) -> list[dict]:
        """Return governed source revisions with exactly the same file bytes."""

        if len(sha256) != 64 or any(character not in "0123456789abcdefABCDEF" for character in sha256):
            raise ValueError("sha256 must contain exactly 64 hexadecimal characters")
        connection = self._connect(database or self.governed_database(), read_only=True)
        try:
            return [
                {
                    "source_revision_id": row[0],
                    "source_file_id": row[1],
                    "original_filename": row[2],
                    "election_id": row[3],
                    "revision_number": row[4],
                    "publication_status": row[5],
                    "record_status": row[6],
                }
                for row in connection.execute(
                    """SELECT revision.source_revision_id, source.source_file_id,
                              revision.original_filename, source.election_id,
                              revision.revision_number, revision.publication_status,
                              revision.record_status
                       FROM provenance.source_file_revision revision
                       JOIN provenance.source_file source
                         ON source.source_file_id=revision.source_file_id
                       WHERE lower(revision.sha256)=lower(?)
                       ORDER BY revision.downloaded_at DESC, revision.revision_number DESC""",
                    [sha256],
                ).fetchall()
            ]
        finally:
            connection.close()

    def begin_job(
        self,
        *,
        name: str | None,
        authority_id: str | None,
        election_id: str | None,
        publication_phase: str = "final",
        source_url: str | None = None,
        operator_note: str | None = None,
        requested_adapter_id: str | None = None,
    ) -> dict:
        if not self.governed_database().is_file():
            raise FileNotFoundError("The governed DuckDB database is unavailable")
        if publication_phase != "final":
            raise ValueError("Individual result-file ingestion supports final publication inputs only")
        base_release = self.capture_base_release(include_artifacts=True)
        job_id = str(uuid.uuid4())
        now = utc_now()
        metadata = {
            "job_id": job_id,
            "name": name or f"Ingestion {now[:10]}",
            "mode": "uploaded_files",
            "state": "uploading",
            "authority_id": authority_id,
            "election_id": election_id,
            "configuration": {
                "publication_phase": publication_phase,
                "source_url": source_url,
                "operator_note": operator_note,
                "requested_adapter_id": requested_adapter_id,
            },
            "created_at": now,
            "updated_at": now,
            "uploads": [],
            "ignored_archive_members": [],
            "datasets": [],
            "mapping_issues": [],
            "mapping_resolutions": {},
            "execution": {
                "import_run_id": None,
                "completed_dataset_ids": [],
                "dataset_results": {},
                "pending_mapping_issues": [],
                "requires_restage": False,
                "canonical_complete": False,
                "source_identities": {},
                "base_release": base_release,
            },
            "validation": None,
            "publication": None,
            "events": [{"at": now, "type": "job_created", "message": "Upload job created."}],
            "last_error": None,
        }
        return self.store.create(metadata)

    def begin_reproduce_2025(self, name: str | None = None) -> dict:
        base_release = self.capture_base_release(include_artifacts=False)
        job_id = str(uuid.uuid4())
        now = utc_now()
        metadata = {
            "job_id": job_id,
            "name": name or "Reproduce governed AEC 2025 release",
            "mode": BUILTIN_AEC_2025,
            "state": "ready",
            "authority_id": "authority_aec",
            "election_id": "election_fed_2025_05_03_general",
            "created_at": now,
            "updated_at": now,
            "uploads": [],
            "ignored_archive_members": [],
            "datasets": [],
            "mapping_issues": [],
            "mapping_resolutions": {},
            "execution": {
                "import_run_id": None,
                "completed_dataset_ids": [],
                "dataset_results": {},
                "requires_restage": False,
                "canonical_complete": False,
                "builtin_pipeline": BUILTIN_AEC_2025,
                "base_release": base_release,
            },
            "validation": None,
            "publication": None,
            "events": [
                {
                    "at": now,
                    "type": "job_created",
                    "message": "Governed AEC 2025 reproduction job created from 45 registered sources.",
                }
            ],
            "last_error": None,
        }
        return self.store.create(metadata)

    def begin_aec_election_bootstrap(
        self,
        *,
        election_name: str,
        official_event_id: str,
        election_date: str,
        election_type_code: str = "general",
        publication_phase: str = "final",
        contest_status: str = "nominations_closed",
        senate_state_vacancies: int = 6,
        senate_territory_vacancies: int = 2,
        senate_whole_chamber: bool = False,
        source_url: str | None = None,
        operator_note: str | None = None,
    ) -> dict:
        from .aec_bootstrap import governed_election_id, validate_configuration

        if not self.governed_database().is_file():
            raise FileNotFoundError("The governed DuckDB database is unavailable")
        configuration = validate_configuration(
            {
                "election_name": election_name,
                "official_event_id": official_event_id,
                "election_date": election_date,
                "election_type_code": election_type_code,
                "publication_phase": publication_phase,
                "contest_status": contest_status,
                "senate_state_vacancies": senate_state_vacancies,
                "senate_territory_vacancies": senate_territory_vacancies,
                "senate_whole_chamber": senate_whole_chamber,
                "source_url": source_url,
                "operator_note": operator_note,
                "requested_adapter_id": "adapter_aec_2025_v1",
            }
        )
        base_release = self.capture_base_release(include_artifacts=True)
        job_id = str(uuid.uuid4())
        now = utc_now()
        election_identifier = governed_election_id(configuration)
        metadata = {
            "job_id": job_id,
            "name": f"Register {configuration['election_name']}",
            "mode": AEC_ELECTION_BOOTSTRAP,
            "state": "uploading",
            "authority_id": "authority_aec",
            "election_id": election_identifier,
            "configuration": configuration,
            "created_at": now,
            "updated_at": now,
            "uploads": [],
            "ignored_archive_members": [],
            "datasets": [],
            "mapping_issues": [],
            "mapping_resolutions": {},
            "bootstrap_preview": None,
            "execution": {
                "import_run_id": None,
                "completed_dataset_ids": [],
                "dataset_results": {},
                "pending_mapping_issues": [],
                "requires_restage": False,
                "canonical_complete": False,
                "source_identities": {},
                "base_release": base_release,
                "bootstrap_report": None,
            },
            "validation": None,
            "publication": None,
            "events": [
                {
                    "at": now,
                    "type": "job_created",
                    "message": (
                        f"New AEC election preview job created for event "
                        f"{configuration['official_event_id']}."
                    ),
                }
            ],
            "last_error": None,
        }
        return self.store.create(metadata)

    def finalise_uploads(self, job_id: str, uploads: list[dict]) -> dict:
        with self._job_operation_lock(job_id):
            return self._finalise_uploads(job_id, uploads)

    def _finalise_uploads(self, job_id: str, uploads: list[dict]) -> dict:
        def record(metadata: dict) -> None:
            if metadata["state"] != "uploading":
                raise InvalidJobStateError("Uploads can only be attached while a job is uploading")
            metadata["uploads"] = uploads
            metadata["state"] = "inspecting"
            metadata["events"].append(
                {
                    "at": utc_now(),
                    "type": "uploads_preserved",
                    "message": f"Preserved {len(uploads)} immutable upload(s).",
                }
            )

        self.store.mutate(job_id, record)
        try:
            return self._inspect_job(job_id)
        except Exception as exc:
            self._fail_job(job_id, "inspection_failed", exc)
            raise

    def _inspect_job(self, job_id: str) -> dict:
        job = self.store.read(job_id)
        job_directory = self.store.job_dir(job_id)
        datasets: list[dict] = []
        ignored: list[dict] = []
        for upload in job["uploads"]:
            path = job_directory / "uploads" / upload["stored_name"]
            inspected, ignored_members = inspect_upload(
                path,
                upload["upload_id"],
                upload["original_name"],
                preview_rows=self.settings.preview_rows,
                max_archive_bytes=self.settings.max_archive_bytes,
                max_archive_members=self.settings.max_archive_members,
                max_xlsx_member_bytes=self.settings.max_xlsx_member_bytes,
            )
            for dataset in inspected:
                dataset["dataset_id"] = _dataset_id(job_id, dataset)
                dataset["detection"] = self.adapters.detect(
                    dataset["virtual_name"], dataset["headers"], job.get("authority_id")
                )
                requested_adapter = job.get("configuration", {}).get("requested_adapter_id")
                if requested_adapter:
                    current = dataset["detection"].get("selection")
                    candidates = dataset["detection"].get("candidates", [])
                    requested = [
                        candidate
                        for candidate in candidates
                        if candidate["adapter_id"] == requested_adapter
                    ]
                    if current and current["adapter_id"] == requested_adapter:
                        pass
                    elif len(requested) == 1:
                        dataset["detection"].update(
                            {"status": "operator_requested", "selection": requested[0]}
                        )
                    elif not requested:
                        warning = (
                            f"Requested adapter {requested_adapter!r} does not accept the "
                            f"headers in {dataset['virtual_name']!r}; no incompatible adapter "
                            "was forced."
                        )
                        dataset["detection"].setdefault("warnings", []).append(warning)
                        dataset["detection"]["requested_adapter_id"] = requested_adapter
                        dataset["detection"]["requested_adapter_status"] = "incompatible"
                        if dataset["detection"]["status"] != "unknown":
                            dataset["detection"].update(
                                {"status": "operator_request_incompatible", "selection": None}
                            )
                if dataset["detection"]["status"] == "unknown":
                    # Unknown schemas still enter the governed source-native
                    # quarantine. This is not a canonical adapter and can never
                    # satisfy the publication transform gate.
                    dataset["detection"].update(
                        {
                            "status": "quarantine_only",
                            "selection": {
                                "adapter_id": "stage3_quarantine",
                                "adapter_version": APP_VERSION,
                                "authority_id": job.get("authority_id"),
                                "dataset_key": (
                                    "unregistered_" + dataset["schema_signature_sha256"][:16]
                                ),
                                "destination": "staging.source_record",
                                "grain": "one unclassified source-native row",
                                "required_headers": [],
                                "match_method": "quarantine_only",
                            },
                        }
                    )
                selection = dataset["detection"].get("selection")
                grouped_bootstrap_capable = bool(
                    job.get("mode") == AEC_ELECTION_BOOTSTRAP
                    and selection
                    and selection.get("adapter_id") == "adapter_aec_2025_v1"
                    and selection.get("dataset_key")
                    in {"house_candidates", "senate_candidates"}
                )
                canonical_capable = bool(
                    grouped_bootstrap_capable
                    or (
                        selection
                        and get_transformer(
                            selection["adapter_id"], selection["dataset_key"]
                        )
                    )
                )
                dataset["detection"]["canonical_capable"] = canonical_capable
                dataset["detection"]["execution_mode"] = (
                    "grouped_election_bootstrap"
                    if grouped_bootstrap_capable
                    else "canonical_transform"
                    if canonical_capable
                    else "staging_only"
                )
                datasets.append(dataset)
            ignored.extend(
                [{"upload_id": upload["upload_id"], **item} for item in ignored_members]
            )

        base_release = job.get("execution", {}).get("base_release") or {}
        database = (
            self._resolve_portable_path(base_release["database_path"])
            if base_release.get("database_path")
            else self.governed_database()
        )
        bootstrap_preview = None
        issues: dict[str, dict] = {}
        if job.get("mode") == AEC_ELECTION_BOOTSTRAP:
            from .aec_bootstrap import inspect_aec_bootstrap

            bootstrap_preview = inspect_aec_bootstrap(
                database,
                job_directory,
                {**job, "datasets": datasets},
            )
        else:
            connection = self._connect(database, read_only=True)
            try:
                matcher = ReferenceMatcher(connection, job.get("authority_id"))
                for dataset in datasets:
                    selection = dataset["detection"].get("selection")
                    if selection and selection["adapter_id"] == "stage3_quarantine":
                        continue
                    for row in dataset["preview"]:
                        _, unresolved = map_row(
                            row,
                            matcher,
                            {},
                            self._mapping_entity_types(dataset),
                        )
                        for observation in unresolved:
                            merge_issue(issues, observation, dataset["dataset_id"])
            finally:
                connection.close()

        def finish(metadata: dict) -> None:
            metadata["datasets"] = datasets
            metadata["ignored_archive_members"] = ignored
            metadata["mapping_issues"] = list(issues.values())
            metadata["bootstrap_preview"] = bootstrap_preview
            metadata["state"] = self._review_state(metadata)
            metadata["events"].append(
                {
                    "at": utc_now(),
                    "type": "inspection_completed",
                    "message": (
                        (
                            f"Previewed new AEC event {bootstrap_preview['official_event_id']}: "
                            f"{bootstrap_preview['total_contests']:,} contests and "
                            f"{bootstrap_preview['total_candidates']:,} candidates; no database "
                            "records were changed."
                            if bootstrap_preview
                            else f"Inspected {len(datasets)} dataset(s); "
                            f"{len(issues)} reference mapping(s) require review."
                        )
                    ),
                }
            )

        return self.store.mutate(job_id, finish)

    @staticmethod
    def _review_state(job: dict) -> str:
        if any(dataset["detection"].get("selection") is None for dataset in job["datasets"]):
            return "format_review"
        unresolved = [issue for issue in job["mapping_issues"] if issue["status"] == "unresolved"]
        return "mapping_review" if unresolved else "ready"

    @staticmethod
    def _mapping_entity_types(dataset: dict) -> set[str] | None:
        selection = dataset.get("detection", {}).get("selection") or {}
        configured = selection.get("mapping_entities")
        return None if configured is None else set(configured)

    def select_dataset(
        self, job_id: str, dataset_id: str, adapter_id: str, dataset_key: str
    ) -> dict:
        with self._job_operation_lock(job_id):
            return self._select_dataset(job_id, dataset_id, adapter_id, dataset_key)

    def _select_dataset(
        self, job_id: str, dataset_id: str, adapter_id: str, dataset_key: str
    ) -> dict:
        job = self.store.read(job_id)
        dataset = next(
            (item for item in job["datasets"] if item["dataset_id"] == dataset_id), None
        )
        if dataset is None:
            raise KeyError(dataset_id)
        selection = self.adapters.validate_selection(
            adapter_id,
            dataset_key,
            dataset["headers"],
            job.get("authority_id"),
        )

        def apply(metadata: dict) -> None:
            if metadata["state"] in {"queued", "executing", "publishing", "published"}:
                raise InvalidJobStateError("Dataset selection is locked in the current job state")
            selected = next(item for item in metadata["datasets"] if item["dataset_id"] == dataset_id)
            selected["detection"]["selection"] = selection
            selected["detection"]["status"] = "operator_selected"
            metadata["validation"] = None
            if (self.store.job_dir(job_id) / "work/database.duckdb").exists():
                metadata["execution"]["requires_restage"] = True
            metadata["state"] = self._review_state(metadata)
            metadata["events"].append(
                {
                    "at": utc_now(),
                    "type": "dataset_selected",
                    "message": f"Selected {adapter_id}/{dataset_key} for {dataset_id}.",
                }
            )

        return self.store.mutate(job_id, apply)

    def resolve_mapping(self, job_id: str, mapping_id: str, resolution: dict) -> dict:
        with self._job_operation_lock(job_id):
            return self._resolve_mapping(job_id, mapping_id, resolution)

    def _resolve_mapping(self, job_id: str, mapping_id: str, resolution: dict) -> dict:
        job = self.store.read(job_id)
        issue = next(
            (item for item in job["mapping_issues"] if item["issue_id"] == mapping_id), None
        )
        if issue is None:
            raise KeyError(mapping_id)
        if resolution["resolution_type"] == "matched":
            database = self.governed_database()
            connection = self._connect(database, read_only=True)
            try:
                matcher = ReferenceMatcher(connection, job.get("authority_id"))
                if not matcher.canonical_exists(
                    connection, issue["entity_type"], resolution["canonical_id"]
                ):
                    raise MappingResolutionError(
                        f"Unknown canonical {issue['entity_type']} ID: {resolution['canonical_id']}"
                    )
            finally:
                connection.close()
        resolved_at = utc_now()

        def apply(metadata: dict) -> None:
            if metadata["state"] in {"queued", "executing", "publishing", "published"}:
                raise InvalidJobStateError("Mappings are locked in the current job state")
            target = next(
                item for item in metadata["mapping_issues"] if item["issue_id"] == mapping_id
            )
            target.update(
                {
                    "status": "resolved",
                    "resolution_type": resolution["resolution_type"],
                    "canonical_id": resolution.get("canonical_id"),
                    "notes": resolution.get("notes"),
                    "resolved_by": resolution["resolved_by"],
                    "resolved_at": resolved_at,
                }
            )
            metadata["mapping_resolutions"][mapping_id] = {
                **resolution,
                "resolved_at": resolved_at,
            }
            metadata["validation"] = None
            if (self.store.job_dir(job_id) / "work/database.duckdb").exists():
                metadata["execution"]["requires_restage"] = True
            metadata["state"] = self._review_state(metadata)
            metadata["events"].append(
                {
                    "at": resolved_at,
                    "type": "mapping_resolved",
                    "message": f"Resolved {mapping_id} as {resolution['resolution_type']}.",
                }
            )

        return self.store.mutate(job_id, apply)

    def queue_execution(self, job_id: str) -> dict:
        with self._job_operation_lock(job_id):
            return self._queue_execution(job_id)

    def _queue_execution(self, job_id: str) -> dict:
        def queue(metadata: dict) -> None:
            if metadata["state"] not in {
                "ready",
                "failed",
                "interrupted",
                "staged",
                "validation_failed",
            }:
                raise InvalidJobStateError(
                    f"Job must be ready, staged or resumable; current state is {metadata['state']}"
                )
            if metadata["mode"] == "uploaded_files":
                if any(
                    dataset["detection"].get("selection") is None
                    for dataset in metadata["datasets"]
                ):
                    raise InvalidJobStateError("Every dataset requires an adapter selection")
                if any(issue["status"] == "unresolved" for issue in metadata["mapping_issues"]):
                    raise InvalidJobStateError("Resolve preview mapping issues before execution")
            elif metadata["mode"] == AEC_ELECTION_BOOTSTRAP:
                if not metadata.get("bootstrap_preview"):
                    raise InvalidJobStateError("Review a successful Stage 6 preview before execution")
            metadata["state"] = "queued"
            metadata["last_error"] = None
            metadata["execution"]["queued_by_instance_id"] = self.instance_id
            metadata["events"].append(
                {"at": utc_now(), "type": "execution_queued", "message": "Execution queued."}
            )

        return self.store.mutate(job_id, queue)

    def cancel_job(self, job_id: str) -> dict:
        job_directory = self.store.job_dir(job_id)
        if not job_directory.is_dir():
            raise JobNotFoundError(job_id)
        lock = FileLock(str(job_directory / ".operation.lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise InvalidJobStateError(
                "The active transaction cannot be interrupted safely; wait for its checkpoint."
            ) from exc
        try:
            def cancel(metadata: dict) -> None:
                if metadata["state"] in {"executing", "publishing", "published"}:
                    raise InvalidJobStateError(
                        "An executing, publishing or published job cannot be cancelled."
                    )
                metadata["state"] = "cancelled"
                metadata["events"].append(
                    {
                        "at": utc_now(),
                        "type": "job_cancelled",
                        "message": "Job cancelled before execution.",
                    }
                )

            return self.store.mutate(job_id, cancel)
        finally:
            lock.release()

    def execute_job(self, job_id: str) -> dict:
        with self._job_operation_lock(job_id):
            return self._execute_job(job_id)

    def _execute_job(self, job_id: str) -> dict:
        def start(metadata: dict) -> None:
            if metadata["state"] != "queued":
                raise InvalidJobStateError("Job is not queued")
            metadata["state"] = "executing"
            metadata["execution"]["worker_instance_id"] = self.instance_id
            metadata["execution"]["worker_pid"] = os.getpid()
            metadata["events"].append(
                {"at": utc_now(), "type": "execution_started", "message": "Execution started."}
            )

        self.store.mutate(job_id, start)
        job = self.store.read(job_id)
        try:
            if job["mode"] == BUILTIN_AEC_2025:
                completed = self._execute_builtin_2025(job_id)
            else:
                completed = self._execute_upload_job(job_id)
            if (
                completed.get("state") == "staged"
                and completed.get("execution", {}).get("canonical_complete") is True
            ):
                self._validate_job(job_id, requested_by="Automated governed ingestion")
                return self.store.read(job_id)
            return completed
        except Exception as exc:
            self._fail_job(job_id, "execution_failed", exc)
            raise

    def _working_database(self, job_id: str) -> Path:
        return self.store.job_dir(job_id) / "work/database.duckdb"

    def _copy_governed_database(self, job_id: str) -> tuple[Path, str]:
        job = self.store.read(job_id)
        base_release = job.get("execution", {}).get("base_release")
        if not base_release:
            base_release = self.capture_base_release(include_artifacts=True)
            self.store.mutate(
                job_id,
                lambda metadata: metadata["execution"].update(
                    {"base_release": base_release}
                ),
            )
        source = self._resolve_portable_path(base_release["database_path"])
        if not source.is_file():
            raise FileNotFoundError(f"Pinned governed database does not exist: {source}")
        source_hash = _sha256_file(source)
        if source_hash != base_release["database_sha256"]:
            raise RuntimeError(
                "The database pinned when this job was created has changed. Start a new job."
            )
        destination = self._working_database(job_id)
        temporary = destination.with_suffix(".duckdb.copying")
        temporary.unlink(missing_ok=True)
        shutil.copy2(source, temporary)
        copied_hash = _sha256_file(temporary)
        if copied_hash != source_hash:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Working database copy failed checksum verification")
        os.replace(temporary, destination)
        return destination, copied_hash

    def _prepare_job_external_artifacts(self, job_id: str) -> Path:
        """Pin existing Parquet artifacts into a ballot job's isolated work tree.

        Ballot archives are external-data transforms: the DuckDB file contains
        portable views while the anonymous ballot paths live in Parquet. Existing
        immutable Parquet files are hard-linked when possible (copied otherwise),
        and manifests are copied normally so a job can safely write new metadata.
        """

        job = self.store.read(job_id)
        base_release = job.get("execution", {}).get("base_release") or {}
        source_value = base_release.get("artifact_root")
        source_root = (
            self._resolve_portable_path(source_value)
            if source_value
            else self.settings.project_root.resolve()
        )
        expected = base_release.get("artifact_files") or []
        if expected:
            self._verify_artifact_inventory(source_root, expected)

        work_root = self.store.job_dir(job_id) / "work"
        marker = work_root / ".external_artifacts_ready.json"
        expected_digest = base_release.get("artifact_inventory_sha256") or hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with FileLock(str(self.store.job_dir(job_id) / ".external-artifacts.lock")):
            if marker.is_file():
                try:
                    document = json.loads(marker.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    document = {}
                if document.get("base_artifact_inventory_sha256") == expected_digest:
                    return work_root

            parquet_destination = work_root / "data" / "parquet"
            manifests_destination = work_root / "data" / "manifests"
            shutil.rmtree(parquet_destination, ignore_errors=True)
            shutil.rmtree(manifests_destination, ignore_errors=True)
            parquet_destination.parent.mkdir(parents=True, exist_ok=True)

            def hardlink_or_copy(source: str, destination: str) -> str:
                try:
                    os.link(source, destination)
                    return destination
                except OSError:
                    return shutil.copy2(source, destination)

            source_parquet = source_root / "data" / "parquet"
            if source_parquet.is_dir():
                shutil.copytree(
                    source_parquet,
                    parquet_destination,
                    copy_function=hardlink_or_copy,
                )
            else:
                parquet_destination.mkdir(parents=True, exist_ok=True)
            source_manifests = source_root / "data" / "manifests"
            if source_manifests.is_dir():
                shutil.copytree(
                    source_manifests,
                    manifests_destination,
                    copy_function=shutil.copy2,
                )
            else:
                manifests_destination.mkdir(parents=True, exist_ok=True)

            temporary = marker.with_suffix(f".tmp-{uuid.uuid4().hex}")
            temporary.write_text(
                json.dumps(
                    {
                        "base_artifact_inventory_sha256": expected_digest,
                        "source_root": self._portable_path(source_root),
                        "prepared_at": utc_now(),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, marker)
        return work_root

    def _execute_upload_job(self, job_id: str) -> dict:
        job = self.store.read(job_id)
        work_database = self._working_database(job_id)
        changed_uploads = []
        for upload in job.get("uploads", []):
            source = self.store.job_dir(job_id) / "uploads" / upload["stored_name"]
            if not source.is_file() or _sha256_file(source) != upload["sha256"]:
                changed_uploads.append(upload["original_name"])
        if changed_uploads:
            raise InvalidJobStateError(
                "Immutable upload checksum verification failed before execution: "
                + ", ".join(changed_uploads)
            )
        if job["execution"].get("requires_restage"):
            work_database.unlink(missing_ok=True)
            Path(str(work_database) + ".wal").unlink(missing_ok=True)

            def reset(metadata: dict) -> None:
                metadata["execution"].update(
                    {
                        "import_run_id": None,
                        "completed_dataset_ids": [],
                        "dataset_results": {},
                        "pending_mapping_issues": [],
                        "requires_restage": False,
                        "canonical_complete": False,
                        "source_identities": {},
                    }
                )

            self.store.mutate(job_id, reset)
            job = self.store.read(job_id)
        if not work_database.is_file():
            base_release = job.get("execution", {}).get("base_release") or {}
            base_database = (
                self._resolve_portable_path(base_release["database_path"])
                if base_release.get("database_path")
                else self.governed_database()
            )
            duplicate_uploads = []
            for upload in job["uploads"]:
                matches = self.duplicate_source_revisions(upload["sha256"], base_database)
                if matches:
                    duplicate_uploads.append(
                        {
                            "upload": upload["original_name"],
                            "matches": matches,
                        }
                    )
            if duplicate_uploads:
                summary = "; ".join(
                    f"{item['upload']} already registered as "
                    f"{item['matches'][0]['source_revision_id']}"
                    for item in duplicate_uploads
                )
                raise InvalidJobStateError(
                    "Exact duplicate source bytes cannot create another ingestion release: "
                    + summary
                )
            _, base_hash = self._copy_governed_database(job_id)

            def copied(metadata: dict) -> None:
                metadata["execution"]["base_database_sha256"] = base_hash
                metadata["events"].append(
                    {
                        "at": utc_now(),
                        "type": "working_copy_created",
                        "message": "Checksum-verified working database copy created.",
                    }
                )

            self.store.mutate(job_id, copied)

        job = self.store.read(job_id)
        if any(
            (dataset.get("detection", {}).get("selection") or {}).get("dataset_key")
            in BULK_EXTERNAL_DATASET_KEYS
            for dataset in job.get("datasets", [])
        ):
            self._prepare_job_external_artifacts(job_id)
            job = self.store.read(job_id)
        import_run_id = job["execution"].get("import_run_id") or _uuid("import_run", job_id)
        connection = self._connect(work_database)
        try:
            source_identities = self._register_run_and_sources(
                connection, job, import_run_id
            )
        finally:
            connection.close()

        self.store.mutate(
            job_id,
            lambda metadata: metadata["execution"].update(
                {
                    "import_run_id": import_run_id,
                    "source_identities": source_identities,
                }
            ),
        )
        job = self.store.read(job_id)
        completed = set(job["execution"]["completed_dataset_ids"])
        execution_issues: dict[str, dict] = {
            issue["issue_id"]: issue
            for issue in job["execution"].get("pending_mapping_issues", [])
        }
        for dataset in job["datasets"]:
            if dataset["dataset_id"] in completed:
                continue
            checkpoint = self._recover_dataset_checkpoint(
                work_database, job, dataset, import_run_id
            )
            if checkpoint is None:
                result, dataset_issues = self._stage_dataset(
                    work_database, job, dataset, import_run_id
                )
            else:
                result, dataset_issues = checkpoint
            for issue in dataset_issues.values():
                if issue["issue_id"] not in execution_issues:
                    execution_issues[issue["issue_id"]] = issue
                else:
                    execution_issues[issue["issue_id"]]["occurrences"] += issue["occurrences"]
                    for value in issue["dataset_ids"]:
                        if value not in execution_issues[issue["issue_id"]]["dataset_ids"]:
                            execution_issues[issue["issue_id"]]["dataset_ids"].append(value)

            def save_checkpoint(
                metadata: dict,
                dataset_id=dataset["dataset_id"],
                result=result,
                dataset_issues=dataset_issues,
            ) -> None:
                if dataset_id not in metadata["execution"]["completed_dataset_ids"]:
                    metadata["execution"]["completed_dataset_ids"].append(dataset_id)
                metadata["execution"]["dataset_results"][dataset_id] = result
                pending = {
                    issue["issue_id"]: issue
                    for issue in metadata["execution"].get("pending_mapping_issues", [])
                }
                for issue in dataset_issues.values():
                    pending[issue["issue_id"]] = issue
                metadata["execution"]["pending_mapping_issues"] = list(pending.values())
                metadata["events"].append(
                    {
                        "at": utc_now(),
                        "type": "dataset_checkpoint",
                        "message": f"Checkpointed {dataset_id}: {result['staged_rows']:,} rows.",
                    }
                )

            self.store.mutate(job_id, save_checkpoint)

        job = self.store.read(job_id)
        if job.get("mode") == AEC_ELECTION_BOOTSTRAP:
            from .aec_bootstrap import bootstrap_aec_election, reference_snapshot

            transform_ids = {
                dataset["dataset_id"]: _uuid(
                    "transform", import_run_id, dataset["dataset_id"]
                )
                for dataset in job["datasets"]
            }
            source_revisions = {
                upload_id: identity["source_revision_id"]
                for upload_id, identity in job["execution"]["source_identities"].items()
            }
            report = job["execution"].get("bootstrap_report")
            connection = self._connect(work_database)
            try:
                if report is None:
                    transform_rows = connection.execute(
                        """SELECT transform_run_id, output_row_count, transform_status
                           FROM provenance.transform_run WHERE import_run_id=?""",
                        [import_run_id],
                    ).fetchall()
                    status_by_id = {
                        str(row[0]): (int(row[1] or 0), row[2]) for row in transform_rows
                    }
                    committed = bool(transform_ids) and all(
                        status_by_id.get(str(transform_id), (0, None))[1] == "completed"
                        for transform_id in transform_ids.values()
                    )
                    election_present = connection.execute(
                        "SELECT count(*) FROM core.election WHERE election_id=?",
                        [job["election_id"]],
                    ).fetchone()[0] == 1
                    if committed and election_present:
                        expected_reference_snapshot = (
                            job.get("bootstrap_preview", {}).get("reference_snapshot") or {}
                        )
                        current_reference_snapshot = reference_snapshot(connection)
                        if current_reference_snapshot != expected_reference_snapshot:
                            raise RuntimeError(
                                "The Grand Database reference snapshot differs from the reviewed preview."
                            )
                        reference_counts = {
                            key: value.get("row_count", 0)
                            for key, value in current_reference_snapshot.items()
                        }
                        dataset_counts = {
                            dataset_id: status_by_id[str(transform_id)][0]
                            for dataset_id, transform_id in transform_ids.items()
                        }
                        report = {
                            **(job.get("bootstrap_preview") or {}),
                            "status": "PASS",
                            "inserted_rows": sum(dataset_counts.values()),
                            "dataset_inserted_rows": dataset_counts,
                            "lineage_rows": connection.execute(
                                "SELECT count(*) FROM provenance.row_lineage WHERE import_run_id=?",
                                [import_run_id],
                            ).fetchone()[0],
                            "reference_snapshot_before": current_reference_snapshot,
                            "reference_snapshot_after": current_reference_snapshot,
                            "reference_counts_before": reference_counts,
                            "reference_counts_after": reference_counts,
                            "completed_at": utc_now(),
                            "recovered_from_database": True,
                        }
                    else:
                        report = bootstrap_aec_election(
                            connection,
                            job_directory=self.store.job_dir(job_id),
                            job=job,
                            import_run_id=import_run_id,
                            source_revision_by_upload=source_revisions,
                            transform_run_by_dataset=transform_ids,
                        )
                connection.execute("CHECKPOINT")
            finally:
                connection.close()

            def save_bootstrap(metadata: dict) -> None:
                metadata["execution"]["bootstrap_report"] = report
                for dataset_id, inserted in report["dataset_inserted_rows"].items():
                    result = metadata["execution"]["dataset_results"][dataset_id]
                    result.update(
                        {
                            "inserted_rows": int(inserted),
                            "transform_status": "completed",
                            "transform_notes": (
                                "Stage 6 grouped candidate bootstrap created the governed "
                                "election, contests, snapshots and candidacies atomically."
                            ),
                        }
                    )
                metadata["events"].append(
                    {
                        "at": utc_now(),
                        "type": "election_bootstrap_completed",
                        "message": (
                            f"Registered event {report['official_event_id']} in the isolated "
                            f"working copy: {report['total_contests']:,} contests and "
                            f"{report['total_candidates']:,} candidacies."
                        ),
                    }
                )

            self.store.mutate(job_id, save_bootstrap)
            job = self.store.read(job_id)

        execution_issues = {
            issue["issue_id"]: issue
            for issue in job["execution"].get("pending_mapping_issues", [])
        }
        existing_resolved = [
            issue for issue in job["mapping_issues"] if issue["status"] == "resolved"
        ]
        staged_rows = sum(
            item["staged_rows"] for item in job["execution"]["dataset_results"].values()
        )
        source_rows = sum(
            item.get("source_rows", item["staged_rows"])
            for item in job["execution"]["dataset_results"].values()
        )
        inserted_rows = sum(
            item["inserted_rows"] for item in job["execution"]["dataset_results"].values()
        )
        quarantined_rows = sum(
            item["quarantined_rows"]
            for item in job["execution"]["dataset_results"].values()
        )
        all_transformed = all(
            item["transform_status"] == "completed"
            for item in job["execution"]["dataset_results"].values()
        )
        connection = self._connect(work_database)
        try:
            connection.execute(
                """UPDATE provenance.import_run
                   SET completed_at=?, import_status=?, source_row_count=?, staged_row_count=?,
                       inserted_row_count=?, rejected_row_count=?, notes=?
                   WHERE import_run_id=?""",
                [
                    datetime.now(timezone.utc),
                    "awaiting_mapping"
                    if execution_issues
                    else ("staged" if not all_transformed else "transformed"),
                    source_rows,
                    staged_rows,
                    inserted_rows,
                    quarantined_rows,
                    (
                        "Stage 6 AEC new-election bootstrap completed on an isolated working copy."
                        if job.get("mode") == AEC_ELECTION_BOOTSTRAP
                        else "Governed upload execution; canonical facts require registered transformers."
                    ),
                    import_run_id,
                ],
            )
            connection.execute("CHECKPOINT")
        finally:
            connection.close()

        def finish(metadata: dict) -> None:
            metadata["mapping_issues"] = existing_resolved + list(execution_issues.values())
            metadata["execution"]["canonical_complete"] = all_transformed and not execution_issues
            metadata["execution"]["completed_at"] = utc_now()
            metadata["execution"]["requires_restage"] = bool(execution_issues)
            metadata["validation"] = None
            metadata["state"] = "mapping_review" if execution_issues else "staged"
            metadata["events"].append(
                {
                    "at": utc_now(),
                    "type": "execution_completed",
                    "message": (
                        f"Read {source_rows:,} source rows, staged {staged_rows:,} governed records "
                        f"and inserted {inserted_rows:,} canonical rows; "
                        f"{len(execution_issues)} mapping issue(s) remain."
                    ),
                }
            )

        return self.store.mutate(job_id, finish)

    def _recover_dataset_checkpoint(
        self,
        work_database: Path,
        job: dict,
        dataset: dict,
        import_run_id: str,
    ) -> tuple[dict, dict[str, dict]] | None:
        """Recover a DB commit that preceded its JSON job checkpoint."""

        transform_id = _uuid("transform", import_run_id, dataset["dataset_id"])
        connection = self._connect(work_database, read_only=True)
        try:
            row = connection.execute(
                """SELECT completed_at, input_row_count, output_row_count, transform_status
                   FROM provenance.transform_run WHERE transform_run_id=?""",
                [transform_id],
            ).fetchone()
            if row is None or row[0] is None or row[3] == "running":
                return None
            selection = dataset["detection"]["selection"]
            upload = next(
                item for item in job["uploads"] if item["upload_id"] == dataset["upload_id"]
            )
            source_revision_id = self._source_revision_id(job, upload)
            prefix = dataset["virtual_name"]
            if dataset.get("sheet"):
                prefix += f"!{dataset['sheet']}"
            prefix += "#row="
            staged_count = int(row[1] or 0)
            if selection["dataset_key"] in BULK_EXTERNAL_DATASET_KEYS:
                staged_count = connection.execute(
                    """SELECT count(*) FROM staging.source_record
                       WHERE import_run_id=? AND source_revision_id=? AND dataset_key=?
                         AND source_locator=?""",
                    [
                        import_run_id,
                        source_revision_id,
                        selection["dataset_key"],
                        f"{dataset['virtual_name']}#bulk-external",
                    ],
                ).fetchone()[0]
            quarantined = connection.execute(
                """SELECT count(*) FROM staging.source_record
                   WHERE import_run_id=? AND source_revision_id=? AND dataset_key=?
                     AND starts_with(source_locator, ?) AND mapping_status='quarantined'""",
                [import_run_id, source_revision_id, selection["dataset_key"], prefix],
            ).fetchone()[0]
            issues: dict[str, dict] = {}
            if row[3] == "awaiting_mapping":
                matcher = ReferenceMatcher(connection, job.get("authority_id"))
                container = (
                    self.store.job_dir(job["job_id"]) / "uploads" / upload["stored_name"]
                )
                for _, source_row in iter_dataset_rows(container, dataset):
                    _, unresolved = map_row(
                        source_row,
                        matcher,
                        job.get("mapping_resolutions", {}),
                        self._mapping_entity_types(dataset),
                    )
                    for observation in unresolved:
                        merge_issue(issues, observation, dataset["dataset_id"])
            result = {
                "staged_rows": int(staged_count),
                "source_rows": int(row[1] or 0),
                "quarantined_rows": int(quarantined),
                "inserted_rows": int(row[2] or 0),
                "rejected_rows": int(quarantined),
                "transform_status": row[3],
                "transform_notes": "Recovered from a committed DuckDB transform checkpoint.",
                "completed_at": row[0].isoformat(),
                "recovered_from_database": True,
            }
            return result, issues
        finally:
            connection.close()

    def _register_run_and_sources(
        self, connection: duckdb.DuckDBPyConnection, job: dict, import_run_id: str
    ) -> dict[str, dict]:
        selections = [dataset["detection"]["selection"] for dataset in job["datasets"]]
        adapter_ids = sorted({selection["adapter_id"] for selection in selections})
        adapter_versions = sorted({selection["adapter_version"] for selection in selections})
        adapter_id = adapter_ids[0] if len(adapter_ids) == 1 else "stage3_multi_adapter"
        adapter_version = adapter_versions[0] if len(adapter_versions) == 1 else APP_VERSION
        configuration = job.get("configuration", {})
        publication_phase = configuration.get("publication_phase") or "final"
        source_url = configuration.get("source_url")
        operator_note = configuration.get("operator_note")
        landing_page_id = _uuid("source_landing_page", job["job_id"], source_url) if source_url else None
        provenance_note = (
            f"Publication phase: {publication_phase}."
            + (f" Operator note: {operator_note}" if operator_note else "")
        )
        identities: dict[str, dict] = {}
        connection.execute("BEGIN TRANSACTION")
        try:
            if source_url:
                connection.execute(
                    """INSERT OR IGNORE INTO provenance.source_landing_page VALUES
                       (?, ?, ?, ?, ?, NULL, ?, ?, 'available', ?)""",
                    [
                        landing_page_id,
                        job.get("authority_id") or "authority_unknown",
                        job.get("election_id"),
                        f"Operator-supplied landing page for {job['name']}",
                        source_url,
                        datetime.now(timezone.utc),
                        datetime.now(timezone.utc),
                        provenance_note,
                    ],
                )
            connection.execute(
                """INSERT OR IGNORE INTO provenance.import_run VALUES
                   (?, ?, ?, ?, ?, NULL, 'running', ?, NULL, NULL, NULL, 0, NULL, ?)""",
                [
                    import_run_id,
                    job.get("election_id"),
                    adapter_id,
                    adapter_version,
                    datetime.now(timezone.utc),
                    len(job["uploads"]),
                    (
                        f"Governed upload job {job['job_id']}; state is external to the "
                        f"governed database. {provenance_note}"
                    ),
                ],
            )
            for upload in job["uploads"]:
                datasets = [
                    item for item in job["datasets"] if item["upload_id"] == upload["upload_id"]
                ]
                existing_revision = connection.execute(
                    """SELECT revision.source_file_id, revision.source_revision_id,
                              revision.revision_number, revision.supersedes_source_revision_id
                       FROM provenance.source_file_revision revision
                       JOIN provenance.source_file source
                         ON source.source_file_id=revision.source_file_id
                       WHERE source.authority_id=?
                         AND source.election_id IS NOT DISTINCT FROM ?
                         AND lower(revision.original_filename)=lower(?)
                         AND lower(revision.sha256)=lower(?)
                       ORDER BY revision.revision_number DESC LIMIT 1""",
                    [
                        job.get("authority_id") or "authority_unknown",
                        job.get("election_id"),
                        upload["original_name"],
                        upload["sha256"],
                    ],
                ).fetchone()
                if existing_revision:
                    source_file_id, revision_id, revision_number, supersedes = existing_revision
                else:
                    previous = connection.execute(
                        """SELECT revision.source_file_id, revision.source_revision_id,
                                  revision.revision_number
                           FROM provenance.source_file_revision revision
                           JOIN provenance.source_file source
                             ON source.source_file_id=revision.source_file_id
                           WHERE source.authority_id=?
                             AND source.election_id IS NOT DISTINCT FROM ?
                             AND lower(revision.original_filename)=lower(?)
                           ORDER BY revision.revision_number DESC LIMIT 1""",
                        [
                            job.get("authority_id") or "authority_unknown",
                            job.get("election_id"),
                            upload["original_name"],
                        ],
                    ).fetchone()
                    if previous:
                        source_file_id, supersedes, previous_number = previous
                        revision_number = int(previous_number) + 1
                    else:
                        source_file_id = self._logical_source_file_id(job, upload)
                        supersedes = None
                        revision_number = 1
                    revision_id = governed_source_revision_id(
                        source_file_id, upload["sha256"]
                    )
                primary = datasets[0]["detection"]["selection"] if datasets else {}
                connection.execute(
                    """INSERT OR IGNORE INTO provenance.source_file VALUES
                       (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
                    [
                        source_file_id,
                        job.get("authority_id") or "authority_unknown",
                        job.get("election_id"),
                        landing_page_id,
                        upload["original_name"],
                        primary.get("dataset_family") or primary.get("dataset_key") or "operator_upload",
                        primary.get("chamber_code"),
                        primary.get("geographic_scope"),
                        f"Immutable upload for governed job {job['job_id']}. {provenance_note}",
                    ],
                )
                connection.execute(
                    """INSERT OR IGNORE INTO provenance.source_file_revision VALUES
                       (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 'active')""",
                    [
                        revision_id,
                        source_file_id,
                        revision_number,
                        source_url,
                        upload["original_name"],
                        f"data/app/jobs/{job['job_id']}/uploads/{upload['stored_name']}",
                        upload.get("content_type") or mimetypes.guess_type(upload["original_name"])[0],
                        "zip" if Path(upload["original_name"]).suffix.lower() == ".zip" else None,
                        upload["size_bytes"],
                        sum(item["row_count"] or 0 for item in datasets) or None,
                        upload["sha256"],
                        datetime.now(timezone.utc),
                        publication_phase,
                        hashlib.sha256(
                            "".join(item["schema_signature_sha256"] for item in datasets).encode()
                        ).hexdigest(),
                        supersedes,
                    ],
                )
                if supersedes and revision_id != supersedes:
                    connection.execute(
                        """UPDATE provenance.source_file_revision
                           SET record_status='superseded'
                           WHERE source_revision_id=? AND record_status='active'""",
                        [supersedes],
                    )
                identities[upload["upload_id"]] = {
                    "source_file_id": source_file_id,
                    "source_revision_id": revision_id,
                    "revision_number": int(revision_number),
                    "supersedes_source_revision_id": supersedes,
                }
                for dataset in datasets:
                    connection.execute(
                        """INSERT OR IGNORE INTO provenance.import_run_input VALUES (?, ?, ?, ?)""",
                        [
                            _uuid("import_input", import_run_id, dataset["dataset_id"]),
                            import_run_id,
                            revision_id,
                            dataset["dataset_id"],
                        ],
                    )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return identities

    @staticmethod
    def _logical_source_file_id(job: dict, upload: dict) -> str:
        logical_key = "\x1f".join(
            [
                job.get("authority_id") or "authority_unknown",
                job.get("election_id") or "unassigned_election",
                Path(upload["original_name"]).name.casefold(),
            ]
        )
        digest = hashlib.sha256(logical_key.encode("utf-8")).hexdigest()
        return f"source_file_operator_{digest[:24]}"

    @staticmethod
    def _source_revision_id(job: dict, upload: dict) -> str:
        identity = (
            job.get("execution", {})
            .get("source_identities", {})
            .get(upload["upload_id"])
        )
        if identity:
            return identity["source_revision_id"]
        # Backwards compatibility for resumable Stage 3 jobs created before
        # logical source identities were persisted.
        return f"source_revision_stage3_{upload['sha256'][:32]}"

    def _stage_dataset(
        self,
        work_database: Path,
        job: dict,
        dataset: dict,
        import_run_id: str,
    ) -> tuple[dict, dict[str, dict]]:
        upload = next(item for item in job["uploads"] if item["upload_id"] == dataset["upload_id"])
        container = self.store.job_dir(job["job_id"]) / "uploads" / upload["stored_name"]
        source_revision_id = self._source_revision_id(job, upload)
        selection = dataset["detection"]["selection"]
        registered = get_transformer(selection["adapter_id"], selection["dataset_key"])
        transform_id = _uuid("transform", import_run_id, dataset["dataset_id"])
        staged_rows = 0
        quarantined_rows = 0
        issues: dict[str, dict] = {}
        connection = self._connect(work_database)
        try:
            matcher = ReferenceMatcher(connection, job.get("authority_id"))
            connection.execute("BEGIN TRANSACTION")
            connection.execute(
                """INSERT INTO provenance.transform_run VALUES
                   (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 'running')""",
                [
                    transform_id,
                    import_run_id,
                    f"{selection['adapter_id']}/{selection['dataset_key']}",
                    registered[0] if registered else APP_VERSION,
                    datetime.now(timezone.utc),
                ],
            )
            batch: list[list] = []
            bulk_external = selection["dataset_key"] in BULK_EXTERNAL_DATASET_KEYS
            if bulk_external:
                source_locator = f"{dataset['virtual_name']}#bulk-external"
                native = {
                    "container": upload["original_name"],
                    "member": dataset.get("member"),
                    "format": dataset.get("format"),
                    "headers": dataset.get("headers"),
                    "schema_signature_sha256": dataset.get("schema_signature_sha256"),
                    "storage": "external_parquet_transform",
                }
                native_json = json.dumps(native, ensure_ascii=False, separators=(",", ":"))
                row_hash = hashlib.sha256(native_json.encode("utf-8")).hexdigest()
                batch.append(
                    [
                        _uuid("staging", import_run_id, dataset["dataset_id"], "bulk-external"),
                        import_run_id,
                        source_revision_id,
                        selection["dataset_key"],
                        source_locator,
                        None,
                        native_json,
                        json.dumps({"source": native, "canonical": {}}, separators=(",", ":")),
                        "mapped",
                        row_hash,
                    ]
                )
                staged_rows += 1
            else:
                for source_row_number, row in iter_dataset_rows(container, dataset):
                    quarantine_only = selection["adapter_id"] == "stage3_quarantine"
                    if quarantine_only:
                        mapped, unresolved = {"source": row, "canonical": {}}, []
                    else:
                        mapped, unresolved = map_row(
                            row,
                            matcher,
                            job["mapping_resolutions"],
                            self._mapping_entity_types(dataset),
                        )
                    for observation in unresolved:
                        merge_issue(issues, observation, dataset["dataset_id"])
                    mapping_status = "quarantined" if unresolved or quarantine_only else "mapped"
                    if unresolved or quarantine_only:
                        quarantined_rows += 1
                    source_locator = self._source_locator(dataset, source_row_number)
                    native_json = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                    mapped_json = json.dumps(mapped, ensure_ascii=False, separators=(",", ":"))
                    row_hash = hashlib.sha256(native_json.encode("utf-8")).hexdigest()
                    batch.append(
                        [
                            _uuid("staging", import_run_id, dataset["dataset_id"], source_row_number),
                            import_run_id,
                            source_revision_id,
                            selection["dataset_key"],
                            source_locator,
                            source_row_number,
                            native_json,
                            mapped_json,
                            mapping_status,
                            row_hash,
                        ]
                    )
                    staged_rows += 1
                    if len(batch) >= self.settings.stage_batch_size:
                        self._insert_staging_batch(connection, batch)
                        batch.clear()
            if batch:
                self._insert_staging_batch(connection, batch)

            inserted_rows = 0
            rejected_rows = quarantined_rows
            transform_status = "awaiting_transform"
            transform_input_rows = staged_rows
            notes = "Source-native rows staged; no registered canonical transformer is installed."
            if registered and not issues:
                version, transformer = registered
                transform_result = transformer(
                    TransformContext(
                        connection=connection,
                        job=job,
                        dataset=dataset,
                        import_run_id=import_run_id,
                        source_revision_id=source_revision_id,
                        transform_run_id=transform_id,
                        source_container=container,
                        work_root=self.store.job_dir(job["job_id"]) / "work",
                        base_artifact_root=self._resolve_portable_path(
                            (job.get("execution", {}).get("base_release") or {}).get(
                                "artifact_root", "."
                            )
                        ),
                    )
                )
                inserted_rows = transform_result.inserted_rows
                rejected_rows += transform_result.rejected_rows
                transform_input_rows = transform_result.source_rows or staged_rows
                transform_status = "completed"
                notes = transform_result.notes
            elif issues:
                transform_status = "awaiting_mapping"
                notes = f"{len(issues)} unresolved canonical mapping(s) quarantine source rows."
            elif selection["adapter_id"] == "stage3_quarantine":
                notes = (
                    "Unknown schema preserved in source-native quarantine; register and test an "
                    "adapter before canonical transformation."
                )
            connection.execute(
                """UPDATE provenance.transform_run
                   SET completed_at=?, input_row_count=?, output_row_count=?, output_hash=?, transform_status=?
                   WHERE transform_run_id=?""",
                [
                    datetime.now(timezone.utc),
                    transform_input_rows,
                    inserted_rows,
                    hashlib.sha256(
                        f"{dataset['schema_signature_sha256']}:{staged_rows}:{inserted_rows}".encode()
                    ).hexdigest(),
                    transform_status,
                    transform_id,
                ],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        return (
            {
                "staged_rows": staged_rows,
                "source_rows": transform_input_rows,
                "quarantined_rows": quarantined_rows,
                "inserted_rows": inserted_rows,
                "rejected_rows": rejected_rows,
                "transform_status": transform_status,
                "transform_notes": notes,
                "completed_at": utc_now(),
            },
            issues,
        )

    @staticmethod
    def _insert_staging_batch(connection: duckdb.DuckDBPyConnection, batch: list[list]) -> None:
        connection.executemany(
            """INSERT INTO staging.source_record
               (staging_record_id, import_run_id, source_revision_id, dataset_key,
                source_locator, source_row_number, source_native_json, mapped_json,
                mapping_status, source_row_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            batch,
        )

    @staticmethod
    def _source_locator(dataset: dict, row_number: int) -> str:
        locator = dataset["virtual_name"]
        if dataset.get("sheet"):
            locator += f"!{dataset['sheet']}"
        return f"{locator}#row={row_number}"

    def _execute_builtin_2025(self, job_id: str) -> dict:
        from ..import_2025 import import_2025, resume_2025

        work_database = self._working_database(job_id)
        local_project = self._prepare_builtin_project(job_id)
        resume = False
        if work_database.is_file():
            try:
                connection = self._connect(work_database, read_only=True)
                try:
                    resume = (
                        connection.execute(
                            """SELECT count(*) FROM provenance.import_run
                               WHERE adapter_id='adapter_aec_2025_v1'
                                 AND import_status IN ('running', 'failed')"""
                        ).fetchone()[0]
                        > 0
                    )
                finally:
                    connection.close()
            except duckdb.Error:
                resume = False
        report = (
            resume_2025(work_database, local_project)
            if resume
            else import_2025(work_database, local_project, rebuild=True)
        )
        reference_overlay = self._preserve_governed_reference_snapshot(
            job_id, work_database
        )
        report["reference_overlay"] = reference_overlay

        def finish(metadata: dict) -> None:
            metadata["execution"].update(
                {
                    "import_run_id": report["import_run_id"],
                    "canonical_complete": report["status"] == "PASS",
                    "completed_at": report["completed_at"],
                    "builtin_report": report,
                }
            )
            metadata["state"] = "staged" if report["status"] == "PASS" else "failed"
            metadata["events"].append(
                {
                    "at": utc_now(),
                    "type": "canonical_pipeline_completed",
                    "message": (
                        "AEC 2025 canonical reproduction completed on the isolated working database "
                        f"with status {report['status']}."
                    ),
                }
            )

        return self.store.mutate(job_id, finish)

    def _preserve_governed_reference_snapshot(
        self, job_id: str, work_database: Path
    ) -> dict[str, int]:
        """Restore the job's pinned People, Parties and Constituencies after rebuild."""

        job = self.store.read(job_id)
        base_release = job.get("execution", {}).get("base_release") or {}
        source_value = base_release.get("database_path")
        if not source_value:
            raise RuntimeError("The reproduction job has no pinned governed database")
        source = self._resolve_portable_path(source_value)
        if not source.is_file() or _sha256_file(source) != base_release.get("database_sha256"):
            raise RuntimeError(
                "The governed database pinned for reference preservation changed during reproduction"
            )
        escaped_source = str(source.resolve()).replace("'", "''")
        connection = self._connect(work_database)
        attached = False
        try:
            connection.execute(
                f"ATTACH '{escaped_source}' AS governed_reference (READ_ONLY)"
            )
            attached = True
            for table in ("person", "party", "constituency"):
                target_columns = [
                    row[0]
                    for row in connection.execute(
                        f"DESCRIBE SELECT * FROM sync.{table}"
                    ).fetchall()
                ]
                source_columns = [
                    row[0]
                    for row in connection.execute(
                        f"DESCRIBE SELECT * FROM governed_reference.sync.{table}"
                    ).fetchall()
                ]
                if source_columns != target_columns:
                    raise RuntimeError(
                        f"Pinned sync.{table} schema does not match the rebuilt database"
                    )
            connection.execute("BEGIN TRANSACTION")
            for table in ("person", "party", "constituency"):
                connection.execute(f"DELETE FROM sync.{table}")
                connection.execute(
                    f"INSERT INTO sync.{table} SELECT * FROM governed_reference.sync.{table}"
                )
            connection.execute("COMMIT")
            counts = {
                "people": connection.execute(
                    "SELECT count(*) FROM sync.person"
                ).fetchone()[0],
                "parties": connection.execute(
                    "SELECT count(*) FROM sync.party"
                ).fetchone()[0],
                "constituencies": connection.execute(
                    "SELECT count(*) FROM sync.constituency"
                ).fetchone()[0],
            }
            expected = {
                "people": connection.execute(
                    "SELECT count(*) FROM governed_reference.sync.person"
                ).fetchone()[0],
                "parties": connection.execute(
                    "SELECT count(*) FROM governed_reference.sync.party"
                ).fetchone()[0],
                "constituencies": connection.execute(
                    "SELECT count(*) FROM governed_reference.sync.constituency"
                ).fetchone()[0],
            }
            if counts != expected:
                raise RuntimeError(
                    f"Reference preservation count mismatch: observed {counts}, expected {expected}"
                )
            connection.execute("CHECKPOINT")
            return counts
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except duckdb.Error:
                pass
            raise
        finally:
            if attached:
                try:
                    connection.execute("DETACH governed_reference")
                except duckdb.Error:
                    pass
            connection.close()

    def _prepare_builtin_project(self, job_id: str) -> Path:
        """Create a job-local pipeline tree so reproduction cannot alter Stage 2 files."""

        job_directory = self.store.job_dir(job_id)
        destination = job_directory / "work"
        destination.mkdir(parents=True, exist_ok=True)
        marker = destination / ".builtin_inputs_ready"
        relatives = (
            "config",
            "schema",
            "data/raw",
            "data/manifests",
            "data/snapshots",
        )

        def hardlink_or_copy(source: str, target: str) -> str:
            try:
                os.link(source, target)
                return target
            except OSError:
                return shutil.copy2(source, target)

        def remove_local_path(path: Path) -> None:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

        with FileLock(str(job_directory / ".builtin-inputs.lock")):
            if marker.is_file() and all(
                (destination / relative).is_dir() for relative in relatives
            ):
                return destination
            marker.unlink(missing_ok=True)

            for relative in relatives:
                source = self.settings.project_root / relative
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                for stale in target.parent.glob(f".{target.name}.copying-*"):
                    remove_local_path(stale)
                temporary = target.parent / f".{target.name}.copying-{uuid.uuid4().hex}"
                # Raw source files are immutable inputs and may be hard-linked to
                # avoid a large duplicate. Every other tree is copied because the
                # existing importer legitimately rewrites manifests/catalogues.
                copy_function = hardlink_or_copy if relative == "data/raw" else shutil.copy2
                try:
                    shutil.copytree(source, temporary, copy_function=copy_function)
                    remove_local_path(target)
                    os.replace(temporary, target)
                finally:
                    remove_local_path(temporary)

            (destination / "data/parquet").mkdir(parents=True, exist_ok=True)
            (destination / "data/database").mkdir(parents=True, exist_ok=True)
            (destination / "docs").mkdir(parents=True, exist_ok=True)
            (destination / "dist").mkdir(parents=True, exist_ok=True)
            descriptor, marker_name = tempfile.mkstemp(
                prefix=".builtin_inputs_ready-", suffix=".tmp", dir=destination
            )
            temporary_marker = Path(marker_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(
                        "Job-local inputs prepared atomically; generated catalogues, "
                        "Parquet and reports remain here.\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_marker, marker)
            finally:
                temporary_marker.unlink(missing_ok=True)
        return destination

    def _fail_job(self, job_id: str, event_type: str, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"

        def fail(metadata: dict) -> None:
            metadata["state"] = "failed"
            metadata["last_error"] = message
            metadata["events"].append(
                {"at": utc_now(), "type": event_type, "message": message}
            )

        try:
            self.store.mutate(job_id, fail)
        except JobNotFoundError:
            pass

    def canonical_options(
        self, entity_type: str, query: str | None = None, limit: int = 100
    ) -> list[dict]:
        definitions = {
            "person": (
                "sync.person",
                "person_id",
                "coalesce(display_name, full_name)",
                "full_name",
            ),
            "party": (
                "sync.party",
                "party_id",
                "party_name",
                "coalesce(abbreviation, short_name, '')",
            ),
            "constituency": (
                "sync.constituency",
                "constituency_id",
                "constituency_name",
                "coalesce(official_constituency_code, '')",
            ),
        }
        if entity_type not in definitions:
            raise ValueError("entity_type must be person, party or constituency")
        table, key, label, secondary = definitions[entity_type]
        limit = min(max(limit, 1), 500)
        sql = f"SELECT {key}, {label}, {secondary} FROM {table}"
        parameters: list[object] = []
        if query:
            sql += f" WHERE lower({label}) LIKE ? OR lower({key}) LIKE ? OR lower({secondary}) LIKE ?"
            pattern = f"%{query.casefold()}%"
            parameters.extend([pattern, pattern, pattern])
        sql += f" ORDER BY {label} LIMIT ?"
        parameters.append(limit)
        connection = self._connect(self.governed_database(), read_only=True)
        try:
            return [
                {"canonical_id": row[0], "label": row[1], "secondary_label": row[2]}
                for row in connection.execute(sql, parameters).fetchall()
            ]
        finally:
            connection.close()

    def validate_job(self, job_id: str, requested_by: str = "Local operator") -> dict:
        with self._job_operation_lock(job_id):
            return self._validate_job(job_id, requested_by)

    def _validate_job(self, job_id: str, requested_by: str = "Local operator") -> dict:
        job = self.store.read(job_id)
        if job["state"] in {"queued", "executing", "publishing", "cancelled"}:
            raise InvalidJobStateError(
                f"Validation is locked while the job is {job['state']}."
            )
        work_database = self._working_database(job_id)
        checks: list[dict] = []

        def check(
            rule_id: str,
            passed: bool,
            message: str,
            *,
            observed: object = None,
            expected: object = None,
            severity: str = "blocker",
        ) -> None:
            checks.append(
                {
                    "rule_id": rule_id,
                    "status": "passed" if passed else "failed",
                    "severity": severity,
                    "message": message,
                    "observed": observed,
                    "expected": expected,
                }
            )

        check(
            "stage3_working_database",
            work_database.is_file(),
            "An isolated working database exists.",
            observed=str(work_database) if work_database.is_file() else None,
            expected="checksum-verified working database",
        )
        unresolved = [issue for issue in job["mapping_issues"] if issue["status"] == "unresolved"]
        check(
            "stage3_mapping_resolution",
            not unresolved,
            "All discovered canonical entity mappings are resolved.",
            observed=len(unresolved),
            expected=0,
        )
        if job["mode"] == AEC_ELECTION_BOOTSTRAP:
            preview = job.get("bootstrap_preview") or {}
            report = job["execution"].get("bootstrap_report") or {}
            check(
                "stage6_reviewed_preview",
                preview.get("status") == "PASS",
                "The candidate files and election metadata passed a read-only preview before execution.",
                observed=preview.get("status"),
                expected="PASS",
            )
            check(
                "stage6_bootstrap_pipeline",
                report.get("status") == "PASS"
                and job["execution"].get("canonical_complete") is True,
                "The grouped AEC election bootstrap completed atomically.",
                observed=report.get("status"),
                expected="PASS",
            )
            check(
                "stage6_bootstrap_counts",
                bool(report)
                and report.get("total_contests") == preview.get("total_contests")
                and report.get("total_candidates") == preview.get("total_candidates"),
                "Created contest and candidacy counts reconcile exactly to the reviewed preview.",
                observed={
                    "contests": report.get("total_contests"),
                    "candidates": report.get("total_candidates"),
                },
                expected={
                    "contests": preview.get("total_contests"),
                    "candidates": preview.get("total_candidates"),
                },
            )
            check(
                "stage6_reference_immutability",
                bool(report)
                and report.get("reference_snapshot_before")
                == report.get("reference_snapshot_after")
                == preview.get("reference_snapshot"),
                "People, Parties and Constituencies remained an unchanged read-only snapshot.",
                observed=report.get("reference_snapshot_after"),
                expected=preview.get("reference_snapshot"),
            )
            check(
                "stage6_source_lineage",
                int(report.get("lineage_rows") or 0)
                >= int(report.get("total_candidates") or 0),
                "Every candidacy and its governing election structure has immutable source lineage.",
                observed=report.get("lineage_rows"),
                expected=f">= {report.get('total_candidates', 0)}",
            )
        elif job["mode"] == "uploaded_files":
            unselected = [
                dataset["dataset_id"]
                for dataset in job["datasets"]
                if dataset["detection"].get("selection") is None
            ]
            check(
                "stage3_adapter_selection",
                not unselected,
                "Every input dataset has an explicit registered adapter selection.",
                observed=unselected,
                expected=[],
            )
            results = job["execution"].get("dataset_results", {})
            incomplete = [
                dataset["dataset_id"]
                for dataset in job["datasets"]
                if dataset["dataset_id"] not in results
            ]
            check(
                "stage3_execution_completion",
                not incomplete,
                "Every selected dataset reached a durable execution checkpoint.",
                observed=incomplete,
                expected=[],
            )
            for dataset in job["datasets"]:
                result = results.get(dataset["dataset_id"])
                status = result.get("transform_status") if result else None
                check(
                    "stage3_transform_completion",
                    status == "completed",
                    (
                        f"Dataset {dataset['dataset_id']} completed its registered canonical transformer."
                        if status == "completed"
                        else (
                            f"Dataset {dataset['dataset_id']} is staged only; its selected format has "
                            "no installed canonical transformer."
                        )
                    ),
                    observed=status,
                    expected="completed",
                )
            stage7_datasets = [
                dataset
                for dataset in job["datasets"]
                if (
                    (dataset.get("detection", {}).get("selection") or {}).get("dataset_key")
                    in STAGE7_DATASET_KEYS
                )
            ]
            if stage7_datasets:
                stage7_failures = []
                for dataset in stage7_datasets:
                    result = results.get(dataset["dataset_id"]) or {}
                    if (
                        result.get("transform_status") != "completed"
                        or int(result.get("inserted_rows") or 0) <= 0
                        or int(result.get("rejected_rows") or 0) != 0
                    ):
                        stage7_failures.append(dataset["dataset_id"])
                check(
                    "stage7_senate_canonical_group",
                    not stage7_failures,
                    "Every selected Stage 7 Senate source completed a non-empty, zero-rejection canonical transformation.",
                    observed=stage7_failures,
                    expected=[],
                )
            stage8_datasets = [
                dataset
                for dataset in job["datasets"]
                if (
                    (dataset.get("detection", {}).get("selection") or {}).get("dataset_key")
                    in STAGE8_DATASET_KEYS
                )
            ]
            if stage8_datasets:
                stage8_failures = []
                for dataset in stage8_datasets:
                    result = results.get(dataset["dataset_id"]) or {}
                    if (
                        result.get("transform_status") != "completed"
                        or int(result.get("inserted_rows") or 0) <= 0
                        or int(result.get("rejected_rows") or 0) != 0
                    ):
                        stage8_failures.append(dataset["dataset_id"])
                check(
                    "stage8_senate_remaining_canonical_group",
                    not stage8_failures,
                    "Every selected Stage 8 Senate source completed a non-empty, zero-rejection canonical transformation.",
                    observed=stage8_failures,
                    expected=[],
                )
                uploads = {
                    upload["upload_id"]: upload for upload in job.get("uploads", [])
                }
                datasets_by_upload: dict[str, list[dict]] = {}
                for dataset in job["datasets"]:
                    datasets_by_upload.setdefault(dataset["upload_id"], []).append(dataset)
                dop_by_upload: dict[str, list[dict]] = {}
                formal_by_upload: dict[str, list[dict]] = {}
                for dataset in stage8_datasets:
                    key = dataset["detection"]["selection"]["dataset_key"]
                    if key == "senate_distribution":
                        dop_by_upload.setdefault(dataset["upload_id"], []).append(dataset)
                    elif key == "senate_formal_preferences":
                        formal_by_upload.setdefault(dataset["upload_id"], []).append(dataset)

                incomplete_dop_archives: list[str] = []
                for upload_id, members in dop_by_upload.items():
                    upload = uploads[upload_id]
                    outer = re.fullmatch(
                        r"SenateDopDownload-(?P<event>\d+)\.zip",
                        Path(upload["original_name"]).name,
                        flags=re.IGNORECASE,
                    )
                    inner = [
                        re.fullmatch(
                            r"SenateStateDOPDownload-(?P<event>\d+)-(?P<state>ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\.csv",
                            Path(dataset["virtual_name"]).name,
                            flags=re.IGNORECASE,
                        )
                        for dataset in members
                    ]
                    states = {
                        match.group("state").upper() for match in inner if match
                    }
                    if (
                        outer is None
                        or len(members) != 8
                        or len(datasets_by_upload.get(upload_id, [])) != 8
                        or any(match is None for match in inner)
                        or states
                        != {"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"}
                        or any(
                            match.group("event") != outer.group("event")
                            for match in inner
                            if match
                        )
                    ):
                        incomplete_dop_archives.append(upload["original_name"])
                check(
                    "stage8_complete_dop_archive",
                    not incomplete_dop_archives,
                    "Every Senate DOP source is the official outer ZIP with exactly eight matching state and territory members.",
                    observed=incomplete_dop_archives,
                    expected=[],
                )
                invalid_formal_archives: list[str] = []
                for upload_id, members in formal_by_upload.items():
                    upload = uploads[upload_id]
                    outer = re.fullmatch(
                        r"aec-senate-formalpreferences-(?P<event>\d+)-(?P<state>ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\.zip",
                        Path(upload["original_name"]).name,
                        flags=re.IGNORECASE,
                    )
                    inner = (
                        re.fullmatch(
                            r"aec-senate-formalpreferences-(?P<event>\d+)-(?P<state>ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\.csv",
                            Path(members[0]["virtual_name"]).name,
                            flags=re.IGNORECASE,
                        )
                        if len(members) == 1
                        else None
                    )
                    if (
                        outer is None
                        or inner is None
                        or len(datasets_by_upload.get(upload_id, [])) != 1
                        or outer.group("event") != inner.group("event")
                        or outer.group("state").upper() != inner.group("state").upper()
                    ):
                        invalid_formal_archives.append(upload["original_name"])
                check(
                    "stage8_formal_archive_identity",
                    not invalid_formal_archives,
                    "Every formal-preference source is one correctly named state ZIP with a matching member.",
                    observed=invalid_formal_archives,
                    expected=[],
                )
                invalid_group_sources: list[str] = []
                for dataset in stage8_datasets:
                    key = dataset["detection"]["selection"]["dataset_key"]
                    if key not in {
                        "senate_group_preferences_national",
                        "senate_group_preferences_state",
                    }:
                        continue
                    upload = uploads[dataset["upload_id"]]
                    if (
                        Path(upload["original_name"]).name.casefold()
                        != Path(dataset["virtual_name"]).name.casefold()
                        or len(datasets_by_upload.get(dataset["upload_id"], [])) != 1
                    ):
                        invalid_group_sources.append(upload["original_name"])
                check(
                    "stage8_group_source_identity",
                    not invalid_group_sources,
                    "Every Senate source-group aggregate is supplied as its complete official CSV.",
                    observed=sorted(set(invalid_group_sources)),
                    expected=[],
                )
                selected_stage8_keys = {
                    dataset["detection"]["selection"]["dataset_key"]
                    for dataset in stage8_datasets
                }
                group_mismatches: list[dict] = []
                group_keys = {
                    "senate_group_preferences_national",
                    "senate_group_preferences_state",
                }
                if selected_stage8_keys & group_keys and work_database.is_file():
                    connection = self._connect(work_database, read_only=True)
                    try:
                        group_presence = connection.execute(
                            """SELECT
                                 count(*) FILTER (WHERE contest_id IS NULL),
                                 count(*) FILTER (WHERE contest_id IS NOT NULL)
                               FROM results.vote_result
                               WHERE election_id=? AND subject_type='source_group'
                                 AND result_type='group_total' AND measure_type='votes'
                                 AND record_status='active'""",
                            [job["election_id"]],
                        ).fetchone()
                        if all(int(value or 0) > 0 for value in group_presence):
                            group_mismatches = [
                                {
                                    "source_group": row[0],
                                    "vote_type": row[1],
                                    "national_votes": row[2],
                                    "state_votes": row[3],
                                }
                                for row in connection.execute(
                                    """WITH national AS (
                                         SELECT question_option_code, vote_type,
                                                sum(integer_value)::BIGINT AS votes
                                         FROM results.vote_result
                                         WHERE election_id=? AND subject_type='source_group'
                                           AND result_type='group_total' AND measure_type='votes'
                                           AND contest_id IS NULL
                                           AND election_reporting_unit_id IS NOT NULL
                                           AND record_status='active'
                                         GROUP BY question_option_code, vote_type
                                       ), states AS (
                                         SELECT question_option_code, vote_type,
                                                sum(integer_value)::BIGINT AS votes
                                         FROM results.vote_result
                                         WHERE election_id=? AND subject_type='source_group'
                                           AND result_type='group_total' AND measure_type='votes'
                                           AND contest_id IS NOT NULL
                                           AND election_reporting_unit_id IS NULL
                                           AND record_status='active'
                                         GROUP BY question_option_code, vote_type
                                       )
                                       SELECT coalesce(national.question_option_code,
                                                       states.question_option_code),
                                              coalesce(national.vote_type, states.vote_type),
                                              national.votes, states.votes
                                       FROM national FULL OUTER JOIN states
                                         USING (question_option_code, vote_type)
                                       WHERE national.votes IS DISTINCT FROM states.votes
                                       ORDER BY 1, 2""",
                                    [job["election_id"], job["election_id"]],
                                ).fetchall()
                            ]
                    finally:
                        connection.close()
                check(
                    "stage8_group_aggregate_reconciliation",
                    not group_mismatches,
                    "When both grains exist, national Senate source-group votes equal the sum of state and territory source-group votes.",
                    observed=group_mismatches,
                    expected=[],
                )
        elif job["mode"] == BUILTIN_AEC_2025:
            report = job["execution"].get("builtin_report") or {}
            check(
                "stage3_builtin_pipeline",
                report.get("status") == "PASS" and job["execution"].get("canonical_complete"),
                "The complete governed AEC 2025 canonical pipeline passed.",
                observed=report.get("status"),
                expected="PASS",
            )
            artifact_validation: dict | None = None
            artifact_error: str | None = None
            if work_database.is_file():
                try:
                    from ..validate import validate_database

                    artifact_validation = validate_database(
                        work_database, self._release_artifact_source(job)
                    )
                except Exception as exc:
                    artifact_error = f"{type(exc).__name__}: {exc}"
            check(
                "stage3_builtin_artifact_coherence",
                bool(artifact_validation and artifact_validation["status"] == "PASS"),
                "The reproduced database resolves its ballot views against the job-local Parquet artifacts.",
                observed=(
                    artifact_validation.get("failures")
                    if artifact_validation
                    else artifact_error
                ),
                expected=[],
            )
            overlay = report.get("reference_overlay") or {}
            check(
                "stage4_reference_baseline_preserved",
                bool(overlay)
                and all(int(overlay.get(key, 0)) > 0 for key in ("people", "parties", "constituencies")),
                "The full reproduction retained the pinned active People, Parties and Constituencies snapshot.",
                observed=overlay,
                expected="the exact pinned governed reference snapshot",
            )
        elif job["mode"] == "reference_sync":
            snapshot = job.get("reference_snapshot") or {}
            source_revision = snapshot.get("source_revision_sha256") or job["execution"].get(
                "source_revision_sha256"
            )
            applied = bool(snapshot) and job["execution"].get("canonical_complete") is True
            check(
                "stage3_reference_sync_review",
                bool(source_revision) and applied,
                "A reviewed Google Sheets revision token was applied to the isolated working copy.",
                observed=source_revision if applied else None,
                expected="reviewed and applied source revision SHA-256",
            )
        else:
            check(
                "stage3_execution_mode",
                False,
                f"Unsupported Stage 3 execution mode: {job['mode']}",
                observed=job["mode"],
                expected=(
                    "uploaded_files, aec_election_bootstrap, reproduce_aec_2025 or "
                    "reference_sync"
                ),
            )

        checksum_failures: list[str] = []
        for upload in job["uploads"]:
            path = self.store.job_dir(job_id) / "uploads" / upload["stored_name"]
            if not path.is_file() or _sha256_file(path) != upload["sha256"]:
                checksum_failures.append(upload["upload_id"])
        check(
            "stage3_source_checksums",
            not checksum_failures,
            "Every immutable upload still matches its recorded SHA-256 checksum.",
            observed=checksum_failures,
            expected=[],
        )

        if work_database.is_file():
            connection = self._connect(work_database, read_only=True)
            try:
                required_tables = {
                    "provenance.import_run",
                    "provenance.source_file_revision",
                    "staging.source_record",
                    "audit.validation_run",
                    "publish.publication_snapshot",
                }
                observed_tables = {
                    f"{row[0]}.{row[1]}"
                    for row in connection.execute(
                        """SELECT table_schema, table_name FROM information_schema.tables
                           WHERE table_type='BASE TABLE'"""
                    ).fetchall()
                }
                check(
                    "stage3_database_structure",
                    required_tables.issubset(observed_tables),
                    "The working database retains all governed pipeline tables.",
                    observed=sorted(required_tables - observed_tables),
                    expected=[],
                )
                baseline = connection.execute(
                    """SELECT count(*) FROM control.database_release
                       WHERE release_id='release_0_2_0_aec_2025' AND release_status='validated'"""
                ).fetchone()[0]
                check(
                    "stage3_validated_baseline",
                    baseline == 1,
                    "The validated Stage 2 baseline remains present in the working database.",
                    observed=baseline,
                    expected=1,
                )
                if job["mode"] in {"uploaded_files", AEC_ELECTION_BOOTSTRAP} and job["execution"].get("import_run_id"):
                    actual_staged = connection.execute(
                        "SELECT count(*) FROM staging.source_record WHERE import_run_id=?",
                        [job["execution"]["import_run_id"]],
                    ).fetchone()[0]
                    expected_staged = sum(
                        result["staged_rows"]
                        for result in job["execution"].get("dataset_results", {}).values()
                    )
                    check(
                        "stage3_staging_reconciliation",
                        actual_staged == expected_staged,
                        "Staged database rows reconcile to durable job checkpoints.",
                        observed=actual_staged,
                        expected=expected_staged,
                    )
            finally:
                connection.close()

        blockers = [
            item for item in checks if item["status"] == "failed" and item["severity"] == "blocker"
        ]
        warnings = [
            item for item in checks if item["status"] == "failed" and item["severity"] == "warning"
        ]
        selected_keys = {
            (dataset.get("detection", {}).get("selection") or {}).get("dataset_key")
            for dataset in job.get("datasets", [])
        }
        ruleset_version = (
            "stage6_v1"
            if job["mode"] == AEC_ELECTION_BOOTSTRAP
            else "stage8_v1"
            if selected_keys & STAGE8_DATASET_KEYS
            else "stage7_v1"
            if selected_keys & STAGE7_DATASET_KEYS
            else "stage3_v1"
        )
        validation_id = _uuid("governed_validation", job_id, utc_now())
        report = {
            "validation_run_id": validation_id,
            "job_id": job_id,
            "status": "PASS" if not blockers else "FAIL",
            "ruleset_version": ruleset_version,
            "requested_by": requested_by,
            "completed_at": utc_now(),
            "rules_executed": len(checks),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "checks": checks,
        }
        if work_database.is_file():
            self._record_validation(work_database, job, report)

        def finish(metadata: dict) -> None:
            metadata["validation"] = report
            if report["status"] == "PASS" and metadata["state"] != "published":
                metadata["state"] = "validated"
            elif any(issue["status"] == "unresolved" for issue in metadata["mapping_issues"]):
                metadata["state"] = "mapping_review"
            elif metadata["execution"].get("completed_at"):
                metadata["state"] = "validation_failed"
            metadata["events"].append(
                {
                    "at": utc_now(),
                    "type": "validation_completed",
                    "message": (
                        f"Validation {report['status']}: {report['blocker_count']} blocker(s), "
                        f"{report['warning_count']} warning(s)."
                    ),
                }
            )

        self.store.mutate(job_id, finish)
        return report

    def _record_validation(self, database: Path, job: dict, report: dict) -> None:
        rule_definitions = {
            "stage3_working_database": "An isolated working database exists.",
            "stage3_mapping_resolution": "Canonical entity mappings are resolved.",
            "stage3_adapter_selection": "Every dataset has an explicit adapter selection.",
            "stage3_execution_completion": "Every dataset has an execution checkpoint.",
            "stage3_transform_completion": "Every dataset completed a canonical transformer.",
            "stage3_builtin_pipeline": "The built-in governed pipeline passed.",
            "stage3_builtin_artifact_coherence": (
                "The reproduced database and its job-local Parquet artifacts validate together."
            ),
            "stage4_reference_baseline_preserved": (
                "The reproduction preserves the pinned active Grand Database references."
            ),
            "stage3_reference_sync_review": "A reviewed reference revision was applied.",
            "stage3_execution_mode": "The job uses a supported execution mode.",
            "stage3_source_checksums": "Immutable source checksums still match.",
            "stage3_database_structure": "Governed pipeline tables remain present.",
            "stage3_validated_baseline": "Validated Stage 2 baseline remains present.",
            "stage3_staging_reconciliation": "Staging rows reconcile to checkpoints.",
            "stage6_reviewed_preview": (
                "New-election metadata and candidate sources passed a read-only preview."
            ),
            "stage6_bootstrap_pipeline": (
                "The grouped AEC election bootstrap completed atomically."
            ),
            "stage6_bootstrap_counts": (
                "Created contests and candidacies reconcile to the reviewed preview."
            ),
            "stage6_reference_immutability": (
                "Grand Database reference snapshots are unchanged by election registration."
            ),
            "stage6_source_lineage": (
                "New election structure and candidacies retain immutable row lineage."
            ),
            "stage7_senate_canonical_group": (
                "Stage 7 Senate datasets complete with non-empty canonical output and zero rejected rows."
            ),
            "stage8_senate_remaining_canonical_group": (
                "Stage 8 Senate distribution, source-group and formal-ballot datasets complete with non-empty canonical output and zero rejected rows."
            ),
            "stage8_complete_dop_archive": (
                "The official Senate DOP ZIP contains exactly eight matching state and territory members."
            ),
            "stage8_formal_archive_identity": (
                "A formal-preference ZIP contains one member with the same event and state identity."
            ),
            "stage8_group_aggregate_reconciliation": (
                "National Senate source-group vote totals reconcile to the state and territory totals."
            ),
            "stage8_group_source_identity": (
                "A Senate source-group aggregate is supplied directly as its official CSV."
            ),
        }
        connection = self._connect(database)
        try:
            connection.execute("BEGIN TRANSACTION")
            for rule_id, description in rule_definitions.items():
                connection.execute(
                    """INSERT OR IGNORE INTO audit.validation_rule VALUES
                       (?, ?, '1.0.0', 'blocker', NULL, NULL, NULL, ?, TRUE)""",
                    [rule_id, rule_id.replace("_", " ").title(), description],
                )
            connection.execute(
                """INSERT INTO audit.validation_run VALUES
                   (?, ?, 'ingestion_job', ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    report["validation_run_id"],
                    job["execution"].get("import_run_id"),
                    job["job_id"],
                    report["ruleset_version"],
                    datetime.fromisoformat(report["completed_at"]),
                    datetime.fromisoformat(report["completed_at"]),
                    report["rules_executed"],
                    report["blocker_count"],
                    report["warning_count"],
                    "passed" if report["status"] == "PASS" else "failed",
                ],
            )
            for index, item in enumerate(report["checks"]):
                if item["status"] == "passed":
                    continue
                connection.execute(
                    """INSERT INTO audit.validation_issue VALUES
                       (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, 'open', NULL, NULL, NULL)""",
                    [
                        _uuid("stage3_validation_issue", report["validation_run_id"], index),
                        report["validation_run_id"],
                        item["rule_id"],
                        item["severity"],
                        item["message"],
                        json.dumps(item["observed"], ensure_ascii=False),
                        json.dumps(item["expected"], ensure_ascii=False),
                    ],
                )
            connection.execute("COMMIT")
            connection.execute("CHECKPOINT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def validation_overview(self) -> dict:
        jobs = self.store.list()
        reports = [job["validation"] for job in jobs if job.get("validation")]
        return {
            "job_count": len(jobs),
            "validated_job_count": sum(job["state"] in {"validated", "published"} for job in jobs),
            "latest": reports[0] if reports else None,
            "reports": reports[:25],
        }

    def current_release_validation(self) -> dict:
        database = self.governed_database()
        connection = self._connect(database, read_only=True)
        try:
            run = connection.execute(
                """SELECT validation_run_id, completed_at, rules_executed, blocker_count,
                          warning_count, validation_status, ruleset_version
                   FROM audit.validation_run ORDER BY completed_at DESC NULLS LAST LIMIT 1"""
            ).fetchone()
            if run is None:
                return {
                    "status": "PENDING",
                    "checks": [],
                    "passed": 0,
                    "warnings": 0,
                    "failed": 0,
                    "can_publish": False,
                }
            issue_rows = connection.execute(
                """SELECT validation_rule_id, severity, issue_message, resolution_status
                   FROM audit.validation_issue WHERE validation_run_id=?""",
                [run[0]],
            ).fetchall()
            issues_by_rule: dict[str, list[tuple]] = {}
            for issue in issue_rows:
                issues_by_rule.setdefault(issue[0], []).append(issue)
            rules = connection.execute(
                """SELECT validation_rule_id, rule_name, severity, description
                   FROM audit.validation_rule WHERE active ORDER BY validation_rule_id"""
            ).fetchall()
            issue_rule_ids = set(issues_by_rule)
            if run[6] == "2025_federal_v1":
                preferred_prefix = "rule_2025_"
            elif run[6] == "stage6_v1":
                preferred_prefix = "stage6_"
            elif run[6] == "stage3_v1":
                preferred_prefix = "stage3_"
            else:
                preferred_prefix = ""
            rules = sorted(
                rules,
                key=lambda rule: (
                    0 if rule[0] in issue_rule_ids else 1,
                    0 if preferred_prefix and rule[0].startswith(preferred_prefix) else 1,
                    rule[0],
                ),
            )[: run[2]]
            checks = []
            for rule_id, name, severity, description in rules:
                issues = issues_by_rule.get(rule_id, [])
                failed = any(issue[1] == "blocker" for issue in issues)
                warned = bool(issues) and not failed
                checks.append(
                    {
                        "rule_id": rule_id,
                        "name": name,
                        "description": (
                            (
                                issues[0][2]
                                + " This is baseline release evidence, not an item in a Stage 3 "
                                "job's Mapping Review queue. Refresh the Grand Database reference "
                                "snapshot and reproduce the governed 2025 release to resolve it."
                            )
                            if issues and warned
                            else (issues[0][2] if issues else description)
                        ),
                        "status": "failed" if failed else ("warning" if warned else "passed"),
                        "severity": severity,
                        "blocking": severity == "blocker",
                        "count": len(issues),
                    }
                )
            return {
                "validation_run_id": str(run[0]),
                "status": "PASS" if run[5] == "passed" else "FAIL",
                "completed_at": run[1].isoformat() if run[1] else None,
                "checks": checks,
                "passed": max(0, run[2] - run[3]),
                "warnings": run[4],
                "failed": run[3],
                "total": run[2],
                "open_mappings": 0,
                "included_jobs": 1,
                "can_publish": False,
                "publication_note": "The current governed release is already published.",
                "warning_context": (
                    "The 52 baseline warnings are audit evidence from the 2025 import, not open "
                    "Stage 3 mapping jobs. Resolve them by syncing updated Grand Database "
                    "references and rerunning the governed 2025 reproduction."
                    if run[4]
                    else None
                ),
            }
        finally:
            connection.close()

    def publish_job(
        self,
        job_id: str,
        *,
        approved_by: str,
        snapshot_name: str | None,
        notes: str | None,
    ) -> dict:
        with self._job_operation_lock(job_id):
            return self._publish_job(
                job_id,
                approved_by=approved_by,
                snapshot_name=snapshot_name,
                notes=notes,
            )

    def _publish_job(
        self,
        job_id: str,
        *,
        approved_by: str,
        snapshot_name: str | None,
        notes: str | None,
    ) -> dict:
        job = self.store.read(job_id)
        if job["state"] == "published" and job.get("publication"):
            return job["publication"]

        def begin(metadata: dict) -> None:
            if metadata["state"] != "validated" or not metadata.get("validation"):
                raise InvalidJobStateError("Only a validated job can be published")
            if metadata["validation"]["status"] != "PASS" or metadata["validation"]["blocker_count"]:
                raise InvalidJobStateError("Blocking validation must pass before publication")
            if not metadata.get("execution", {}).get("base_release", {}).get("generation"):
                raise InvalidJobStateError(
                    "This job predates pinned release generations. Start a new job before publishing."
                )
            metadata["state"] = "publishing"
            metadata["events"].append(
                {"at": utc_now(), "type": "publication_started", "message": "Publication started."}
            )

        self.store.mutate(job_id, begin)
        work_database = self._working_database(job_id)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        release_name = f"politica-election-results-{timestamp}-{job_id[:8]}"
        release_root = self.settings.releases_root / release_name
        temporary = self.settings.releases_root / f".{release_name}.tmp-{uuid.uuid4().hex}"
        try:
            temporary.mkdir(parents=False)
            candidate_database = (
                temporary / "data" / "database" / "politica_election_results.duckdb"
            )
            candidate_database.parent.mkdir(parents=True)
            shutil.copy2(work_database, candidate_database)
            publishing_job = self.store.read(job_id)
            artifact_source = self._release_artifact_source(publishing_job)
            base_release = publishing_job.get("execution", {}).get("base_release") or {}
            pinned_root = (
                self._resolve_portable_path(base_release["artifact_root"])
                if base_release.get("artifact_root")
                else None
            )
            expected_artifacts = (
                base_release.get("artifact_files")
                if pinned_root is not None and artifact_source.resolve() == pinned_root.resolve()
                else None
            )
            self._copy_release_artifacts(
                source_root=artifact_source,
                release_root=temporary,
                database=candidate_database,
                expected_artifacts=expected_artifacts,
            )
            self._copy_job_uploads_to_release(
                candidate_database, publishing_job, temporary
            )
            publication = self._approve_release_database(
                candidate_database,
                self.store.read(job_id),
                approved_by=approved_by,
                snapshot_name=snapshot_name,
                notes=notes,
            )
            from ..validate import validate_database

            release_validation = validate_database(candidate_database, temporary)
            if release_validation["status"] != "PASS":
                raise RuntimeError(
                    "The copied release unit failed validation and was not activated: "
                    + json.dumps(release_validation["failures"], ensure_ascii=False)
                )
            release_sha256 = _sha256_file(candidate_database)
            release_manifest = self._write_release_manifest(
                temporary,
                publication=publication,
                database_sha256=release_sha256,
                validation=release_validation,
                artifact_source=artifact_source,
            )
            os.replace(temporary, release_root)
            release_database = (
                release_root / "data" / "database" / "politica_election_results.duckdb"
            )
            release_manifest_path = release_root / "release_manifest.json"
            publication.update(
                {
                    "release_root": self._portable_path(release_root),
                    "database_path": self._portable_path(release_database),
                    "release_manifest_path": self._portable_path(release_manifest_path),
                    "database_size_bytes": release_database.stat().st_size,
                    "database_sha256": release_sha256,
                    "artifact_file_count": release_manifest["artifact_file_count"],
                    "artifact_size_bytes": release_manifest["artifact_size_bytes"],
                    "artifact_manifest_sha256": release_manifest[
                        "artifact_manifest_sha256"
                    ],
                    "release_manifest_sha256": _sha256_file(release_manifest_path),
                    "release_validation": release_validation,
                    "activated_at": utc_now(),
                }
            )
            self._activate_release(
                publication, expected_generation=base_release.get("generation")
            )

            def finish(metadata: dict) -> None:
                metadata["publication"] = publication
                metadata["state"] = "published"
                metadata["events"].append(
                    {
                        "at": utc_now(),
                        "type": "publication_activated",
                        "message": f"Activated immutable release {release_name}.",
                    }
                )

            self.store.mutate(job_id, finish)
            return publication
        except Exception as exc:
            shutil.rmtree(temporary, ignore_errors=True)

            try:
                interrupted_job = self.store.read(job_id)
                reconciled = self._reconcile_activated_publication(interrupted_job)
            except Exception:
                reconciled = None
            if reconciled is not None:
                def finish_reconciled(metadata: dict) -> None:
                    if metadata.get("state") == "published" and metadata.get("publication"):
                        return
                    metadata["publication"] = reconciled
                    metadata["state"] = "published"
                    metadata["last_error"] = None
                    metadata["events"].append(
                        {
                            "at": utc_now(),
                            "type": "publication_reconciled",
                            "message": (
                                "Activation completed before the job checkpoint; reconciled "
                                "the immutable release without creating a duplicate."
                            ),
                        }
                    )

                self.store.mutate(job_id, finish_reconciled)
                return reconciled

            active_pointer_claims_job = self._active_pointer_claims_job(job_id)
            quarantined = self._quarantine_unactivated_releases(job_id)

            def restore(metadata: dict) -> None:
                if metadata.get("state") != "publishing":
                    return
                if active_pointer_claims_job:
                    metadata["last_error"] = (
                        f"{type(exc).__name__}: {exc}. The active pointer identifies this job's "
                        "release, so its bundle and publishing state were preserved for recovery."
                    )
                    metadata["events"].append(
                        {
                            "at": utc_now(),
                            "type": "publication_reconciliation_required",
                            "message": metadata["last_error"],
                        }
                    )
                    return
                metadata["state"] = "validated"
                metadata["last_error"] = f"{type(exc).__name__}: {exc}"
                if quarantined:
                    metadata["last_error"] += (
                        f" Quarantined {len(quarantined)} finalized but unactivated "
                        "release candidate(s)."
                    )
                metadata["events"].append(
                    {
                        "at": utc_now(),
                        "type": "publication_failed",
                        "message": metadata["last_error"],
                    }
                )

            self.store.mutate(job_id, restore)
            raise

    def _release_artifact_source(self, job: dict) -> Path:
        """Locate the Parquet/manifests that belong to a job's working database."""

        job_work = self.store.job_dir(job["job_id"]) / "work"
        local_parquet = job_work / "data" / "parquet"
        if local_parquet.is_dir() and any(path.is_file() for path in local_parquet.rglob("*")):
            return job_work
        base_release = job.get("execution", {}).get("base_release") or {}
        if base_release.get("artifact_root"):
            return self._resolve_portable_path(base_release["artifact_root"])
        return self.settings.project_root.resolve()

    def _copy_release_artifacts(
        self,
        *,
        source_root: Path,
        release_root: Path,
        database: Path,
        expected_artifacts: list[dict] | None = None,
    ) -> None:
        """Copy every external file needed by the candidate database into its release unit."""

        if expected_artifacts is not None:
            self._verify_artifact_inventory(source_root, expected_artifacts)

        parquet_destination = release_root / "data" / "parquet"
        manifests_destination = release_root / "data" / "manifests"
        parquet_destination.mkdir(parents=True, exist_ok=True)
        manifests_destination.mkdir(parents=True, exist_ok=True)

        connection = self._connect(database, read_only=True, external_data_root=source_root)
        try:
            parquet_backed = connection.execute(
                """SELECT count(*) FROM information_schema.tables
                   WHERE table_schema='ballot' AND table_name IN ('ballot', 'ballot_preference')
                     AND table_type='VIEW'"""
            ).fetchone()[0]
        finally:
            connection.close()

        source_parquet = source_root / "data" / "parquet"
        if parquet_backed:
            if not source_parquet.is_dir() or not any(source_parquet.rglob("*.parquet")):
                raise FileNotFoundError(
                    f"The candidate database uses Parquet-backed ballot views, but no Parquet "
                    f"files were found under {source_parquet}."
                )
            shutil.rmtree(parquet_destination)
            shutil.copytree(source_parquet, parquet_destination, copy_function=shutil.copy2)

        source_manifests = source_root / "data" / "manifests"
        if source_manifests.is_dir():
            shutil.rmtree(manifests_destination)
            shutil.copytree(source_manifests, manifests_destination, copy_function=shutil.copy2)
        if expected_artifacts is not None:
            self._verify_artifact_inventory(release_root, expected_artifacts)

    def _copy_job_uploads_to_release(
        self, database: Path, job: dict, release_root: Path
    ) -> None:
        """Bind uploaded source bytes into the immutable release unit."""

        if job.get("mode") not in {"uploaded_files", AEC_ELECTION_BOOTSTRAP} or not job.get("uploads"):
            return
        destination_root = (
            release_root / "data" / "raw" / "operator_uploads" / job["job_id"]
        )
        destination_root.mkdir(parents=True, exist_ok=True)
        identities = job.get("execution", {}).get("source_identities", {})
        updates: list[tuple[str, str]] = []
        for upload in job["uploads"]:
            source = self.store.job_dir(job["job_id"]) / "uploads" / upload["stored_name"]
            if not source.is_file() or _sha256_file(source) != upload["sha256"]:
                raise RuntimeError(
                    f"Immutable upload failed checksum verification: {upload['original_name']}"
                )
            destination = destination_root / upload["stored_name"]
            shutil.copy2(source, destination)
            if _sha256_file(destination) != upload["sha256"]:
                raise RuntimeError(
                    f"Release source copy failed checksum verification: {upload['original_name']}"
                )
            identity = identities.get(upload["upload_id"])
            if not identity:
                raise RuntimeError(
                    f"Source identity checkpoint is missing for {upload['original_name']}"
                )
            updates.append(
                (
                    destination.relative_to(release_root).as_posix(),
                    identity["source_revision_id"],
                )
            )
        connection = self._connect(database)
        try:
            connection.execute("BEGIN TRANSACTION")
            connection.executemany(
                """UPDATE provenance.source_file_revision SET archive_path=?
                   WHERE source_revision_id=?""",
                updates,
            )
            connection.execute("COMMIT")
            connection.execute("CHECKPOINT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _write_release_manifest(
        self,
        release_root: Path,
        *,
        publication: dict,
        database_sha256: str,
        validation: dict,
        artifact_source: Path,
    ) -> dict:
        entries = []
        for path in sorted(
            candidate
            for candidate in (release_root / "data").rglob("*")
            if candidate.is_file()
        ):
            entries.append(
                {
                    "path": path.relative_to(release_root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
        artifact_manifest_sha256 = hashlib.sha256(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest = {
            "format_version": "1.0",
            "release_id": publication["release_id"],
            "job_id": publication["job_id"],
            "created_at": utc_now(),
            "database_path": "data/database/politica_election_results.duckdb",
            "database_sha256": database_sha256,
            "artifact_source": self._portable_path(artifact_source),
            "artifact_file_count": len(entries),
            "artifact_size_bytes": sum(entry["size_bytes"] for entry in entries),
            "artifact_manifest_sha256": artifact_manifest_sha256,
            "validation": {
                "status": validation["status"],
                "stage": validation["stage"],
                "failures": validation["failures"],
            },
            "files": entries,
        }
        (release_root / "release_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        return manifest

    def _approve_release_database(
        self,
        database: Path,
        job: dict,
        *,
        approved_by: str,
        snapshot_name: str | None,
        notes: str | None,
    ) -> dict:
        connection = self._connect(database)
        approved_at = datetime.now(timezone.utc)
        try:
            schema_version = connection.execute(
                "SELECT schema_version FROM control.schema_version ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()[0]
            source_revisions = []
            if job["execution"].get("import_run_id"):
                source_revisions = [
                    row[0]
                    for row in connection.execute(
                        """SELECT DISTINCT source_revision_id FROM provenance.import_run_input
                           WHERE import_run_id=? ORDER BY source_revision_id""",
                        [job["execution"]["import_run_id"]],
                    ).fetchall()
                ]
            snapshot_hash = hashlib.sha256(
                json.dumps(
                    {
                        "job_id": job["job_id"],
                        "validation_run_id": job["validation"]["validation_run_id"],
                        "source_revisions": source_revisions,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            snapshot_id = _uuid("publication_snapshot", job["job_id"], snapshot_hash)
            release_id = f"release_stage3_{job['job_id'].replace('-', '')}"
            connection.execute("BEGIN TRANSACTION")
            connection.execute(
                """INSERT INTO publish.publication_snapshot VALUES
                   (?, ?, ?, ?, 'approved', ?, ?, ?, ?, ?)""",
                [
                    snapshot_id,
                    snapshot_name or job["name"],
                    approved_at,
                    schema_version,
                    approved_at,
                    approved_by,
                    f"All source revisions attached to Stage 3 job {job['job_id']}.",
                    snapshot_hash,
                    notes,
                ],
            )
            for revision_id in source_revisions:
                connection.execute(
                    """INSERT INTO publish.publication_snapshot_source_revision VALUES (?, ?, ?)""",
                    [_uuid("snapshot_source", snapshot_id, revision_id), snapshot_id, revision_id],
                )
            connection.execute(
                """INSERT INTO control.database_release VALUES (?, ?, 'validated', ?, ?, ?, ?)""",
                [
                    release_id,
                    schema_version,
                    datetime.fromisoformat(job["created_at"]),
                    approved_at,
                    approved_by,
                    notes or f"Stage 3 publication for job {job['job_id']}.",
                ],
            )
            if job["execution"].get("import_run_id"):
                connection.execute(
                    """UPDATE provenance.import_run SET import_status='published', completed_at=?
                       WHERE import_run_id=?""",
                    [approved_at, job["execution"]["import_run_id"]],
                )
            connection.execute("COMMIT")
            connection.execute("CHECKPOINT")
            return {
                "job_id": job["job_id"],
                "release_id": release_id,
                "publication_snapshot_id": snapshot_id,
                "snapshot_hash": snapshot_hash,
                "schema_version": schema_version,
                "approved_by": approved_by,
                "approved_at": approved_at.isoformat(),
                "source_revision_count": len(source_revisions),
            }
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _activate_release(
        self, publication: dict, *, expected_generation: str | None = None
    ) -> None:
        pointer = self.settings.releases_root / "active.json"
        database_path = self._resolve_portable_path(publication["database_path"])
        path_base, database_value = self._pointer_path(database_path)
        pointer_document = {
            "release_id": publication["release_id"],
            "path_base": path_base,
            "database_path": database_value,
            "sha256": publication["database_sha256"],
            "activated_at": publication["activated_at"],
        }
        for optional_key in (
            "release_root",
            "release_manifest_path",
        ):
            if publication.get(optional_key) is not None:
                optional_path = self._resolve_portable_path(publication[optional_key])
                optional_base, optional_value = self._pointer_path(optional_path)
                pointer_document[optional_key] = (
                    optional_value if optional_base == path_base else str(optional_path)
                )
        if publication.get("artifact_manifest_sha256") is not None:
            pointer_document["artifact_manifest_sha256"] = publication[
                "artifact_manifest_sha256"
            ]
        if publication.get("release_manifest_sha256") is not None:
            pointer_document["release_manifest_sha256"] = publication[
                "release_manifest_sha256"
            ]
        with FileLock(str(self.settings.releases_root / ".activation.lock")):
            if (
                expected_generation is not None
                and self.current_release_generation() != expected_generation
            ):
                raise InvalidJobStateError(
                    "The governed release changed after this job was created. Start a new job "
                    "from the current release; the active pointer was not overwritten."
                )
            if (
                not database_path.is_file()
                or _sha256_file(database_path) != publication["database_sha256"]
            ):
                raise RuntimeError("The release database failed activation checksum verification")
            self._verify_release_bundle(pointer_document, database_path)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix="active-", suffix=".json.tmp", dir=self.settings.releases_root
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(pointer_document, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, pointer)
                self._active_pointer_signature = None
                self._active_database_signature = None
                self._active_database_path = None
                self._active_bundle_files = None
            finally:
                temporary.unlink(missing_ok=True)

    def publications(self) -> dict:
        active_path = self.settings.releases_root / "active.json"
        active = None
        if active_path.is_file():
            try:
                active = json.loads(active_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        releases = [
            job["publication"] for job in self.store.list() if job.get("publication")
        ]
        return {"active": active, "releases": releases}
