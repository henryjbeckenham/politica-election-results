from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

from .app.config import PROJECT_ROOT
from .grand_sync import sync_grand_snapshot
from .db import bulk_insert


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def apply_migrations(connection: duckdb.DuckDBPyConnection, project_root: Path) -> list[dict]:
    applied: list[dict] = []
    for path in sorted((project_root / "schema").glob("*.sql")):
        sql_bytes = path.read_bytes()
        checksum = sha256_bytes(sql_bytes)
        connection.execute(sql_bytes.decode("utf-8"))
        applied.append({"migration": path.name, "sha256": checksum})
    return applied


def seed_controlled_values(connection: duckdb.DuckDBPyConnection, project_root: Path) -> int:
    values = load_yaml(project_root / "config" / "controlled_values.yml")
    rows: list[tuple] = []
    for set_name, entries in values.items():
        for index, (code, description) in enumerate(entries, start=1):
            rows.append((set_name, code, code.replace("_", " ").title(), description, index, None, None, True))
    connection.execute("DELETE FROM control.controlled_value")
    bulk_insert(
        connection,
        """INSERT INTO control.controlled_value
        (value_set_name, value_code, display_name, description, sort_order, valid_from, valid_to, active)""",
        rows,
    )
    return len(rows)


def seed_adapter_registry(connection: duckdb.DuckDBPyConnection, project_root: Path) -> int:
    rows = []
    for path in sorted((project_root / "config" / "adapters").glob("*.yml")):
        config = load_yaml(path)
        signature = sha256_bytes(path.read_bytes())
        rows.append(
            (
                config["adapter_id"],
                config["authority_id"],
                str(config["adapter_version"]),
                str(path.relative_to(project_root)),
                "0.2.0",
                config.get("status", "foundation"),
                signature,
                True,
            )
        )
    connection.execute("DELETE FROM control.adapter_registry")
    bulk_insert(
        connection,
        """INSERT INTO control.adapter_registry
        (adapter_id, authority_id, adapter_version, config_path, code_version, status,
         schema_signature_sha256, active)""",
        rows,
    )
    return len(rows)


def seed_relationship_contract(
    connection: duckdb.DuckDBPyConnection, project_root: Path, schema_version: str
) -> int:
    config = load_yaml(project_root / "config" / "relationship_contract.yml")
    targets = {**config.get("fields", {}), **config.get("aliases", {})}
    excluded = set(config.get("excluded_tables", []))
    columns = connection.execute(
        """
        SELECT table_schema, table_name, column_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema NOT IN ('information_schema', 'main', 'pg_catalog')
        ORDER BY table_schema, table_name, ordinal_position
        """
    ).fetchall()
    rows = []
    for child_schema, child_table, child_field, is_nullable in columns:
        target = targets.get(child_field)
        if target is None or f"{child_schema}.{child_table}" in excluded:
            continue
        parent_schema, parent_table, parent_field = target
        if (child_schema, child_table, child_field) == (parent_schema, parent_table, parent_field):
            continue
        relationship_id = f"rel_{child_schema}_{child_table}_{child_field}"
        rows.append(
            (
                relationship_id,
                child_schema,
                child_table,
                child_field,
                parent_schema,
                parent_table,
                parent_field,
                is_nullable == "NO",
                "logical_foreign_key",
                schema_version,
                True,
            )
        )
    connection.execute("DELETE FROM control.relationship_contract")
    bulk_insert(
        connection,
        "INSERT INTO control.relationship_contract",
        rows,
    )
    return len(rows)


def refresh_data_dictionary(connection: duckdb.DuckDBPyConnection, schema_version: str) -> int:
    connection.execute("DELETE FROM control.data_dictionary")
    columns = connection.execute(
        """
        SELECT table_schema, table_name, column_name, ordinal_position, data_type,
               is_nullable = 'NO' AS required
        FROM information_schema.columns
        WHERE table_schema NOT IN ('information_schema', 'main', 'pg_catalog')
          AND NOT (table_schema = 'control' AND table_name = 'data_dictionary')
        ORDER BY table_schema, table_name, ordinal_position
        """
    ).fetchall()
    pk_columns = {
        (row[0], row[1], row[2])
        for row in connection.execute(
            """
            SELECT schema_name, table_name, column_name
            FROM duckdb_constraints(), unnest(constraint_column_names) AS t(column_name)
            WHERE constraint_type = 'PRIMARY KEY'
            """
        ).fetchall()
    }
    relationships = {
        (row[0], row[1], row[2]): (row[3], row[4], row[5])
        for row in connection.execute(
            """
            SELECT child_schema, child_table, child_field,
                   parent_schema, parent_table, parent_field
            FROM control.relationship_contract
            WHERE active
            """
        ).fetchall()
    }
    rows = []
    for schema, table, field, order, field_type, required in columns:
        relationship = relationships.get((schema, table, field))
        rows.append(
            (
                schema,
                table,
                field,
                order,
                field_type,
                required,
                (schema, table, field) in pk_columns,
                relationship is not None,
                relationship[0] if relationship else None,
                relationship[1] if relationship else None,
                relationship[2] if relationship else None,
                None,
                f"Field {field} in {schema}.{table}.",
                "Blank is permitted only when the field is nullable and the source/value status explains absence.",
                None,
                schema_version,
                None,
                True,
            )
        )
    bulk_insert(
        connection,
        "INSERT INTO control.data_dictionary",
        rows,
    )
    return len(rows)


