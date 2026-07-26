"""Stage 10.1 correction for two shortened canonical People identities.

The 2025 AEC register includes middle names for Anne Maree Stanley and Luke
John Gosling, while the authoritative Grand Database People records use their
shorter public names.  This module repairs the existing immutable release from
an isolated copy and leaves the prior release untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from filelock import FileLock

from .app.config import AppSettings
from .app.service import JobService, _sha256_file
from .ids import deterministic_uuid
from .import_2025 import ReferenceMatcher
from .validate import validate_database


CORRECTION_VERSION = "1.0.1"
ELECTION_ID = "election_fed_2025_05_03_general"
TARGETS = (
    {
        "official_candidate_id": "41328",
        "contest_id": "contest_fed_2025_05_03_general_house_153",
        "contest_name": "Werriwa",
        "ballot_given_names": "Anne Maree",
        "ballot_family_name": "STANLEY",
    },
    {
        "official_candidate_id": "41012",
        "contest_id": "contest_fed_2025_05_03_general_house_307",
        "contest_name": "Solomon",
        "ballot_given_names": "Luke John",
        "ballot_family_name": "GOSLING",
    },
)


class PersonLinkCorrectionError(RuntimeError):
    """Raised when an active release does not satisfy the guarded repair contract."""


def _target_row(
    connection: duckdb.DuckDBPyConnection,
    target: dict[str, str],
) -> dict:
    rows = connection.execute(
        """SELECT CAST(ca.candidacy_id AS VARCHAR), ca.official_candidate_id,
                  ca.ballot_name, ca.ballot_given_names, ca.ballot_family_name,
                  ca.person_id, ca.match_status, c.contest_id, c.contest_name,
                  e.election_id, CAST(em.elected_member_id AS VARCHAR), em.person_id
           FROM core.candidacy ca
           JOIN core.contest c USING (contest_id)
           JOIN core.election_chamber ec USING (election_chamber_id)
           JOIN core.election e USING (election_id)
           JOIN results.contest_outcome outcome USING (candidacy_id, contest_id)
           JOIN results.elected_member em USING (contest_outcome_id, candidacy_id, contest_id)
           WHERE e.election_id=? AND c.contest_id=?
             AND ca.official_candidate_id=?
             AND outcome.outcome_type='elected' AND outcome.record_status='active'""",
        [ELECTION_ID, target["contest_id"], target["official_candidate_id"]],
    ).fetchall()
    if len(rows) != 1:
        raise PersonLinkCorrectionError(
            f"Expected one active elected candidacy for {target['contest_name']} "
            f"candidate {target['official_candidate_id']}; found {len(rows)}."
        )
    columns = (
        "candidacy_id",
        "official_candidate_id",
        "ballot_name",
        "ballot_given_names",
        "ballot_family_name",
        "candidacy_person_id",
        "match_status",
        "contest_id",
        "contest_name",
        "election_id",
        "elected_member_id",
        "elected_member_person_id",
    )
    row = dict(zip(columns, rows[0], strict=True))
    for field in (
        "contest_name",
        "ballot_given_names",
        "ballot_family_name",
    ):
        if str(row[field]).casefold() != target[field].casefold():
            raise PersonLinkCorrectionError(
                f"The guarded {target['contest_name']} candidacy has unexpected {field}: "
                f"{row[field]!r}."
            )
    return row


def assess_person_identity_links(
    connection: duckdb.DuckDBPyConnection,
) -> dict:
    """Assess the two guarded AEC-to-People identity links without writing."""

    matcher = ReferenceMatcher(connection)
    assessed: list[dict] = []
    for target in TARGETS:
        row = _target_row(connection, target)
        canonical_id, match_status = matcher.person(
            row["ballot_given_names"], row["ballot_family_name"]
        )
        if match_status != "matched" or canonical_id is None:
            raise PersonLinkCorrectionError(
                f"The authoritative People table does not uniquely identify "
                f"{row['ballot_name']} by first given name and family name "
                f"(status: {match_status})."
            )
        canonical = connection.execute(
            """SELECT full_name, display_name, given_names, family_name, source_row_hash
               FROM sync.person WHERE person_id=?""",
            [canonical_id],
        ).fetchone()
        if canonical is None:
            raise PersonLinkCorrectionError(
                f"Resolved canonical person {canonical_id} is absent from sync.person."
            )
        for field in ("candidacy_person_id", "elected_member_person_id"):
            current = row[field]
            if current is not None and str(current) != str(canonical_id):
                raise PersonLinkCorrectionError(
                    f"Refused to replace existing {field}={current!r} for "
                    f"{row['ballot_name']}; expected {canonical_id!r}."
                )
        assessed.append(
            {
                **row,
                "canonical_person_id": str(canonical_id),
                "canonical_full_name": canonical[0],
                "canonical_display_name": canonical[1],
                "canonical_given_names": canonical[2],
                "canonical_family_name": canonical[3],
                "canonical_source_row_hash": canonical[4],
                "requires_candidacy_update": row["candidacy_person_id"] is None,
                "requires_elected_member_update": row["elected_member_person_id"] is None,
            }
        )
    pending = sum(
        item["requires_candidacy_update"] or item["requires_elected_member_update"]
        for item in assessed
    )
    return {
        "status": "CORRECTION_REQUIRED" if pending else "CORRECT",
        "target_count": len(assessed),
        "pending_target_count": pending,
        "targets": assessed,
    }


def _public_assessment(assessment: dict) -> dict:
    return {
        "status": assessment["status"],
        "target_count": assessment["target_count"],
        "pending_target_count": assessment["pending_target_count"],
        "targets": [
            {
                key: value
                for key, value in item.items()
                if key != "canonical_source_row_hash"
            }
            for item in assessment["targets"]
        ],
    }


def apply_person_link_correction(
    connection: duckdb.DuckDBPyConnection,
    *,
    base_database_sha256: str,
    release_id: str,
) -> dict:
    """Repair the governed person links transactionally in a disposable copy."""

    assessment = assess_person_identity_links(connection)
    if assessment["pending_target_count"] == 0:
        return {
            "status": "NO_CHANGE",
            "correction_version": CORRECTION_VERSION,
            "assessment": _public_assessment(assessment),
        }

    mappings = sorted(
        (item["candidacy_id"], item["canonical_person_id"])
        for item in assessment["targets"]
    )
    correction_token = hashlib.sha256(
        json.dumps(
            {
                "base_database_sha256": base_database_sha256,
                "mappings": mappings,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    import_run_id = deterministic_uuid("import_run", "person_link_correction", correction_token)
    transform_run_id = deterministic_uuid(
        "transform_run", import_run_id, "link_unique_first_family_person", CORRECTION_VERSION
    )
    validation_run_id = deterministic_uuid(
        "validation_run", import_run_id, "stage10_1_person_link_correction"
    )
    source_revisions = sorted(
        {
            row[0]
            for item in assessment["targets"]
            for row in connection.execute(
                """SELECT DISTINCT source_revision_id FROM provenance.row_lineage
                   WHERE target_schema='core' AND target_table='candidacy'
                     AND target_record_id=?""",
                [item["candidacy_id"]],
            ).fetchall()
        }
    )
    now = datetime.now(timezone.utc)
    output_hash = hashlib.sha256(
        json.dumps(mappings, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            """INSERT INTO provenance.import_run VALUES
               (?, ?, 'adapter_aec_2025_v1', ?, ?, NULL, 'running', ?, ?, ?, 0, 0, ?, ?)""",
            [
                import_run_id,
                ELECTION_ID,
                CORRECTION_VERSION,
                now,
                len(source_revisions),
                len(assessment["targets"]),
                len(assessment["targets"]),
                f"stage10.1-{correction_token[:12]}",
                (
                    "Link two elected 2025 House candidacies to unique authoritative "
                    "People records using first given name plus family name."
                ),
            ],
        )
        if source_revisions:
            connection.executemany(
                "INSERT INTO provenance.import_run_input VALUES (?, ?, ?, 'identity_source')",
                [
                    (
                        deterministic_uuid("import_run_input", import_run_id, revision_id),
                        import_run_id,
                        revision_id,
                    )
                    for revision_id in source_revisions
                ],
            )
        connection.execute(
            """INSERT INTO provenance.transform_run VALUES
               (?, ?, 'link_unique_first_family_person', ?, ?, NULL, ?, NULL, NULL, 'running')""",
            [
                transform_run_id,
                import_run_id,
                CORRECTION_VERSION,
                now,
                len(assessment["targets"]),
            ],
        )
        for item in assessment["targets"]:
            connection.execute(
                """UPDATE core.candidacy SET person_id=?, match_status='matched'
                   WHERE candidacy_id=?""",
                [item["canonical_person_id"], item["candidacy_id"]],
            )
            connection.execute(
                "UPDATE results.elected_member SET person_id=? WHERE elected_member_id=?",
                [item["canonical_person_id"], item["elected_member_id"]],
            )

        after = assess_person_identity_links(connection)
        if after["pending_target_count"] != 0:
            raise PersonLinkCorrectionError("The identity correction did not resolve both targets.")
        for item in after["targets"]:
            if item["match_status"] != "matched":
                raise PersonLinkCorrectionError(
                    f"Candidacy {item['candidacy_id']} does not retain matched status."
                )
        connection.execute(
            """UPDATE provenance.transform_run
               SET completed_at=?, output_row_count=?, output_hash=?, transform_status='completed'
               WHERE transform_run_id=?""",
            [now, len(mappings), output_hash, transform_run_id],
        )
        connection.execute(
            """UPDATE provenance.import_run
               SET completed_at=?, import_status='published', inserted_row_count=?
               WHERE import_run_id=?""",
            [now, len(mappings), import_run_id],
        )
        connection.execute(
            """INSERT INTO audit.validation_run VALUES
               (?, ?, 'election', ?, 'stage10_1_person_link_correction_v1',
                ?, ?, 5, 0, 0, 'passed')""",
            [validation_run_id, import_run_id, ELECTION_ID, now, now],
        )
        schema_version = connection.execute(
            "SELECT schema_version FROM control.schema_version ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO control.database_release VALUES
               (?, ?, 'validated', ?, ?, 'Politica Stage 10.1 corrective updater', ?)""",
            [
                release_id,
                schema_version,
                now,
                now,
                (
                    "Linked Anne Maree Stanley and Luke John Gosling to their unique "
                    "authoritative People records; the prior immutable release is retained."
                ),
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("CHECKPOINT")

    return {
        "status": "CORRECTED",
        "correction_version": CORRECTION_VERSION,
        "release_id": release_id,
        "base_database_sha256": base_database_sha256,
        "import_run_id": str(import_run_id),
        "transform_run_id": str(transform_run_id),
        "validation_run_id": str(validation_run_id),
        "corrected_target_count": len(mappings),
        "person_mappings_sha256": output_hash,
        "before": _public_assessment(assessment),
        "after": _public_assessment(after),
    }


def _existing_publication(
    service: JobService,
    release_root: Path,
    *,
    release_id: str,
) -> dict:
    manifest_path = release_root / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("release_id") != release_id:
        raise PersonLinkCorrectionError(
            f"An unrelated release already occupies {release_root}; no activation was attempted."
        )
    database = release_root / manifest["database_path"]
    return {
        "job_id": manifest["job_id"],
        "release_id": release_id,
        "release_root": service._portable_path(release_root),
        "database_path": service._portable_path(database),
        "release_manifest_path": service._portable_path(manifest_path),
        "database_size_bytes": database.stat().st_size,
        "database_sha256": manifest["database_sha256"],
        "artifact_file_count": manifest["artifact_file_count"],
        "artifact_size_bytes": manifest["artifact_size_bytes"],
        "artifact_manifest_sha256": manifest["artifact_manifest_sha256"],
        "release_manifest_sha256": _sha256_file(manifest_path),
        "release_validation": manifest["validation"],
        "activated_at": datetime.now(timezone.utc).isoformat(),
    }


def correct_active_release(settings: AppSettings | None = None) -> dict:
    """Create, validate, freeze and activate the Stage 10.1 corrected release."""

    settings = settings or AppSettings.from_environment()
    service = JobService(settings)
    with FileLock(str(settings.releases_root / ".person-link-correction.lock")):
        base = service.capture_base_release(include_artifacts=True)
        base_database = service._resolve_portable_path(base["database_path"])
        connection = service._connect(base_database, read_only=True)
        try:
            assessment = assess_person_identity_links(connection)
        finally:
            connection.close()
        if assessment["pending_target_count"] == 0:
            return {
                "status": "NO_CHANGE",
                "message": "The active release already contains both corrected person links.",
                "active_database": service._portable_path(base_database),
                "assessment": _public_assessment(assessment),
            }

        base_sha256 = base["database_sha256"]
        mapping_basis = sorted(
            (item["candidacy_id"], item["canonical_person_id"])
            for item in assessment["targets"]
        )
        token = hashlib.sha256(
            json.dumps(
                {"base_database_sha256": base_sha256, "mappings": mapping_basis},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        job_id = f"stage10_1_person_link_correction_{token}"
        release_id = f"release_1_0_1_person_link_correction_{token}"
        release_root = settings.releases_root / f"politica-person-link-correction-{token}"
        temporary = settings.releases_root / (
            f".politica-person-link-correction-{token}.tmp-{uuid.uuid4().hex}"
        )

        if release_root.exists():
            publication = _existing_publication(service, release_root, release_id=release_id)
            service._activate_release(publication, expected_generation=base["generation"])
            publication["status"] = "RECOVERED_AND_ACTIVATED"
            return publication

        try:
            temporary.mkdir(parents=False)
            candidate_database = (
                temporary / "data" / "database" / "politica_election_results.duckdb"
            )
            candidate_database.parent.mkdir(parents=True)
            shutil.copy2(base_database, candidate_database)
            artifact_source = service._resolve_portable_path(base["artifact_root"])
            service._copy_release_artifacts(
                source_root=artifact_source,
                release_root=temporary,
                database=candidate_database,
                expected_artifacts=base.get("artifact_files"),
            )
            candidate = service._connect(candidate_database, external_data_root=temporary)
            try:
                correction = apply_person_link_correction(
                    candidate,
                    base_database_sha256=base_sha256,
                    release_id=release_id,
                )
            finally:
                candidate.close()
            if correction["status"] != "CORRECTED":
                raise PersonLinkCorrectionError(
                    "The disposable candidate did not require the expected correction."
                )

            correction_manifest = (
                temporary / "data" / "manifests" / "person_link_correction_1_0_1.json"
            )
            correction_manifest.parent.mkdir(parents=True, exist_ok=True)
            correction_manifest.write_text(
                json.dumps(correction, indent=2) + "\n",
                encoding="utf-8",
            )
            release_validation = validate_database(candidate_database, temporary)
            if release_validation["status"] != "PASS":
                raise PersonLinkCorrectionError(
                    "The corrected release copy failed validation and was not activated: "
                    + json.dumps(release_validation["failures"], ensure_ascii=False)
                )
            release_sha256 = _sha256_file(candidate_database)
            publication = {"job_id": job_id, "release_id": release_id}
            release_manifest = service._write_release_manifest(
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
                    "release_root": service._portable_path(release_root),
                    "database_path": service._portable_path(release_database),
                    "release_manifest_path": service._portable_path(release_manifest_path),
                    "database_size_bytes": release_database.stat().st_size,
                    "database_sha256": release_sha256,
                    "artifact_file_count": release_manifest["artifact_file_count"],
                    "artifact_size_bytes": release_manifest["artifact_size_bytes"],
                    "artifact_manifest_sha256": release_manifest[
                        "artifact_manifest_sha256"
                    ],
                    "release_manifest_sha256": _sha256_file(release_manifest_path),
                    "release_validation": release_validation,
                    "activated_at": datetime.now(timezone.utc).isoformat(),
                    "correction": correction,
                }
            )
            service._activate_release(publication, expected_generation=base["generation"])
            publication["status"] = "CORRECTED_AND_ACTIVATED"
            return publication
        except Exception:
            if temporary.is_dir() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create and activate the guarded Stage 10.1 person-link correction release."
    )
    parser.parse_args()
    print(json.dumps(correct_active_release(), indent=2))


if __name__ == "__main__":
    main()
