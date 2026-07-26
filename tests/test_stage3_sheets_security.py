import copy
import hashlib
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
from fastapi import HTTPException
from filelock import FileLock

from politica_erd.app.config import AppSettings
from politica_erd.app.service import InvalidJobStateError, JobService
import politica_erd.app.sheets_routes as sheets_routes
from politica_erd.app.sheets_routes import (
    SheetsApplyRequest,
    SheetsPreviewRequest,
    _activate_reference_snapshot,
    _create_reference_job,
    _local_release_lifecycle,
    apply_sheets_sync,
    preview_sheets_sync,
    reconcile_interrupted_sheets_syncs,
)
from politica_erd.app.sheets_sync import (
    GoogleSheetsReferenceSynchronizer,
    SheetsSyncError,
    SyncContractError,
    _source_revision,
)
from politica_erd.build import PROJECT_ROOT, build
from politica_erd.correct_tcp_measures import apply_tcp_measure_correction


class _Reader:
    credential_descriptor = {"source": "test"}


class _RouteSnapshotReader:
    credential_descriptor = {"source": "test", "oauth_scopes": []}
    snapshot: dict = {}

    def __init__(self, *args, **kwargs):
        descriptor = kwargs.get("credential_descriptor")
        if descriptor:
            self.credential_descriptor = descriptor

    def fetch_reference_snapshot(self, _spreadsheet_id: str) -> dict:
        return copy.deepcopy(self.snapshot)


class Stage3SheetsSecurityTests(unittest.TestCase):
    def test_arbitrary_workbook_override_is_rejected_against_pinned_id(self):
        database = PROJECT_ROOT / "data/database/politica_election_results.duckdb"
        connection = duckdb.connect(str(database), read_only=True)
        try:
            with self.assertRaises(SyncContractError):
                GoogleSheetsReferenceSynchronizer(
                    connection,
                    PROJECT_ROOT,
                    reader=_Reader(),
                    workbook_id="arbitrary_unpinned_spreadsheet_id",
                )
        finally:
            connection.close()

    def test_successful_preview_token_replay_returns_existing_audit_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = AppSettings(
                project_root=root,
                base_database=root / "base.duckdb",
                app_data=root / "app",
            )
            token = "a" * 32
            preview_path = settings.app_data / "sheets/previews" / f"{token}.json"
            preview_path.parent.mkdir(parents=True)
            release_path = root / "immutable-release.duckdb"
            release_path.write_bytes(b"immutable-release-sentinel")
            release_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
            audit_path = root / "audit.json"
            audit = {
                "ok": True,
                "preview_id": token,
                "publication": {"database_path": str(release_path)},
                "local_release_lifecycle": _local_release_lifecycle(
                    "activated", {"release_id": "release_test"}
                ),
            }
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            preview_path.write_text(
                json.dumps(
                    {
                        "state": "applied",
                        "applications": [{"audit_path": str(audit_path)}],
                    }
                ),
                encoding="utf-8",
            )
            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(settings=settings))
            )

            first = apply_sheets_sync(SheetsApplyRequest(preview_id=token), request)
            second = apply_sheets_sync(SheetsApplyRequest(preview_id=token), request)

            self.assertTrue(first["idempotent_replay"])
            self.assertTrue(second["idempotent_replay"])
            self.assertEqual(first["preview_token_state"], "applied")
            self.assertEqual(
                hashlib.sha256(release_path.read_bytes()).hexdigest(), release_hash
            )

    def test_consumed_incomplete_token_cannot_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = AppSettings(
                project_root=root,
                base_database=root / "base.duckdb",
                app_data=root / "app",
            )
            token = "b" * 32
            preview_path = settings.app_data / "sheets/previews" / f"{token}.json"
            preview_path.parent.mkdir(parents=True)
            preview_path.write_text(
                json.dumps({"state": "applying", "applications": []}),
                encoding="utf-8",
            )
            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(settings=settings))
            )
            with self.assertRaises(HTTPException) as raised:
                apply_sheets_sync(SheetsApplyRequest(preview_id=token), request)
            self.assertEqual(raised.exception.status_code, 409)

    def test_existing_immutable_release_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "base.duckdb"
            build(database, PROJECT_ROOT)
            settings = AppSettings(
                project_root=root,
                base_database=database,
                app_data=root / "app",
            )
            service = JobService(settings)
            request = SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        settings=settings,
                        job_service=service,
                        store=service.store,
                    )
                )
            )
            revision = "c" * 64
            job_id, working = _create_reference_job(request, revision)
            publication, _ = _activate_reference_snapshot(
                request,
                job_id,
                working,
                revision,
                ["People"],
                preview_token="c" * 32,
            )
            release_path = service._resolve_portable_path(
                publication["database_path"]
            )
            original_hash = hashlib.sha256(release_path.read_bytes()).hexdigest()
            with self.assertRaises(SheetsSyncError):
                _activate_reference_snapshot(
                    request,
                    job_id,
                    working,
                    revision,
                    ["People"],
                    preview_token="c" * 32,
                )

            self.assertEqual(
                hashlib.sha256(release_path.read_bytes()).hexdigest(), original_hash
            )
            self.assertEqual(
                service.governed_database().resolve(), release_path.resolve()
            )

    def test_activated_lifecycle_is_explicit_about_local_mutations(self):
        lifecycle = _local_release_lifecycle(
            "activated",
            {
                "release_id": "release_test",
                "database_sha256": "d" * 64,
                "activated_at": "2026-07-17T00:00:00+00:00",
            },
        )
        self.assertTrue(lifecycle["local_working_snapshot_written"])
        self.assertTrue(lifecycle["local_database_validated"])
        self.assertTrue(lifecycle["immutable_local_release_created"])
        self.assertTrue(lifecycle["local_release_activated"])
        self.assertFalse(lifecycle["google_source_modified"])
        self.assertFalse(lifecycle["canonical_base_database_modified"])


