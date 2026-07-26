import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from filelock import FileLock

from politica_erd.app.config import AppSettings
from politica_erd.app.service import InvalidJobStateError, JobService
from politica_erd.build import PROJECT_ROOT, build


class Stage3RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.base_database = cls.root / "foundation.duckdb"
        build(cls.base_database, PROJECT_ROOT)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def settings(self, name: str) -> AppSettings:
        return AppSettings(
            project_root=PROJECT_ROOT,
            base_database=self.base_database,
            app_data=self.root / name,
        )

    @staticmethod
    def mark_validated(service: JobService, job_id: str) -> None:
        def update(metadata: dict) -> None:
            metadata["state"] = "validated"
            metadata["validation"] = {
                "status": "PASS",
                "blocker_count": 0,
                "validation_run_id": str(uuid.uuid4()),
            }

        service.store.mutate(job_id, update)

    def validated_reproduction(self, service: JobService, name: str) -> dict:
        job = service.begin_reproduce_2025(name)
        service._copy_governed_database(job["job_id"])
        self.mark_validated(service, job["job_id"])
        return service.store.read(job["job_id"])

    def test_builtin_preparation_replaces_an_unmarked_partial_tree(self):
        service = JobService(self.settings("partial-preparation"))
        job = service.begin_reproduce_2025("Partial preparation recovery")
        work = service.store.job_dir(job["job_id"]) / "work"
        partial = work / "config"
        partial.mkdir(parents=True)
        (partial / "partial-only.txt").write_text("incomplete", encoding="utf-8")
        stale = work / "data" / ".raw.copying-stale"
        stale.mkdir(parents=True)
        (stale / "partial-only.txt").write_text("incomplete", encoding="utf-8")

        prepared = service._prepare_builtin_project(job["job_id"])

        self.assertFalse((prepared / "config" / "partial-only.txt").exists())
        self.assertTrue((prepared / ".builtin_inputs_ready").is_file())
        self.assertFalse(any(".copying-" in path.name for path in prepared.rglob("*")))
        for relative in (
            "config",
            "schema",
            "data/raw",
            "data/manifests",
            "data/snapshots",
        ):
            source = {
                path.relative_to(PROJECT_ROOT / relative).as_posix()
                for path in (PROJECT_ROOT / relative).rglob("*")
                if path.is_file()
            }
            copied = {
                path.relative_to(prepared / relative).as_posix()
                for path in (prepared / relative).rglob("*")
                if path.is_file()
            }
            self.assertEqual(copied, source)

    def test_unactivated_publication_returns_to_validated_and_removes_temp(self):
        settings = self.settings("unactivated-publication")
        service = JobService(settings)
        job = service.begin_reproduce_2025("Interrupted publication")
        self.mark_validated(service, job["job_id"])
        service.store.mutate(
            job["job_id"], lambda metadata: metadata.update({"state": "publishing"})
        )
        temporary = settings.releases_root / (
            ".politica-election-results-20260717T000000Z-"
            f"{job['job_id'][:8]}.tmp-interrupted"
        )
        temporary.mkdir()
        (temporary / "partial").write_text("incomplete", encoding="utf-8")

        restarted = JobService(settings)
        recovered = restarted.store.read(job["job_id"])

        self.assertEqual(recovered["state"], "validated")
        self.assertFalse(temporary.exists())
        self.assertEqual(recovered["events"][-1]["type"], "publication_interrupted")

    def test_activated_publication_is_reconciled_without_a_second_release(self):
        settings = self.settings("activated-publication")
        service = JobService(settings)
        job = self.validated_reproduction(service, "Activated publication")
        publication = service.publish_job(
            job["job_id"],
            approved_by="Recovery test",
            snapshot_name="Recovery test",
            notes="Simulate activation preceding the JSON checkpoint.",
        )
        release_directories = {
            path.name
            for path in settings.releases_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        }

        def lose_json_checkpoint(metadata: dict) -> None:
            metadata["state"] = "publishing"
            metadata["publication"] = None

        service.store.mutate(job["job_id"], lose_json_checkpoint)
        restarted = JobService(settings)
        recovered = restarted.store.read(job["job_id"])

        self.assertEqual(recovered["state"], "published")
        self.assertTrue(recovered["publication"]["recovered_after_restart"])
        self.assertEqual(recovered["publication"]["release_id"], publication["release_id"])
        repeated = restarted.publish_job(
            job["job_id"], approved_by="Ignored", snapshot_name=None, notes=None
        )
        self.assertEqual(repeated["release_id"], publication["release_id"])
        self.assertEqual(
            release_directories,
            {
                path.name
                for path in settings.releases_root.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            },
        )
        pointer = json.loads((settings.releases_root / "active.json").read_text())
        self.assertEqual(pointer["path_base"], "releases_root")
        self.assertFalse(Path(pointer["database_path"]).is_absolute())

    def test_finalized_but_unactivated_release_is_quarantined_on_restart(self):
        settings = self.settings("finalized-unactivated")
        service = JobService(settings)
        job = self.validated_reproduction(service, "Finalized interruption")
        publication = service.publish_job(
            job["job_id"], approved_by="Recovery test", snapshot_name=None, notes=None
        )
        release_root = Path(publication["release_root"])
        (settings.releases_root / "active.json").unlink()

        def lose_activation_checkpoint(metadata: dict) -> None:
            metadata["state"] = "publishing"
            metadata["publication"] = None

        service.store.mutate(job["job_id"], lose_activation_checkpoint)
        restarted = JobService(settings)
        recovered = restarted.store.read(job["job_id"])

        self.assertEqual(recovered["state"], "validated")
        self.assertFalse(release_root.exists())
        quarantined = list((settings.releases_root / ".quarantine").iterdir())
        self.assertEqual(len(quarantined), 1)
        self.assertTrue((quarantined[0] / "release_manifest.json").is_file())

    def test_finish_checkpoint_failure_reconciles_instead_of_downgrading(self):
        settings = self.settings("finish-checkpoint")
        service = JobService(settings)
        job = self.validated_reproduction(service, "Finish checkpoint")
        original_mutate = service.store.mutate
        armed = {"value": True}

        def flaky_mutate(job_id, mutation):
            if armed["value"] and getattr(mutation, "__name__", "") == "finish":
                armed["value"] = False
                raise OSError("simulated job checkpoint interruption")
            return original_mutate(job_id, mutation)

        with patch.object(service.store, "mutate", side_effect=flaky_mutate):
            publication = service.publish_job(
                job["job_id"], approved_by="Recovery test", snapshot_name=None, notes=None
            )

        recovered = service.store.read(job["job_id"])
        self.assertEqual(recovered["state"], "published")
        self.assertEqual(recovered["publication"]["release_id"], publication["release_id"])
        self.assertEqual(recovered["events"][-1]["type"], "publication_reconciled")

    def test_active_release_is_not_quarantined_when_job_metadata_is_malformed(self):
        settings = self.settings("active-malformed-job")
        service = JobService(settings)
        job = self.validated_reproduction(service, "Malformed active checkpoint")
        publication = service.publish_job(
            job["job_id"], approved_by="Recovery test", snapshot_name=None, notes=None
        )
        release_root = Path(publication["release_root"])

        def corrupt_job_checkpoint(metadata: dict) -> None:
            metadata["state"] = "publishing"
            metadata["publication"] = None
            metadata["validation"] = None

        service.store.mutate(job["job_id"], corrupt_job_checkpoint)
        restarted = JobService(settings)
        recovered = restarted.store.read(job["job_id"])

        self.assertEqual(recovered["state"], "publishing")
        self.assertTrue(release_root.is_dir())
        self.assertEqual(
            recovered["events"][-1]["type"], "publication_reconciliation_required"
        )

    def test_compare_and_swap_preserves_first_concurrent_publication(self):
        settings = self.settings("publication-cas")
        service = JobService(settings)
        first = self.validated_reproduction(service, "First")
        second = self.validated_reproduction(service, "Second")
        first_publication = service.publish_job(
            first["job_id"], approved_by="Recovery test", snapshot_name=None, notes=None
        )

        with self.assertRaises(InvalidJobStateError):
            service.publish_job(
                second["job_id"], approved_by="Recovery test", snapshot_name=None, notes=None
            )

        pointer = json.loads((settings.releases_root / "active.json").read_text())
        self.assertEqual(pointer["release_id"], first_publication["release_id"])
        self.assertEqual(service.store.read(second["job_id"])["state"], "validated")
        self.assertTrue(list((settings.releases_root / ".quarantine").iterdir()))

    def test_active_bundle_mutation_falls_back_to_validated_base(self):
        settings = self.settings("bundle-integrity")
        service = JobService(settings)
        job = self.validated_reproduction(service, "Bundle integrity")
        publication = service.publish_job(
            job["job_id"], approved_by="Recovery test", snapshot_name=None, notes=None
        )
        active_database = Path(publication["database_path"])
        self.assertEqual(service.governed_database(), active_database)
        manifest_artifact = next(
            path
            for path in (Path(publication["release_root"]) / "data" / "manifests").rglob("*")
            if path.is_file()
        )
        with manifest_artifact.open("ab") as handle:
            handle.write(b"\n")

        self.assertEqual(service.governed_database(), self.base_database)

    def test_release_manifest_mutation_falls_back_to_validated_base(self):
        settings = self.settings("release-manifest-integrity")
        service = JobService(settings)
        job = self.validated_reproduction(service, "Release manifest integrity")
        publication = service.publish_job(
            job["job_id"], approved_by="Recovery test", snapshot_name=None, notes=None
        )
        active_database = Path(publication["database_path"])
        self.assertEqual(service.governed_database(), active_database)
        manifest = Path(publication["release_manifest_path"])
        with manifest.open("ab") as handle:
            handle.write(b"\n")

        self.assertEqual(service.governed_database(), self.base_database)

    def test_destination_artifact_mismatch_is_rejected_after_copy(self):
        settings = self.settings("artifact-toctou")
        service = JobService(settings)
        source_root = self.root / "artifact-source"
        source_manifest = source_root / "data" / "manifests" / "source.json"
        source_manifest.parent.mkdir(parents=True)
        source_manifest.write_text('{"source": true}\n', encoding="utf-8")
        expected = service._artifact_inventory(source_root)
        release_root = self.root / "artifact-candidate"
        release_root.mkdir()
        real_copytree = shutil.copytree

        def copy_then_corrupt(source, destination, **kwargs):
            result = real_copytree(source, destination, **kwargs)
            if Path(destination).name == "manifests":
                (Path(destination) / "source.json").write_text("changed\n", encoding="utf-8")
            return result

        with patch("politica_erd.app.service.shutil.copytree", side_effect=copy_then_corrupt):
            with self.assertRaises(RuntimeError):
                service._copy_release_artifacts(
                    source_root=source_root,
                    release_root=release_root,
                    database=self.base_database,
                    expected_artifacts=expected,
                )

    def test_cancel_rejects_a_job_with_an_active_operation_lock(self):
        settings = self.settings("operation-lock")
        service = JobService(settings)
        job = service.begin_reproduce_2025("Operation lock")
        lock = FileLock(str(service.store.job_dir(job["job_id"]) / ".operation.lock"))
        with lock:
            with self.assertRaises(InvalidJobStateError):
                service.cancel_job(job["job_id"])

    def test_second_app_instance_for_same_data_directory_is_rejected(self):
        from politica_erd.app.api import create_app

        settings = self.settings("singleton-app")
        application = create_app(settings)
        try:
            with self.assertRaises(RuntimeError):
                create_app(settings)
        finally:
            application.state.instance_lock.release()


if __name__ == "__main__":
    unittest.main()
