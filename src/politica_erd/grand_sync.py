from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import yaml

from .db import bulk_insert


def _load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _row_hash(record: dict) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _boolean(value: str | None) -> bool | None:
    if value in (None, ""):
        return None
    normalised = str(value).strip().lower()
    if normalised in {"true", "yes", "1"}:
        return True
    if normalised in {"false", "no", "0"}:
        return False
    raise ValueError(f"Unsupported boolean value: {value!r}")


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%d/%m/%Y").date()


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _records(table_payload: dict, expected_headers: list[str]) -> list[dict]:
    values = table_payload.get("values", [])
    if not values:
        raise ValueError("Grand Database snapshot contains no rows")
    headers = values[0]
    if headers != expected_headers:
        raise ValueError(f"Grand Database header mismatch: expected {expected_headers!r}; found {headers!r}")
    records = []
    for raw_row in values[1:]:
        row = list(raw_row) + [""] * (len(headers) - len(raw_row))
        record = dict(zip(headers, row[: len(headers)], strict=True))
        if record[expected_headers[0]]:
            records.append(record)
    return records


def sync_grand_snapshot(
    connection: duckdb.DuckDBPyConnection, project_root: Path, snapshot_path: Path | None = None
) -> dict[str, int]:
    contract = _load_yaml(project_root / "config" / "grand_sync_contract.yml")
    if snapshot_path is None:
        snapshots = sorted((project_root / "data" / "snapshots").glob("grand_database_*.json"))
        if not snapshots:
            return {"people": 0, "parties": 0, "constituencies": 0}
        snapshot_path = snapshots[-1]
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    synced_at = datetime.fromisoformat(snapshot["captured_at"]).replace(tzinfo=timezone.utc)

    people = _records(snapshot["tables"]["People"], contract["tables"]["People"]["headers"])
    parties = _records(snapshot["tables"]["Parties"], contract["tables"]["Parties"]["headers"])
    constituencies = _records(
        snapshot["tables"]["Constituencies"], contract["tables"]["Constituencies"]["headers"]
    )

    connection.execute("DELETE FROM sync.person")
    bulk_insert(
        connection,
        "INSERT INTO sync.person",
        [
            (
                row["person_id"], row["full_name"], row["display_name"] or None,
                row["given_names"] or None, row["family_name"] or None, row["aliases"] or None,
                _date(row["date_of_birth"]), row["country"] or None, _boolean(row["active"]),
                row["record_status"] or None, row["audit_status"] or None, _row_hash(row), synced_at,
            )
            for row in people
        ],
    )

    connection.execute("DELETE FROM sync.party")
    bulk_insert(
        connection,
        "INSERT INTO sync.party",
        [
            (
                row["party_id"], row["party_name"], row["short_name"] or None,
                row["abbreviation"] or None, row["aliases"] or None, row["party_family"] or None,
                row["colour_hex"] or None, row["jurisdiction"] or None, row["country"] or None,
                _boolean(row["active"]), _date(row["valid_from"]), _date(row["valid_to"]),
                row["record_status"] or None, row["audit_status"] or None, _row_hash(row), synced_at,
            )
            for row in parties
        ],
    )

    connection.execute("DELETE FROM sync.constituency")
    bulk_insert(
        connection,
        "INSERT INTO sync.constituency",
        [
            (
                row["constituency_id"], row["constituency_name"], row["constituency_type"],
                row["jurisdiction"], row["chamber"] or None, row["state_territory"] or None,
                row["country"] or None, row["election_context"] or None, row["boundary_version"] or None,
                _date(row["valid_from"]), _date(row["valid_to"]), row["parent_constituency_id"] or None,
                row["aliases"] or None, row["legacy_group_id"] or None, row["source_id"] or None,
                row["source_locator"] or None, row["evidence_status"] or None,
                row["record_status"] or None, row["audit_status"] or None, _timestamp(row["audited_at"]),
                row["audited_by"] or None, row["superseded_by_constituency_id"] or None,
                row["notes"] or None, row["official_constituency_code"] or None,
                row["official_code_status"] or None, _row_hash(row), synced_at,
            )
            for row in constituencies
        ],
    )
    return {"people": len(people), "parties": len(parties), "constituencies": len(constituencies)}