class Stage3SheetsBundleRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._foundation_directory = tempfile.TemporaryDirectory()
        cls.foundation_database = (
            Path(cls._foundation_directory.name) / "foundation.duckdb"
        )
        build(cls.foundation_database, PROJECT_ROOT)
        legacy = json.loads(
            (
                PROJECT_ROOT
                / "data/snapshots/grand_database_2026-07-16.json"
            ).read_text(encoding="utf-8")
        )
        snapshot = {
            "spreadsheet_id": legacy["source_workbook_id"],
            "spreadsheet_properties": {"title": "Politica Grand Database"},
            "captured_at": "2026-07-16T00:00:00+00:00",
            "credential": _RouteSnapshotReader.credential_descriptor,
            "tables": legacy["tables"],
        }
        snapshot["source_revision_sha256"] = _source_revision(snapshot)
        _RouteSnapshotReader.snapshot = snapshot

    @classmethod
    def tearDownClass(cls):
        cls._foundation_directory.cleanup()

    def _context(self, root: Path):
        base_database = root / "base.duckdb"
        shutil.copy2(self.foundation_database, base_database)
        config_root = root / "config"
        config_root.mkdir()
        shutil.copy2(
            PROJECT_ROOT / "config/grand_sync_contract.yml",
            config_root / "grand_sync_contract.yml",
        )
        settings = AppSettings(
            project_root=root,
            base_database=base_database,
            app_data=root / "app",
        )
        service = JobService(settings)
        app = SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                job_service=service,
                store=service.store,
            )
        )
        return settings, service, app, SimpleNamespace(app=app)

    def _preview(self, request):
        with patch.object(
            sheets_routes, "GoogleSheetsReader", _RouteSnapshotReader
        ):
            return preview_sheets_sync(SheetsPreviewRequest(), request)

    def _apply(self, token: str, request):
        with patch.object(
            sheets_routes, "GoogleSheetsReader", _RouteSnapshotReader
        ):
            return apply_sheets_sync(
                SheetsApplyRequest(preview_id=token), request
            )

    def test_preview_diff_is_bound_to_its_pinned_working_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, service, _, request = self._context(root)
            observed_databases = []
            original_run = GoogleSheetsReferenceSynchronizer.run

            def record_database(synchronizer, *args, **kwargs):
                database_path = synchronizer.connection.execute(
                    "PRAGMA database_list"
                ).fetchone()[2]
                observed_databases.append(Path(database_path).resolve())
                return original_run(synchronizer, *args, **kwargs)

            with patch.object(
                GoogleSheetsReferenceSynchronizer,
                "run",
                record_database,
            ):
                preview = self._preview(request)

            working = (
                settings.jobs_root
                / preview["job_id"]
                / "work/database.duckdb"
            ).resolve()
            self.assertEqual(observed_databases, [working])
            job = service.store.read(preview["job_id"])
            self.assertEqual(
                job["execution"]["base_database_sha256"],
                hashlib.sha256(working.read_bytes()).hexdigest(),
            )

    def test_stale_base_cas_preserves_active_release_and_quarantines_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, service, _, request = self._context(root)
            revision_one = "1" * 64
            revision_two = "2" * 64
            first_job, first_database = _create_reference_job(
                request, revision_one
            )
            second_job, second_database = _create_reference_job(
                request, revision_two
            )
            first, _ = _activate_reference_snapshot(
                request,
                first_job,
                first_database,
                revision_one,
                ["People"],
                preview_token="1" * 32,
            )
            with self.assertRaises(InvalidJobStateError):
                _activate_reference_snapshot(
                    request,
                    second_job,
                    second_database,
                    revision_two,
                    ["People"],
                    preview_token="2" * 32,
                )

            pointer = json.loads(
                (settings.releases_root / "active.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(pointer["release_id"], first["release_id"])
            self.assertEqual(
                service.governed_database().resolve(),
                service._resolve_portable_path(first["database_path"]).resolve(),
            )
            self.assertFalse(
                any(
                    path.is_dir()
                    for path in settings.releases_root.glob(
                        f"politica-reference-sync-{revision_two[:16]}-*"
                    )
                )
            )
            self.assertTrue(any((settings.releases_root / ".quarantine").iterdir()))

    def test_startup_resumes_checkpointed_candidate_before_pointer_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, service, app, request = self._context(root)
            preview = self._preview(request)
            token = preview["preview_token"]
            with patch.object(
                service,
                "_activate_release",
                side_effect=SystemExit("crash before pointer write"),
            ):
                with self.assertRaises(SystemExit):
                    self._apply(token, request)

            preview_path = (
                settings.app_data / "sheets/previews" / f"{token}.json"
            )
            checkpoint = json.loads(preview_path.read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["state"], "applying")
            self.assertIn("candidate_publication", checkpoint)
            self.assertFalse((settings.releases_root / "active.json").exists())

            self.assertEqual(reconcile_interrupted_sheets_syncs(app), [token])
            repaired = service.store.read(preview["job_id"])
            self.assertEqual(repaired["state"], "published")
            self.assertTrue(repaired["execution"]["canonical_complete"])

    def test_startup_reconciles_crash_immediately_after_pointer_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, service, app, request = self._context(root)
            preview = self._preview(request)
            token = preview["preview_token"]
            original_activate = service._activate_release

            def activate_then_crash(publication, *, expected_generation=None):
                original_activate(
                    publication, expected_generation=expected_generation
                )
                raise SystemExit("crash after pointer write")

            with patch.object(
                service, "_activate_release", side_effect=activate_then_crash
            ):
                with self.assertRaises(SystemExit):
                    self._apply(token, request)

            self.assertTrue((settings.releases_root / "active.json").is_file())
            self.assertEqual(reconcile_interrupted_sheets_syncs(app), [token])
            replay = self._apply(token, request)
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(replay["preview_token_state"], "applied")

    def test_precheckpoint_frozen_candidate_is_atomic_and_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, _, app, request = self._context(root)
            preview = self._preview(request)
            token = preview["preview_token"]
            preview_path = (
                settings.app_data / "sheets/previews" / f"{token}.json"
            )
            original_write = sheets_routes._write_json
            tripped = False

            def crash_before_checkpoint(path, payload):
                nonlocal tripped
                if (
                    not tripped
                    and Path(path) == preview_path
                    and payload.get("candidate_publication")
                ):
                    tripped = True
                    raise SystemExit("crash before checkpoint")
                return original_write(path, payload)

            with patch.object(
                sheets_routes, "_write_json", side_effect=crash_before_checkpoint
            ):
                with self.assertRaises(SystemExit):
                    self._apply(token, request)

            candidates = list(
                settings.releases_root.glob("politica-reference-sync-*")
            )
            self.assertEqual(len(candidates), 1)
            self.assertTrue((candidates[0] / "release_manifest.json").is_file())
            self.assertTrue(
                (
                    candidates[0]
                    / "data/database/politica_election_results.duckdb"
                ).is_file()
            )

            self.assertEqual(reconcile_interrupted_sheets_syncs(app), [])
            stored = json.loads(preview_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["state"], "failed")
            self.assertIn("Create a new Google Sheets preview", stored["failure"])
            self.assertFalse(
                any(
                    settings.releases_root.glob(
                        "politica-reference-sync-*"
                    )
                )
            )
            self.assertTrue(any((settings.releases_root / ".quarantine").iterdir()))

    def test_startup_removes_abandoned_prefreeze_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, _, app, request = self._context(root)
            preview = self._preview(request)
            token = preview["preview_token"]
            preview_path = (
                settings.app_data / "sheets/previews" / f"{token}.json"
            )
            stored = json.loads(preview_path.read_text(encoding="utf-8"))
            stored["state"] = "applying"
            sheets_routes._write_json(preview_path, stored)
            temporary = settings.releases_root / (
                ".politica-reference-sync-"
                f"{stored['source_revision_sha256'][:16]}-"
                f"{stored['job_id'].replace('-', '')}.tmp-deadbeef"
            )
            temporary.mkdir()
            (temporary / "partial").write_text("partial", encoding="utf-8")

            self.assertEqual(reconcile_interrupted_sheets_syncs(app), [])
            self.assertFalse(temporary.exists())
            recovered = json.loads(preview_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered["state"], "failed")
            self.assertIn("abandoned temporary bundle", recovered["failure"])

    def test_startup_repairs_applied_token_before_job_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, service, app, request = self._context(root)
            preview = self._preview(request)
            token = preview["preview_token"]
            original_mutate = service.store.mutate

            def crash_at_job_checkpoint(job_id, mutation):
                if getattr(mutation, "__name__", "") == "record_sync":
                    raise SystemExit("crash before job checkpoint")
                return original_mutate(job_id, mutation)

            with patch.object(
                service.store, "mutate", side_effect=crash_at_job_checkpoint
            ):
                with self.assertRaises(SystemExit):
                    self._apply(token, request)

            preview_path = (
                settings.app_data / "sheets/previews" / f"{token}.json"
            )
            self.assertEqual(
                json.loads(preview_path.read_text(encoding="utf-8"))["state"],
                "applied",
            )
            self.assertNotEqual(
                service.store.read(preview["job_id"])["state"], "published"
            )

            self.assertEqual(reconcile_interrupted_sheets_syncs(app), [token])
            job = service.store.read(preview["job_id"])
            self.assertEqual(job["state"], "published")
            self.assertEqual(len(job["reference_syncs"]), 1)

    def test_apply_waits_for_the_job_operation_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, service, _, request = self._context(root)
            preview = self._preview(request)
            token = preview["preview_token"]
            preview_path = (
                settings.app_data / "sheets/previews" / f"{token}.json"
            )
            lock = FileLock(
                str(
                    service.store.job_dir(preview["job_id"])
                    / ".operation.lock"
                )
            )
            results = []
            failures = []

            def apply_in_thread():
                try:
                    results.append(self._apply(token, request))
                except BaseException as exc:
                    failures.append(exc)

            with lock:
                thread = threading.Thread(target=apply_in_thread)
                thread.start()
                time.sleep(0.2)
                self.assertTrue(thread.is_alive())
                self.assertEqual(
                    json.loads(preview_path.read_text(encoding="utf-8"))["state"],
                    "ready",
                )
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(results[0]["ok"])

    def test_stage2_ballot_views_work_from_self_contained_bundle(self):
        archived_database = (
            PROJECT_ROOT
            / "data/database/politica_election_results.duckdb"
        )
        archived_hash = hashlib.sha256(archived_database.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "corrected-stage2-fixture.duckdb"
            shutil.copy2(archived_database, database)
            connection = duckdb.connect(str(database))
            try:
                apply_tcp_measure_correction(
                    connection,
                    base_database_sha256=archived_hash,
                    release_id="release_test_stage3_bundle_stage9_1_contract",
                )
                connection.execute("CHECKPOINT")
            finally:
                connection.close()
            source_hash = hashlib.sha256(database.read_bytes()).hexdigest()
            settings = AppSettings(
                project_root=PROJECT_ROOT,
                base_database=database,
                app_data=Path(directory) / "app",
            )
            service = JobService(settings)
            app = SimpleNamespace(
                state=SimpleNamespace(
                    settings=settings,
                    job_service=service,
                    store=service.store,
                )
            )
            request = SimpleNamespace(app=app)
            revision = "f" * 64
            job = service.begin_job(
                name="Bundle ballot smoke",
                authority_id=None,
                election_id=None,
            )
            publication, validation = _activate_reference_snapshot(
                request,
                job["job_id"],
                database,
                revision,
                ["People", "Parties", "Constituencies"],
                preview_token="f" * 32,
            )
            self.assertEqual(validation["status"], "PASS")
            release_root = service._resolve_portable_path(
                publication["release_root"]
            )
            manifest_path = service._resolve_portable_path(
                publication["release_manifest_path"]
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            paths = {entry["path"] for entry in manifest["files"]}
            self.assertIn(
                "data/database/politica_election_results.duckdb", paths
            )
            self.assertTrue(
                any(path.startswith("data/parquet/") for path in paths)
            )
            self.assertTrue(
                any(path.startswith("data/manifests/") for path in paths)
            )
            self.assertEqual(
                hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                publication["release_manifest_sha256"],
            )
            release_database = service._resolve_portable_path(
                publication["database_path"]
            )
            connection = service._connect(
                release_database,
                read_only=True,
                external_data_root=release_root,
            )
            try:
                self.assertGreater(
                    connection.execute(
                        "SELECT count(*) FROM ballot.ballot"
                    ).fetchone()[0],
                    0,
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT ballot_id FROM ballot.ballot_preference LIMIT 1"
                    ).fetchone()
                )
            finally:
                connection.close()
            self.assertEqual(
                hashlib.sha256(database.read_bytes()).hexdigest(), source_hash
            )
        self.assertEqual(
            hashlib.sha256(archived_database.read_bytes()).hexdigest(), archived_hash
        )


if __name__ == "__main__":
    unittest.main()
