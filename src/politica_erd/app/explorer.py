from __future__ import annotations

import csv
import io
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb


DATASET_LABELS = {
    "results": "Election results",
    "outcomes": "Elected candidates",
    "participation": "Participation and turnout",
    "count_rounds": "Senate count rounds",
    "count_totals": "Senate candidate count totals",
    "ballot_datasets": "Formal-ballot datasets",
    "contests": "Contests and candidacies",
}

STANDARD_STATES = ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA")
STATE_SOURCE_SQL = """upper(trim(COALESCE(
    sc.state_territory,
    c.official_contest_id,
    c.contest_name
)))"""
STATE_SQL = f"""CASE {STATE_SOURCE_SQL}
    WHEN 'ACT' THEN 'ACT'
    WHEN 'AUSTRALIAN CAPITAL TERRITORY' THEN 'ACT'
    WHEN 'NSW' THEN 'NSW'
    WHEN 'NEW SOUTH WALES' THEN 'NSW'
    WHEN 'NT' THEN 'NT'
    WHEN 'NORTHERN TERRITORY' THEN 'NT'
    WHEN 'QLD' THEN 'QLD'
    WHEN 'QUEENSLAND' THEN 'QLD'
    WHEN 'SA' THEN 'SA'
    WHEN 'SOUTH AUSTRALIA' THEN 'SA'
    WHEN 'TAS' THEN 'TAS'
    WHEN 'TASMANIA' THEN 'TAS'
    WHEN 'VIC' THEN 'VIC'
    WHEN 'VICTORIA' THEN 'VIC'
    WHEN 'WA' THEN 'WA'
    WHEN 'WESTERN AUSTRALIA' THEN 'WA'
END"""
RESULT_CHAMBER_SQL = """COALESCE(
    ch.chamber_id,
    CASE
      WHEN v.result_type='group_total' OR v.subject_type IN ('ballot_group','source_group')
        THEN 'chamber_senate'
      WHEN v.result_type IN ('tpp','tcp') THEN 'chamber_house'
    END
)"""
RESULT_CHAMBER_NAME_SQL = """COALESCE(
    ch.chamber_name,
    CASE
      WHEN v.result_type='group_total' OR v.subject_type IN ('ballot_group','source_group')
        THEN 'Senate'
      WHEN v.result_type IN ('tpp','tcp') THEN 'House of Representatives'
    END
)"""
REPORTING_LEVEL_SQL = """CASE
    WHEN v.election_reporting_unit_id IS NULL THEN 'contest'
    WHEN u.reporting_unit_type IN ('state_total','national_total') THEN 'state'
    WHEN u.reporting_unit_type='division' THEN 'division'
    WHEN u.reporting_unit_type='polling_place' THEN 'polling_place'
    ELSE u.reporting_unit_type
END"""


class ExplorerError(ValueError):
    pass


class ExportTooLargeError(ExplorerError):
    pass


@dataclass(frozen=True)
class ExplorerFilters:
    election_id: str | None = None
    chamber_id: str | None = None
    state: str | None = None
    contest_id: str | None = None
    result_type: str | None = None
    vote_type: str | None = None
    reporting_level: str | None = None
    q: str | None = None


@dataclass(frozen=True)
class QuerySpec:
    sql: str
    params: tuple[Any, ...]
    order_by: str


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if isinstance(value, uuid.UUID) else value


def _safe_export_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value[:120] or "politica_export"


