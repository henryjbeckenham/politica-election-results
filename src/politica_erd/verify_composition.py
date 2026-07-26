from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

import duckdb

from .app.explorer import ElectionExplorer
from .app.publication import PublicationFilters, VisualisationFeedService
from .app.service import APP_VERSION
from .build import PROJECT_ROOT
from .validate import _default_external_data_root, resolve_default_database_path


EXPECTED_STATES = {
    "ACT": 2,
    "NSW": 12,
    "NT": 2,
    "QLD": 12,
    "SA": 12,
    "TAS": 12,
    "VIC": 12,
    "WA": 12,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_composition(database: Path) -> dict:
    database = database.resolve()
    before = _sha256(database)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        election = connection.execute(
            """SELECT election_id, election_name FROM core.election
               WHERE record_status='active'
               ORDER BY election_date DESC, election_id LIMIT 1"""
        ).fetchone()
    finally:
        connection.close()
    if not election:
        raise RuntimeError("No active election is available for composition verification")
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
        },
        composition_contract_path=(
            PROJECT_ROOT / "config" / "parliament_composition_48th.yml"
        ),
    )
    representation = feeds.build(
        "senate_composition",
        PublicationFilters(election_id=election[0]),
    )
    rows = json.loads(representation.json_bytes)["data"]
    states = dict(sorted(collections.Counter(row["state"] for row in rows).items()))
    parties = dict(
        sorted(collections.Counter(row["party_abbreviation"] for row in rows).items())
    )
    if len(rows) != 76 or states != EXPECTED_STATES:
        raise RuntimeError("The governed Senate composition does not reconcile to 76 seats")
    if len({row["person_id"] for row in rows}) != 76:
        raise RuntimeError("The governed Senate composition contains duplicate people")
    after = _sha256(database)
    if after != before:
        raise RuntimeError("Composition verification changed the immutable election database")
    return {
        "status": "PASS",
        "application_version": APP_VERSION,
        "database": str(database),
        "database_sha256": before,
        "election_id": election[0],
        "election_name": election[1],
        "composition_snapshot": feeds.composition_snapshot,
        "senate_seat_count": len(rows),
        "senate_seats_by_state": states,
        "senate_seats_by_party": parties,
        "failures": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    database = args.database or resolve_default_database_path(PROJECT_ROOT)
    print(json.dumps(verify_composition(database), indent=2))


if __name__ == "__main__":
    main()
