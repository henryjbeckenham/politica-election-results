"""Stage 9.1 correction for mixed semantics in the AEC House TCP ``Swing`` column."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
from filelock import FileLock

from .app.config import AppSettings
from .app.service import JobService, _sha256_file
from .ids import deterministic_uuid, fact_id
from .tcp_measures import TcpReportedPercentage, classify_tcp_reported_percentages
from .validate import validate_database


CORRECTION_VERSION = "0.9.1"
ELECTION_ID = "election_fed_2025_05_03_general"
TCP_FILENAME = "HouseTcpByCandidateByVoteTypeDownload-31496.csv"
TCP_SHA256 = "870d7d326df7f59cf3c0638045eb0a42722f39b2b40b1b7e475d31a15bd221b7"
EXPECTED_CONTESTS = 150
EXPECTED_PERCENTAGE_ROWS = 300
EXPECTED_SWING_ROWS = 266
EXPECTED_VOTE_SHARE_ROWS = 34


class TcpMeasureCorrectionError(RuntimeError):
    """Raised when the governed release does not match the correction contract."""


def _candidate_rows(connection: duckdb.DuckDBPyConnection) -> list[dict]:
    columns = (
        "vote_result_id",
        "election_id",
        "contest_id",
        "official_contest_id",
        "contest_name",
        "election_reporting_unit_id",
        "subject_type",
        "candidacy_id",
        "candidate_name",
        "party_abbreviation",
        "ballot_group_id",
        "party_id",
        "question_option_code",
        "result_type",
        "vote_type",
        "measure_type",
        "integer_value",
        "decimal_value",
        "value_status",
        "value_basis",
        "publication_status",
        "source_revision_id",
        "source_locator",
        "import_run_id",
        "record_status",
        "total_votes",
        "source_row_hash",
    )
    rows = connection.execute(
        """
        SELECT percentage.vote_result_id,
               percentage.election_id,
               percentage.contest_id,
               contest.official_contest_id,
               contest.contest_name,
               percentage.election_reporting_unit_id,
               percentage.subject_type,
               percentage.candidacy_id,
               candidacy.ballot_name,
               candidacy.official_party_abbreviation,
               percentage.ballot_group_id,
               percentage.party_id,
               percentage.question_option_code,
               percentage.result_type,
               percentage.vote_type,
               percentage.measure_type,
               percentage.integer_value,
               percentage.decimal_value,
               percentage.value_status,
               percentage.value_basis,
               percentage.publication_status,
               percentage.source_revision_id,
               percentage.source_locator,
               percentage.import_run_id,
               percentage.record_status,
               votes.integer_value AS total_votes,
               (
                 SELECT lineage.source_row_hash
                 FROM provenance.row_lineage lineage
                 WHERE lineage.target_schema='results'
                   AND lineage.target_table='vote_result'
                   AND lineage.target_record_id=CAST(percentage.vote_result_id AS VARCHAR)
                   AND lineage.source_revision_id=percentage.source_revision_id
                 ORDER BY lineage.source_locator
                 LIMIT 1
               ) AS source_row_hash
        FROM results.vote_result percentage
        JOIN provenance.source_file_revision revision
          ON revision.source_revision_id=percentage.source_revision_id
        JOIN core.contest contest ON contest.contest_id=percentage.contest_id
        JOIN core.candidacy candidacy
          ON candidacy.candidacy_id=percentage.candidacy_id
        JOIN results.vote_result votes
          ON votes.election_id=percentage.election_id
         AND votes.contest_id=percentage.contest_id
         AND votes.candidacy_id=percentage.candidacy_id
         AND votes.result_type=percentage.result_type
         AND votes.vote_type='total'
         AND votes.measure_type='votes'
         AND votes.source_revision_id=percentage.source_revision_id
         AND votes.record_status='active'
        WHERE percentage.election_id=?
          AND percentage.result_type='tcp'
          AND percentage.vote_type='total'
          AND percentage.measure_type IN ('swing', 'vote_share')
          AND percentage.record_status='active'
          AND lower(revision.original_filename)=lower(?)
          AND revision.sha256=?
        ORDER BY percentage.contest_id, percentage.candidacy_id
        """,
        [ELECTION_ID, TCP_FILENAME, TCP_SHA256],
    ).fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def assess_tcp_measure_semantics(
    connection: duckdb.DuckDBPyConnection,
    *,
    strict_official_baseline: bool = True,
) -> dict:
    """Assess the current active TCP measures without changing the database."""

    rows = _candidate_rows(connection)
    if not rows:
        raise TcpMeasureCorrectionError(
            "The checksum-pinned official 2025 House TCP percentage rows were not found; "
            "the active release is outside the Stage 9.1 correction contract."
        )

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["source_revision_id"], row["contest_id"])].append(row)

    expected_counts: Counter[str] = Counter()
    mismatches: list[dict] = []
    contests: list[dict] = []
    for (_revision_id, contest_id), pair in sorted(grouped.items()):
        expected = classify_tcp_reported_percentages(
            (
                TcpReportedPercentage(Decimal(row["decimal_value"]), int(row["total_votes"]))
                for row in pair
            ),
            context=f"House TCP contest {contest_id}",
        )
        expected_counts[expected] += len(pair)
        contest_mismatches = []
        for row in pair:
            if row["measure_type"] != expected:
                mismatch = {
                    "vote_result_id": str(row["vote_result_id"]),
                    "contest_id": row["contest_id"],
                    "official_contest_id": row["official_contest_id"],
                    "contest_name": row["contest_name"],
                    "candidacy_id": str(row["candidacy_id"]),
                    "candidate_name": row["candidate_name"],
                    "party_abbreviation": row["party_abbreviation"],
                    "reported_value": str(row["decimal_value"]),
                    "current_measure_type": row["measure_type"],
                    "expected_measure_type": expected,
                }
                mismatches.append(mismatch)
                contest_mismatches.append(mismatch)
        if contest_mismatches:
            contests.append(
                {
                    "contest_id": contest_id,
                    "official_contest_id": pair[0]["official_contest_id"],
                    "contest_name": pair[0]["contest_name"],
                    "affected_observations": len(contest_mismatches),
                }
            )

    if strict_official_baseline:
        observed = {
            "contests": len(grouped),
            "percentage_rows": len(rows),
            "expected_swing_rows": expected_counts["swing"],
            "expected_vote_share_rows": expected_counts["vote_share"],
        }
        expected = {
            "contests": EXPECTED_CONTESTS,
            "percentage_rows": EXPECTED_PERCENTAGE_ROWS,
            "expected_swing_rows": EXPECTED_SWING_ROWS,
            "expected_vote_share_rows": EXPECTED_VOTE_SHARE_ROWS,
        }
        if observed != expected:
            raise TcpMeasureCorrectionError(
                "The official 2025 TCP population does not match the pinned correction "
                f"contract: observed {observed}; expected {expected}."
            )
        if len(mismatches) not in {0, EXPECTED_VOTE_SHARE_ROWS}:
            raise TcpMeasureCorrectionError(
                "The official 2025 TCP release is only partly corrected or otherwise differs "
                f"from the Stage 9.1 contract: found {len(mismatches)} mismatches."
            )

    return {
        "source_filename": TCP_FILENAME,
        "source_sha256": TCP_SHA256,
        "contest_count": len(grouped),
        "percentage_row_count": len(rows),
        "expected_measure_counts": dict(sorted(expected_counts.items())),
        "mismatch_count": len(mismatches),
        "affected_contest_count": len(contests),
        "affected_contests": contests,
        "mismatches": mismatches,
        "_rows": rows,
    }


def _public_assessment(assessment: dict) -> dict:
    return {key: value for key, value in assessment.items() if key != "_rows"}


def apply_tcp_measure_correction(
    connection: duckdb.DuckDBPyConnection,
    *,
    base_database_sha256: str,
    release_id: str,
    strict_official_baseline: bool = True,
) -> dict:
    """Apply the correction transaction to a disposable release copy."""

    before_active_count = connection.execute(
        "SELECT count(*) FROM results.vote_result WHERE record_status='active'"
    ).fetchone()[0]
    before_history_count = connection.execute(
        "SELECT count(*) FROM results.vote_result WHERE record_status<>'active'"
    ).fetchone()[0]
    assessment = assess_tcp_measure_semantics(
        connection,
        strict_official_baseline=strict_official_baseline,
    )
    rows_by_id = {str(row["vote_result_id"]): row for row in assessment["_rows"]}
    mismatches = assessment["mismatches"]
    if not mismatches:
        return {
            "status": "NO_CHANGE",
            "correction_version": CORRECTION_VERSION,
            "assessment": _public_assessment(assessment),
        }

    correction_token = hashlib.sha256(
        json.dumps(
            {
                "base_database_sha256": base_database_sha256,
                "old_vote_result_ids": sorted(item["vote_result_id"] for item in mismatches),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    import_run_id = deterministic_uuid("import_run", "tcp_measure_correction", correction_token)
    transform_run_id = deterministic_uuid(
        "transform_run", import_run_id, "classify_aec_tcp_percentages", CORRECTION_VERSION
    )
    validation_run_id = deterministic_uuid(
        "validation_run", import_run_id, "stage9_1_tcp_measure_correction"
    )
    now = datetime.now(timezone.utc)
    source_revisions = sorted(
        {rows_by_id[item["vote_result_id"]]["source_revision_id"] for item in mismatches}
    )

    new_facts: list[tuple] = []
    new_lineage: list[tuple] = []
    old_ids: list[str] = []
    new_ids: list[str] = []
    for mismatch in mismatches:
        old = rows_by_id[mismatch["vote_result_id"]]
        natural = (
            old["election_id"],
            old["contest_id"],
            old["election_reporting_unit_id"],
            old["subject_type"],
            old["candidacy_id"],
            old["result_type"],
            old["vote_type"],
            mismatch["expected_measure_type"],
        )
        new_id = fact_id("vote_result", natural, old["source_revision_id"])
        exists = connection.execute(
            "SELECT record_status FROM results.vote_result WHERE vote_result_id=?",
            [new_id],
        ).fetchone()
        if exists is not None:
            raise TcpMeasureCorrectionError(
                f"Corrected vote-result identifier already exists ({new_id}, {exists[0]}); "
                "the candidate release was not changed."
            )
        old_ids.append(str(old["vote_result_id"]))
        new_ids.append(str(new_id))
        new_facts.append(
            (
                new_id,
                old["election_id"],
                old["contest_id"],
                old["election_reporting_unit_id"],
                old["subject_type"],
                old["candidacy_id"],
                old["ballot_group_id"],
                old["party_id"],
                old["question_option_code"],
                old["result_type"],
                old["vote_type"],
                mismatch["expected_measure_type"],
                old["integer_value"],
                old["decimal_value"],
                old["value_status"],
                old["value_basis"],
                old["publication_status"],
                old["source_revision_id"],
                old["source_locator"],
                import_run_id,
                "active",
            )
        )
        lineage_id = fact_id(
            "row_lineage",
            ["results", "vote_result", str(new_id), old["source_locator"]],
            old["source_revision_id"],
        )
        new_lineage.append(
            (
                lineage_id,
                "results",
                "vote_result",
                str(new_id),
                old["source_revision_id"],
                old["source_locator"],
                import_run_id,
                transform_run_id,
                old["source_row_hash"],
            )
        )

    output_hash = hashlib.sha256(
        json.dumps(sorted(new_ids), separators=(",", ":")).encode("utf-8")
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
                len(mismatches),
                len(mismatches),
                f"stage9.1-{correction_token[:12]}",
                "Correct mixed semantics in the AEC House TCP Swing column.",
            ],
        )
        connection.executemany(
            "INSERT INTO provenance.import_run_input VALUES (?, ?, ?, 'corrected_source')",
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
               (?, ?, 'classify_aec_house_tcp_percentages', ?, ?, NULL, ?, NULL, NULL, 'running')""",
            [
                transform_run_id,
                import_run_id,
                CORRECTION_VERSION,
                now,
                len(mismatches),
            ],
        )
        connection.executemany(
            "UPDATE results.vote_result SET record_status='superseded' WHERE vote_result_id=? AND record_status='active'",
            [(identifier,) for identifier in old_ids],
        )
        connection.executemany(
            "INSERT INTO results.vote_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            new_facts,
        )
        connection.executemany(
            "INSERT INTO provenance.row_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            new_lineage,
        )
        after = assess_tcp_measure_semantics(
            connection,
            strict_official_baseline=strict_official_baseline,
        )
        after_active_count = connection.execute(
            "SELECT count(*) FROM results.vote_result WHERE record_status='active'"
        ).fetchone()[0]
        after_history_count = connection.execute(
            "SELECT count(*) FROM results.vote_result WHERE record_status<>'active'"
        ).fetchone()[0]
        lineage_count = connection.execute(
            """SELECT count(*) FROM provenance.row_lineage
               WHERE import_run_id=? AND target_schema='results' AND target_table='vote_result'""",
            [import_run_id],
        ).fetchone()[0]
        if after["mismatch_count"] != 0:
            raise TcpMeasureCorrectionError(
                f"Correction left {after['mismatch_count']} active TCP measure mismatches"
            )
        if after_active_count != before_active_count:
            raise TcpMeasureCorrectionError("Correction changed the active vote-result count")
        if after_history_count != before_history_count + len(mismatches):
            raise TcpMeasureCorrectionError("Correction did not preserve every superseded fact")
        if lineage_count != len(mismatches):
            raise TcpMeasureCorrectionError("Correction lineage is incomplete")

        connection.execute(
            """UPDATE provenance.transform_run
               SET completed_at=?, output_row_count=?, output_hash=?, transform_status='completed'
               WHERE transform_run_id=?""",
            [now, len(new_facts), output_hash, transform_run_id],
        )
        connection.execute(
            """UPDATE provenance.import_run
               SET completed_at=?, import_status='published', inserted_row_count=?
               WHERE import_run_id=?""",
            [now, len(new_facts), import_run_id],
        )
        connection.execute(
            """INSERT INTO audit.validation_run VALUES
               (?, ?, 'election', ?, 'stage9_1_tcp_measure_correction_v1', ?, ?, 4, 0, 0, 'passed')""",
            [validation_run_id, import_run_id, ELECTION_ID, now, now],
        )
        schema_version = connection.execute(
            "SELECT schema_version FROM control.schema_version ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO control.database_release VALUES
               (?, ?, 'validated', ?, ?, 'Politica Stage 9.1 corrective updater', ?)""",
            [
                release_id,
                schema_version,
                now,
                now,
                (
                    "Superseded 34 mislabelled House TCP swing facts and inserted 34 "
                    "source-lineaged vote-share facts; the prior immutable release is retained."
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
        "superseded_observations": len(old_ids),
        "inserted_observations": len(new_ids),
        "active_vote_result_count_before": before_active_count,
        "active_vote_result_count_after": after_active_count,
        "historical_vote_result_count_before": before_history_count,
        "historical_vote_result_count_after": after_history_count,
        "before": _public_assessment(assessment),
        "after": _public_assessment(after),
        "new_vote_result_ids_sha256": output_hash,
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
        raise TcpMeasureCorrectionError(
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
    """Create, validate, freeze and activate the Stage 9.1 corrected release."""

    settings = settings or AppSettings.from_environment()
    service = JobService(settings)
    with FileLock(str(settings.releases_root / ".tcp-measure-correction.lock")):
        base = service.capture_base_release(include_artifacts=True)
        base_database = service._resolve_portable_path(base["database_path"])
        connection = service._connect(base_database, read_only=True)
        try:
            assessment = assess_tcp_measure_semantics(connection)
        finally:
            connection.close()
        if assessment["mismatch_count"] == 0:
            return {
                "status": "NO_CHANGE",
                "message": "The active release already contains the corrected TCP measure semantics.",
                "active_database": service._portable_path(base_database),
                "assessment": _public_assessment(assessment),
            }

        base_sha256 = base["database_sha256"]
        token = base_sha256[:16]
        job_id = f"stage9_1_tcp_measure_correction_{token}"
        release_id = f"release_0_9_1_tcp_measure_correction_{token}"
        release_root = settings.releases_root / f"politica-tcp-measure-correction-{token}"
        temporary = settings.releases_root / (
            f".politica-tcp-measure-correction-{token}.tmp-{uuid.uuid4().hex}"
        )

        if release_root.exists():
            publication = _existing_publication(
                service,
                release_root,
                release_id=release_id,
            )
            service._activate_release(
                publication,
                expected_generation=base["generation"],
            )
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

            candidate = service._connect(
                candidate_database,
                external_data_root=temporary,
            )
            try:
                correction = apply_tcp_measure_correction(
                    candidate,
                    base_database_sha256=base_sha256,
                    release_id=release_id,
                )
            finally:
                candidate.close()
            if correction["status"] != "CORRECTED":
                raise TcpMeasureCorrectionError(
                    "The disposable candidate did not require the expected correction"
                )

            correction_manifest = (
                temporary / "data" / "manifests" / "tcp_measure_correction_0_9_1.json"
            )
            correction_manifest.parent.mkdir(parents=True, exist_ok=True)
            correction_manifest.write_text(
                json.dumps(correction, indent=2) + "\n",
                encoding="utf-8",
            )

            release_validation = validate_database(candidate_database, temporary)
            if release_validation["status"] != "PASS":
                raise TcpMeasureCorrectionError(
                    "The corrected release copy failed validation and was not activated: "
                    + json.dumps(release_validation["failures"], ensure_ascii=False)
                )
            release_sha256 = _sha256_file(candidate_database)
            publication = {
                "job_id": job_id,
                "release_id": release_id,
            }
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
            service._activate_release(
                publication,
                expected_generation=base["generation"],
            )
            publication["status"] = "CORRECTED_AND_ACTIVATED"
            return publication
        except Exception:
            if temporary.is_dir() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create and activate the guarded Stage 9.1 TCP-measure correction release."
    )
    parser.parse_args()
    report = correct_active_release()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