def export_catalogues(connection: duckdb.DuckDBPyConnection, project_root: Path) -> None:
    docs = project_root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    exports = {
        "data_dictionary.csv": "SELECT * FROM control.data_dictionary ORDER BY table_schema, table_name, field_order",
        "controlled_values.csv": "SELECT * FROM control.controlled_value ORDER BY value_set_name, sort_order",
        "table_catalogue.csv": """
            SELECT table_schema, table_name,
                   count(*) AS field_count,
                   string_agg(column_name, ', ' ORDER BY ordinal_position) AS fields
            FROM information_schema.columns
            WHERE table_schema NOT IN ('information_schema', 'main', 'pg_catalog')
            GROUP BY table_schema, table_name
            ORDER BY table_schema, table_name
        """,
    }
    for filename, query in exports.items():
        cursor = connection.execute(query)
        with (docs / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([item[0] for item in cursor.description])
            writer.writerows(cursor.fetchall())


def build(database_path: Path, project_root: Path = PROJECT_ROOT) -> dict:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    config = load_yaml(project_root / "config" / "database.yml")
    schema_version = str(config["database"]["schema_version"])
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("SET TimeZone = 'Australia/Sydney'")
        migrations = apply_migrations(connection, project_root)
        migration_hash = sha256_bytes("".join(item["sha256"] for item in migrations).encode())
        connection.execute("DELETE FROM control.schema_version WHERE schema_version = ? OR migration_id = ?", [schema_version, "stage_1_initial"])
        connection.execute(
            """
            INSERT INTO control.schema_version
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [schema_version, "stage_1_initial", datetime.now(timezone.utc), migration_hash, False, "Initial governed empty schema"],
        )
        controlled_value_count = seed_controlled_values(connection, project_root)
        adapter_count = seed_adapter_registry(connection, project_root)
        relationship_count = seed_relationship_contract(connection, project_root, schema_version)
        grand_sync_counts = sync_grand_snapshot(connection, project_root)
        dictionary_count = refresh_data_dictionary(connection, schema_version)
        release_id = f"release_{schema_version.replace('.', '_')}_foundation"
        connection.execute(
            """
            INSERT OR REPLACE INTO control.database_release
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [release_id, schema_version, "development", datetime.now(timezone.utc), None, "Codex", "Governed foundation build; election facts are loaded only by an explicit adapter run"],
        )
        export_catalogues(connection, project_root)
        table_count = connection.execute(
            """SELECT count(*) FROM information_schema.tables
               WHERE table_schema NOT IN ('information_schema', 'main', 'pg_catalog') AND table_type='BASE TABLE'"""
        ).fetchone()[0]
        election_count = connection.execute("SELECT count(*) FROM core.election").fetchone()[0]
        manifest = {
            "database": config["database"]["name"],
            "schema_version": schema_version,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "database_path": str(
                database_path.relative_to(project_root)
                if database_path.is_relative_to(project_root)
                else database_path
            ),
            "database_sha256": None,
            "migration_hash": migration_hash,
            "migrations": migrations,
            "table_count": table_count,
            "data_dictionary_field_count": dictionary_count,
            "controlled_value_count": controlled_value_count,
            "adapter_count": adapter_count,
            "relationship_count": relationship_count,
            "grand_sync_counts": grand_sync_counts,
            "election_count": election_count,
            "stage_1_empty_schema": election_count == 0,
        }
    finally:
        connection.close()
    manifest["database_sha256"] = sha256_bytes(database_path.read_bytes())
    default_database_path = project_root / config["database"]["default_path"]
    if database_path.resolve() == default_database_path.resolve():
        dist = project_root / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "build_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data/database/politica_election_results.duckdb")
    args = parser.parse_args()
    manifest = build(args.database)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
