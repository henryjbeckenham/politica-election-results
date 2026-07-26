"""Stage 11.3 completion of the governed 2025 Senate group aggregates.

The two official AEC files were registered by the original 2025 reproduction,
before their individual Stage 8 canonical transformer existed.  Ordinary
operator ingestion correctly rejects those bytes as duplicates.  This command
reuses the registered revisions in a normal isolated ingestion job, validates
the richer group totals, publishes a new immutable release, and leaves the
previous release untouched.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import shutil

from filelock import FileLock

from .app.config import AppSettings
from .app.service import JobService, utc_now


ELECTION_ID = "election_fed_2025_05_03_general"
AUTHORITY_ID = "authority_aec"
COMPLETION_VERSION = "1.1.3"
EXPECTED_ACTIVE_FACTS = 1_872
EXPECTED_STATE_GROUPS = 123
EXPECTED_NATIONAL_GROUPS = 33
OFFICIAL_SOURCES = {
    "SenateFirstPrefsByGroupByVoteTypeDownload-31496.csv": (
        "1b527253ec43188f4d923349344ad3ed3dcb10e588f87daa888fe211c0c31ced"
    ),
    "SenateFirstPrefsByStateByGroupByVoteTypeDownload-31496.csv": (
        "084c7f88e18f62db0b1a18099b081c7ae12680240435a5f4c9aa14e92b29efd5"
    ),
}


class SenateGroupCompletionError(RuntimeError):
    """Raised when the active release is outside the completion contract."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_paths(settings: AppSettings) -> dict[str, Path]:
    root = settings.project_root / "data/raw/aec/2025_federal/31496/final"
    paths: dict[str, Path] = {}
    for filename, expected_sha256 in OFFICIAL_SOURCES.items():
        path = root / filename
        if not path.is_file():
            raise SenateGroupCompletionError(
                f"The required official source is missing: {path}"
            )
        observed = _sha256_file(path)
        if observed != expected_sha256:
            raise SenateGroupCompletionError(
                f"The official source checksum does not match for {filename}: {observed}"
            )
        paths[filename] = path
    return paths


def assess_senate_group_completion(connection) -> dict:
    row = connection.execute(
        """SELECT count(*) AS active_facts,
                  count(*) FILTER (
                    WHERE contest_id IS NOT NULL AND vote_type='total'
                      AND measure_type='votes'
                  ) AS state_groups,
                  count(*) FILTER (
                    WHERE contest_id IS NULL AND vote_type='total'
                      AND measure_type='votes'
                  ) AS national_groups,
                  count(DISTINCT contest_id) FILTER (WHERE contest_id IS NOT NULL)
                    AS state_contests,
                  count(DISTINCT source_revision_id) AS source_revisions
           FROM results.vote_result
           WHERE election_id=? AND result_type='group_total'
             AND subject_type='source_group' AND record_status='active'""",
        [ELECTION_ID],
    ).fetchone()
    hashes = [
        item[0]
        for item in connection.execute(
            """SELECT DISTINCT lower(revision.sha256)
               FROM results.vote_result result
               JOIN provenance.source_file_revision revision
                 ON revision.source_revision_id=result.source_revision_id
               WHERE result.election_id=? AND result.result_type='group_total'
                 AND result.subject_type='source_group'
                 AND result.record_status='active'
               ORDER BY lower(revision.sha256)""",
            [ELECTION_ID],
        ).fetchall()
    ]
    observed = {
        "active_group_total_facts": int(row[0]),
        "state_group_rows": int(row[1]),
        "national_group_rows": int(row[2]),
        "state_contests": int(row[3]),
        "source_revision_count": int(row[4]),
        "source_sha256s": hashes,
    }
    expected_hashes = sorted(OFFICIAL_SOURCES.values())
    complete = (
        observed["active_group_total_facts"] == EXPECTED_ACTIVE_FACTS
        and observed["state_group_rows"] == EXPECTED_STATE_GROUPS
        and observed["national_group_rows"] == EXPECTED_NATIONAL_GROUPS
        and observed["state_contests"] == 8
        and observed["source_revision_count"] == 2
        and observed["source_sha256s"] == expected_hashes
    )
    observed["status"] = (
        "COMPLETE"
        if complete
        else "EMPTY"
        if observed["active_group_total_facts"] == 0
        else "PARTIAL"
    )
    return observed


def _copy_registered_uploads(
    service: JobService, job: dict, paths: dict[str, Path]
) -> dict:
    uploads = []
    for index, (filename, source) in enumerate(paths.items(), start=1):
        destination = service.store.job_dir(job["job_id"]) / "uploads" / filename
        shutil.copy2(source, destination)
        uploads.append(
            {
                "upload_id": f"stage11-3-{index}",
                "original_name": filename,
                "stored_name": filename,
                "size_bytes": destination.stat().st_size,
                "sha256": _sha256_file(destination),
                "content_type": mimetypes.guess_type(filename)[0] or "text/csv",
            }
        )
    return service.finalise_uploads(job["job_id"], uploads)


