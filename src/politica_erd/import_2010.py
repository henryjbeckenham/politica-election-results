from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable

import duckdb

from .aec import SourceRow, decimal_text, integer, normalise_label, source_rows, yes
from .build import PROJECT_ROOT, build, export_catalogues, refresh_data_dictionary
from .db import bulk_insert
from .pre_reform_preferences_2010 import import_pre_reform_preferences
from .ids import (
    candidacy_id,
    contest_id,
    deterministic_uuid,
    election_chamber_id,
    fact_id,
    reporting_unit_id,
    source_revision_id,
)
from .tcp_measures import TcpReportedPercentage, classify_tcp_reported_percentages


ELECTION_ID = "election_fed_2010_08_21_general"
HOUSE_CHAMBER_ID = election_chamber_id(ELECTION_ID, "house")
SENATE_CHAMBER_ID = election_chamber_id(ELECTION_ID, "senate")
ADAPTER_ID = "adapter_aec_2010_v1"
ADAPTER_VERSION = "1.8.0"
STATE_NAMES = {
    "NSW": "New South Wales",
    "VIC": "Victoria",
    "QLD": "Queensland",
    "WA": "Western Australia",
    "SA": "South Australia",
    "TAS": "Tasmania",
    "ACT": "Australian Capital Territory",
    "NT": "Northern Territory",
}
VOTE_TYPE_FIELDS = {
    "OrdinaryVotes": "ordinary",
    "AbsentVotes": "absent",
    "ProvisionalVotes": "provisional",
    "PrePollVotes": "early",
    "DeclarationPrePollVotes": "early",
    "PostalVotes": "postal",
    "TotalVotes": "total",
}
POLLING_PLACE_TYPES = {
    "1": "polling_place",
    "2": "mobile_team",
    "3": "remote_team",
    "4": "mobile_team",
    "5": "early_centre",
}
KNOWN_PARTY_ABBREVIATIONS = {
    "ALP": "party_alp",
    "LP": "party_liberal",
    "LIB": "party_liberal",
    "NP": "party_nationals",
    "NAT": "party_nationals",
    "GRN": "party_greens",
    "ON": "party_one_nation",
    "PHON": "party_one_nation",
    "IND": "party_independent",
    "LNP": "party_lnp_qld",
    "CLP": "party_country_liberal",
    "KAP": "party_katters_australian_party",
    "CA": "party_centre_alliance",
    "GRPF": "party_people_first",
    "LPNP": "party_coalition",
}
TPP_PARTY_COLUMNS = {
    "Australian Labor Party": "party_alp",
    "Liberal/National Coalition": "party_coalition",
}
TPP_VOTE_TYPE_SUFFIXES = {
    "Ordinary": "ordinary",
    "Absent": "absent",
    "Provisional": "provisional",
    "Postal": "postal",
    "DeclarationPrePoll": "early",
}


HISTORICAL_2010_CONSTITUENCIES = (
    {
        "constituency_id": "constituency_federal_house_act_fraser_2013",
        "constituency_name": "Fraser",
        "state_territory": "act",
        "official_constituency_code": "102",
    },
    {
        "constituency_id": "constituency_federal_house_nsw_charlton_2013",
        "constituency_name": "Charlton",
        "state_territory": "nsw",
        "official_constituency_code": "110",
    },
    {
        "constituency_id": "constituency_federal_house_nsw_throsby_2013",
        "constituency_name": "Throsby",
        "state_territory": "nsw",
        "official_constituency_code": "150",
    },
)


def seed_packaged_reference_snapshot(
    connection: duckdb.DuckDBPyConnection,
    project_root: Path,
) -> dict[str, int]:
    """Reuse the exact canonical reference snapshot carried by Stage 14.4."""

    counts: dict[str, int] = {}
    for table in ("person", "party", "constituency"):
        source = project_root / "data" / "stage14_4" / "tables" / "sync" / f"{table}.parquet"
        if not source.is_file():
            raise RuntimeError(
                f"The Stage 14.4 canonical reference shard is missing: {source}"
            )
        escaped = str(source.resolve()).replace("'", "''")
        connection.execute(f'DELETE FROM sync."{table}"')
        connection.execute(
            f'INSERT INTO sync."{table}" SELECT * FROM read_parquet(\'{escaped}\')'
        )
        counts[table] = connection.execute(
            f'SELECT count(*) FROM sync."{table}"'
        ).fetchone()[0]
        if counts[table] == 0:
            raise RuntimeError(f"The packaged sync.{table} reference shard is empty.")
    return counts


