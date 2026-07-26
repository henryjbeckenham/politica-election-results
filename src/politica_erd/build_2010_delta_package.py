"""Export the prevalidated 2010 delta as portable table shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import duckdb

from .build import PROJECT_ROOT


ELECTION_ID = "election_fed_2010_08_21_general"
SCHEMA_ORDER = (
    "control",
    "sync",
    "core",
    "geography",
    "provenance",
    "staging",
    "results",
    "count",
    "ballot",
    "audit",
    "derived",
    "publish",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_delta_tables(
    connection: duckdb.DuckDBPyConnection,
    project_root: Path = PROJECT_ROOT,
) -> dict:
    output_root = project_root / "data" / "stage14_6" / "tables"
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    rows: list[dict] = []
    available = connection.execute(
        """SELECT table_schema, table_name
           FROM information_schema.tables
           WHERE table_catalog=current_database() AND table_type='BASE TABLE'
             AND table_schema NOT IN ('information_schema','main','pg_catalog')"""
    ).fetchall()
    order = {name: index for index, name in enumerate(SCHEMA_ORDER)}
    available.sort(key=lambda item: (order.get(item[0], 999), item[0], item[1]))

    for schema, table in available:
        destination = output_root / schema / f"{table}.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        escaped = str(destination.resolve()).replace("'", "''")
        connection.execute(
            f'COPY (SELECT * FROM "{schema}"."{table}") '
            f"TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        count = connection.execute(
            f'SELECT count(*) FROM "{schema}"."{table}"'
        ).fetchone()[0]
        exported = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(destination)]
        ).fetchone()[0]
        if exported != count:
            raise RuntimeError(
                f"Delta table {schema}.{table} exported {exported:,} rows; "
                f"expected {count:,}."
            )
        rows.append(
            {
                "schema": schema,
                "table": table,
                "path": destination.relative_to(project_root).as_posix(),
                "row_count": count,
                "size_bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
        )

    manifest = {
        "format": "politica_delta_table_parquet_v1",
        "election_id": ELECTION_ID,
        "table_count": len(rows),
        "total_row_count": sum(row["row_count"] for row in rows),
        "total_size_bytes": sum(row["size_bytes"] for row in rows),
        "maximum_file_size_bytes": max(
            (row["size_bytes"] for row in rows), default=0
        ),
        "tables": rows,
    }
    core = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    manifest["manifest_sha256"] = hashlib.sha256(core).hexdigest()
    manifest_path = project_root / "data" / "manifests" / "aec_2010_delta_tables.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def export_existing_delta(
    database: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> dict:
    database = database or (
        project_root / "data" / "stage14_6" / "politica_2010_delta.duckdb"
    )
    report_path = project_root / "dist" / "stage_14_6_2010_import_report.json"
    if not report_path.is_file():
        raise RuntimeError("The reconciled 2010 import report is missing.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "PASS" or report.get("election_id") != ELECTION_ID:
        raise RuntimeError("The 2010 import has not passed; no delta will be exported.")
    blockers = [
        check
        for check in report.get("validation", {}).get("checks", [])
        if check.get("severity") == "blocker" and not check.get("passed")
    ]
    if blockers:
        raise RuntimeError("The 2010 import report contains failed blockers.")
    if not database.is_file():
        raise RuntimeError(f"The reconciled 2010 database is missing: {database}")

    connection = duckdb.connect(str(database), read_only=True)
    try:
        election_count = connection.execute(
            "SELECT count(*) FROM core.election WHERE election_id=?", [ELECTION_ID]
        ).fetchone()[0]
        if election_count != 1:
            raise RuntimeError("The delta database does not contain the 2010 election.")
        manifest = export_delta_tables(connection, project_root)
    finally:
        connection.close()

    return {
        "status": "PASS",
        "import_report": str(report_path),
        "table_manifest": manifest,
        "source_database": str(database),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    print(json.dumps(export_existing_delta(args.database), indent=2))


if __name__ == "__main__":
    main()
