"""Build and export the prevalidated 2019 delta as portable table shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import duckdb

from .build import PROJECT_ROOT
from .import_2019 import import_2019


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
    output_root = project_root / "data" / "stage14_3" / "tables"
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
            f"COPY (SELECT * FROM \"{schema}\".\"{table}\") "
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
                f"Delta table {schema}.{table} exported {exported:,} rows; expected {count:,}."
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
    maximum = max((row["size_bytes"] for row in rows), default=0)
    manifest = {
        "format": "politica_delta_table_parquet_v1",
        "election_id": "election_fed_2019_05_18_general",
        "table_count": len(rows),
        "total_row_count": sum(row["row_count"] for row in rows),
        "total_size_bytes": sum(row["size_bytes"] for row in rows),
        "maximum_file_size_bytes": maximum,
        "tables": rows,
    }
    core = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(core).hexdigest()
    manifest_path = project_root / "data" / "manifests" / "aec_2019_delta_tables.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def build_delta_package(project_root: Path = PROJECT_ROOT) -> dict:
    database = project_root / "data" / "stage14_3" / "politica_2019_delta.duckdb"
    exported: dict = {}

    def _export_before_close(connection: duckdb.DuckDBPyConnection) -> None:
        nonlocal exported
        exported = export_delta_tables(connection, project_root)

    import_report = import_2019(
        database,
        project_root=project_root,
        rebuild=True,
        before_close=_export_before_close,
    )
    if import_report.get("status") != "PASS":
        raise RuntimeError("The 2019 import did not pass; no delta package was exported.")
    if not exported:
        raise RuntimeError("The 2019 delta tables were not exported before database close.")
    database.unlink(missing_ok=True)
    Path(str(database) + ".wal").unlink(missing_ok=True)
    return {
        "status": "PASS",
        "import_report": str(
            project_root / "dist" / "stage_14_3_2019_import_report.json"
        ),
        "table_manifest": exported,
        "monolithic_delta_removed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    print(json.dumps(build_delta_package(), indent=2))


if __name__ == "__main__":
    main()