def ensure_2010_historical_constituencies(
    connection: duckdb.DuckDBPyConnection,
) -> int:
    """Add the three shared 2010 and 2013 division identities absent from Stage 14.4.

    Fraser and Throsby reuse official codes that now identify Fenner and
    Whitlam, while Charlton ceased to exist. They therefore receive explicit
    historical canonical identities already established by Stage 14.5 instead
    of being matched by code to a later redistribution.
    """

    synced_at = connection.execute(
        "SELECT max(grand_synced_at) FROM sync.constituency"
    ).fetchone()[0]
    rows: list[tuple] = []
    for record in HISTORICAL_2010_CONSTITUENCIES:
        if connection.execute(
            "SELECT 1 FROM sync.constituency WHERE constituency_id=?",
            [record["constituency_id"]],
        ).fetchone():
            continue
        source_payload = {
            **record,
            "election_id": ELECTION_ID,
            "source": "HouseCandidatesDownload-15508.csv",
        }
        source_row_hash = hashlib.sha256(
            json.dumps(source_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        rows.append(
            (
                record["constituency_id"],
                record["constituency_name"],
                "federal_lower_house_division",
                "federal",
                "house",
                record["state_territory"],
                "Australia",
                "2010_federal_election",
                "aec_boundaries_applicable_to_2010_federal_election",
                None,
                None,
                None,
                f"Division of {record['constituency_name']}",
                None,
                "source_file_aec_2010_house_candidates",
                (
                    "HouseCandidatesDownload-15508.csv:DivisionID="
                    f"{record['official_constituency_code']}"
                ),
                "published",
                "historical",
                "verified",
                datetime(2026, 7, 23, tzinfo=timezone.utc),
                "Codex",
                None,
                "Historical constituency governed by the final AEC 2010 candidate register.",
                record["official_constituency_code"],
                "published",
                source_row_hash,
                synced_at,
            )
        )
    return bulk_insert(connection, "INSERT INTO sync.constituency", rows)


def _source_file_id(key: str) -> str:
    return f"source_file_aec_2010_{key}"


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_release_manifest(
    database_path: Path,
    project_root: Path,
    report: dict,
) -> None:
    try:
        recorded_database_path = str(
            database_path.resolve().relative_to(project_root.resolve())
        )
    except ValueError:
        # Production-scale development builds may run the monolithic DuckDB
        # under /tmp to avoid a mirrored-workspace partial-file hand-off.  The
        # manifest is rewritten against the completed project copy before
        # packaging; retaining the temporary path here lets the governed build
        # finish and its portable shards be exported first.
        recorded_database_path = str(database_path.resolve())
    migrations = [
        {
            "migration": path.name,
            "sha256": _sha256_path(path),
        }
        for path in sorted((project_root / "schema").glob("*.sql"))
    ]
    manifest = {
        "database": "Politica Election Results Database",
        "release_version": "0.2.0",
        "schema_version": report["schema_version"],
        "release_status": "validated" if report["status"] == "PASS" else "blocked",
        "built_at": report["completed_at"],
        "database_path": recorded_database_path,
        "database_size_bytes": database_path.stat().st_size,
        "database_sha256": _sha256_path(database_path),
        "source_manifest_sha256": report["source_manifest_sha256"],
        "source_count": report["source_count"],
        "import_run_id": report["import_run_id"],
        "table_counts": report["table_counts"],
        "validation": {
            "status": report["validation"]["status"],
            "blocker_count": report["validation"]["blocker_count"],
            "warning_count": report["validation"]["warning_count"],
            "check_count": len(report["validation"]["checks"]),
        },
        "parquet_partition_count": report["parquet_partition_count"],
        "formal_preferences": {
            key: report["formal_preferences"][key]
            for key in (
                "state_count",
                "ballot_count",
                "preference_count",
                "above_the_line_ballot_count",
                "below_the_line_ballot_count",
                "official_non_ticket_vote_count",
                "unavailable_ballot_count",
                "represented_formal_vote_count",
                "file_count",
            )
        },
        "migrations": migrations,
    }
    output = project_root / "dist" / "stage_14_6_2010_build_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _validate_formal_checkpoint(project_root: Path, manifest: dict) -> None:
    expected = {
        "state_count": 8,
        "ballot_count": 493129,
        "above_the_line_ballot_count": 0,
        "below_the_line_ballot_count": 493129,
        "official_non_ticket_vote_count": 493142,
        "unavailable_ballot_count": 13,
        "group_ticket_vote_count": 12229091,
        "formal_vote_count": 12722233,
        "represented_formal_vote_count": 12722220,
    }
    observed = {key: manifest.get(key) for key in expected}
    if observed != expected:
        raise RuntimeError(
            f"Formal-preference checkpoint counts do not reconcile: {observed}"
        )
    for item in manifest.get("files", []):
        path = project_root / item["path"]
        if not path.is_file():
            raise RuntimeError(f"Formal-preference checkpoint file is missing: {path}")
        if path.stat().st_size != item["size_bytes"] or _sha256_path(path) != item["sha256"]:
            raise RuntimeError(f"Formal-preference checkpoint checksum failed: {path}")


def _parse_generated(signature: list[str]) -> datetime | None:
    if not signature:
        return None
    match = re.search(r"Generated:([^\s\]]+)", signature[0])
    if not match:
        return None
    parsed = datetime.fromisoformat(match.group(1))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _flush(connection: duckdb.DuckDBPyConnection, table: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    count = bulk_insert(connection, f"INSERT INTO {table}", rows)
    rows.clear()
    return count


def _senate_group_code(data: dict[str, str]) -> str:
    group_code = data.get("Group") or data.get("Ticket")
    if not group_code:
        raise ValueError("A Senate result row has no Group or Ticket code.")
    return group_code


def _alpha_group_column_number(group_code: str) -> int:
    if not group_code.isalpha() or group_code == "UG":
        raise ValueError(f"Unsupported grouped Senate column code: {group_code!r}")
    value = 0
    for character in group_code.upper():
        value = value * 26 + ord(character) - ord("A") + 1
    return value


class ReferenceMatcher:
    def __init__(self, connection: duckdb.DuckDBPyConnection):
        self.party_tokens: dict[str, set[str]] = defaultdict(set)
        for row in connection.execute(
            "SELECT party_id, party_name, short_name, abbreviation, aliases FROM sync.party"
        ).fetchall():
            party_id, *values = row
            for value in values:
                if not value:
                    continue
                self.party_tokens[normalise_label(value)].add(party_id)
                if value == row[-1]:
                    for alias in value.split(","):
                        self.party_tokens[normalise_label(alias)].add(party_id)
        self.person_tokens: dict[str, set[str]] = defaultdict(set)
        self.person_first_family_tokens: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in connection.execute(
            "SELECT person_id, full_name, display_name, given_names, family_name, aliases FROM sync.person"
        ).fetchall():
            person_id, full_name, display_name, given_names, family_name, aliases = row
            values = [full_name, display_name, f"{given_names or ''} {family_name or ''}".strip()]
            if aliases:
                values.extend(aliases.split(","))
            for value in values:
                if value:
                    self.person_tokens[normalise_label(value)].add(person_id)
            # AEC candidate registers commonly include middle names even when the
            # authoritative People record intentionally uses only the first given
            # name.  Retain a deliberately conservative fallback: first given name
            # plus family name, and only accept it when it identifies one person.
            # Exact full-name and alias matches continue to take precedence.
            if given_names and family_name:
                first_given = str(given_names).strip().split()[0]
                key = (normalise_label(first_given), normalise_label(family_name))
                self.person_first_family_tokens[key].add(person_id)
        self.constituencies_by_code = {
            str(code): constituency_id
            for constituency_id, code in connection.execute(
                """SELECT constituency_id, official_constituency_code
                   FROM sync.constituency
                   WHERE official_constituency_code IS NOT NULL"""
            ).fetchall()
        }
        self.constituencies_by_code.update(
            {
                record["official_constituency_code"]: record["constituency_id"]
                for record in HISTORICAL_2010_CONSTITUENCIES
            }
        )

    def party(self, name: str | None, abbreviation: str | None = None) -> tuple[str | None, str]:
        if abbreviation and abbreviation.upper() in KNOWN_PARTY_ABBREVIATIONS:
            candidate = KNOWN_PARTY_ABBREVIATIONS[abbreviation.upper()]
            if candidate in {ids for values in self.party_tokens.values() for ids in values}:
                return candidate, "matched"
        candidates: set[str] = set()
        for value in (name, abbreviation):
            if value:
                candidates.update(self.party_tokens.get(normalise_label(value), set()))
        if len(candidates) == 1:
            return next(iter(candidates)), "matched"
        if len(candidates) > 1:
            return None, "conflict"
        if not (name or abbreviation):
            return None, "not_applicable"
        return None, "unmatched"

    def person(self, given_names: str, family_name: str) -> tuple[str | None, str]:
        candidates = self.person_tokens.get(normalise_label(f"{given_names} {family_name}"), set())
        if len(candidates) == 1:
            return next(iter(candidates)), "matched"
        if len(candidates) > 1:
            return None, "conflict"
        first_given = str(given_names or "").strip().split()
        if first_given and family_name:
            candidates = self.person_first_family_tokens.get(
                (normalise_label(first_given[0]), normalise_label(family_name)),
                set(),
            )
            if len(candidates) == 1:
                return next(iter(candidates)), "matched"
            if len(candidates) > 1:
                return None, "conflict"
        return None, "unmatched"

    def constituency(self, official_code: str) -> tuple[str | None, str]:
        match = self.constituencies_by_code.get(str(official_code))
        return (match, "matched") if match else (None, "unmatched")


@dataclass
class ImportContext:
    connection: duckdb.DuckDBPyConnection
    project_root: Path
    manifest: dict
    started_at: datetime
    import_run_id: object
    matcher: ReferenceMatcher
    source_by_key: dict[str, dict] = field(default_factory=dict)
    revision_by_key: dict[str, str] = field(default_factory=dict)
    house_contests: dict[str, str] = field(default_factory=dict)
    senate_contests: dict[str, str] = field(default_factory=dict)
    candidacies: dict[tuple[str, str, str], object] = field(default_factory=dict)
    ballot_groups: dict[tuple[str, str], object] = field(default_factory=dict)
    house_polling_units: dict[tuple[str, str], object] = field(default_factory=dict)
    house_state_units: dict[str, object] = field(default_factory=dict)
    senate_division_units: dict[tuple[str, str], object] = field(default_factory=dict)
    lineage_rows: list[tuple] = field(default_factory=list)
    unresolved_parties: set[tuple[str, str]] = field(default_factory=set)
    election_id: str = ELECTION_ID
    election_year: int = 2010
    senate_chamber_id: str = SENATE_CHAMBER_ID
    formal_preference_chunk_size: int = 500000
    formal_preference_chunk_sizes: dict[str, int] = field(
        default_factory=lambda: {"QLD": 250000, "VIC": 250000, "NSW": 250000}
    )

    def source_path(self, key: str) -> Path:
        return self.project_root / self.source_by_key[key]["path"]

    def rows(self, key: str) -> Iterable[SourceRow]:
        return source_rows(self.source_path(key))

    def add_lineage(
        self,
        target_schema: str,
        target_table: str,
        target_id: object,
        source_key: str,
        locator: str,
        source_row_hash: str | None,
    ) -> None:
        revision = self.revision_by_key[source_key]
        lineage_id = fact_id(
            "row_lineage",
            [target_schema, target_table, str(target_id), locator],
            revision,
        )
        self.lineage_rows.append(
            (
                lineage_id,
                target_schema,
                target_table,
                str(target_id),
                revision,
                locator,
                self.import_run_id,
                None,
                source_row_hash,
            )
        )
        if len(self.lineage_rows) >= 1000:
            _flush(self.connection, "provenance.row_lineage", self.lineage_rows)


def register_sources(context: ImportContext) -> None:
    connection = context.connection
    manifest = context.manifest
    retrieved_at = datetime.fromisoformat(manifest["retrieved_at"])
    landing_rows = [
        (
            "source_landing_page_aec_2010_house",
            "authority_aec",
            ELECTION_ID,
            "2010 Federal Election House downloads",
            manifest["landing_pages"]["house"],
            None,
            retrieved_at,
            retrieved_at,
            "available",
            "Official AEC final-results download menu.",
        ),
        (
            "source_landing_page_aec_2010_senate",
            "authority_aec",
            ELECTION_ID,
            "2010 Federal Election Senate downloads",
            manifest["landing_pages"]["senate"],
            None,
            retrieved_at,
            retrieved_at,
            "available",
            "Official AEC final-results download menu.",
        ),
    ]
    bulk_insert(connection, "INSERT INTO provenance.source_landing_page", landing_rows)

    file_rows, revision_rows, input_rows = [], [], []
    for source in manifest["sources"]:
        key = source["key"]
        context.source_by_key[key] = source
        file_id = _source_file_id(key)
        revision_id = source_revision_id(file_id, source["sha256"])
        context.revision_by_key[key] = revision_id
        landing_id = (
            "source_landing_page_aec_2010_house"
            if source["chamber"] in {"house", "both"} and not key.startswith("senate_")
            else "source_landing_page_aec_2010_senate"
        )
        file_rows.append(
            (
                file_id,
                "authority_aec",
                ELECTION_ID,
                landing_id,
                key.replace("_", " ").title(),
                source["family"],
                source["chamber"],
                "Australia",
                "current",
                "Registered by the governed AEC 2010 source catalogue.",
            )
        )
        revision_rows.append(
            (
                revision_id,
                file_id,
                1,
                source["url"],
                source["file"],
                source["path"],
                "application/zip" if source["file"].endswith(".zip") else "text/csv",
                None if source["file"].endswith(".zip") else "utf-8-sig",
                None if source["file"].endswith(".zip") else ",",
                "zip" if source["file"].endswith(".zip") else None,
                source["size_bytes"],
                source["row_count"],
                source["sha256"],
                _parse_generated(source.get("source_signature", [])),
                retrieved_at,
                "final",
                source.get("schema_signature_sha256"),
                None,
                "active",
            )
        )
        input_rows.append(
            (
                deterministic_uuid("import_run_input", context.import_run_id, revision_id, key),
                context.import_run_id,
                revision_id,
                key,
            )
        )
    bulk_insert(connection, "INSERT INTO provenance.source_file", file_rows)
    bulk_insert(connection, "INSERT INTO provenance.source_file_revision", revision_rows)
    bulk_insert(connection, "INSERT INTO provenance.import_run_input", input_rows)


def stage_sources(context: ImportContext) -> int:
    total = 0
    for key, source in context.source_by_key.items():
        if source.get("family") in {"formal_preferences", "below_the_line_preferences"}:
            # The candidate-by-ballot matrices contain 36.8 million rows. The
            # immutable ZIPs are the raw layer; governed counted paths are
            # written directly to partitioned Parquet rather than duplicated.
            continue
        revision = context.revision_by_key[key]
        batch: list[tuple] = []
        row_count = 0
        for source_row in context.rows(key):
            row_count += 1
            staging_id = fact_id(
                "staging.source_record", [key, source_row.locator], revision
            )
            batch.append(
                (
                    staging_id,
                    context.import_run_id,
                    revision,
                    key,
                    source_row.locator,
                    source_row.row_number,
                    json.dumps(source_row.data, ensure_ascii=False, sort_keys=True),
                    None,
                    "parsed",
                    source_row.source_row_hash,
                )
            )
            if len(batch) >= 500:
                total += _flush(context.connection, "staging.source_record", batch)
        total += _flush(context.connection, "staging.source_record", batch)
        if source["row_count"] is None:
            context.connection.execute(
                "UPDATE provenance.source_file_revision SET row_count = ? WHERE source_revision_id = ?",
                [row_count, revision],
            )
    return total


def seed_election(context: ImportContext) -> None:
    connection = context.connection
    now = context.started_at
    connection.execute(
        """INSERT INTO control.electoral_system_version VALUES
        ('electoral_system_federal_house_irv_2010', 'irv', 'Instant-runoff voting', '2010 federal',
         'jurisdiction_aus_federal', DATE '2010-08-21', NULL, 1, 'full preferential', NULL,
         'sequential exclusion and transfer', 'AEC statutory rules', ?, 'House system for the 2010 federal election'),
        ('electoral_system_federal_senate_stv_2010', 'stv', 'Single transferable vote', '2010 federal',
         'jurisdiction_aus_federal', DATE '2010-08-21', NULL, NULL, 'full preferential below the line; group voting ticket above the line', 'Droop quota',
         'inclusive Gregory method with registered group voting tickets', 'AEC statutory rules', ?, 'Pre-reform Senate system for the 2010 federal election')""",
        [context.revision_by_key["house_distribution"], context.revision_by_key["senate_distribution"]],
    )
    connection.execute(
        """INSERT INTO core.election VALUES
        (?, '15508', '2010 Australian federal election', DATE '2010-08-21', 2010,
         'jurisdiction_aus_federal', 'authority_aec', 'election_type_general', 'final', 'declared',
         NULL, 'active', ?, ?)""",
        [ELECTION_ID, now, now],
    )
    bulk_insert(
        connection,
        "INSERT INTO core.election_chamber",
        [
            (HOUSE_CHAMBER_ID, ELECTION_ID, "chamber_house", "electoral_system_federal_house_irv_2010", 150, True, "final", "active"),
            (SENATE_CHAMBER_ID, ELECTION_ID, "chamber_senate", "electoral_system_federal_senate_stv_2010", 40, False, "final", "active"),
        ],
    )
    connection.execute(
        "INSERT INTO core.election_key_date VALUES (?, ?, 'polling_day', DATE '2010-08-21', 'official', ?)",
        [
            "election_key_date_fed_2010_polling_day",
            ELECTION_ID,
            context.revision_by_key["house_candidates"],
        ],
    )


def _rows_by_key(rows: Iterable[SourceRow], key_field: str) -> dict[str, SourceRow]:
    return {row.data[key_field]: row for row in rows}


def import_contests_and_candidates(context: ImportContext) -> None:
    connection = context.connection
    enrolment_division = _rows_by_key(context.rows("enrolment_division"), "DivisionID")
    enrolment_state = _rows_by_key(context.rows("enrolment_state"), "StateAb")
    house_rows = list(context.rows("house_candidates"))
    senate_rows = list(context.rows("senate_candidates"))
    house_by_division: dict[str, list[SourceRow]] = defaultdict(list)
    senate_by_state: dict[str, list[SourceRow]] = defaultdict(list)
    for row in house_rows:
        house_by_division[row.data["DivisionID"]].append(row)
    for row in senate_rows:
        senate_by_state[row.data["StateAb"]].append(row)

    contest_rows, snapshot_rows, candidacy_rows = [], [], []
    for division_code, rows in sorted(house_by_division.items(), key=lambda item: int(item[0])):
        first = rows[0]
        contest = contest_id(ELECTION_ID, "house", division_code, first.data["DivisionNm"])
        context.house_contests[division_code] = contest
        canonical_id, match_status = context.matcher.constituency(division_code)
        contest_rows.append(
            (contest, HOUSE_CHAMBER_ID, canonical_id, division_code, first.data["DivisionNm"], 1,
             "electoral_system_federal_house_irv_2010", "declared", False, None, "final", "active")
        )
        enrolment_row = enrolment_division[division_code]
        snapshot_id = f"snapshot_{contest.removeprefix('contest_')}"
        snapshot_rows.append(
            (snapshot_id, contest, canonical_id, division_code, first.data["DivisionNm"],
             "federal_lower_house_division", None, integer(enrolment_row.data["Enrolment"]),
             context.revision_by_key["enrolment_division"], enrolment_row.locator, match_status)
        )
        context.add_lineage("core", "contest", contest, "house_candidates", first.locator, first.source_row_hash)
        context.add_lineage("core", "contest_constituency_snapshot", snapshot_id, "enrolment_division", enrolment_row.locator, enrolment_row.source_row_hash)
        for row in rows:
            data = row.data
            candidate = candidacy_id(contest, data["CandidateID"])
            context.candidacies[("house", division_code, data["CandidateID"])] = candidate
            person_id, person_status = context.matcher.person(data["GivenNm"], data["Surname"])
            party_id, party_status = context.matcher.party(data["PartyNm"], data["PartyAb"])
            if party_status in {"unmatched", "conflict"} and (data["PartyNm"] or data["PartyAb"]):
                context.unresolved_parties.add((data["PartyAb"], data["PartyNm"]))
            candidacy_rows.append(
                (candidate, contest, person_id, party_id, data["CandidateID"],
                 f"{data['GivenNm']} {data['Surname']}".strip(), data["GivenNm"] or None,
                 data["Surname"] or None, data["PartyNm"] or None, data["PartyAb"] or None,
                 "incumbent" if yes(data.get("HistoricElected")) else "not_incumbent", "nominated",
                 person_status, "final", "active")
            )
            context.add_lineage("core", "candidacy", candidate, "house_candidates", row.locator, row.source_row_hash)

    for state, rows in sorted(senate_by_state.items()):
        contest = contest_id(ELECTION_ID, "senate", state, STATE_NAMES[state])
        context.senate_contests[state] = contest
        vacancies = 2 if state in {"ACT", "NT"} else 6
        contest_rows.append(
            (contest, SENATE_CHAMBER_ID, None, state, STATE_NAMES[state], vacancies,
             "electoral_system_federal_senate_stv_2010",
             "declared", False, None, "final", "active")
        )
        enrolment_row = enrolment_state[state]
        snapshot_id = f"snapshot_{contest.removeprefix('contest_')}"
        snapshot_rows.append(
            (snapshot_id, contest, None, state, STATE_NAMES[state], "federal_upper_house_state_contest",
             None, integer(enrolment_row.data["Enrolment"]), context.revision_by_key["enrolment_state"],
             enrolment_row.locator, "not_applicable")
        )
        context.add_lineage("core", "contest", contest, "senate_candidates", rows[0].locator, rows[0].source_row_hash)
        context.add_lineage("core", "contest_constituency_snapshot", snapshot_id, "enrolment_state", enrolment_row.locator, enrolment_row.source_row_hash)
        for row in rows:
            data = row.data
            candidate = candidacy_id(contest, data["CandidateID"])
            context.candidacies[("senate", state, data["CandidateID"])] = candidate
            person_id, person_status = context.matcher.person(data["GivenNm"], data["Surname"])
            party_id, party_status = context.matcher.party(data["PartyNm"], data["PartyAb"])
            if party_status in {"unmatched", "conflict"} and (data["PartyNm"] or data["PartyAb"]):
                context.unresolved_parties.add((data["PartyAb"], data["PartyNm"]))
            candidacy_rows.append(
                (candidate, contest, person_id, party_id, data["CandidateID"],
                 f"{data['GivenNm']} {data['Surname']}".strip(), data["GivenNm"] or None,
                 data["Surname"] or None, data["PartyNm"] or None, data["PartyAb"] or None,
                 "incumbent" if yes(data.get("HistoricElected")) else "not_incumbent", "nominated",
                 person_status, "final", "active")
            )
            context.add_lineage("core", "candidacy", candidate, "senate_candidates", row.locator, row.source_row_hash)

    bulk_insert(connection, "INSERT INTO core.contest", contest_rows)
    bulk_insert(connection, "INSERT INTO core.contest_constituency_snapshot", snapshot_rows)
    bulk_insert(connection, "INSERT INTO core.candidacy", candidacy_rows)


def import_ballot_structure(context: ImportContext) -> None:
    connection = context.connection
    ballot_position_rows: list[tuple] = []
    for row in context.rows("house_fp_vote_type"):
        data = row.data
        contest = context.house_contests[data["DivisionID"]]
        candidate = context.candidacies.get(("house", data["DivisionID"], data["CandidateID"]))
        if candidate is None:
            continue
        position_id = deterministic_uuid("ballot_position", contest, candidate)
        ballot_position_rows.append(
            (position_id, contest, candidate, None, 1, integer(data["BallotPosition"]), "division_ballot",
             context.revision_by_key["house_fp_vote_type"])
        )
        context.add_lineage("core", "ballot_position", position_id, "house_fp_vote_type", row.locator, row.source_row_hash)

    senate_rows = list(context.rows("senate_fp_state_vote_type"))
    by_group: dict[tuple[str, str], list[SourceRow]] = defaultdict(list)
    for row in senate_rows:
        by_group[(row.data["StateAb"], _senate_group_code(row.data))].append(row)
    last_group_column = {
        state: max(
            _alpha_group_column_number(code)
            for grouped_state, code in by_group
            if grouped_state == state and code != "UG"
        )
        for state in context.senate_contests
    }
    group_rows, membership_rows = [], []
    for (state, group_code), rows in sorted(by_group.items()):
        contest = context.senate_contests[state]
        group_id = deterministic_uuid("ballot_group", contest, group_code)
        context.ballot_groups[(state, group_code)] = group_id
        group_label = next((row.data["PartyName"] for row in rows if row.data["PartyName"]), None)
        if group_code == "UG":
            party_id, party_status = None, "not_applicable"
        else:
            party_id, party_status = context.matcher.party(group_label, None)
        if party_status in {"unmatched", "conflict"} and group_label:
            context.unresolved_parties.add(("", group_label))
        group_rows.append(
            (group_id, contest, group_code, group_code, group_label or "Ungrouped", party_id,
             "ungrouped" if group_code == "UG" else "above_the_line", group_code == "UG", "final", "active")
        )
        first = rows[0]
        context.add_lineage("core", "ballot_group", group_id, "senate_fp_state_vote_type", first.locator, first.source_row_hash)
        candidate_rows = [row for row in rows if integer(row.data["BallotPosition"]) and ("senate", state, row.data["CandidateID"]) in context.candidacies]
        candidate_rows.sort(key=lambda row: integer(row.data["BallotPosition"]) or 0)
        column_number = (
            last_group_column[state] + 1
            if group_code == "UG"
            else _alpha_group_column_number(group_code)
        )
        if group_code != "UG":
            group_position_id = deterministic_uuid("ballot_position", contest, group_id)
            ballot_position_rows.append(
                (group_position_id, contest, None, group_id, column_number, 1, "senate_group_column",
                 context.revision_by_key["senate_fp_state_vote_type"])
            )
            context.add_lineage("core", "ballot_position", group_position_id, "senate_fp_state_vote_type", first.locator, first.source_row_hash)
        for group_position, row in enumerate(candidate_rows, start=1):
            candidate = context.candidacies[("senate", state, row.data["CandidateID"])]
            membership_id = deterministic_uuid("ballot_group_membership", group_id, candidate)
            membership_rows.append((membership_id, group_id, candidate, group_position, "candidate"))
            ballot_position_id = deterministic_uuid("ballot_position", contest, candidate)
            ballot_position_rows.append(
                (ballot_position_id, contest, candidate, None, column_number, group_position,
                 "senate_candidate_column", context.revision_by_key["senate_fp_state_vote_type"])
            )
            context.add_lineage("core", "ballot_group_membership", membership_id, "senate_fp_state_vote_type", row.locator, row.source_row_hash)
            context.add_lineage("core", "ballot_position", ballot_position_id, "senate_fp_state_vote_type", row.locator, row.source_row_hash)
    bulk_insert(connection, "INSERT INTO core.ballot_group", group_rows)
    bulk_insert(connection, "INSERT INTO core.ballot_group_membership", membership_rows)
    bulk_insert(connection, "INSERT INTO core.ballot_position", ballot_position_rows)


def import_group_voting_tickets(context: ImportContext) -> tuple[int, int]:
    """Normalise every registered 2010 Senate group voting ticket.

    A ticket is a complete, ordered candidate path. Several groups lodged two
    or three alternatives, so the ticket number is part of the governed key.
    """

    grouped: dict[tuple[str, str, int], list[SourceRow]] = defaultdict(list)
    for source_row in context.rows("senate_group_voting_tickets"):
        data = source_row.data
        grouped[(data["State"], data["OwnerTicket"], integer(data["TicketNo"]) or 0)].append(
            source_row
        )

    ticket_rows: list[tuple] = []
    preference_rows: list[tuple] = []
    revision = context.revision_by_key["senate_group_voting_tickets"]
    for (state, group_code, ticket_number), source_rows_for_ticket in sorted(
        grouped.items()
    ):
        if ticket_number < 1:
            raise ValueError(f"Invalid group voting ticket number in {state}/{group_code}.")
        contest = context.senate_contests[state]
        ballot_group = context.ballot_groups.get((state, group_code))
        if ballot_group is None:
            raise ValueError(
                f"The {state} group voting ticket {group_code}/{ticket_number} has no ballot group."
            )
        ordered = sorted(
            source_rows_for_ticket,
            key=lambda row: integer(row.data["PreferenceNo"]) or 0,
        )
        observed_ranks = [integer(row.data["PreferenceNo"]) for row in ordered]
        expected_ranks = list(range(1, len(ordered) + 1))
        if observed_ranks != expected_ranks:
            raise ValueError(
                f"The {state} group voting ticket {group_code}/{ticket_number} "
                "does not contain one contiguous preference sequence."
            )
        expected_candidate_count = sum(
            1 for chamber, candidate_state, _ in context.candidacies
            if chamber == "senate" and candidate_state == state
        )
        if len(ordered) != expected_candidate_count:
            raise ValueError(
                f"The {state} group voting ticket {group_code}/{ticket_number} covers "
                f"{len(ordered)} candidates; expected {expected_candidate_count}."
            )

        ticket_id = deterministic_uuid(
            "group_voting_ticket", contest, ballot_group, ticket_number, revision
        )
        first = ordered[0]
        ticket_rows.append(
            (
                ticket_id,
                contest,
                ballot_group,
                ticket_number,
                len(ordered),
                "final",
                revision,
                (
                    f"state:{state};owner_ticket:{group_code};"
                    f"ticket_number:{ticket_number}"
                ),
                "active",
            )
        )
        context.add_lineage(
            "ballot",
            "group_voting_ticket",
            ticket_id,
            "senate_group_voting_tickets",
            first.locator,
            first.source_row_hash,
        )
        seen_candidates: set[object] = set()
        for source_row in ordered:
            data = source_row.data
            rank = integer(data["PreferenceNo"]) or 0
            candidacy = context.candidacies.get(("senate", state, data["CandidateID"]))
            if candidacy is None:
                raise ValueError(
                    f"Unmapped candidate {data['CandidateID']} in {state} ticket {group_code}/{ticket_number}."
                )
            if candidacy in seen_candidates:
                raise ValueError(
                    f"Candidate {data['CandidateID']} repeats in {state} ticket {group_code}/{ticket_number}."
                )
            seen_candidates.add(candidacy)
            preference_id = deterministic_uuid(
                "group_voting_ticket_preference", ticket_id, rank
            )
            preference_rows.append(
                (
                    preference_id,
                    ticket_id,
                    rank,
                    candidacy,
                    data["PreferenceNo"],
                    source_row.locator,
                )
            )
            context.add_lineage(
                "ballot",
                "group_voting_ticket_preference",
                preference_id,
                "senate_group_voting_tickets",
                source_row.locator,
                source_row.source_row_hash,
            )
    bulk_insert(context.connection, "INSERT INTO ballot.group_voting_ticket", ticket_rows)
    bulk_insert(
        context.connection,
        "INSERT INTO ballot.group_voting_ticket_preference",
        preference_rows,
    )
    return len(ticket_rows), len(preference_rows)


def import_reporting_units(context: ImportContext) -> None:
    connection = context.connection
    canonical_rows, election_rows = [], []
    seen_canonical = set()
    for row in context.rows("polling_places"):
        data = row.data
        division = data["DivisionID"]
        contest = context.house_contests[division]
        official_code = data["PollingPlaceID"]
        canonical_id = reporting_unit_id("authority_aec", f"polling_place:{official_code}")
        election_id = deterministic_uuid("election_reporting_unit", ELECTION_ID, contest, official_code)
        context.house_polling_units[(division, official_code)] = election_id
        unit_type = POLLING_PLACE_TYPES.get(data["PollingPlaceTypeID"], "vote_centre")
        if canonical_id not in seen_canonical:
            canonical_rows.append(
                (canonical_id, "authority_aec", f"polling_place:{official_code}", data["PollingPlaceNm"],
                 unit_type, datetime(2010, 8, 21).date(), None, "active")
            )
            seen_canonical.add(canonical_id)
        address = ", ".join(value for value in [data["PremisesNm"], data["PremisesAddress1"], data["PremisesAddress2"], data["PremisesAddress3"]] if value)
        election_rows.append(
            (election_id, ELECTION_ID, contest, canonical_id, official_code, data["PollingPlaceNm"],
             data["PollingPlaceTypeID"], unit_type, address or None, data["PremisesSuburb"] or None,
             data["PremisesPostCode"] or None, decimal_text(data["Latitude"]), decimal_text(data["Longitude"]),
             "matched", context.revision_by_key["polling_places"])
        )
        context.add_lineage("geography", "election_reporting_unit", election_id, "polling_places", row.locator, row.source_row_hash)

    for row in context.rows("enrolment_division"):
        data = row.data
        state = data["StateAb"]
        contest = context.senate_contests[state]
        division = data["DivisionID"]
        canonical_id = reporting_unit_id("authority_aec", f"division:{division}")
        election_id = deterministic_uuid("election_reporting_unit", ELECTION_ID, contest, f"division:{division}")
        context.senate_division_units[(state, division)] = election_id
        if canonical_id not in seen_canonical:
            canonical_rows.append(
                (canonical_id, "authority_aec", f"division:{division}", data["DivisionNm"],
                 "district_total", datetime(2010, 8, 21).date(), None, "active")
            )
            seen_canonical.add(canonical_id)
        election_rows.append(
            (election_id, ELECTION_ID, contest, canonical_id, division, data["DivisionNm"],
             "division", "district_total", None, None, None, None, None, "matched",
             context.revision_by_key["enrolment_division"])
        )
        context.add_lineage("geography", "election_reporting_unit", election_id, "enrolment_division", row.locator, row.source_row_hash)

    for row in context.rows("house_tpp_state"):
        data = row.data
        state = data["StateAb"]
        canonical_id = reporting_unit_id("authority_aec", f"state:{state}")
        election_id = deterministic_uuid(
            "election_reporting_unit", ELECTION_ID, HOUSE_CHAMBER_ID, f"state:{state}"
        )
        context.house_state_units[state] = election_id
        if canonical_id not in seen_canonical:
            canonical_rows.append(
                (
                    canonical_id,
                    "authority_aec",
                    f"state:{state}",
                    data["StateNm"],
                    "state_total",
                    datetime(2010, 8, 21).date(),
                    None,
                    "active",
                )
            )
            seen_canonical.add(canonical_id)
        election_rows.append(
            (
                election_id,
                ELECTION_ID,
                None,
                canonical_id,
                f"state:{state}",
                data["StateNm"],
                "state",
                "state_total",
                None,
                None,
                None,
                None,
                None,
                "matched",
                context.revision_by_key["house_tpp_state"],
            )
        )
        context.add_lineage(
            "geography",
            "election_reporting_unit",
            election_id,
            "house_tpp_state",
            row.locator,
            row.source_row_hash,
        )
    bulk_insert(connection, "INSERT INTO geography.reporting_unit", canonical_rows)
    bulk_insert(connection, "INSERT INTO geography.election_reporting_unit", election_rows)


def _vote_fact(
    context: ImportContext,
    source_key: str,
    source_row: SourceRow,
    contest: str | None,
    election_reporting_unit_id: object | None,
    subject_type: str,
    subject_id: object | str,
    result_type: str,
    vote_type: str,
    measure_type: str,
    value: str,
    source_field: str,
    value_basis: str | None = None,
) -> tuple:
    revision = context.revision_by_key[source_key]
    locator = f"{source_row.locator};field:{source_field}"
    natural = [
        ELECTION_ID,
        contest,
        election_reporting_unit_id,
        subject_type,
        subject_id,
        result_type,
        vote_type,
        measure_type,
    ]
    identifier = fact_id("vote_result", natural, revision)
    candidacy = subject_id if subject_type == "candidacy" else None
    ballot_group = subject_id if subject_type == "ballot_group" else None
    party = subject_id if subject_type == "party" else None
    integer_value = integer(value) if measure_type == "votes" else None
    decimal_value = decimal_text(value) if measure_type != "votes" else None
    context.add_lineage("results", "vote_result", identifier, source_key, locator, source_row.source_row_hash)
    return (
        identifier,
        ELECTION_ID,
        contest,
        election_reporting_unit_id,
        subject_type,
        candidacy,
        ballot_group,
        party,
        None,
        result_type,
        vote_type,
        measure_type,
        integer_value,
        decimal_value,
        "reported",
        value_basis
        or ("official_reported" if measure_type == "votes" else "official_calculated"),
        "final",
        revision,
        locator,
        context.import_run_id,
        "active",
    )


def _insert_vote_source(
    context: ImportContext,
    source_key: str,
    chamber: str,
    result_type: str,
    polling_place: bool = False,
    senate_division: bool = False,
) -> int:
    source_rows_to_process = list(context.rows(source_key))
    tcp_measure_by_division: dict[str, str] = {}
    if chamber == "house" and result_type == "tcp" and not polling_place:
        reported_by_division: dict[str, list[TcpReportedPercentage]] = defaultdict(list)
        for source_row in source_rows_to_process:
            data = source_row.data
            swing = decimal_text(data.get("Swing"))
            total_votes = integer(data.get("TotalVotes"))
            if swing is None or total_votes is None:
                raise ValueError(
                    "Every House TCP candidate row must contain Swing and TotalVotes "
                    f"({source_row.locator})"
                )
            reported_by_division[data["DivisionID"]].append(
                TcpReportedPercentage(Decimal(swing), total_votes)
            )
        tcp_measure_by_division = {
            division: classify_tcp_reported_percentages(
                values,
                context=f"House TCP DivisionID {division}",
            )
            for division, values in reported_by_division.items()
        }

    rows_to_insert: list[tuple] = []
    inserted = 0
    for source_row in source_rows_to_process:
        data = source_row.data
        if chamber == "house":
            division = data["DivisionID"]
            contest = context.house_contests[division]
            subject_id = context.candidacies.get(("house", division, data["CandidateID"]))
            subject_type = "candidacy"
            unit_id = context.house_polling_units.get((division, data.get("PollingPlaceID", ""))) if polling_place else None
        else:
            state = data["StateAb"]
            contest = context.senate_contests[state]
            ballot_position = integer(data.get("BallotPosition"))
            if ballot_position == 0:
                subject_type = "ballot_group"
                subject_id = context.ballot_groups.get(
                    (state, _senate_group_code(data))
                )
            else:
                subject_type = "candidacy"
                subject_id = context.candidacies.get(("senate", state, data["CandidateID"]))
            unit_id = context.senate_division_units.get((state, data.get("DivisionID", ""))) if senate_division else None
        if subject_id is None:
            continue
        fields = {"OrdinaryVotes": "ordinary"} if polling_place else VOTE_TYPE_FIELDS
        for source_field, vote_type in fields.items():
            value = data.get(source_field, "")
            if value == "":
                continue
            rows_to_insert.append(
                _vote_fact(
                    context,
                    source_key,
                    source_row,
                    contest,
                    unit_id,
                    subject_type,
                    subject_id,
                    result_type,
                    vote_type,
                    "votes",
                    value,
                    source_field,
                )
            )
        if not polling_place and data.get("Swing", "") != "":
            percentage_measure = tcp_measure_by_division.get(division, "swing")
            rows_to_insert.append(
                _vote_fact(
                    context,
                    source_key,
                    source_row,
                    contest,
                    unit_id,
                    subject_type,
                    subject_id,
                    result_type,
                    "total",
                    percentage_measure,
                    data["Swing"],
                    "Swing",
                )
            )
        if len(rows_to_insert) >= 1000:
            inserted += _flush(context.connection, "results.vote_result", rows_to_insert)
    inserted += _flush(context.connection, "results.vote_result", rows_to_insert)
    return inserted


def _insert_senate_party_aggregates(context: ImportContext) -> int:
    inserted = 0
    aggregate_rows: list[tuple] = []
    for source_row in context.rows("senate_fp_state_group_vote_type"):
        data = source_row.data
        party_id, status = context.matcher.party(data["GroupNm"], data["GroupAb"])
        if party_id is None:
            context.unresolved_parties.add((data["GroupAb"], data["GroupNm"]))
            continue
        contest = context.senate_contests[data["StateAb"]]
        for source_field, vote_type in VOTE_TYPE_FIELDS.items():
            if source_field not in data or data[source_field] == "":
                continue
            aggregate_rows.append(
                _vote_fact(
                    context,
                    "senate_fp_state_group_vote_type",
                    source_row,
                    contest,
                    None,
                    "party",
                    party_id,
                    "party_total",
                    vote_type,
                    "votes",
                    data[source_field],
                    source_field,
                )
            )
            percentage_field = source_field.replace("Votes", "Percentage")
            if data.get(percentage_field, "") != "":
                aggregate_rows.append(
                    _vote_fact(
                        context,
                        "senate_fp_state_group_vote_type",
                        source_row,
                        contest,
                        None,
                        "party",
                        party_id,
                        "party_total",
                        vote_type,
                        "vote_share",
                        data[percentage_field],
                        percentage_field,
                    )
                )
        if len(aggregate_rows) >= 1000:
            inserted += _flush(context.connection, "results.vote_result", aggregate_rows)
    inserted += _flush(context.connection, "results.vote_result", aggregate_rows)
    return inserted


def _insert_group_voting_ticket_usage(
    context: ImportContext,
    source_key: str,
    level: str,
) -> int:
    """Insert official ticket and non-ticket vote counts and shares."""

    rows_to_insert: list[tuple] = []
    for source_row in context.rows(source_key):
        data = source_row.data
        state = data["StateAb"]
        contest = context.senate_contests[state]
        if level == "state":
            subject_type = "contest"
            subject_id: object | str = contest
        else:
            subject_type = "ballot_group"
            subject_id = context.ballot_groups.get((state, data["Ticket"]))
            if subject_id is None:
                if all(
                    integer(data[field]) == 0
                    for field in ("TicketVotes", "NonTicketVotes", "TotalVotes")
                ):
                    continue
                raise ValueError(
                    f"The {state} ticket-usage row {data['Ticket']} has votes but no ballot group."
                )
        for result_type, votes_field, percentage_field in (
            ("ticket_vote", "TicketVotes", "TicketPercentage"),
            ("non_ticket_vote", "NonTicketVotes", "NonTicketPercentage"),
        ):
            rows_to_insert.append(
                _vote_fact(
                    context,
                    source_key,
                    source_row,
                    contest,
                    None,
                    subject_type,
                    subject_id,
                    result_type,
                    "total",
                    "votes",
                    data[votes_field],
                    votes_field,
                )
            )
            rows_to_insert.append(
                _vote_fact(
                    context,
                    source_key,
                    source_row,
                    contest,
                    None,
                    subject_type,
                    subject_id,
                    result_type,
                    "total",
                    "vote_share",
                    data[percentage_field],
                    percentage_field,
                )
            )
    return bulk_insert(context.connection, "INSERT INTO results.vote_result", rows_to_insert)


def _append_tpp_party_values(
    context: ImportContext,
    rows_to_insert: list[tuple],
    source_key: str,
    source_row: SourceRow,
    contest: str | None,
    unit_id: object | None,
    party_label: str,
    party_id: str,
    vote_type: str,
    votes_field: str,
    percentage_field: str,
) -> None:
    data = source_row.data
    if data.get(votes_field, "") != "":
        rows_to_insert.append(
            _vote_fact(
                context,
                source_key,
                source_row,
                contest,
                unit_id,
                "party",
                party_id,
                "tpp",
                vote_type,
                "votes",
                data[votes_field],
                votes_field,
                "official_calculated",
            )
        )
    if data.get(percentage_field, "") != "":
        rows_to_insert.append(
            _vote_fact(
                context,
                source_key,
                source_row,
                contest,
                unit_id,
                "party",
                party_id,
                "tpp",
                vote_type,
                "vote_share",
                data[percentage_field],
                percentage_field,
                "official_calculated",
            )
        )


def _append_tpp_swing(
    context: ImportContext,
    rows_to_insert: list[tuple],
    source_key: str,
    source_row: SourceRow,
    contest: str | None,
    unit_id: object | None,
    party_id: str,
) -> None:
    if source_row.data.get("Swing", "") == "":
        return
    rows_to_insert.append(
        _vote_fact(
            context,
            source_key,
            source_row,
            contest,
            unit_id,
            "party",
            party_id,
            "tpp",
            "total",
            "swing",
            source_row.data["Swing"],
            "Swing",
            "official_calculated",
        )
    )


def _insert_tpp_aggregate_source(
    context: ImportContext,
    source_key: str,
    level: str,
) -> int:
    inserted = 0
    rows_to_insert: list[tuple] = []
    for source_row in context.rows(source_key):
        data = source_row.data
        if level == "state":
            contest = None
            unit_id = context.house_state_units[data["StateAb"]]
        elif level == "division":
            contest = context.house_contests[data["DivisionID"]]
            unit_id = None
        else:
            contest = context.house_contests[data["DivisionID"]]
            unit_id = context.house_polling_units[(data["DivisionID"], data["PollingPlaceID"])]
        for party_label, party_id in TPP_PARTY_COLUMNS.items():
            _append_tpp_party_values(
                context,
                rows_to_insert,
                source_key,
                source_row,
                contest,
                unit_id,
                party_label,
                party_id,
                "total" if level != "polling" else "ordinary",
                f"{party_label} Votes",
                f"{party_label} Percentage",
            )
        if level == "division":
            swing_party_id = (
                "party_alp" if data.get("PartyAb") == "ALP" else "party_coalition"
            )
        else:
            # AEC state and polling-place files express Swing from Labor's TPP side.
            swing_party_id = "party_alp"
        _append_tpp_swing(
            context,
            rows_to_insert,
            source_key,
            source_row,
            contest,
            unit_id,
            swing_party_id,
        )
        if len(rows_to_insert) >= 1000:
            inserted += _flush(context.connection, "results.vote_result", rows_to_insert)
    inserted += _flush(context.connection, "results.vote_result", rows_to_insert)
    return inserted


def _insert_tpp_division_vote_type(context: ImportContext) -> int:
    source_key = "house_tpp_division_vote_type"
    inserted = 0
    rows_to_insert: list[tuple] = []
    for source_row in context.rows(source_key):
        data = source_row.data
        contest = context.house_contests[data["DivisionID"]]
        for party_label, party_id in TPP_PARTY_COLUMNS.items():
            for suffix, vote_type in TPP_VOTE_TYPE_SUFFIXES.items():
                _append_tpp_party_values(
                    context,
                    rows_to_insert,
                    source_key,
                    source_row,
                    contest,
                    None,
                    party_label,
                    party_id,
                    vote_type,
                    f"{party_label} {suffix}Votes",
                    f"{party_label} {suffix}Percentage",
                )
        if len(rows_to_insert) >= 1000:
            inserted += _flush(context.connection, "results.vote_result", rows_to_insert)
    inserted += _flush(context.connection, "results.vote_result", rows_to_insert)
    return inserted


def _checkpointed_vote_source(
    context: ImportContext,
    source_key: str,
    runner,
) -> int:
    connection = context.connection
    transform_name = f"vote_results:{source_key}"
    transform_id = deterministic_uuid("transform_run", context.import_run_id, transform_name, "1.0.0")
    revision = context.revision_by_key[source_key]
    existing = connection.execute(
        """SELECT transform_status, output_row_count FROM provenance.transform_run
           WHERE transform_run_id=?""",
        [transform_id],
    ).fetchone()
    observed = connection.execute(
        "SELECT count(*) FROM results.vote_result WHERE source_revision_id=?", [revision]
    ).fetchone()[0]
    if existing and existing[0] == "completed" and existing[1] == observed:
        print(f"      reusing {source_key}: {observed:,} rows", flush=True)
        return observed

    connection.execute(
        """DELETE FROM provenance.row_lineage
           WHERE target_schema='results' AND target_table='vote_result'
             AND source_revision_id=?""",
        [revision],
    )
    connection.execute("DELETE FROM results.vote_result WHERE source_revision_id=?", [revision])
    connection.execute("DELETE FROM provenance.transform_run WHERE transform_run_id=?", [transform_id])
    connection.execute(
        """INSERT INTO provenance.transform_run
           VALUES (?, ?, ?, '1.0.0', ?, NULL, ?, NULL, NULL, 'running')""",
        [
            transform_id,
            context.import_run_id,
            transform_name,
            datetime.now(timezone.utc),
            context.source_by_key[source_key].get("row_count"),
        ],
    )
    try:
        runner()
        _flush(connection, "provenance.row_lineage", context.lineage_rows)
        observed = connection.execute(
            "SELECT count(*) FROM results.vote_result WHERE source_revision_id=?", [revision]
        ).fetchone()[0]
        connection.execute(
            """UPDATE provenance.transform_run
               SET completed_at=?, output_row_count=?, transform_status='completed'
               WHERE transform_run_id=?""",
            [datetime.now(timezone.utc), observed, transform_id],
        )
        connection.execute("CHECKPOINT")
        print(f"      completed {source_key}: {observed:,} rows", flush=True)
        return observed
    except Exception:
        connection.execute(
            """UPDATE provenance.transform_run
               SET completed_at=?, transform_status='failed' WHERE transform_run_id=?""",
            [datetime.now(timezone.utc), transform_id],
        )
        connection.execute("CHECKPOINT")
        raise


def import_vote_results(context: ImportContext) -> int:
    inserted = 0
    inserted += _checkpointed_vote_source(
        context,
        "house_fp_vote_type",
        lambda: _insert_vote_source(context, "house_fp_vote_type", "house", "first_preference"),
    )
    for state in ("nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt"):
        source_key = f"house_fp_polling_{state}"
        inserted += _checkpointed_vote_source(
            context,
            source_key,
            lambda source_key=source_key: _insert_vote_source(
                context, source_key, "house", "first_preference", polling_place=True
            ),
        )
    inserted += _checkpointed_vote_source(
        context,
        "house_tcp_vote_type",
        lambda: _insert_vote_source(context, "house_tcp_vote_type", "house", "tcp"),
    )
    inserted += _checkpointed_vote_source(
        context,
        "house_tcp_polling",
        lambda: _insert_vote_source(
            context, "house_tcp_polling", "house", "tcp", polling_place=True
        ),
    )
    inserted += _checkpointed_vote_source(
        context,
        "house_tpp_state",
        lambda: _insert_tpp_aggregate_source(context, "house_tpp_state", "state"),
    )
    inserted += _checkpointed_vote_source(
        context,
        "house_tpp_division",
        lambda: _insert_tpp_aggregate_source(context, "house_tpp_division", "division"),
    )
    inserted += _checkpointed_vote_source(
        context,
        "house_tpp_polling",
        lambda: _insert_tpp_aggregate_source(context, "house_tpp_polling", "polling"),
    )
    inserted += _checkpointed_vote_source(
        context,
        "senate_fp_state_vote_type",
        lambda: _insert_vote_source(
            context, "senate_fp_state_vote_type", "senate", "first_preference"
        ),
    )
    inserted += _checkpointed_vote_source(
        context,
        "senate_fp_division_vote_type",
        lambda: _insert_vote_source(
            context,
            "senate_fp_division_vote_type",
            "senate",
            "first_preference",
            senate_division=True,
        ),
    )
    inserted += _checkpointed_vote_source(
        context,
        "senate_fp_state_group_vote_type",
        lambda: _insert_senate_party_aggregates(context),
    )
    inserted += _checkpointed_vote_source(
        context,
        "senate_gvt_usage_state",
        lambda: _insert_group_voting_ticket_usage(
            context, "senate_gvt_usage_state", "state"
        ),
    )
    inserted += _checkpointed_vote_source(
        context,
        "senate_gvt_usage_group",
        lambda: _insert_group_voting_ticket_usage(
            context, "senate_gvt_usage_group", "group"
        ),
    )
    return inserted


def _participation_fact(
    context: ImportContext,
    source_key: str,
    source_row: SourceRow,
    contest: str,
    vote_type: str,
    measure_type: str,
    value: str,
    source_field: str,
    decimal_measure: bool = False,
) -> tuple:
    revision = context.revision_by_key[source_key]
    locator = f"{source_row.locator};field:{source_field}"
    identifier = fact_id(
        "participation_result",
        [ELECTION_ID, contest, None, vote_type, measure_type],
        revision,
    )
    context.add_lineage(
        "results", "participation_result", identifier, source_key, locator, source_row.source_row_hash
    )
    return (
        identifier,
        ELECTION_ID,
        contest,
        None,
        vote_type,
        measure_type,
        None if decimal_measure else integer(value),
        decimal_text(value) if decimal_measure else None,
        "reported",
        "official_calculated" if decimal_measure else "official_reported",
        "final",
        revision,
        locator,
        context.import_run_id,
        "active",
    )


def import_participation(context: ImportContext) -> int:
    facts: list[tuple] = []
    configurations = [
        ("enrolment_division", "DivisionID", context.house_contests, [("Enrolment", "total", "enrolment", False)]),
        ("house_informal_division", "DivisionID", context.house_contests,
         [("FormalVotes", "total", "formal_votes", False), ("InformalVotes", "total", "informal_votes", False),
          ("InformalPercent", "total", "informality_percentage", True)]),
        ("house_turnout_division", "DivisionID", context.house_contests,
         [("Turnout", "total", "turnout", False), ("TurnoutPercentage", "total", "turnout_percentage", True)]),
        ("house_votes_division", "DivisionID", context.house_contests,
         [(field, vote_type, "total_votes", False) for field, vote_type in VOTE_TYPE_FIELDS.items()]),
        ("enrolment_state", "StateAb", context.senate_contests, [("Enrolment", "total", "enrolment", False)]),
        ("senate_informal_state", "StateAb", context.senate_contests,
         [("FormalVotes", "total", "formal_votes", False), ("InformalVotes", "total", "informal_votes", False),
          ("InformalPercent", "total", "informality_percentage", True)]),
        ("senate_turnout_state", "StateAb", context.senate_contests,
         [("Turnout", "total", "turnout", False), ("TurnoutPercentage", "total", "turnout_percentage", True)]),
        ("senate_votes_state", "StateAb", context.senate_contests,
         [(field, vote_type, "total_votes", False) for field, vote_type in VOTE_TYPE_FIELDS.items()]),
    ]
    inserted = 0
    for source_key, key_field, contest_map, fields in configurations:
        for source_row in context.rows(source_key):
            contest = contest_map[source_row.data[key_field]]
            for source_field, vote_type, measure_type, is_decimal in fields:
                value = source_row.data.get(source_field, "")
                if value == "":
                    continue
                facts.append(
                    _participation_fact(
                        context,
                        source_key,
                        source_row,
                        contest,
                        vote_type,
                        measure_type,
                        value,
                        source_field,
                        is_decimal,
                    )
                )
            if len(facts) >= 1000:
                inserted += _flush(context.connection, "results.participation_result", facts)
    inserted += _flush(context.connection, "results.participation_result", facts)
    return inserted


def import_outcomes(context: ImportContext) -> int:
    outcome_rows, elected_rows = [], []
    for source_row in context.rows("house_elected"):
        data = source_row.data
        contest = context.house_contests[data["DivisionID"]]
        candidate = context.candidacies[("house", data["DivisionID"], data["CandidateID"])]
        outcome_id = deterministic_uuid("contest_outcome", contest, candidate, "elected")
        outcome_rows.append(
            (outcome_id, contest, candidate, "elected", 1, None, "final",
             context.revision_by_key["house_elected"], source_row.locator, "active")
        )
        person_id = context.connection.execute(
            "SELECT person_id FROM core.candidacy WHERE candidacy_id = ?", [candidate]
        ).fetchone()[0]
        member_id = deterministic_uuid("elected_member", outcome_id)
        elected_rows.append((member_id, outcome_id, ELECTION_ID, contest, candidate, person_id, 1, "pending"))
        context.add_lineage("results", "contest_outcome", outcome_id, "house_elected", source_row.locator, source_row.source_row_hash)
        context.add_lineage("results", "elected_member", member_id, "house_elected", source_row.locator, source_row.source_row_hash)

    senate_name_map: dict[tuple[str, str], object] = {}
    for source_row in context.rows("senate_candidates"):
        data = source_row.data
        senate_name_map[(data["StateAb"], normalise_label(f"{data['GivenNm']} {data['Surname']}"))] = context.candidacies[
            ("senate", data["StateAb"], data["CandidateID"])
        ]
    for source_row in context.rows("senate_elected"):
        data = source_row.data
        contest = context.senate_contests[data["StateAb"]]
        candidate = senate_name_map[(data["StateAb"], normalise_label(f"{data['GivenNm']} {data['Surname']}"))]
        order = integer(data["ElectedOrder"])
        outcome_id = deterministic_uuid("contest_outcome", contest, candidate, "elected")
        outcome_rows.append(
            (outcome_id, contest, candidate, "elected", order, None,
             "final",
             context.revision_by_key["senate_elected"], source_row.locator, "active")
        )
        person_id = context.connection.execute(
            "SELECT person_id FROM core.candidacy WHERE candidacy_id = ?", [candidate]
        ).fetchone()[0]
        member_id = deterministic_uuid("elected_member", outcome_id)
        elected_rows.append((member_id, outcome_id, ELECTION_ID, contest, candidate, person_id, order, "pending"))
        context.add_lineage("results", "contest_outcome", outcome_id, "senate_elected", source_row.locator, source_row.source_row_hash)
        context.add_lineage("results", "elected_member", member_id, "senate_elected", source_row.locator, source_row.source_row_hash)
    bulk_insert(context.connection, "INSERT INTO results.contest_outcome", outcome_rows)
    bulk_insert(context.connection, "INSERT INTO results.elected_member", elected_rows)
    return len(outcome_rows)


def import_house_counts(context: ImportContext) -> tuple[int, int, int]:
    grouped: dict[tuple[str, int, str], dict[str, SourceRow]] = defaultdict(dict)
    round_sources: dict[tuple[str, int], SourceRow] = {}
    for source_row in context.rows("house_distribution"):
        data = source_row.data
        key = (data["DivisionID"], integer(data["CountNumber"]) or 0, data["CandidateID"])
        grouped[key][data["CalculationType"]] = source_row
        round_sources.setdefault((key[0], key[1]), source_row)
    round_rows, total_rows, transfer_rows = [], [], []
    round_ids = {}
    revision = context.revision_by_key["house_distribution"]
    for (division, count_number), source_row in sorted(round_sources.items(), key=lambda item: (int(item[0][0]), item[0][1])):
        contest = context.house_contests[division]
        round_id = deterministic_uuid("count_round", contest, count_number, revision)
        round_ids[(division, count_number)] = round_id
        round_rows.append(
            (round_id, contest, count_number, f"Count {count_number}",
             "first_preferences" if count_number == 0 else "transfer", None, None,
             "AEC House distribution", None, None, "final", revision,
             f"division:{division};count:{count_number}")
        )
        context.add_lineage("count", "count_round", round_id, "house_distribution", source_row.locator, source_row.source_row_hash)
    for (division, count_number, candidate_id), calculations in grouped.items():
        candidate = context.candidacies.get(("house", division, candidate_id))
        if candidate is None or "Preference Count" not in calculations:
            continue
        preference = calculations["Preference Count"]
        value = integer(preference.data["CalculationValue"])
        total_id = deterministic_uuid("count_candidate_total", round_ids[(division, count_number)], candidate)
        total_rows.append(
            (total_id, round_ids[(division, count_number)], candidate, value, str(value), str(value),
             "elected" if yes(preference.data.get("Elected")) else "continuing", "reported", revision,
             preference.locator)
        )
        context.add_lineage("count", "count_candidate_total", total_id, "house_distribution", preference.locator, preference.source_row_hash)
        transfer = calculations.get("Transfer Count")
        transfer_value = integer(transfer.data["CalculationValue"]) if transfer else None
        if transfer and transfer_value not in {None, 0}:
            transfer_id = deterministic_uuid("preference_transfer", round_ids[(division, count_number)], candidate, transfer.locator)
            transfer_rows.append(
                (transfer_id, round_ids[(division, count_number)], None, candidate, None, str(transfer_value),
                 False, "reported", revision, transfer.locator)
            )
            context.add_lineage("count", "preference_transfer", transfer_id, "house_distribution", transfer.locator, transfer.source_row_hash)
    bulk_insert(context.connection, 'INSERT INTO "count".count_round', round_rows)
    bulk_insert(context.connection, 'INSERT INTO "count".count_candidate_total', total_rows)
    bulk_insert(context.connection, 'INSERT INTO "count".preference_transfer', transfer_rows)
    return len(round_rows), len(total_rows), len(transfer_rows)


def import_senate_counts(context: ImportContext) -> tuple[int, int, int]:
    candidate_name_map: dict[tuple[str, str], object] = {}
    for source_row in context.rows("senate_candidates"):
        data = source_row.data
        candidate_name_map[(data["StateAb"], normalise_label(f"{data['GivenNm']} {data['Surname']}"))] = context.candidacies[
                ("senate", data["StateAb"], data["CandidateID"])
        ]
    source_records = list(context.rows("senate_distribution"))
    round_sources: dict[tuple[str, int], SourceRow] = {}
    for source_row in source_records:
        data = source_row.data
        count_number = integer(data.get("Count"))
        if data.get("State") in STATE_NAMES and count_number is not None:
            round_sources.setdefault((data["State"], count_number), source_row)
    revision = context.revision_by_key["senate_distribution"]
    round_rows, total_rows, transfer_rows = [], [], []
    round_ids = {}
    for (state, count_number), source_row in sorted(round_sources.items()):
        contest = context.senate_contests[state]
        round_id = deterministic_uuid("count_round", contest, count_number, revision)
        round_ids[(state, count_number)] = round_id
        comment = source_row.data.get("Comment", "")
        lower_comment = comment.casefold()
        if count_number == 1:
            action = "first_preferences"
        elif "surplus" in lower_comment:
            action = "surplus_distribution"
        elif "exclud" in lower_comment:
            action = "exclusion"
        else:
            action = "transfer"
        round_rows.append(
            (round_id, contest, count_number, f"Count {count_number}", action,
             decimal_text(source_row.data.get("Quota")), decimal_text(source_row.data.get("Transfer Value")),
             "AEC Senate distribution", None, comment or None, "final", revision, source_row.locator)
        )
        context.add_lineage("count", "count_round", round_id, "senate_distribution", source_row.locator, source_row.source_row_hash)
    seen_totals = set()
    for source_row in source_records:
        data = source_row.data
        state = data.get("State", "")
        count_number = integer(data.get("Count"))
        candidate = candidate_name_map.get(
            (state, normalise_label(f"{data.get('GivenNm', '')} {data.get('Surname', '')}"))
        )
        if candidate is None or count_number is None or (state, count_number) not in round_ids:
            continue
        total_key = (round_ids[(state, count_number)], candidate)
        if total_key in seen_totals:
            continue
        seen_totals.add(total_key)
        total_id = deterministic_uuid("count_candidate_total", *total_key)
        papers = integer(data.get("Papers"))
        nonnegative_papers = papers if papers is not None and papers >= 0 else None
        total_rows.append(
            (total_id, total_key[0], candidate, nonnegative_papers, decimal_text(data.get("VoteTransferred")),
             decimal_text(data.get("ProgressiveVoteTotal")), data.get("Status") or "continuing",
             "reported", revision, source_row.locator)
        )
        context.add_lineage("count", "count_candidate_total", total_id, "senate_distribution", source_row.locator, source_row.source_row_hash)
        transferred = decimal_text(data.get("VoteTransferred"))
        if transferred not in {None, "0", "0.0", "0.000000000000000000000000000"}:
            transfer_id = deterministic_uuid("preference_transfer", total_key[0], candidate, source_row.locator)
            transfer_rows.append(
                (transfer_id, total_key[0], None, candidate, nonnegative_papers,
                 transferred, False, "reported", revision, source_row.locator)
            )
            context.add_lineage("count", "preference_transfer", transfer_id, "senate_distribution", source_row.locator, source_row.source_row_hash)
    bulk_insert(context.connection, 'INSERT INTO "count".count_round', round_rows)
    bulk_insert(context.connection, 'INSERT INTO "count".count_candidate_total', total_rows)
    bulk_insert(context.connection, 'INSERT INTO "count".preference_transfer', transfer_rows)
    return len(round_rows), len(total_rows), len(transfer_rows)


def import_external_identifiers(context: ImportContext) -> int:
    rows: list[tuple] = []
    # One constituency crosswalk per division, sourced from the first candidate row in each division.
    seen_divisions = set()
    for source_row in context.rows("house_candidates"):
        data = source_row.data
        if data["DivisionID"] in seen_divisions:
            continue
        seen_divisions.add(data["DivisionID"])
        canonical_id, status = context.matcher.constituency(data["DivisionID"])
        if canonical_id:
            rows.append(
                (f"external_identifier_aec_2010_constituency_{data['DivisionID']}", "constituency", canonical_id,
                 "authority_aec", "aec_division_id", data["DivisionID"], datetime(2010, 8, 21).date(),
                 None, context.revision_by_key["house_candidates"], source_row.locator, status, "active")
            )
    seen_parties = set()
    for source_key in ("house_candidates", "senate_candidates"):
        for source_row in context.rows(source_key):
            data = source_row.data
            abbreviation = data.get("PartyAb", "")
            if not abbreviation or abbreviation in seen_parties:
                continue
            seen_parties.add(abbreviation)
            party_id, status = context.matcher.party(data.get("PartyNm"), abbreviation)
            if party_id:
                rows.append(
                    (f"external_identifier_aec_2010_party_{normalise_label(abbreviation).replace(' ', '_')}",
                     "party", party_id, "authority_aec", "aec_party_abbreviation", abbreviation,
                     datetime(2010, 8, 21).date(), None, context.revision_by_key[source_key],
                     source_row.locator, status, "active")
                )
    seen_people = set()
    for source_key, chamber, key_field in (
        ("house_candidates", "house", "DivisionID"),
        ("senate_candidates", "senate", "StateAb"),
    ):
        for source_row in context.rows(source_key):
            data = source_row.data
            person_id, status = context.matcher.person(data["GivenNm"], data["Surname"])
            if person_id is None or data["CandidateID"] in seen_people:
                continue
            seen_people.add(data["CandidateID"])
            rows.append(
                (f"external_identifier_aec_2010_person_candidate_{data['CandidateID']}", "person", person_id,
                 "authority_aec", "aec_candidate_id", data["CandidateID"], datetime(2010, 8, 21).date(),
                 None, context.revision_by_key[source_key], source_row.locator, status, "active")
            )
    bulk_insert(context.connection, "INSERT INTO sync.external_identifier", rows)
    return len(rows)


def reconcile(context: ImportContext) -> dict:
    connection = context.connection
    checks: list[dict] = []

    def add_check(name: str, observed, expected, rule_id: str) -> None:
        checks.append(
            {
                "name": name,
                "observed": observed,
                "expected": expected,
                "passed": observed == expected,
                "severity": "blocker",
                "rule_id": rule_id,
            }
        )

    add_check(
        "source_revision_count",
        connection.execute("SELECT count(*) FROM provenance.source_file_revision").fetchone()[0],
        47,
        "rule_2010_source_count",
    )
    add_check(
        "house_contest_count",
        connection.execute("SELECT count(*) FROM core.contest WHERE election_chamber_id = ?", [HOUSE_CHAMBER_ID]).fetchone()[0],
        150,
        "rule_2010_house_contests",
    )
    add_check(
        "senate_contest_count",
        connection.execute("SELECT count(*) FROM core.contest WHERE election_chamber_id = ?", [SENATE_CHAMBER_ID]).fetchone()[0],
        8,
        "rule_2010_senate_contests",
    )
    add_check(
        "house_candidate_count",
        connection.execute(
            "SELECT count(*) FROM core.candidacy ca JOIN core.contest c ON c.contest_id=ca.contest_id WHERE c.election_chamber_id=?",
            [HOUSE_CHAMBER_ID],
        ).fetchone()[0],
        849,
        "rule_2010_house_candidates",
    )
    add_check(
        "senate_candidate_count",
        connection.execute(
            "SELECT count(*) FROM core.candidacy ca JOIN core.contest c ON c.contest_id=ca.contest_id WHERE c.election_chamber_id=?",
            [SENATE_CHAMBER_ID],
        ).fetchone()[0],
        349,
        "rule_2010_senate_candidates",
    )
    add_check(
        "house_elected_count",
        connection.execute(
            "SELECT count(*) FROM results.contest_outcome o JOIN core.contest c ON c.contest_id=o.contest_id WHERE c.election_chamber_id=? AND o.outcome_type='elected'",
            [HOUSE_CHAMBER_ID],
        ).fetchone()[0],
        150,
        "rule_2010_house_elected",
    )
    add_check(
        "senate_elected_count",
        connection.execute(
            "SELECT count(*) FROM results.contest_outcome o JOIN core.contest c ON c.contest_id=o.contest_id WHERE c.election_chamber_id=? AND o.outcome_type='elected'",
            [SENATE_CHAMBER_ID],
        ).fetchone()[0],
        40,
        "rule_2010_senate_elected",
    )
    add_check(
        "senate_count_candidate_total_count",
        connection.execute(
            """SELECT count(*) FROM "count".count_candidate_total t
               JOIN "count".count_round r ON r.count_round_id=t.count_round_id
               JOIN core.contest c ON c.contest_id=r.contest_id
               WHERE c.election_chamber_id=?""",
            [SENATE_CHAMBER_ID],
        ).fetchone()[0],
        70042,
        "rule_2010_senate_counts",
    )
    house_formal_mismatches = connection.execute(
        """
        WITH votes AS (
          SELECT v.contest_id, sum(v.integer_value) AS votes
          FROM results.vote_result v JOIN core.contest c ON c.contest_id=v.contest_id
          WHERE c.election_chamber_id=? AND v.result_type='first_preference'
            AND v.vote_type='total' AND v.measure_type='votes' AND v.election_reporting_unit_id IS NULL
          GROUP BY v.contest_id
        ), formal AS (
          SELECT contest_id, max(integer_value) AS formal
          FROM results.participation_result
          WHERE measure_type='formal_votes' AND vote_type='total'
          GROUP BY contest_id
        )
        SELECT count(*) FROM votes JOIN formal USING (contest_id) WHERE votes <> formal
        """,
        [HOUSE_CHAMBER_ID],
    ).fetchone()[0]
    add_check("house_formal_mismatches", house_formal_mismatches, 0, "rule_2010_house_formal_reconciliation")
    senate_formal_mismatches = connection.execute(
        """
        WITH votes AS (
          SELECT v.contest_id, sum(v.integer_value) AS votes
          FROM results.vote_result v JOIN core.contest c ON c.contest_id=v.contest_id
          WHERE c.election_chamber_id=? AND v.result_type='first_preference'
            AND v.vote_type='total' AND v.measure_type='votes' AND v.election_reporting_unit_id IS NULL
          GROUP BY v.contest_id
        ), formal AS (
          SELECT contest_id, max(integer_value) AS formal
          FROM results.participation_result
          WHERE measure_type='formal_votes' AND vote_type='total'
          GROUP BY contest_id
        )
        SELECT count(*) FROM votes JOIN formal USING (contest_id) WHERE votes <> formal
        """,
        [SENATE_CHAMBER_ID],
    ).fetchone()[0]
    add_check("senate_formal_mismatches", senate_formal_mismatches, 0, "rule_2010_senate_formal_reconciliation")
    participation_mismatches = connection.execute(
        """
        WITH p AS (
          SELECT contest_id,
            max(integer_value) FILTER (WHERE measure_type='formal_votes') AS formal,
            max(integer_value) FILTER (WHERE measure_type='informal_votes') AS informal,
            max(integer_value) FILTER (WHERE measure_type='total_votes' AND vote_type='total') AS total
          FROM results.participation_result GROUP BY contest_id
        ) SELECT count(*) FROM p WHERE formal + informal <> total
        """
    ).fetchone()[0]
    add_check("participation_mismatches", participation_mismatches, 0, "rule_2010_participation_reconciliation")
    lineage_missing = connection.execute(
        """
        SELECT sum(missing) FROM (
          SELECT count(*) AS missing FROM results.vote_result t
            WHERE NOT EXISTS (SELECT 1 FROM provenance.row_lineage l WHERE l.target_table='vote_result' AND l.target_record_id=CAST(t.vote_result_id AS VARCHAR))
          UNION ALL SELECT count(*) FROM results.participation_result t
            WHERE NOT EXISTS (SELECT 1 FROM provenance.row_lineage l WHERE l.target_table='participation_result' AND l.target_record_id=CAST(t.participation_result_id AS VARCHAR))
          UNION ALL SELECT count(*) FROM results.contest_outcome t
            WHERE NOT EXISTS (SELECT 1 FROM provenance.row_lineage l WHERE l.target_table='contest_outcome' AND l.target_record_id=CAST(t.contest_outcome_id AS VARCHAR))
          UNION ALL SELECT count(*) FROM "count".count_round t
            WHERE NOT EXISTS (SELECT 1 FROM provenance.row_lineage l WHERE l.target_table='count_round' AND l.target_record_id=CAST(t.count_round_id AS VARCHAR))
          UNION ALL SELECT count(*) FROM "count".count_candidate_total t
            WHERE NOT EXISTS (SELECT 1 FROM provenance.row_lineage l WHERE l.target_table='count_candidate_total' AND l.target_record_id=CAST(t.count_candidate_total_id AS VARCHAR))
        )
        """
    ).fetchone()[0]
    add_check("missing_fact_lineage", lineage_missing, 0, "rule_2010_source_lineage")
    duplicate_facts = connection.execute(
        """
        SELECT count(*) FROM (
          SELECT election_id, contest_id, coalesce(CAST(election_reporting_unit_id AS VARCHAR), ''),
                 subject_type, coalesce(CAST(candidacy_id AS VARCHAR), ''),
                 coalesce(CAST(ballot_group_id AS VARCHAR), ''), coalesce(party_id, ''),
                 result_type, vote_type, measure_type, source_revision_id, count(*) AS n
          FROM results.vote_result GROUP BY ALL HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    add_check("duplicate_vote_fact_grains", duplicate_facts, 0, "rule_2010_no_duplicate_facts")
    unmatched_house_constituencies = connection.execute(
        "SELECT count(*) FROM core.contest_constituency_snapshot WHERE constituency_type='federal_lower_house_division' AND match_status <> 'matched'"
    ).fetchone()[0]
    add_check("unmatched_house_constituencies", unmatched_house_constituencies, 0, "rule_2010_house_contests")
    null_polling_units = connection.execute(
        """SELECT count(*) FROM results.vote_result
           WHERE source_revision_id IN (
             SELECT source_revision_id FROM provenance.source_file_revision r
             JOIN provenance.source_file f USING (source_file_id)
             WHERE f.dataset_family LIKE '%polling_place%')
           AND election_reporting_unit_id IS NULL"""
    ).fetchone()[0]
    add_check("unresolved_polling_result_units", null_polling_units, 0, "rule_2010_source_lineage")

    for source_key, expected in (
        ("house_tpp_state", 40),
        ("house_tpp_division", 750),
        ("house_tpp_polling", 44755),
    ):
        add_check(
            f"{source_key}_fact_count",
            connection.execute(
                "SELECT count(*) FROM results.vote_result WHERE source_revision_id=?",
                [context.revision_by_key[source_key]],
            ).fetchone()[0],
            expected,
            "rule_2010_house_tpp_sources",
        )

    tpp_division_formal_mismatches = connection.execute(
        """
        WITH tpp AS (
          SELECT contest_id, sum(integer_value) AS votes
          FROM results.vote_result
          WHERE source_revision_id=? AND result_type='tpp'
            AND vote_type='total' AND measure_type='votes'
          GROUP BY contest_id
        ), formal AS (
          SELECT p.contest_id, max(p.integer_value) AS votes
          FROM results.participation_result p
          JOIN core.contest c USING (contest_id)
          WHERE c.election_chamber_id=?
            AND p.measure_type='formal_votes' AND p.vote_type='total'
          GROUP BY p.contest_id
        )
        SELECT count(*) FROM tpp FULL OUTER JOIN formal USING (contest_id)
        WHERE tpp.votes IS DISTINCT FROM formal.votes
        """,
        [context.revision_by_key["house_tpp_division"], HOUSE_CHAMBER_ID],
    ).fetchone()[0]
    add_check(
        "house_tpp_division_formal_mismatches",
        tpp_division_formal_mismatches,
        0,
        "rule_2010_house_tpp_formal_reconciliation",
    )

    tpp_polling_mismatches = connection.execute(
        """
        WITH tpp AS (
          SELECT election_reporting_unit_id, sum(integer_value) AS votes
          FROM results.vote_result
          WHERE source_revision_id=? AND result_type='tpp'
            AND vote_type='ordinary' AND measure_type='votes'
          GROUP BY election_reporting_unit_id
        ), first_preferences AS (
          SELECT v.election_reporting_unit_id, sum(v.integer_value) AS votes
          FROM results.vote_result v
          JOIN core.contest c USING (contest_id)
          WHERE c.election_chamber_id=? AND v.result_type='first_preference'
            AND v.vote_type='ordinary' AND v.measure_type='votes'
            AND v.election_reporting_unit_id IS NOT NULL
          GROUP BY v.election_reporting_unit_id
        )
        SELECT count(*)
        FROM tpp FULL OUTER JOIN first_preferences USING (election_reporting_unit_id)
        WHERE tpp.votes IS DISTINCT FROM first_preferences.votes
        """,
        [context.revision_by_key["house_tpp_polling"], HOUSE_CHAMBER_ID],
    ).fetchone()[0]
    add_check(
        "house_tpp_polling_mismatches",
        tpp_polling_mismatches,
        0,
        "rule_2010_house_tpp_polling_reconciliation",
    )

    tpp_state_mismatches = connection.execute(
        """
        WITH division AS (
          SELECT upper(s.state_territory) AS state_ab, v.party_id,
                 sum(v.integer_value) AS votes
          FROM results.vote_result v
          JOIN core.contest c USING (contest_id)
          JOIN sync.constituency s ON s.constituency_id=c.canonical_constituency_id
          WHERE v.source_revision_id=? AND v.result_type='tpp'
            AND v.vote_type='total' AND v.measure_type='votes'
          GROUP BY upper(s.state_territory), v.party_id
        ), state AS (
          SELECT replace(u.official_reporting_unit_code, 'state:', '') AS state_ab,
                 v.party_id, v.integer_value AS votes
          FROM results.vote_result v
          JOIN geography.election_reporting_unit u USING (election_reporting_unit_id)
          WHERE v.source_revision_id=? AND v.result_type='tpp'
            AND v.vote_type='total' AND v.measure_type='votes'
        )
        SELECT count(*)
        FROM division FULL OUTER JOIN state USING (state_ab, party_id)
        WHERE division.votes IS DISTINCT FROM state.votes
        """,
        [
            context.revision_by_key["house_tpp_division"],
            context.revision_by_key["house_tpp_state"],
        ],
    ).fetchone()[0]
    add_check(
        "house_tpp_state_mismatches",
        tpp_state_mismatches,
        0,
        "rule_2010_house_tpp_state_reconciliation",
    )

    add_check(
        "formal_preference_dataset_count",
        connection.execute("SELECT count(*) FROM ballot.ballot_dataset").fetchone()[0],
        8,
        "rule_2010_formal_preference_sources",
    )
    add_check(
        "formal_senate_ballot_count",
        connection.execute(
            """SELECT count(*) FROM ballot.ballot b
               JOIN ballot.ballot_dataset d USING (ballot_dataset_id)
               WHERE d.election_chamber_id=?""",
            [SENATE_CHAMBER_ID],
        ).fetchone()[0],
        493129,
        "rule_2010_formal_ballot_count",
    )
    formal_ballot_availability_gaps = connection.execute(
        """
        WITH published AS (
          SELECT contest_id, max(integer_value) AS non_ticket_votes
          FROM results.vote_result
          WHERE result_type='non_ticket_vote' AND vote_type='total'
            AND measure_type='votes' AND subject_type='contest'
          GROUP BY contest_id
        )
        SELECT c.official_contest_id, p.non_ticket_votes - d.row_count AS unavailable
        FROM ballot.ballot_dataset d
        JOIN published p USING (contest_id)
        JOIN core.contest c USING (contest_id)
        WHERE d.election_chamber_id=?
        ORDER BY c.official_contest_id
        """,
        [SENATE_CHAMBER_ID],
    ).fetchall()
    add_check(
        "formal_ballot_state_availability_gaps",
        formal_ballot_availability_gaps,
        [
            ("ACT", 0),
            ("NSW", 10),
            ("NT", 0),
            ("QLD", 1),
            ("SA", 0),
            ("TAS", 1),
            ("VIC", 1),
            ("WA", 0),
        ],
        "rule_2010_formal_ballot_reconciliation",
    )
    add_check(
        "formal_ballot_invalid_preference_paths",
        connection.execute(
            """SELECT count(*) FROM ballot.ballot b
               JOIN ballot.ballot_dataset d USING (ballot_dataset_id)
               WHERE d.election_chamber_id=?
                 AND (b.preference_count IS NULL OR b.preference_count < 1)""",
            [SENATE_CHAMBER_ID],
        ).fetchone()[0],
        0,
        "rule_2010_formal_preference_path",
    )
    add_check(
        "group_voting_ticket_count",
        connection.execute("SELECT count(*) FROM ballot.group_voting_ticket").fetchone()[0],
        156,
        "rule_2010_group_voting_tickets",
    )
    add_check(
        "group_voting_ticket_preference_count",
        connection.execute(
            "SELECT count(*) FROM ballot.group_voting_ticket_preference"
        ).fetchone()[0],
        9048,
        "rule_2010_group_voting_tickets",
    )
    ticket_formal_mismatches = connection.execute(
        """
        WITH usage AS (
          SELECT contest_id,
                 sum(integer_value) FILTER (WHERE result_type='ticket_vote') AS ticket_votes,
                 sum(integer_value) FILTER (WHERE result_type='non_ticket_vote') AS non_ticket_votes
          FROM results.vote_result
          WHERE subject_type='contest' AND measure_type='votes' AND vote_type='total'
            AND result_type IN ('ticket_vote', 'non_ticket_vote')
          GROUP BY contest_id
        ), formal AS (
          SELECT contest_id, max(integer_value) AS formal_votes
          FROM results.participation_result
          WHERE measure_type='formal_votes' AND vote_type='total'
          GROUP BY contest_id
        )
        SELECT count(*) FROM usage JOIN formal USING (contest_id)
        WHERE usage.ticket_votes + usage.non_ticket_votes <> formal.formal_votes
        """
    ).fetchone()[0]
    add_check(
        "ticket_plus_non_ticket_formal_mismatches",
        ticket_formal_mismatches,
        0,
        "rule_2010_group_voting_ticket_reconciliation",
    )

    # Retain the inherited deterministic namespace so resumable builds reproduce
    # the already-reconciled validation identity byte for byte.
    validation_run_id = deterministic_uuid("validation_run", context.import_run_id, "stage_2")
    blockers = [check for check in checks if not check["passed"] and check["severity"] == "blocker"]
    warning_count = len(context.unresolved_parties)
    validation_status = "passed" if not blockers else "failed"
    connection.execute(
        "INSERT INTO audit.validation_run VALUES (?, ?, 'election', ?, '2010_federal_v1', ?, ?, ?, ?, ?, ?)",
        [validation_run_id, context.import_run_id, ELECTION_ID, context.started_at, datetime.now(timezone.utc),
         len(checks), len(blockers), warning_count, validation_status],
    )
    issue_rows = []
    for check in blockers:
        issue_rows.append(
            (deterministic_uuid("validation_issue", validation_run_id, check["name"]), validation_run_id,
             check["rule_id"], "blocker", None, None, None, None, None,
             f"{check['name']} did not reconcile.", str(check["observed"]), str(check["expected"]),
             "open", None, None, None)
        )
    for abbreviation, name in sorted(context.unresolved_parties):
        connection.execute(
            """UPDATE staging.source_record
               SET mapping_status='quarantined'
               WHERE coalesce(json_extract_string(source_native_json, '$.PartyAb'), '')=?
                 AND coalesce(json_extract_string(source_native_json, '$.PartyNm'), '')=?""",
            [abbreviation, name],
        )
        if name:
            connection.execute(
                """UPDATE staging.source_record
                   SET mapping_status='quarantined'
                   WHERE json_extract_string(source_native_json, '$.PartyName')=?
                      OR json_extract_string(source_native_json, '$.GroupNm')=?""",
                [name, name],
            )
        issue_rows.append(
            (deterministic_uuid("validation_issue", validation_run_id, "party", abbreviation, name),
             validation_run_id, "rule_unknown_source_labels_quarantined", "warning", "core", "candidacy",
             None, None, None, f"AEC party label is retained without a canonical Grand Database party match: {name or abbreviation}",
             f"{abbreviation}|{name}", "Canonical party ID or approved no-match", "open", None, None,
             "Official source label retained; no fallback party was assigned.")
        )
    if issue_rows:
        bulk_insert(connection, "INSERT INTO audit.validation_issue", issue_rows)
    return {
        "validation_run_id": str(validation_run_id),
        "status": validation_status,
        "blocker_count": len(blockers),
        "warning_count": warning_count,
        "checks": checks,
    }


def create_publication_and_derived(context: ImportContext, validation: dict) -> str | None:
    if validation["status"] != "passed":
        return None
    connection = context.connection
    revision_ids = sorted(context.revision_by_key.values())
    snapshot_hash = hashlib.sha256("\n".join(revision_ids).encode("utf-8")).hexdigest()
    snapshot_id = deterministic_uuid("publication_snapshot", ELECTION_ID, snapshot_hash)
    now = datetime.now(timezone.utc)
    connection.execute(
        "INSERT INTO publish.publication_snapshot VALUES (?, ?, ?, '0.2.0', 'approved', ?, 'Codex', ?, ?, ?)",
        [snapshot_id, "2010 federal election final AEC release", now, now,
         "All 47 governed AEC final-result revisions retrieved in the Stage 14.6 manifest.", snapshot_hash,
         "Automated reconciliation passed; unresolved canonical party mappings remain visible warnings."],
    )
    source_rows_to_insert = [
        (deterministic_uuid("publication_snapshot_source", snapshot_id, revision), snapshot_id, revision)
        for revision in revision_ids
    ]
    bulk_insert(connection, "INSERT INTO publish.publication_snapshot_source_revision", source_rows_to_insert)

    contest_summary_rows = []
    for contest, winner in connection.execute(
        """SELECT c.contest_id, min(o.candidacy_id) FILTER (WHERE o.elected_order=1 OR o.elected_order IS NULL)
           FROM core.contest c LEFT JOIN results.contest_outcome o ON o.contest_id=c.contest_id
           GROUP BY c.contest_id"""
    ).fetchall():
        tcp = connection.execute(
            """SELECT integer_value FROM results.vote_result
               WHERE contest_id=? AND result_type='tcp' AND vote_type='total' AND measure_type='votes'
                 AND election_reporting_unit_id IS NULL ORDER BY integer_value DESC""",
            [contest],
        ).fetchall()
        margin_votes = tcp[0][0] - tcp[1][0] if len(tcp) >= 2 else None
        percentages = connection.execute(
            """SELECT
                 max(decimal_value) FILTER (WHERE measure_type='turnout_percentage'),
                 max(decimal_value) FILTER (WHERE measure_type='informality_percentage')
               FROM results.participation_result WHERE contest_id=?""",
            [contest],
        ).fetchone()
        payload = f"{contest}|{winner}|{margin_votes}|{percentages[0]}|{percentages[1]}"
        contest_summary_rows.append(
            (deterministic_uuid("contest_summary", contest, snapshot_id), contest, snapshot_id, winner,
             margin_votes, None, percentages[0], percentages[1], "final", "1.0.0",
             hashlib.sha256(payload.encode()).hexdigest())
        )
    bulk_insert(connection, "INSERT INTO derived.contest_summary", contest_summary_rows)

    party_summary_rows = []
    for chamber_id in (HOUSE_CHAMBER_ID, SENATE_CHAMBER_ID):
        result_type = "first_preference" if chamber_id == HOUSE_CHAMBER_ID else "party_total"
        if chamber_id == HOUSE_CHAMBER_ID:
            vote_rows = connection.execute(
                """SELECT ca.party_id, sum(v.integer_value)
                   FROM results.vote_result v JOIN core.candidacy ca ON ca.candidacy_id=v.candidacy_id
                   JOIN core.contest c ON c.contest_id=v.contest_id
                   WHERE c.election_chamber_id=? AND v.result_type=? AND v.vote_type='total'
                     AND v.measure_type='votes' AND v.election_reporting_unit_id IS NULL AND ca.party_id IS NOT NULL
                   GROUP BY ca.party_id""",
                [chamber_id, result_type],
            ).fetchall()
        else:
            vote_rows = connection.execute(
                """SELECT v.party_id, sum(v.integer_value) FROM results.vote_result v
                   JOIN core.contest c ON c.contest_id=v.contest_id
                   WHERE c.election_chamber_id=? AND v.result_type=? AND v.vote_type='total'
                     AND v.measure_type='votes' AND v.party_id IS NOT NULL GROUP BY v.party_id""",
                [chamber_id, result_type],
            ).fetchall()
        total_votes = sum(row[1] for row in vote_rows)
        seats = dict(
            connection.execute(
                """SELECT ca.party_id, count(*) FROM results.contest_outcome o
                   JOIN core.candidacy ca ON ca.candidacy_id=o.candidacy_id
                   JOIN core.contest c ON c.contest_id=o.contest_id
                   WHERE c.election_chamber_id=? AND o.outcome_type='elected' AND ca.party_id IS NOT NULL
                   GROUP BY ca.party_id""",
                [chamber_id],
            ).fetchall()
        )
        for party_id, votes in vote_rows:
            share = (votes * 100 / total_votes) if total_votes else None
            payload = f"{chamber_id}|{party_id}|{votes}|{share}|{seats.get(party_id, 0)}"
            party_summary_rows.append(
                (deterministic_uuid("party_summary", chamber_id, party_id, snapshot_id), chamber_id, party_id,
                 snapshot_id, votes, share, None, seats.get(party_id, 0), None, "1.0.0",
                 hashlib.sha256(payload.encode()).hexdigest())
            )
    bulk_insert(connection, "INSERT INTO derived.party_summary", party_summary_rows)
    return str(snapshot_id)


def export_parquet(context: ImportContext) -> dict:
    import shutil

    root = context.project_root / "data" / "parquet" / "aec_2010" / "facts"
    if root.exists():
        shutil.rmtree(root)
    exports = []
    datasets = {
        "vote_result": (
            "results.vote_result",
            "vote_result_id",
            "source_revision_id",
        ),
        "participation_result": (
            "results.participation_result",
            "participation_result_id",
            "source_revision_id",
        ),
        "count_round": ('"count".count_round', "count_round_id", "source_revision_id"),
        "count_candidate_total": ('"count".count_candidate_total', "count_candidate_total_id", "source_revision_id"),
    }
    for dataset_family, (table, primary_key, revision_field) in datasets.items():
        revisions = context.connection.execute(f"SELECT DISTINCT {revision_field} FROM {table} ORDER BY 1").fetchall()
        for (revision,) in revisions:
            for chamber_code, chamber_id in (("house", HOUSE_CHAMBER_ID), ("senate", SENATE_CHAMBER_ID)):
                if dataset_family.startswith("count_"):
                    join = "JOIN \"count\".count_round cr ON cr.count_round_id=t.count_round_id" if dataset_family == "count_candidate_total" else ""
                    contest_expression = "cr.contest_id" if dataset_family == "count_candidate_total" else "t.contest_id"
                else:
                    join = ""
                    contest_expression = "t.contest_id"
                contestless_house = (
                    dataset_family == "vote_result"
                    and chamber_code == "house"
                    and revision == context.revision_by_key["house_tpp_state"]
                )
                contest_join = "LEFT JOIN" if contestless_house else "JOIN"
                chamber_filter = (
                    "(c.election_chamber_id=? OR t.contest_id IS NULL)"
                    if contestless_house
                    else "c.election_chamber_id=?"
                )
                count = context.connection.execute(
                    f"""SELECT count(*) FROM {table} t {join}
                         {contest_join} core.contest c ON c.contest_id={contest_expression}
                         WHERE t.{revision_field}=? AND {chamber_filter}""",
                    [revision, chamber_id],
                ).fetchone()[0]
                if not count:
                    continue
                directory = (
                    root / "jurisdiction_code=fed" / "election_year=2010" /
                    f"election_id={ELECTION_ID}" / f"chamber_code={chamber_code}" /
                    f"dataset_family={dataset_family}" / f"source_revision_id={revision}"
                )
                directory.mkdir(parents=True, exist_ok=True)
                output = directory / "part-00000.parquet"
                escaped_output = str(output).replace("'", "''")
                context.connection.execute(
                    f"""COPY (
                           SELECT t.* FROM {table} t {join}
                           {contest_join} core.contest c ON c.contest_id={contest_expression}
                           WHERE t.{revision_field}=? AND {chamber_filter}
                         ) TO '{escaped_output}' (FORMAT PARQUET, COMPRESSION ZSTD)""",
                    [revision, chamber_id],
                )
                sha = hashlib.sha256(output.read_bytes()).hexdigest()
                exports.append(
                    {"dataset_family": dataset_family, "chamber_code": chamber_code,
                     "source_revision_id": revision, "row_count": count,
                     "path": str(output.relative_to(context.project_root)), "sha256": sha}
                )
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "partition_count": len(exports), "partitions": exports}
    output_manifest = context.project_root / "data" / "manifests" / "aec_2010_parquet.json"
    output_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _rehydrate_context(
    connection: duckdb.DuckDBPyConnection,
    project_root: Path,
    manifest: dict,
    started_at: datetime,
    import_run_id: object,
) -> ImportContext:
    context = ImportContext(
        connection=connection,
        project_root=project_root,
        manifest=manifest,
        started_at=started_at,
        import_run_id=import_run_id,
        matcher=ReferenceMatcher(connection),
    )
    for source in manifest["sources"]:
        key = source["key"]
        context.source_by_key[key] = source
        context.revision_by_key[key] = source_revision_id(_source_file_id(key), source["sha256"])
    context.house_contests = {
        official_id: contest
        for official_id, contest in connection.execute(
            "SELECT official_contest_id, contest_id FROM core.contest WHERE election_chamber_id=?",
            [HOUSE_CHAMBER_ID],
        ).fetchall()
    }
    context.senate_contests = {
        official_id: contest
        for official_id, contest in connection.execute(
            "SELECT official_contest_id, contest_id FROM core.contest WHERE election_chamber_id=?",
            [SENATE_CHAMBER_ID],
        ).fetchall()
    }
    for chamber, chamber_id in (("house", HOUSE_CHAMBER_ID), ("senate", SENATE_CHAMBER_ID)):
        for official_contest_id, official_candidate_id, candidate in connection.execute(
            """SELECT c.official_contest_id, ca.official_candidate_id, ca.candidacy_id
               FROM core.candidacy ca JOIN core.contest c USING (contest_id)
               WHERE c.election_chamber_id=?""",
            [chamber_id],
        ).fetchall():
            context.candidacies[(chamber, official_contest_id, official_candidate_id)] = candidate
    context.ballot_groups = {
        (official_contest_id, official_group_id): ballot_group_id
        for official_contest_id, official_group_id, ballot_group_id in connection.execute(
            """SELECT c.official_contest_id, bg.official_group_id, bg.ballot_group_id
               FROM core.ballot_group bg JOIN core.contest c USING (contest_id)"""
        ).fetchall()
    }
    context.house_polling_units = {
        (official_contest_id, official_code): unit_id
        for official_contest_id, official_code, unit_id in connection.execute(
            """SELECT c.official_contest_id, u.official_reporting_unit_code,
                      u.election_reporting_unit_id
               FROM geography.election_reporting_unit u
               JOIN core.contest c USING (contest_id)
               WHERE c.election_chamber_id=? AND u.reporting_unit_type <> 'district_total'""",
            [HOUSE_CHAMBER_ID],
        ).fetchall()
    }
    context.house_state_units = {
        official_code.removeprefix("state:"): unit_id
        for official_code, unit_id in connection.execute(
            """SELECT official_reporting_unit_code, election_reporting_unit_id
               FROM geography.election_reporting_unit
               WHERE election_id=? AND contest_id IS NULL
                 AND reporting_unit_type='state_total'
                 AND official_reporting_unit_code LIKE 'state:%'""",
            [ELECTION_ID],
        ).fetchall()
    }
    context.senate_division_units = {
        (official_contest_id, official_code): unit_id
        for official_contest_id, official_code, unit_id in connection.execute(
            """SELECT c.official_contest_id, u.official_reporting_unit_code,
                      u.election_reporting_unit_id
               FROM geography.election_reporting_unit u
               JOIN core.contest c USING (contest_id)
               WHERE c.election_chamber_id=? AND u.reporting_unit_type='district_total'""",
            [SENATE_CHAMBER_ID],
        ).fetchall()
    }
    for abbreviation, name in connection.execute(
        """SELECT DISTINCT coalesce(official_party_abbreviation, ''),
                          coalesce(official_party_name, '')
           FROM core.candidacy
           WHERE party_id IS NULL
             AND coalesce(official_party_abbreviation, official_party_name) IS NOT NULL"""
    ).fetchall():
        context.unresolved_parties.add((abbreviation, name))
    for (name,) in connection.execute(
        """SELECT DISTINCT group_label FROM core.ballot_group
           WHERE party_id IS NULL AND group_label IS NOT NULL AND NOT ungrouped"""
    ).fetchall():
        context.unresolved_parties.add(("", name))
    return context


def _reset_resume_downstream(context: ImportContext) -> None:
    connection = context.connection
    connection.execute(
        """DELETE FROM provenance.row_lineage WHERE target_table IN (
             'participation_result', 'contest_outcome', 'elected_member',
             'count_round', 'count_candidate_total', 'preference_transfer'
           )"""
    )
    connection.execute("DELETE FROM results.elected_member")
    connection.execute("DELETE FROM results.contest_outcome")
    connection.execute("DELETE FROM results.participation_result")
    connection.execute('DELETE FROM "count".preference_transfer')
    connection.execute('DELETE FROM "count".count_candidate_total')
    connection.execute('DELETE FROM "count".count_round')
    connection.execute("DELETE FROM sync.external_identifier WHERE authority_id='authority_aec'")
    connection.execute("DELETE FROM audit.validation_issue")
    connection.execute("DELETE FROM audit.validation_run")
    connection.execute("DELETE FROM derived.preference_flow")
    connection.execute("DELETE FROM derived.party_summary")
    connection.execute("DELETE FROM derived.contest_summary")
    connection.execute("DELETE FROM publish.publication_snapshot_source_revision")
    connection.execute("DELETE FROM publish.publication_snapshot")
    connection.execute("DELETE FROM control.database_release WHERE release_id='release_0_2_0_aec_2010'")
    connection.execute("CHECKPOINT")


def _reset_validation_and_publication(context: ImportContext) -> None:
    """Retain completed facts while replacing a failed validation attempt."""

    connection = context.connection
    connection.execute("DELETE FROM audit.validation_issue")
    connection.execute("DELETE FROM audit.validation_run")
    connection.execute("DELETE FROM derived.preference_flow")
    connection.execute("DELETE FROM derived.party_summary")
    connection.execute("DELETE FROM derived.contest_summary")
    connection.execute("DELETE FROM publish.publication_snapshot_source_revision")
    connection.execute("DELETE FROM publish.publication_snapshot")
    connection.execute(
        "DELETE FROM control.database_release "
        "WHERE release_id='release_0_2_0_aec_2010'"
    )
    connection.execute("CHECKPOINT")


def resume_2010(database_path: Path, project_root: Path = PROJECT_ROOT) -> dict:
    manifest_path = project_root / "data" / "manifests" / "aec_2010_sources.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    expected_run_id = deterministic_uuid(
        "import_run", ELECTION_ID, manifest_hash, f"{ADAPTER_ID}.{ADAPTER_VERSION}"
    )
    temporary_directory = Path(tempfile.mkdtemp(prefix="politica-erd-duckdb-"))
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("SET TimeZone = 'Australia/Sydney'")
        escaped_project_root = str(project_root.resolve()).replace("'", "''")
        connection.execute(f"SET file_search_path='{escaped_project_root}'")
        connection.execute(f"SET temp_directory='{str(temporary_directory).replace("'", "''")}'")
        connection.execute("SET memory_limit='12GB'")
        connection.execute("SET preserve_insertion_order=false")
        connection.execute("SET threads=4")
        run = connection.execute(
            "SELECT import_run_id, started_at FROM provenance.import_run WHERE import_run_id=?",
            [expected_run_id],
        ).fetchone()
        if run is None:
            raise RuntimeError("No resumable 2010 import run matches the current source manifest.")
        import_run_id, started_at = run
        connection.execute(
            """UPDATE provenance.import_run SET completed_at=NULL, import_status='running',
               notes=? WHERE import_run_id=?""",
            ["Resumed from governed source-level checkpoints.", import_run_id],
        )
        context = _rehydrate_context(
            connection, project_root, manifest, started_at, import_run_id
        )
        formal_manifest_path = (
            project_root / "data" / "manifests" / "aec_2010_formal_preferences.json"
        )
        try:
            formal_manifest = json.loads(
                formal_manifest_path.read_text(encoding="utf-8")
            )
            _validate_formal_checkpoint(project_root, formal_manifest)
        except (FileNotFoundError, json.JSONDecodeError, RuntimeError):
            print(
                "[resume 0/7] Completing the governed formal-preference checkpoint",
                flush=True,
            )
            formal_manifest = import_pre_reform_preferences(context)
            refresh_data_dictionary(connection, "0.2.0")
            export_catalogues(connection, project_root)

        completed_fact_counts = connection.execute(
            """SELECT
                 (SELECT count(*) FROM results.vote_result),
                 (SELECT count(*) FROM results.participation_result),
                 (SELECT count(*) FROM results.contest_outcome),
                 (SELECT count(*) FROM "count".count_round),
                 (SELECT count(*) FROM "count".count_candidate_total),
                 (SELECT count(*) FROM "count".preference_transfer)"""
        ).fetchone()
        expected_completed_fact_counts = (
            205788,
            1896,
            190,
            1875,
            74400,
            9239,
        )
        if completed_fact_counts == expected_completed_fact_counts:
            print(
                "[resume 1/7] Reusing the complete governed relational checkpoint",
                flush=True,
            )
            _reset_validation_and_publication(context)
            vote_result_count = completed_fact_counts[0]
            participation_count = completed_fact_counts[1]
            outcome_count = completed_fact_counts[2]
            house_counts = (699, 4358, 2578)
            senate_counts = (1176, 70042, 6661)
            external_identifier_count = connection.execute(
                "SELECT count(*) FROM sync.external_identifier WHERE authority_id='authority_aec'"
            ).fetchone()[0]
        else:
            print("[resume 1/7] Completing checkpointed vote facts", flush=True)
            vote_result_count = import_vote_results(context)
            print("[resume 2/7] Resetting incomplete downstream stages", flush=True)
            _reset_resume_downstream(context)
            print("[resume 3/7] Importing participation and declared outcomes", flush=True)
            participation_count = import_participation(context)
            outcome_count = import_outcomes(context)
            _flush(connection, "provenance.row_lineage", context.lineage_rows)
            connection.execute("CHECKPOINT")
            print("[resume 4/7] Importing House and Senate preference counts", flush=True)
            house_counts = import_house_counts(context)
            senate_counts = import_senate_counts(context)
            _flush(connection, "provenance.row_lineage", context.lineage_rows)
            connection.execute("CHECKPOINT")
            external_identifier_count = import_external_identifiers(context)
        print("[resume 5/7] Finalising identifiers and reconciliations", flush=True)
        validation = reconcile(context)
        print("[resume 6/7] Creating publication and derived snapshots", flush=True)
        publication_snapshot_id = create_publication_and_derived(context, validation)
        connection.execute("CHECKPOINT")
        print("[resume 7/7] Exporting governed Parquet facts", flush=True)
        parquet_manifest = (
            export_parquet(context) if validation["status"] == "passed" else {"partition_count": 0}
        )
        completed_at = datetime.now(timezone.utc)
        staged_count = connection.execute("SELECT count(*) FROM staging.source_record").fetchone()[0]
        inserted_count = connection.execute(
            """SELECT
               (SELECT count(*) FROM core.contest) +
               (SELECT count(*) FROM core.candidacy) +
               (SELECT count(*) FROM results.vote_result) +
               (SELECT count(*) FROM results.participation_result) +
               (SELECT count(*) FROM results.contest_outcome) +
               (SELECT count(*) FROM "count".count_candidate_total)"""
        ).fetchone()[0]
        connection.execute(
            """UPDATE provenance.import_run SET completed_at=?, import_status=?, source_row_count=?,
               staged_row_count=?, inserted_row_count=?, rejected_row_count=0, notes=?
               WHERE import_run_id=?""",
            [
                completed_at,
                "validated" if validation["status"] == "passed" else "failed_validation",
                sum(source.get("row_count") or 0 for source in manifest["sources"]),
                staged_count,
                inserted_count + formal_manifest["ballot_count"],
                f"Stage 2 resumed import completed with {validation['warning_count']} canonical party-label warnings.",
                import_run_id,
            ],
        )
        connection.execute(
            """INSERT INTO control.database_release VALUES
               ('release_0_2_0_aec_2010', '0.2.0', ?, ?, ?, 'Codex', ?)""",
            [
                "validated" if validation["status"] == "passed" else "blocked",
                started_at,
                completed_at if validation["status"] == "passed" else None,
                "2010 federal House, House TPP, Senate and formal-preference final-results import.",
            ],
        )
        table_counts = {
            "contests": connection.execute("SELECT count(*) FROM core.contest").fetchone()[0],
            "candidacies": connection.execute("SELECT count(*) FROM core.candidacy").fetchone()[0],
            "reporting_units": connection.execute(
                "SELECT count(*) FROM geography.election_reporting_unit"
            ).fetchone()[0],
            "vote_results": connection.execute("SELECT count(*) FROM results.vote_result").fetchone()[0],
            "participation_results": connection.execute(
                "SELECT count(*) FROM results.participation_result"
            ).fetchone()[0],
            "count_rounds": connection.execute('SELECT count(*) FROM "count".count_round').fetchone()[0],
            "count_candidate_totals": connection.execute(
                'SELECT count(*) FROM "count".count_candidate_total'
            ).fetchone()[0],
            "preference_transfers": connection.execute(
                'SELECT count(*) FROM "count".preference_transfer'
            ).fetchone()[0],
            "outcomes": outcome_count,
            "lineage_records": connection.execute(
                "SELECT count(*) FROM provenance.row_lineage"
            ).fetchone()[0],
            "ballot_datasets": connection.execute(
                "SELECT count(*) FROM ballot.ballot_dataset"
            ).fetchone()[0],
            "group_voting_tickets": connection.execute(
                "SELECT count(*) FROM ballot.group_voting_ticket"
            ).fetchone()[0],
            "group_voting_ticket_preferences": connection.execute(
                "SELECT count(*) FROM ballot.group_voting_ticket_preference"
            ).fetchone()[0],
            "formal_ballots": formal_manifest["ballot_count"],
            "formal_ballot_preferences": formal_manifest["preference_count"],
        }
        report = {
            "status": "PASS" if validation["status"] == "passed" else "FAIL",
            "schema_version": "0.2.0",
            "election_id": ELECTION_ID,
            "import_run_id": str(import_run_id),
            "source_manifest_sha256": manifest_hash,
            "source_count": manifest["source_count"],
            "source_size_bytes": manifest["total_size_bytes"],
            "staged_source_rows": staged_count,
            "quarantined_mapping_rows": connection.execute(
                "SELECT count(*) FROM staging.source_record WHERE mapping_status='quarantined'"
            ).fetchone()[0],
            "table_counts": table_counts,
            "vote_result_rows_inserted": vote_result_count,
            "participation_rows_inserted": participation_count,
            "house_count_rows": {
                "rounds": house_counts[0],
                "candidate_totals": house_counts[1],
                "transfers": house_counts[2],
            },
            "senate_count_rows": {
                "rounds": senate_counts[0],
                "candidate_totals": senate_counts[1],
                "transfers": senate_counts[2],
            },
            "external_identifier_count": external_identifier_count,
            "publication_snapshot_id": publication_snapshot_id,
            "parquet_partition_count": parquet_manifest["partition_count"] + formal_manifest["file_count"],
            "formal_preferences": formal_manifest,
            "validation": validation,
            "completed_at": completed_at.isoformat(),
        }
        connection.execute("CHECKPOINT")
    except Exception:
        connection.execute(
            """UPDATE provenance.import_run SET completed_at=?, import_status='failed'
               WHERE import_run_id=?""",
            [datetime.now(timezone.utc), expected_run_id],
        )
        connection.execute("CHECKPOINT")
        raise
    finally:
        connection.close()
        shutil.rmtree(temporary_directory, ignore_errors=True)
    report_path = project_root / "dist" / "stage_14_6_2010_import_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_release_manifest(database_path, project_root, report)
    return report


def import_2010(
    database_path: Path,
    project_root: Path = PROJECT_ROOT,
    rebuild: bool = True,
    before_close: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
) -> dict:
    if rebuild:
        database_path.unlink(missing_ok=True)
        Path(str(database_path) + ".wal").unlink(missing_ok=True)
        build(database_path, project_root)
    manifest_path = project_root / "data" / "manifests" / "aec_2010_sources.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    started_at = datetime.now(timezone.utc)
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    import_run_id = deterministic_uuid(
        "import_run", ELECTION_ID, manifest_hash, f"{ADAPTER_ID}.{ADAPTER_VERSION}"
    )
    temporary_directory = Path(tempfile.mkdtemp(prefix="politica-erd-duckdb-"))
    connection = duckdb.connect(str(database_path))
    report: dict = {}
    try:
        connection.execute("SET TimeZone = 'Australia/Sydney'")
        escaped_project_root = str(project_root.resolve()).replace("'", "''")
        connection.execute(f"SET file_search_path='{escaped_project_root}'")
        connection.execute(f"SET temp_directory='{str(temporary_directory).replace("'", "''")}'")
        connection.execute("SET memory_limit='12GB'")
        connection.execute("SET preserve_insertion_order=false")
        connection.execute("SET threads=4")
        if rebuild:
            seed_packaged_reference_snapshot(connection, project_root)
        ensure_2010_historical_constituencies(connection)
        connection.execute(
            """INSERT INTO provenance.import_run VALUES
               (?, ?, ?, ?, ?, NULL, 'running', ?, NULL, NULL, NULL, 0, NULL, ?)""",
            [
                import_run_id,
                ELECTION_ID,
                ADAPTER_ID,
                ADAPTER_VERSION,
                started_at,
                manifest["source_count"],
                f"Source manifest SHA-256: {manifest_hash}",
            ],
        )
        context = ImportContext(
            connection=connection,
            project_root=project_root,
            manifest=manifest,
            started_at=started_at,
            import_run_id=import_run_id,
            matcher=ReferenceMatcher(connection),
        )
        print("[1/13] Registering immutable source revisions", flush=True)
        register_sources(context)
        print("[2/13] Preserving source-native staging rows", flush=True)
        staged_count = stage_sources(context)
        print("[3/13] Creating election, contests and candidacies", flush=True)
        seed_election(context)
        import_contests_and_candidates(context)
        print("[4/13] Creating ballot structure and reporting units", flush=True)
        import_ballot_structure(context)
        group_ticket_counts = import_group_voting_tickets(context)
        import_reporting_units(context)
        print("[5/13] Transforming Senate non-ticket BTL ballots", flush=True)
        formal_preference_manifest = import_pre_reform_preferences(context)
        refresh_data_dictionary(connection, "0.2.0")
        export_catalogues(connection, project_root)
        print("[6/13] Importing vote facts and ticket usage", flush=True)
        vote_result_count = import_vote_results(context)
        print("[7/13] Importing participation and outcomes", flush=True)
        participation_count = import_participation(context)
        outcome_count = import_outcomes(context)
        print("[8/13] Importing House and Senate count rounds", flush=True)
        house_counts = import_house_counts(context)
        senate_counts = import_senate_counts(context)
        print("[9/13] Finalising external identifiers and lineage", flush=True)
        external_identifier_count = import_external_identifiers(context)
        _flush(connection, "provenance.row_lineage", context.lineage_rows)
        print("[10/13] Running blocking reconciliations", flush=True)
        validation = reconcile(context)
        print("[11/13] Creating publication and derived snapshots", flush=True)
        publication_snapshot_id = create_publication_and_derived(context, validation)
        connection.execute("CHECKPOINT")
        print("[12/13] Exporting partitioned Parquet facts", flush=True)
        parquet_manifest = export_parquet(context) if validation["status"] == "passed" else {"partition_count": 0}
        completed_at = datetime.now(timezone.utc)
        inserted_count = connection.execute(
            """SELECT
               (SELECT count(*) FROM core.contest) +
               (SELECT count(*) FROM core.candidacy) +
               (SELECT count(*) FROM results.vote_result) +
               (SELECT count(*) FROM results.participation_result) +
               (SELECT count(*) FROM results.contest_outcome) +
               (SELECT count(*) FROM "count".count_candidate_total)"""
        ).fetchone()[0]
        connection.execute(
            """UPDATE provenance.import_run SET completed_at=?, import_status=?, source_row_count=?,
               staged_row_count=?, inserted_row_count=?, rejected_row_count=?, notes=? WHERE import_run_id=?""",
            [completed_at, "validated" if validation["status"] == "passed" else "failed_validation",
             sum(source.get("row_count") or 0 for source in manifest["sources"]),
             staged_count, inserted_count + formal_preference_manifest["ballot_count"], 0,
            f"Stage 14.6 import completed with {validation['warning_count']} unresolved canonical party-label warnings.",
             import_run_id],
        )
        connection.execute(
            """INSERT INTO control.database_release VALUES
               ('release_0_2_0_aec_2010', '0.2.0', ?, ?, ?, 'Codex', ?)""",
            ["validated" if validation["status"] == "passed" else "blocked", started_at,
             completed_at if validation["status"] == "passed" else None,
             "2010 federal House, House TPP, Senate and formal-preference final-results import."],
        )
        table_counts = {
            "contests": connection.execute("SELECT count(*) FROM core.contest").fetchone()[0],
            "candidacies": connection.execute("SELECT count(*) FROM core.candidacy").fetchone()[0],
            "reporting_units": connection.execute("SELECT count(*) FROM geography.election_reporting_unit").fetchone()[0],
            "vote_results": connection.execute("SELECT count(*) FROM results.vote_result").fetchone()[0],
            "participation_results": connection.execute("SELECT count(*) FROM results.participation_result").fetchone()[0],
            "count_rounds": connection.execute('SELECT count(*) FROM "count".count_round').fetchone()[0],
            "count_candidate_totals": connection.execute('SELECT count(*) FROM "count".count_candidate_total').fetchone()[0],
            "preference_transfers": connection.execute('SELECT count(*) FROM "count".preference_transfer').fetchone()[0],
            "outcomes": outcome_count,
            "lineage_records": connection.execute("SELECT count(*) FROM provenance.row_lineage").fetchone()[0],
            "ballot_datasets": connection.execute("SELECT count(*) FROM ballot.ballot_dataset").fetchone()[0],
            "group_voting_tickets": group_ticket_counts[0],
            "group_voting_ticket_preferences": group_ticket_counts[1],
            "formal_ballots": formal_preference_manifest["ballot_count"],
            "formal_ballot_preferences": formal_preference_manifest["preference_count"],
        }
        report = {
            "status": "PASS" if validation["status"] == "passed" else "FAIL",
            "schema_version": "0.2.0",
            "election_id": ELECTION_ID,
            "import_run_id": str(import_run_id),
            "source_manifest_sha256": manifest_hash,
            "source_count": manifest["source_count"],
            "source_size_bytes": manifest["total_size_bytes"],
            "staged_source_rows": staged_count,
            "quarantined_mapping_rows": connection.execute(
                "SELECT count(*) FROM staging.source_record WHERE mapping_status='quarantined'"
            ).fetchone()[0],
            "table_counts": table_counts,
            "vote_result_rows_inserted": vote_result_count,
            "participation_rows_inserted": participation_count,
            "house_count_rows": {"rounds": house_counts[0], "candidate_totals": house_counts[1], "transfers": house_counts[2]},
            "senate_count_rows": {"rounds": senate_counts[0], "candidate_totals": senate_counts[1], "transfers": senate_counts[2]},
            "external_identifier_count": external_identifier_count,
            "publication_snapshot_id": publication_snapshot_id,
            "parquet_partition_count": parquet_manifest["partition_count"] + formal_preference_manifest["file_count"],
            "formal_preferences": formal_preference_manifest,
            "validation": validation,
            "completed_at": completed_at.isoformat(),
        }
        print("[13/13] Finalising governed build report", flush=True)
        connection.execute("CHECKPOINT")
        # Release builders may need to materialise portable artifacts while the
        # reconciled database handle is still authoritative.  In particular,
        # this avoids relying on a large monolithic DuckDB file surviving an
        # intermediate filesystem hand-off before its tables are exported.
        if before_close is not None:
            before_close(connection)
    except Exception:
        connection.execute(
            "UPDATE provenance.import_run SET completed_at=?, import_status='failed' WHERE import_run_id=?",
            [datetime.now(timezone.utc), import_run_id],
        )
        raise
    finally:
        connection.close()
        shutil.rmtree(temporary_directory, ignore_errors=True)
    report_path = project_root / "dist" / "stage_14_6_2010_import_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_release_manifest(database_path, project_root, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "data" / "database" / "politica_election_results.duckdb",
    )
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    report = (
        resume_2010(args.database)
        if args.resume
        else import_2010(args.database, rebuild=not args.no_rebuild)
    )
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
