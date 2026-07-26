from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

from .app.explorer import ElectionExplorer
from .app.publication import PublicationFilters, VisualisationFeedService
from .app.service import APP_VERSION
from .build import PROJECT_ROOT
from .validate import _default_external_data_root, resolve_default_database_path


STATES = ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_senate_group_publication(database: Path) -> dict:
    database = database.resolve()
    before = _sha256(database)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        schema = connection.execute(
            "SELECT schema_version FROM control.schema_version ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
        election = connection.execute(
            """SELECT election_id, election_name FROM core.election
               WHERE record_status='active'
               ORDER BY election_date DESC, election_id LIMIT 1"""
        ).fetchone()
    finally:
        connection.close()
    if not election:
        raise RuntimeError("No active election is available for publication verification")

    explorer = ElectionExplorer(
        lambda: database,
        lambda path: _default_external_data_root(path),
        app_version=APP_VERSION,
    )
    feeds = VisualisationFeedService(
        explorer,
        lambda: {
            "release_id": "verified-active-release",
            "database_sha256": before,
            "application_version": APP_VERSION,
            "schema_version": schema[0] if schema else None,
        },
        composition_contract_path=(
            PROJECT_ROOT / "config" / "parliament_composition_48th.yml"
        ),
    )

    state_counts: dict[str, int] = {}
    source_types: set[str] = set()
    for state in STATES:
        representation = feeds.build(
            "senate_group_results",
            PublicationFilters(election_id=election[0], state=state),
        )
        document = json.loads(representation.json_bytes)
        rows = document["data"]
        if not rows:
            raise RuntimeError(f"The Senate group publication remains empty for {state}")
        if any(row.get("state") != state for row in rows):
            raise RuntimeError(f"The Senate group publication returned a wrong-state row for {state}")
        if any(row.get("result_type") != "group_total" for row in rows):
            raise RuntimeError(f"The Senate group publication contract is inconsistent for {state}")
        if sum(int(row.get("votes") or 0) for row in rows) <= 0:
            raise RuntimeError(f"The Senate group publication has no positive votes for {state}")
        state_counts[state] = len(rows)
        source_types.update(str(row.get("subject_type") or "unknown") for row in rows)

    after = _sha256(database)
    if after != before:
        raise RuntimeError("Publication verification changed the immutable database")
    return {
        "status": "PASS",
        "application_version": APP_VERSION,
        "schema_version": schema[0] if schema else None,
        "database": str(database),
        "database_sha256": before,
        "election_id": election[0],
        "election_name": election[1],
        "senate_group_rows_by_state": state_counts,
        "published_subject_types": sorted(source_types),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    database = args.database or resolve_default_database_path(PROJECT_ROOT)
    print(json.dumps(verify_senate_group_publication(database), indent=2))


if __name__ == "__main__":
    main()