class ElectionExplorer:
    """Fixed, read-only access to the checksum-verified governed release."""

    def __init__(
        self,
        database_resolver: Callable[[], Path],
        external_root_resolver: Callable[[Path], Path],
        *,
        app_version: str,
        max_export_rows: int = 1_000_000,
    ) -> None:
        self._database_resolver = database_resolver
        self._external_root_resolver = external_root_resolver
        self.app_version = app_version
        self.max_export_rows = max_export_rows

    def _database(self) -> Path:
        database = self._database_resolver()
        if not database.is_file():
            raise ExplorerError(f"The governed database is unavailable: {database}")
        return database

    def _connect(self, database: Path | None = None) -> duckdb.DuckDBPyConnection:
        database = database or self._database()
        connection = duckdb.connect(str(database), read_only=True)
        root = str(self._external_root_resolver(database).resolve()).replace("'", "''")
        connection.execute(f"SET file_search_path='{root}'")
        return connection

    @staticmethod
    def _metadata(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
        schema = connection.execute(
            "SELECT schema_version FROM control.schema_version ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
        release = connection.execute(
            """SELECT release_id, release_status, published_at
               FROM control.database_release
               ORDER BY published_at DESC NULLS LAST, release_started_at DESC
               LIMIT 1"""
        ).fetchone()
        return {
            "schema_version": schema[0] if schema else None,
            "release_id": release[0] if release else None,
            "release_status": release[1] if release else None,
            "release_published_at": _json_value(release[2]) if release else None,
        }

    def catalogue(self) -> dict[str, Any]:
        database = self._database()
        connection = self._connect(database)
        try:
            metadata = self._metadata(connection)
            elections = self._rows(
                connection.execute(
                    """SELECT e.election_id, e.official_event_id, e.election_name,
                              e.election_date, e.election_year, e.publication_status,
                              count(DISTINCT c.contest_id) AS contest_count,
                              count(DISTINCT ca.candidacy_id) AS candidacy_count
                       FROM core.election e
                       LEFT JOIN core.election_chamber ec
                         ON ec.election_id=e.election_id AND ec.record_status='active'
                       LEFT JOIN core.contest c
                         ON c.election_chamber_id=ec.election_chamber_id
                        AND c.record_status='active'
                       LEFT JOIN core.candidacy ca
                         ON ca.contest_id=c.contest_id AND ca.record_status='active'
                       WHERE e.record_status='active'
                       GROUP BY e.election_id, e.official_event_id, e.election_name,
                                e.election_date, e.election_year, e.publication_status
                       ORDER BY e.election_date DESC, e.election_name"""
                )
            )
            chambers = self._rows(
                connection.execute(
                    """SELECT ec.election_id, ec.election_chamber_id, ch.chamber_id,
                              ch.chamber_code, ch.chamber_name,
                              count(DISTINCT c.contest_id) AS contest_count
                       FROM core.election_chamber ec
                       JOIN control.chamber ch USING (chamber_id)
                       LEFT JOIN core.contest c
                         ON c.election_chamber_id=ec.election_chamber_id
                        AND c.record_status='active'
                       WHERE ec.record_status='active'
                       GROUP BY ec.election_id, ec.election_chamber_id, ch.chamber_id,
                                ch.chamber_code, ch.chamber_name
                       ORDER BY ec.election_id, ch.chamber_name"""
                )
            )
            contests = self._rows(
                connection.execute(
                    f"""SELECT e.election_id, e.election_date,
                               ch.chamber_id, ch.chamber_name,
                               c.contest_id, c.contest_name, {STATE_SQL} AS state,
                               c.vacancies, c.contest_status,
                               count(ca.candidacy_id) AS candidacy_count
                        FROM core.contest c
                        JOIN core.election_chamber ec USING (election_chamber_id)
                        JOIN core.election e USING (election_id)
                        JOIN control.chamber ch USING (chamber_id)
                        LEFT JOIN core.contest_constituency_snapshot cs USING (contest_id)
                        LEFT JOIN sync.constituency sc
                          ON sc.constituency_id=COALESCE(
                               cs.canonical_constituency_id, c.canonical_constituency_id
                             )
                        LEFT JOIN core.candidacy ca
                          ON ca.contest_id=c.contest_id AND ca.record_status='active'
                        WHERE c.record_status='active' AND ec.record_status='active'
                          AND e.record_status='active'
                        GROUP BY e.election_id, e.election_date,
                                 ch.chamber_id, ch.chamber_name,
                                 c.contest_id, c.contest_name, state,
                                 c.vacancies, c.contest_status
                        ORDER BY e.election_date DESC, ch.chamber_name, state,
                                 c.contest_name"""
                )
            )
            result_types = [
                row[0]
                for row in connection.execute(
                    """SELECT DISTINCT result_type FROM results.vote_result
                       WHERE record_status='active' ORDER BY result_type"""
                ).fetchall()
            ]
            vote_types = [
                row[0]
                for row in connection.execute(
                    """SELECT DISTINCT vote_type FROM results.vote_result
                       WHERE record_status='active' ORDER BY vote_type"""
                ).fetchall()
            ]
            states = sorted(
                {
                    row["state"]
                    for row in contests
                    if row.get("state") in STANDARD_STATES
                }
            )
            counts = connection.execute(
                """SELECT
                     (SELECT count(*) FROM core.contest WHERE record_status='active'),
                     (SELECT count(*) FROM core.candidacy WHERE record_status='active'),
                     (SELECT count(*) FROM results.vote_result WHERE record_status='active'),
                     (SELECT count(*) FROM results.participation_result
                       WHERE record_status='active'),
                     (SELECT count(*) FROM results.contest_outcome
                       WHERE record_status='active'),
                     (SELECT count(*) FROM "count".count_round cr
                       JOIN provenance.source_file_revision sr
                         ON sr.source_revision_id=cr.source_revision_id
                       WHERE sr.record_status='active'),
                     (SELECT count(*) FROM ballot.ballot_dataset
                       WHERE record_status='active'),
                     (SELECT coalesce(sum(row_count), 0) FROM ballot.ballot_dataset
                       WHERE record_status='active')"""
            ).fetchone()
        finally:
            connection.close()
        return {
            "application_version": self.app_version,
            "database": {
                **metadata,
                "path": str(database),
                "size_bytes": database.stat().st_size,
            },
            "elections": elections,
            "chambers": chambers,
            "contests": contests,
            "states": states,
            "result_types": result_types,
            "vote_types": vote_types,
            "reporting_levels": ["contest", "state", "division", "polling_place", "all"],
            "datasets": [
                {"dataset": key, "label": label} for key, label in DATASET_LABELS.items()
            ],
            "counts": {
                "contests": counts[0],
                "candidacies": counts[1],
                "results": counts[2],
                "participation": counts[3],
                "outcomes": counts[4],
                "count_rounds": counts[5],
                "ballot_datasets": counts[6],
                "formal_ballots": counts[7],
            },
        }

    @staticmethod
    def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
        names = [column[0] for column in cursor.description]
        return [
            {name: _json_value(value) for name, value in zip(names, row, strict=True)}
            for row in cursor.fetchall()
        ]

    @staticmethod
    def _conditions(
        filters: ExplorerFilters,
        *,
        search_columns: tuple[str, ...],
        supports_results: bool = False,
    ) -> tuple[str, tuple[Any, ...]]:
        conditions: list[str] = []
        params: list[Any] = []
        for value, column in (
            (filters.election_id, "election_id"),
            (filters.chamber_id, "chamber_id"),
            (filters.contest_id, "contest_id"),
        ):
            if value:
                conditions.append(f"{column}=?")
                params.append(value)
        # Historical Grand Database constituency rows can contain lower-case
        # state codes. State filtering is therefore case-insensitive while the
        # fixed feed contract and returned values remain unchanged.
        if filters.state:
            conditions.append("upper(state)=upper(?)")
            params.append(filters.state)
        if supports_results:
            for value, column in (
                (filters.result_type, "result_type"),
                (filters.vote_type, "vote_type"),
            ):
                if value:
                    conditions.append(f"{column}=?")
                    params.append(value)
            if filters.reporting_level and filters.reporting_level != "all":
                conditions.append("reporting_level=?")
                params.append(filters.reporting_level)
        if filters.q:
            expression = ", ".join(search_columns)
            conditions.append(f"contains(lower(concat_ws(' ', {expression})), lower(?))")
            params.append(filters.q.strip())
        return (" AND ".join(conditions) if conditions else "TRUE"), tuple(params)

    def _spec(self, dataset: str, filters: ExplorerFilters) -> QuerySpec:
        if dataset not in DATASET_LABELS:
            raise ExplorerError(f"Unknown explorer dataset: {dataset}")
        builders = {
            "results": self._results_spec,
            "outcomes": self._outcomes_spec,
            "participation": self._participation_spec,
            "count_rounds": self._count_rounds_spec,
            "count_totals": self._count_totals_spec,
            "ballot_datasets": self._ballot_datasets_spec,
            "contests": self._contests_spec,
        }
        return builders[dataset](filters)

    def _results_spec(self, filters: ExplorerFilters) -> QuerySpec:
        conditions, params = self._conditions(
            filters,
            supports_results=True,
            search_columns=("contest_name", "subject_name", "party_name", "reporting_unit"),
        )
        raw = f"""SELECT
                 v.election_id, e.election_name, e.election_date,
                 {RESULT_CHAMBER_SQL} AS chamber_id,
                 {RESULT_CHAMBER_NAME_SQL} AS chamber_name,
                 v.contest_id, c.contest_name, {STATE_SQL} AS state,
                 v.election_reporting_unit_id,
                 u.official_label AS reporting_unit,
                 {REPORTING_LEVEL_SQL} AS reporting_level,
                 v.subject_type,
                 COALESCE(CAST(ca.candidacy_id AS VARCHAR),
                          CAST(bg.ballot_group_id AS VARCHAR), v.party_id,
                          v.question_option_code, v.contest_id) AS subject_id,
                 COALESCE(ca.party_id, bg.party_id, v.party_id) AS party_id,
                 COALESCE(ca.ballot_name, bg.group_label, subject_party.party_name,
                          v.question_option_code, c.contest_name) AS subject_name,
                 COALESCE(candidate_party.abbreviation, candidate_party.short_name,
                          candidate_party.party_name, group_party.abbreviation,
                          group_party.short_name, group_party.party_name,
                          subject_party.abbreviation, subject_party.short_name,
                          subject_party.party_name, ca.official_party_abbreviation,
                          ca.official_party_name) AS party_name,
                 COALESCE(candidate_party.colour_hex, group_party.colour_hex,
                          subject_party.colour_hex) AS party_colour,
                 v.result_type, v.vote_type, v.measure_type,
                 v.integer_value, v.decimal_value, v.source_revision_id
               FROM results.vote_result v
               JOIN core.election e USING (election_id)
               LEFT JOIN core.contest c USING (contest_id)
               LEFT JOIN core.election_chamber ec USING (election_chamber_id)
               LEFT JOIN control.chamber ch USING (chamber_id)
               LEFT JOIN core.contest_constituency_snapshot cs USING (contest_id)
               LEFT JOIN sync.constituency sc
                 ON sc.constituency_id=COALESCE(
                      cs.canonical_constituency_id, c.canonical_constituency_id
                    )
               LEFT JOIN geography.election_reporting_unit u
                 USING (election_reporting_unit_id)
               LEFT JOIN core.candidacy ca USING (candidacy_id)
               LEFT JOIN sync.party candidate_party ON candidate_party.party_id=ca.party_id
               LEFT JOIN core.ballot_group bg USING (ballot_group_id)
               LEFT JOIN sync.party group_party ON group_party.party_id=bg.party_id
               LEFT JOIN sync.party subject_party ON subject_party.party_id=v.party_id
               WHERE v.record_status='active'"""
        sql = f"""WITH raw AS ({raw}), filtered AS (
                    SELECT * FROM raw WHERE {conditions}
                  )
                  SELECT election_id, election_name, election_date,
                         chamber_id, chamber_name, contest_id, contest_name, state,
                         election_reporting_unit_id, reporting_unit, reporting_level,
                         subject_type, subject_id, party_id, subject_name, party_name, party_colour,
                         result_type, vote_type,
                         max(integer_value) FILTER (WHERE measure_type='votes') AS votes,
                         max(decimal_value) FILTER (WHERE measure_type='vote_share') AS vote_share,
                         max(decimal_value) FILTER (WHERE measure_type='swing') AS swing,
                         string_agg(DISTINCT source_revision_id, '|'
                                    ORDER BY source_revision_id) AS source_revision_id
                  FROM filtered
                  GROUP BY election_id, election_name, election_date,
                           chamber_id, chamber_name, contest_id, contest_name, state,
                           election_reporting_unit_id, reporting_unit, reporting_level,
                           subject_type, subject_id, party_id, subject_name, party_name, party_colour,
                           result_type, vote_type"""
        return QuerySpec(
            sql,
            params,
            "election_date DESC, chamber_name, state, contest_name, reporting_level, "
            "reporting_unit, result_type, vote_type, votes DESC NULLS LAST, subject_name",
        )

    def _outcomes_spec(self, filters: ExplorerFilters) -> QuerySpec:
        conditions, params = self._conditions(
            filters,
            search_columns=("contest_name", "candidate_name", "party_name", "person_name"),
        )
        sql = f"""WITH raw AS (
                    SELECT e.election_id, e.election_name, e.election_date,
                           ch.chamber_id, ch.chamber_name,
                           c.contest_id, c.contest_name, {STATE_SQL} AS state,
                           CAST(ca.candidacy_id AS VARCHAR) AS candidacy_id,
                           ca.person_id, ca.party_id,
                           ca.ballot_name AS candidate_name,
                           COALESCE(pe.display_name, pe.full_name) AS person_name,
                           COALESCE(p.abbreviation, p.short_name, p.party_name,
                                    ca.official_party_abbreviation,
                                    ca.official_party_name) AS party_name,
                           p.colour_hex AS party_colour,
                           c.contest_status, o.outcome_type, o.elected_order, o.declared_at,
                           o.publication_status AS outcome_publication_status,
                           o.source_revision_id
                    FROM results.contest_outcome o
                    JOIN core.contest c USING (contest_id)
                    JOIN core.election_chamber ec USING (election_chamber_id)
                    JOIN core.election e USING (election_id)
                    JOIN control.chamber ch USING (chamber_id)
                    JOIN core.candidacy ca USING (candidacy_id)
                    LEFT JOIN sync.person pe USING (person_id)
                    LEFT JOIN sync.party p USING (party_id)
                    LEFT JOIN core.contest_constituency_snapshot cs USING (contest_id)
                    LEFT JOIN sync.constituency sc
                      ON sc.constituency_id=COALESCE(
                           cs.canonical_constituency_id, c.canonical_constituency_id
                         )
                    WHERE o.record_status='active'
                  ) SELECT * FROM raw WHERE {conditions}"""
        return QuerySpec(sql, params, "election_date DESC, chamber_name, state, contest_name, elected_order, candidate_name")

    def _participation_spec(self, filters: ExplorerFilters) -> QuerySpec:
        conditions, params = self._conditions(
            filters,
            search_columns=("contest_name", "reporting_unit", "measure_type", "vote_type"),
        )
        sql = f"""WITH raw AS (
                    SELECT p.election_id, e.election_name, e.election_date,
                           ch.chamber_id, ch.chamber_name,
                           p.contest_id, c.contest_name, {STATE_SQL} AS state,
                           p.election_reporting_unit_id,
                           u.official_label AS reporting_unit,
                           COALESCE(u.reporting_unit_type,
                                    CASE WHEN p.contest_id IS NOT NULL
                                         THEN 'contest' ELSE 'election' END) AS reporting_level,
                           p.vote_type, p.measure_type, p.integer_value, p.decimal_value,
                           p.value_status, p.source_revision_id
                    FROM results.participation_result p
                    JOIN core.election e USING (election_id)
                    LEFT JOIN core.contest c USING (contest_id)
                    LEFT JOIN core.election_chamber ec USING (election_chamber_id)
                    LEFT JOIN control.chamber ch USING (chamber_id)
                    LEFT JOIN core.contest_constituency_snapshot cs USING (contest_id)
                    LEFT JOIN sync.constituency sc
                      ON sc.constituency_id=COALESCE(
                           cs.canonical_constituency_id, c.canonical_constituency_id
                         )
                    LEFT JOIN geography.election_reporting_unit u
                      USING (election_reporting_unit_id)
                    WHERE p.record_status='active'
                  ) SELECT * FROM raw WHERE {conditions}"""
        return QuerySpec(sql, params, "election_date DESC, chamber_name, state, contest_name, reporting_unit, vote_type, measure_type")

    def _count_rounds_spec(self, filters: ExplorerFilters) -> QuerySpec:
        conditions, params = self._conditions(
            filters,
            search_columns=("contest_name", "round_label", "action_type", "remarks"),
        )
        sql = f"""WITH candidate_counts AS (
                    SELECT count_round_id, count(*) AS candidate_total_rows
                    FROM "count".count_candidate_total ct
                    JOIN provenance.source_file_revision sr
                      ON sr.source_revision_id=ct.source_revision_id
                    WHERE sr.record_status='active' GROUP BY count_round_id
                  ), transfer_counts AS (
                    SELECT count_round_id, count(*) AS transfer_rows,
                           count(*) FILTER (WHERE exhausted) AS exhausted_rows
                    FROM "count".preference_transfer pt
                    JOIN provenance.source_file_revision sr
                      ON sr.source_revision_id=pt.source_revision_id
                    WHERE sr.record_status='active' GROUP BY count_round_id
                  ), raw AS (
                    SELECT e.election_id, e.election_name, e.election_date,
                           ch.chamber_id, ch.chamber_name,
                           c.contest_id, c.contest_name, {STATE_SQL} AS state,
                           cr.round_number, cr.round_label, cr.action_type,
                           cr.quota_value, cr.transfer_value, cr.remarks,
                           coalesce(cc.candidate_total_rows, 0) AS candidate_total_rows,
                           coalesce(tc.transfer_rows, 0) AS transfer_rows,
                           coalesce(tc.exhausted_rows, 0) AS exhausted_rows,
                           cr.source_revision_id
                    FROM "count".count_round cr
                    JOIN provenance.source_file_revision sr
                      ON sr.source_revision_id=cr.source_revision_id
                     AND sr.record_status='active'
                    JOIN core.contest c USING (contest_id)
                    JOIN core.election_chamber ec USING (election_chamber_id)
                    JOIN core.election e USING (election_id)
                    JOIN control.chamber ch USING (chamber_id)
                    LEFT JOIN core.contest_constituency_snapshot cs USING (contest_id)
                    LEFT JOIN sync.constituency sc
                      ON sc.constituency_id=COALESCE(
                           cs.canonical_constituency_id, c.canonical_constituency_id
                         )
                    LEFT JOIN candidate_counts cc USING (count_round_id)
                    LEFT JOIN transfer_counts tc USING (count_round_id)
                  ) SELECT * FROM raw WHERE {conditions}"""
        return QuerySpec(sql, params, "election_date DESC, state, contest_name, round_number")

    def _count_totals_spec(self, filters: ExplorerFilters) -> QuerySpec:
        conditions, params = self._conditions(
            filters,
            search_columns=("contest_name", "candidate_name", "party_name", "candidate_count_status"),
        )
        sql = f"""WITH raw AS (
                    SELECT e.election_id, e.election_name, e.election_date,
                           ch.chamber_id, ch.chamber_name,
                           c.contest_id, c.contest_name, {STATE_SQL} AS state,
                           cr.round_number, cr.round_label, cr.action_type,
                           cr.quota_value, cr.transfer_value, cr.remarks,
                           CAST(ca.candidacy_id AS VARCHAR) AS candidacy_id,
                           ca.ballot_name AS candidate_name,
                           COALESCE(p.abbreviation, p.short_name, p.party_name,
                                    ca.official_party_abbreviation,
                                    ca.official_party_name) AS party_name,
                           p.colour_hex AS party_colour,
                           ct.papers_value, ct.votes_value, ct.progressive_total,
                           ct.candidate_count_status, ct.value_status,
                           ct.source_revision_id
                    FROM "count".count_candidate_total ct
                    JOIN provenance.source_file_revision sr
                      ON sr.source_revision_id=ct.source_revision_id
                     AND sr.record_status='active'
                    JOIN "count".count_round cr USING (count_round_id)
                    JOIN core.contest c USING (contest_id)
                    JOIN core.election_chamber ec USING (election_chamber_id)
                    JOIN core.election e USING (election_id)
                    JOIN control.chamber ch USING (chamber_id)
                    JOIN core.candidacy ca USING (candidacy_id)
                    LEFT JOIN sync.party p USING (party_id)
                    LEFT JOIN core.contest_constituency_snapshot cs USING (contest_id)
                    LEFT JOIN sync.constituency sc
                      ON sc.constituency_id=COALESCE(
                           cs.canonical_constituency_id, c.canonical_constituency_id
                         )
                  ) SELECT * FROM raw WHERE {conditions}"""
        return QuerySpec(sql, params, "election_date DESC, state, contest_name, round_number, progressive_total DESC NULLS LAST, candidate_name")

    def _ballot_datasets_spec(self, filters: ExplorerFilters) -> QuerySpec:
        conditions, params = self._conditions(
            filters,
            search_columns=("contest_name", "dataset_scope", "ballot_channel", "anonymisation_method"),
        )
        sql = f"""WITH raw AS (
                    SELECT e.election_id, e.election_name, e.election_date,
                           ch.chamber_id, ch.chamber_name,
                           bd.contest_id, c.contest_name, {STATE_SQL} AS state,
                           CAST(bd.ballot_dataset_id AS VARCHAR) AS ballot_dataset_id,
                           bd.dataset_scope, bd.ballot_channel,
                           bd.anonymisation_method, bd.privacy_notes,
                           bd.schema_version, bd.row_count, bd.source_revision_id
                    FROM ballot.ballot_dataset bd
                    JOIN core.election_chamber ec USING (election_chamber_id)
                    JOIN core.election e USING (election_id)
                    JOIN control.chamber ch USING (chamber_id)
                    LEFT JOIN core.contest c USING (contest_id)
                    LEFT JOIN core.contest_constituency_snapshot cs USING (contest_id)
                    LEFT JOIN sync.constituency sc
                      ON sc.constituency_id=COALESCE(
                           cs.canonical_constituency_id, c.canonical_constituency_id
                         )
                    WHERE bd.record_status='active'
                  ) SELECT * FROM raw WHERE {conditions}"""
        return QuerySpec(sql, params, "election_date DESC, state, contest_name, dataset_scope")

    def _contests_spec(self, filters: ExplorerFilters) -> QuerySpec:
        conditions, params = self._conditions(
            filters,
            search_columns=("contest_name", "official_contest_id", "contest_status"),
        )
        sql = f"""WITH raw AS (
                    SELECT e.election_id, e.election_name, e.election_date,
                           ch.chamber_id, ch.chamber_name,
                           c.contest_id, c.official_contest_id, c.contest_name,
                           {STATE_SQL} AS state, c.vacancies, c.contest_status,
                           c.uncontested, c.publication_status,
                           count(ca.candidacy_id) AS candidacy_count
                    FROM core.contest c
                    JOIN core.election_chamber ec USING (election_chamber_id)
                    JOIN core.election e USING (election_id)
                    JOIN control.chamber ch USING (chamber_id)
                    LEFT JOIN core.contest_constituency_snapshot cs USING (contest_id)
                    LEFT JOIN sync.constituency sc
                      ON sc.constituency_id=COALESCE(
                           cs.canonical_constituency_id, c.canonical_constituency_id
                         )
                    LEFT JOIN core.candidacy ca
                      ON ca.contest_id=c.contest_id AND ca.record_status='active'
                    WHERE c.record_status='active'
                    GROUP BY e.election_id, e.election_name, e.election_date,
                             ch.chamber_id, ch.chamber_name, c.contest_id,
                             c.official_contest_id, c.contest_name, state, c.vacancies,
                             c.contest_status, c.uncontested, c.publication_status
                  ) SELECT * FROM raw WHERE {conditions}"""
        return QuerySpec(sql, params, "election_date DESC, chamber_name, state, contest_name")

    def query(
        self,
        dataset: str,
        filters: ExplorerFilters,
        *,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        spec = self._spec(dataset, filters)
        offset = (page - 1) * page_size
        database = self._database()
        connection = self._connect(database)
        try:
            metadata = self._metadata(connection)
            cursor = connection.execute(
                f"""SELECT base.*, count(*) OVER () AS __total_rows
                    FROM ({spec.sql}) base
                    ORDER BY {spec.order_by}
                    LIMIT ? OFFSET ?""",
                [*spec.params, page_size, offset],
            )
            names = [column[0] for column in cursor.description]
            raw_rows = cursor.fetchall()
        finally:
            connection.close()
        total = int(raw_rows[0][-1]) if raw_rows else 0
        public_names = names[:-1]
        rows = [
            {
                name: _json_value(value)
                for name, value in zip(public_names, row[:-1], strict=True)
            }
            for row in raw_rows
        ]
        return {
            "dataset": dataset,
            "dataset_label": DATASET_LABELS[dataset],
            "rows": rows,
            "columns": public_names,
            "page": page,
            "page_size": page_size,
            "total_rows": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "release": metadata,
            "read_only": True,
        }

    def export(self, dataset: str, filters: ExplorerFilters) -> tuple[str, int, dict, Iterator[str]]:
        spec = self._spec(dataset, filters)
        database = self._database()
        connection = self._connect(database)
        metadata = self._metadata(connection)
        try:
            total = connection.execute(
                f"SELECT count(*) FROM ({spec.sql}) base", list(spec.params)
            ).fetchone()[0]
        finally:
            connection.close()
        if total > self.max_export_rows:
            raise ExportTooLargeError(
                f"The export contains {total:,} rows; narrow the filters below the "
                f"{self.max_export_rows:,}-row safety limit."
            )
        election = filters.election_id or "all_elections"
        filename = _safe_export_name(
            f"politica_{dataset}_{election}_{datetime.now(timezone.utc):%Y%m%d}.csv"
        )

        def generate() -> Iterator[str]:
            export_connection = self._connect(database)
            try:
                cursor = export_connection.execute(
                    f"SELECT * FROM ({spec.sql}) base ORDER BY {spec.order_by}",
                    list(spec.params),
                )
                names = [column[0] for column in cursor.description]
                buffer = io.StringIO()
                writer = csv.writer(buffer, lineterminator="\n")
                writer.writerow(names)
                yield "\ufeff" + buffer.getvalue()
                while batch := cursor.fetchmany(2_000):
                    buffer.seek(0)
                    buffer.truncate(0)
                    writer.writerows([_csv_value(value) for value in row] for row in batch)
                    yield buffer.getvalue()
            finally:
                export_connection.close()

        return filename, int(total), metadata, generate()