def complete_active_release(settings: AppSettings | None = None) -> dict:
    """Create, validate, publish and activate the richer Senate group release."""

    settings = settings or AppSettings.from_environment()
    settings.ensure_directories()
    service = JobService(settings)
    lock = settings.releases_root / ".senate-group-completion.lock"
    with FileLock(str(lock)):
        base = service.capture_base_release(include_artifacts=True)
        base_database = service._resolve_portable_path(base["database_path"])
        connection = service._connect(base_database, read_only=True)
        try:
            before = assess_senate_group_completion(connection)
        finally:
            connection.close()
        if before["status"] == "COMPLETE":
            return {
                "status": "NO_CHANGE",
                "message": "The active release already contains the complete Senate group totals.",
                "active_database": service._portable_path(base_database),
                "assessment": before,
            }
        if before["status"] != "EMPTY":
            raise SenateGroupCompletionError(
                "The active release contains an incomplete Senate group transformation; "
                "automatic completion was refused."
            )

        paths = _source_paths(settings)
        registered: dict[str, list[dict]] = {}
        for filename, expected_sha256 in OFFICIAL_SOURCES.items():
            matches = service.duplicate_source_revisions(expected_sha256, base_database)
            matching = [
                item
                for item in matches
                if item.get("election_id") == ELECTION_ID
                and str(item.get("original_filename") or "").casefold()
                == filename.casefold()
            ]
            if not matching:
                raise SenateGroupCompletionError(
                    f"The registered official source revision is missing for {filename}."
                )
            registered[filename] = matching

        job = service.begin_job(
            name="Complete official 2025 Senate group totals",
            authority_id=AUTHORITY_ID,
            election_id=ELECTION_ID,
            publication_phase="final",
            operator_note=(
                "Stage 11.3 reuses the checksum-pinned source revisions registered by the "
                "original 2025 reproduction and applies the tested Stage 8 group transformers."
            ),
            requested_adapter_id="adapter_aec_2025_v1",
        )
        inspected = _copy_registered_uploads(service, job, paths)
        if inspected["state"] != "ready":
            raise SenateGroupCompletionError(
                "The two official group files did not reach the ready state: "
                f"{inspected['state']}"
            )
        keys = {
            dataset["detection"]["selection"]["dataset_key"]
            for dataset in inspected["datasets"]
        }
        if keys != {
            "senate_group_preferences_national",
            "senate_group_preferences_state",
        }:
            raise SenateGroupCompletionError(
                f"Unexpected Stage 11.3 dataset selection: {sorted(keys)}"
            )

        # The normal user-facing duplicate gate remains unchanged.  This guarded
        # command pins the working copy first, so the registered revisions can be
        # transformed without registering duplicate source bytes.
        _, copied_sha256 = service._copy_governed_database(job["job_id"])

        def copied(metadata: dict) -> None:
            metadata["execution"]["base_database_sha256"] = copied_sha256
            metadata["events"].append(
                {
                    "at": utc_now(),
                    "type": "registered_source_reprocessing_authorised",
                    "message": (
                        "Pinned a checksum-verified working copy for the guarded Stage 11.3 "
                        "registered-source transformation."
                    ),
                }
            )

        service.store.mutate(job["job_id"], copied)
        service.queue_execution(job["job_id"])
        completed = service.execute_job(job["job_id"])
        if completed["state"] != "validated":
            raise SenateGroupCompletionError(
                "The Senate group completion job did not validate: "
                + json.dumps(completed.get("validation") or completed.get("last_error"))
            )
        work_database = service.store.job_dir(job["job_id"]) / "work/database.duckdb"
        connection = service._connect(work_database, read_only=True)
        try:
            candidate = assess_senate_group_completion(connection)
        finally:
            connection.close()
        if candidate["status"] != "COMPLETE":
            raise SenateGroupCompletionError(
                "The validated working copy did not contain the expected complete group totals: "
                + json.dumps(candidate, sort_keys=True)
            )

        publication = service.publish_job(
            job["job_id"],
            approved_by="Stage 11.3 governed completion",
            snapshot_name="2025 Senate group completion",
            notes=(
                "Applied the existing Stage 8 group transformers to the two checksum-pinned "
                "official AEC source revisions already registered in the governed release."
            ),
        )
        active_database = service._resolve_portable_path(publication["database_path"])
        connection = service._connect(active_database, read_only=True)
        try:
            after = assess_senate_group_completion(connection)
        finally:
            connection.close()
        if after["status"] != "COMPLETE":
            raise SenateGroupCompletionError(
                "The newly active release failed the final Senate group assessment."
            )
        return {
            **publication,
            "status": "COMPLETED_AND_ACTIVATED",
            "completion_version": COMPLETION_VERSION,
            "base_database_sha256": base["database_sha256"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "registered_source_revisions": {
                filename: rows[0]["source_revision_id"]
                for filename, rows in registered.items()
            },
            "before": before,
            "after": after,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Complete and activate the guarded 2025 Senate group result release."
    )
    parser.parse_args()
    print(json.dumps(complete_active_release(), indent=2))


if __name__ == "__main__":
    main()
