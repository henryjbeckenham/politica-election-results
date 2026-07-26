from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

from .build import PROJECT_ROOT

REQUIRED_SCHEMAS = {
    "control", "sync", "core", "geography", "results", "count", "ballot",
    "provenance", "audit", "derived", "publish", "staging",
}

REQUIRED_TABLES = {
    "control.schema_version",
    "control.data_dictionary",
    "control.controlled_value",
    "control.relationship_contract",
    "sync.person",
    "sync.party",
    "sync.constituency",
    "core.election",
    "core.contest",
    "core.candidacy",
    "geography.election_reporting_unit",
    "results.participation_result",
    "results.vote_result",
    "count.count_round",
    "count.preference_transfer",
    "ballot.ballot",
    "ballot.ballot_preference",
    "provenance.source_file_revision",
    "provenance.row_lineage",
    "audit.validation_issue",
    "publish.publication_snapshot",
}

BASE_DATABASE_RELATIVE = Path("data/database/politica_election_results.duckdb")
ACTIVE_RELEASE_RELATIVE = Path("data/app/releases/active.json")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_default_database_path(project_root: Path = PROJECT_ROOT) -> Path:
    """Resolve the active immutable release, falling back only when no pointer exists."""

    root = project_root.resolve()
    pointer_path = root / ACTIVE_RELEASE_RELATIVE
    if not pointer_path.is_file():
        return root / BASE_DATABASE_RELATIVE
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Active release pointer cannot be read: {pointer_path}") from exc
    path_base = pointer.get("path_base", "project_root")
    value = pointer.get("database_path")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("Active release pointer has no database_path")
    configured = Path(value)
    if path_base == "project_root":
        base = root
    elif path_base == "releases_root":
        base = pointer_path.parent.resolve()
    elif path_base == "absolute":
        if not configured.is_absolute():
            raise RuntimeError("Absolute active release pointer contains a relative database_path")
        base = None
    else:
        raise RuntimeError("Active release pointer has an unsupported path_base")
    if base is not None:
        if configured.is_absolute():
            raise RuntimeError("Relative active release pointer contains an absolute database_path")
        database = (base / configured).resolve()
        try:
            database.relative_to(base)
        except ValueError as exc:
            raise RuntimeError("Active release database_path escapes its declared path base") from exc
    else:
        database = configured.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Active release database does not exist: {database}")
    expected_sha256 = pointer.get("sha256")
    if expected_sha256 and _sha256_file(database) != expected_sha256:
        raise RuntimeError("Active release database does not match its recorded SHA-256")
    return database


def _default_external_data_root(database_path: Path) -> Path:
    """Infer the root used by portable relative Parquet-backed views."""

    resolved = database_path.resolve()
    if resolved.parent.name == "database" and resolved.parent.parent.name == "data":
        return resolved.parent.parent.parent
    return PROJECT_ROOT.resolve()


def validate_database(
    database_path: Path, external_data_root: Path | None = None
) -> dict:
    resolved_database = database_path.resolve()
    resolved_external_root = (
        external_data_root.resolve()
        if external_data_root is not None
        else _default_external_data_root(resolved_database)
    )
    try:
        database_label = resolved_database.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        database_label = str(resolved_database)
    try:
        external_root_label = resolved_external_root.relative_to(
            PROJECT_ROOT.resolve()
        ).as_posix() or "."
    except ValueError:
        external_root_label = str(resolved_external_root)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        escaped_external_root = str(resolved_external_root).replace("'", "''")
        connection.execute(f"SET file_search_path='{escaped_external_root}'")
        schemas = {row[0] for row in connection.execute("SELECT schema_name FROM information_schema.schemata").fetchall()}
        tables = {
            f"{row[0]}.{row[1]}"
            for row in connection.execute(
                """SELECT table_schema, table_name FROM information_schema.tables
                   WHERE table_type IN ('BASE TABLE', 'VIEW')"""
            ).fetchall()
        }
        failures = []
        missing_schemas = sorted(REQUIRED_SCHEMAS - schemas)
        missing_tables = sorted(REQUIRED_TABLES - tables)
        if missing_schemas:
            failures.append({"check": "required_schemas", "missing": missing_schemas})
        if missing_tables:
            failures.append({"check": "required_tables", "missing": missing_tables})
        controlled_value_count = connection.execute("SELECT count(*) FROM control.controlled_value").fetchone()[0]
        if controlled_value_count < 80:
            failures.append({"check": "controlled_values", "observed": controlled_value_count, "minimum": 80})
        dictionary_count = connection.execute("SELECT count(*) FROM control.data_dictionary").fetchone()[0]
        relationship_count = connection.execute("SELECT count(*) FROM control.relationship_contract WHERE active").fetchone()[0]
        if relationship_count < 40:
            failures.append({"check": "logical_relationship_contract", "observed": relationship_count, "minimum": 40})
        missing_relationship_targets = connection.execute(
            """
            SELECT count(*)
            FROM control.relationship_contract r
            LEFT JOIN information_schema.columns child
              ON child.table_schema = r.child_schema
             AND child.table_name = r.child_table
             AND child.column_name = r.child_field
            LEFT JOIN information_schema.columns parent
              ON parent.table_schema = r.parent_schema
             AND parent.table_name = r.parent_table
             AND parent.column_name = r.parent_field
            WHERE r.active AND (child.column_name IS NULL OR parent.column_name IS NULL)
            """
        ).fetchone()[0]
        if missing_relationship_targets:
            failures.append({"check": "logical_relationship_targets", "missing": missing_relationship_targets})
        physical_column_count = connection.execute(
            """SELECT count(*) FROM information_schema.columns
               WHERE table_schema NOT IN ('information_schema', 'main', 'pg_catalog')
                 AND NOT (table_schema='control' AND table_name='data_dictionary')"""
        ).fetchone()[0]
        if dictionary_count != physical_column_count:
            failures.append({"check": "data_dictionary_parity", "dictionary": dictionary_count, "physical": physical_column_count})
        election_count = connection.execute(
            "SELECT count(*) FROM core.election WHERE record_status='active'"
        ).fetchone()[0]
        result_count = connection.execute(
            "SELECT count(*) FROM results.vote_result WHERE record_status='active'"
        ).fetchone()[0]
        historical_result_count = connection.execute(
            "SELECT count(*) FROM results.vote_result WHERE record_status<>'active'"
        ).fetchone()[0]
        invalid_tcp_percentage_groups = connection.execute(
            """
            WITH reported AS (
              SELECT percentage.election_id,
                     percentage.contest_id,
                     percentage.source_revision_id,
                     percentage.candidacy_id,
                     percentage.measure_type,
                     percentage.decimal_value AS reported_value,
                     votes.integer_value AS total_votes
              FROM results.vote_result percentage
              JOIN results.vote_result votes
                ON votes.election_id=percentage.election_id
               AND votes.contest_id=percentage.contest_id
               AND votes.candidacy_id=percentage.candidacy_id
               AND votes.result_type=percentage.result_type
               AND votes.vote_type='total'
               AND votes.measure_type='votes'
               AND votes.source_revision_id=percentage.source_revision_id
               AND votes.record_status='active'
              WHERE percentage.result_type='tcp'
                AND percentage.vote_type='total'
                AND percentage.measure_type IN ('swing', 'vote_share')
                AND percentage.record_status='active'
            ), enriched AS (
              SELECT *,
                     sum(total_votes) OVER (
                       PARTITION BY election_id, contest_id, source_revision_id
                     ) AS contest_votes
              FROM reported
            ), grouped AS (
              SELECT election_id, contest_id, source_revision_id,
                     count(*) AS observation_count,
                     sum(reported_value) AS reported_total,
                     count(*) FILTER (WHERE measure_type='swing') AS swing_count,
                     count(*) FILTER (WHERE measure_type='vote_share') AS share_count,
                     max(
                       abs(
                         reported_value
                         - CASE WHEN contest_votes=0 THEN 0
                                ELSE total_votes * 100.0 / contest_votes END
                       )
                     ) AS maximum_share_error
              FROM enriched
              GROUP BY election_id, contest_id, source_revision_id
            )
            SELECT count(*) FROM grouped
            WHERE observation_count<>2
               OR (
                    abs(reported_total)<=0.02
                    AND swing_count<>2
                  )
               OR (
                    abs(reported_total-100)<=0.02
                    AND (share_count<>2 OR maximum_share_error>0.011)
                  )
               OR (
                    abs(reported_total)>0.02
                    AND abs(reported_total-100)>0.02
                  )
            """
        ).fetchone()[0]
        if invalid_tcp_percentage_groups:
            failures.append(
                {
                    "check": "tcp_percentage_semantics",
                    "invalid_contest_source_groups": invalid_tcp_percentage_groups,
                    "expected": 0,
                }
            )
        grand_sync_counts = {
            "people": connection.execute("SELECT count(*) FROM sync.person").fetchone()[0],
            "parties": connection.execute("SELECT count(*) FROM sync.party").fetchone()[0],
            "constituencies": connection.execute("SELECT count(*) FROM sync.constituency").fetchone()[0],
        }
        minimum_sync_counts = {"people": 1, "parties": 1, "constituencies": 1}
        if any(grand_sync_counts[key] < minimum for key, minimum in minimum_sync_counts.items()):
            failures.append({"check": "grand_reference_sync", "observed": grand_sync_counts})
        stage_2_election_id = "election_fed_2025_05_03_general"
        stage_2_election_present = connection.execute(
            "SELECT count(*) FROM core.election WHERE election_id=? AND record_status='active'",
            [stage_2_election_id],
        ).fetchone()[0] == 1
        if election_count == 0:
            stage = "foundation"
            if result_count != 0:
                failures.append({"check": "foundation_empty", "elections": election_count, "vote_results": result_count})
        else:
            stage = (
                "stage_2_2025_federal"
                if election_count == 1 and stage_2_election_present
                else "governed_elections"
            )
        if stage_2_election_present:
            baseline_group_facts = connection.execute(
                """SELECT count(*) FROM results.vote_result result
                   JOIN provenance.source_file_revision revision
                     ON revision.source_revision_id=result.source_revision_id
                   WHERE lower(revision.original_filename)=
                         'senatefirstprefsbystatebygroupbyvotetypedownload-31496.csv'
                     AND revision.revision_number=1
                     AND NOT (
                       result.result_type='group_total'
                       AND result.subject_type='source_group'
                     )"""
            ).fetchone()[0]
            active_group_facts = connection.execute(
                """SELECT count(*) FROM results.vote_result result
                   JOIN provenance.source_file_revision revision
                     ON revision.source_revision_id=result.source_revision_id
                   WHERE lower(revision.original_filename) IN (
                     'senatefirstprefsbygroupbyvotetypedownload-31496.csv',
                     'senatefirstprefsbystatebygroupbyvotetypedownload-31496.csv'
                   ) AND result.record_status='active'
                     AND revision.record_status='active'"""
            ).fetchone()[0]
            baseline_senate_count_totals = connection.execute(
                """SELECT count(*) FROM "count".count_candidate_total total
                   JOIN "count".count_round round USING (count_round_id)
                   JOIN provenance.source_file_revision revision
                     ON revision.source_revision_id=round.source_revision_id
                   WHERE lower(revision.original_filename)='senatedopdownload-31496.zip'
                     AND revision.revision_number=1"""
            ).fetchone()[0]
            active_senate_count_totals = connection.execute(
                """SELECT count(*) FROM "count".count_candidate_total total
                   JOIN "count".count_round round USING (count_round_id)
                   JOIN provenance.source_file_revision revision
                     ON revision.source_revision_id=round.source_revision_id
                   WHERE lower(revision.original_filename)='senatedopdownload-31496.zip'
                     AND revision.record_status='active'"""
            ).fetchone()[0]
            active_ballot_metadata_total = connection.execute(
                """SELECT coalesce(sum(dataset.row_count), 0)
                   FROM ballot.ballot_dataset dataset
                   JOIN core.election_chamber chamber USING (election_chamber_id)
                   WHERE chamber.election_id=? AND dataset.record_status='active'""",
                [stage_2_election_id],
            ).fetchone()[0]
            stage_2_expected = {
                "contests": 158,
                "candidacies": 1456,
                "vote_results": 213328 - baseline_group_facts + active_group_facts,
                "count_candidate_totals": (
                    72687 - baseline_senate_count_totals + active_senate_count_totals
                ),
                "outcomes": 190,
                "ballot_datasets": 8,
                "formal_ballots": int(active_ballot_metadata_total),
            }
            stage_2_observed = {
                "contests": connection.execute(
                    """SELECT count(*) FROM core.contest contest
                       JOIN core.election_chamber chamber USING (election_chamber_id)
                       WHERE chamber.election_id=? AND contest.record_status='active'""",
                    [stage_2_election_id],
                ).fetchone()[0],
                "candidacies": connection.execute(
                    """SELECT count(*) FROM core.candidacy candidacy
                       JOIN core.contest contest USING (contest_id)
                       JOIN core.election_chamber chamber USING (election_chamber_id)
                       WHERE chamber.election_id=? AND candidacy.record_status='active'
                         AND contest.record_status='active'""",
                    [stage_2_election_id],
                ).fetchone()[0],
                "vote_results": connection.execute(
                    """SELECT count(*) FROM results.vote_result
                       WHERE election_id=? AND record_status='active'""",
                    [stage_2_election_id],
                ).fetchone()[0],
                "count_candidate_totals": connection.execute(
                    """SELECT count(*) FROM "count".count_candidate_total total
                       JOIN "count".count_round round USING (count_round_id)
                       JOIN provenance.source_file_revision revision
                         ON revision.source_revision_id=round.source_revision_id
                       JOIN core.contest contest USING (contest_id)
                       JOIN core.election_chamber chamber USING (election_chamber_id)
                       WHERE chamber.election_id=? AND contest.record_status='active'
                         AND revision.record_status='active'""",
                    [stage_2_election_id],
                ).fetchone()[0],
                "outcomes": connection.execute(
                    """SELECT count(*) FROM results.contest_outcome outcome
                       JOIN core.contest contest USING (contest_id)
                       JOIN core.election_chamber chamber USING (election_chamber_id)
                       WHERE chamber.election_id=? AND outcome.record_status='active'
                         AND contest.record_status='active'""",
                    [stage_2_election_id],
                ).fetchone()[0],
                "ballot_datasets": connection.execute(
                    """SELECT count(*) FROM ballot.ballot_dataset dataset
                       JOIN core.election_chamber chamber USING (election_chamber_id)
                       WHERE chamber.election_id=? AND dataset.record_status='active'""",
                    [stage_2_election_id],
                ).fetchone()[0],
                "formal_ballots": connection.execute(
                    """SELECT count(*) FROM ballot.ballot ballot
                       JOIN ballot.ballot_dataset dataset USING (ballot_dataset_id)
                       JOIN core.election_chamber chamber USING (election_chamber_id)
                       WHERE chamber.election_id=? AND dataset.record_status='active'""",
                    [stage_2_election_id],
                ).fetchone()[0],
            }
            if stage_2_observed != stage_2_expected:
                failures.append(
                    {
                        "check": "stage_2_expected_counts",
                        "observed": stage_2_observed,
                        "expected": stage_2_expected,
                    }
                )
            validation = connection.execute(
                "SELECT validation_status, blocker_count FROM audit.validation_run ORDER BY completed_at DESC NULLS LAST LIMIT 1"
            ).fetchone()
            if validation != ("passed", 0):
                failures.append({"check": "stage_2_reconciliation", "observed": validation})
        grand_write_paths = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='sync' AND table_name='result'"
        ).fetchone()[0]
        if grand_write_paths:
            failures.append({"check": "no_polling_results_reuse"})
        return {
            "status": "PASS" if not failures else "FAIL",
            "database": database_label,
            "external_data_root": external_root_label,
            "stage": stage,
            "schema_count": len(REQUIRED_SCHEMAS),
            "table_count": len(tables),
            "controlled_value_count": controlled_value_count,
            "relationship_count": relationship_count,
            "data_dictionary_field_count": dictionary_count,
            "election_count": election_count,
            "vote_result_count": result_count,
            "superseded_vote_result_count": historical_result_count,
            "grand_sync_counts": grand_sync_counts,
            "failures": failures,
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="Database to validate; defaults to the active immutable release pointer.",
    )
    args = parser.parse_args()
    database = args.database or resolve_default_database_path()
    report = validate_database(database)
    output = PROJECT_ROOT / "dist" / "validation_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
