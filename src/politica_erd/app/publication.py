from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import yaml

from .explorer import ElectionExplorer, ExplorerFilters, QuerySpec, STATE_SQL
from .config import PROJECT_ROOT


FEED_VERSION = "1.8.0"
STANDARD_STATES = ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA")
SENATE_STATE_SEAT_COUNTS = {
    "ACT": 2,
    "NSW": 12,
    "NT": 2,
    "QLD": 12,
    "SA": 12,
    "TAS": 12,
    "VIC": 12,
    "WA": 12,
}
SENATE_COMPOSITION_FIELDS = (
    "snapshot_id",
    "snapshot_as_at",
    "parliament_number",
    "chamber_id",
    "chamber_name",
    "seat_id",
    "person_id",
    "person_name",
    "state",
    "party_id",
    "party_name",
    "party_abbreviation",
    "party_colour",
    "bloc",
    "term_expiry",
    "membership_basis",
    "source_revision_id",
    "source_url",
    "source_locator",
)
PROVENANCE_COLUMNS = (
    "_feed_id",
    "_feed_version",
    "_publication_id",
    "_release_id",
    "_database_sha256",
)


class PublicationError(ValueError):
    pass


class PublicationTooLargeError(PublicationError):
    pass


@dataclass(frozen=True)
class PublicationFilters:
    election_id: str | None = None
    state: str | None = None
    contest_id: str | None = None


@dataclass(frozen=True)
class FeedDefinition:
    feed_id: str
    title: str
    description: str
    grain: str
    recommended_for: tuple[str, ...]
    builder: str


@dataclass(frozen=True)
class FeedRepresentation:
    feed_id: str
    publication_id: str
    row_count: int
    manifest: dict[str, Any]
    json_bytes: bytes
    csv_bytes: bytes


FEEDS = {
    item.feed_id: item
    for item in (
        FeedDefinition(
            "house_candidate_results",
            "House candidate results",
            "Contest-total first-preference, TCP and TPP candidate results, with stable candidate and party identities.",
            "One row per contest, result type and candidate or subject",
            ("Observable", "Flourish", "candidate tables", "result charts"),
            "_house_candidate_results",
        ),
        FeedDefinition(
            "house_seat_results",
            "House seat results",
            "One declared member per House contest, with party colour and available TCP winning margin measures.",
            "One row per declared House seat",
            ("seat maps", "winner lists", "margin charts"),
            "_house_seat_results",
        ),
        FeedDefinition(
            "house_party_summary",
            "House party summary",
            "National first-preference votes, calculated vote share and declared seats by canonical party.",
            "One row per party and election",
            ("party leaderboards", "vote-share charts", "seat totals"),
            "_house_party_summary",
        ),
        FeedDefinition(
            "senate_group_results",
            "Senate group results",
            "Current Senate group totals at contest or state level, including stable group and party identities.",
            "One row per state, reporting unit and ballot group",
            ("state comparisons", "group vote charts", "Flourish"),
            "_senate_group_results",
        ),
        FeedDefinition(
            "turnout_informality",
            "Turnout and informality",
            "Current participation measures in a long, chart-ready format across both chambers.",
            "One row per reporting unit, vote type and participation measure",
            ("turnout maps", "informality charts", "regional comparisons"),
            "_turnout_informality",
        ),
        FeedDefinition(
            "declared_members",
            "Declared members",
            "Current elected outcomes for House and Senate with stable person, candidacy and party identities.",
            "One row per declared elected candidacy",
            ("member lists", "seat summaries", "winner profiles"),
            "_declared_members",
        ),
        FeedDefinition(
            "senate_count_progress",
            "Senate count progression",
            "Candidate progressive totals for each current Senate distribution-of-preferences round.",
            "One row per count round and candidate",
            ("count animations", "transfer analysis", "Observable"),
            "_senate_count_progress",
        ),
        FeedDefinition(
            "senate_count_movements",
            "Senate count movements",
            "Reported candidate gains, losses and exhausted values for each current Senate distribution-of-preferences round.",
            "One row per count round and reported candidate movement or exhausted movement",
            ("count-round flows", "elimination analysis", "transfer movements"),
            "_senate_count_movements",
        ),
        FeedDefinition(
            "senate_composition",
            "Senate representation",
            "The governed 48th Parliament snapshot for 2025, or declared elected senators for historical elections where a full continuing-membership snapshot is not governed.",
            "One row per governed Senate membership in the selected election view",
            ("chamber diagrams", "member lists", "party and state composition"),
            "_senate_composition",
        ),
    )
}


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "hex") and value.__class__.__name__ == "UUID":
        return str(value)
    return value


def _csv_value(value: Any) -> Any:
    value = _json_value(value)
    return "" if value is None else value


def _canonical_json(document: Any) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_type(type_name: str) -> str:
    upper = type_name.upper()
    if "BOOL" in upper:
        return "boolean"
    if any(token in upper for token in ("INT", "HUGEINT", "UBIGINT")):
        return "integer"
    if any(token in upper for token in ("DECIMAL", "DOUBLE", "FLOAT", "REAL")):
        return "number"
    return "string"


class VisualisationFeedService:
    """Stable, versioned, read-only publication feeds over a governed release."""

    def __init__(
        self,
        explorer: ElectionExplorer,
        release_identity_resolver: Callable[[], dict[str, Any]],
        *,
        max_rows: int = 500_000,
        composition_contract_path: Path | None = None,
    ) -> None:
        self.explorer = explorer
        self._release_identity_resolver = release_identity_resolver
        self.max_rows = max_rows
        self.composition_contract_path = (
            composition_contract_path
            or PROJECT_ROOT
            / "config"
            / "parliament_composition_48th.yml"
        ).resolve()
        self._composition_document = self._load_composition_contract()
        self.composition_contract_sha256 = hashlib.sha256(
            _canonical_json(self._composition_document)
        ).hexdigest()

    def _load_composition_contract(self) -> dict[str, Any]:
        path = self.composition_contract_path
        if not path.is_file():
            raise PublicationError(
                f"The governed parliamentary composition contract is missing: {path}"
            )
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise PublicationError(
                "The governed parliamentary composition contract could not be read"
            ) from exc
        if not isinstance(document, dict):
            raise PublicationError(
                "The governed parliamentary composition contract must be a mapping"
            )
        senators = document.get("senators")
        parties = document.get("parties")
        if not isinstance(senators, list) or not isinstance(parties, dict):
            raise PublicationError(
                "The governed parliamentary composition contract requires senators and parties"
            )
        if document.get("seat_count") != 76 or len(senators) != 76:
            raise PublicationError(
                "The governed Senate composition must contain exactly 76 memberships"
            )
        identifiers = [str(item.get("person_id") or "") for item in senators]
        if any(not value for value in identifiers) or len(identifiers) != len(set(identifiers)):
            raise PublicationError(
                "Every Senate composition membership requires a unique person_id"
            )
        observed_states = {
            state: sum(item.get("state") == state for item in senators)
            for state in STANDARD_STATES
        }
        if observed_states != SENATE_STATE_SEAT_COUNTS:
            raise PublicationError(
                "The Senate composition does not reconcile to 12 senators per state and two per territory"
            )
        allowed_blocs = {"government", "opposition", "crossbench"}
        for item in senators:
            party_code = str(item.get("party") or "")
            party = parties.get(party_code)
            if not isinstance(party, dict):
                raise PublicationError(
                    f"Senate composition member {item.get('person_id')} uses an unregistered party"
                )
            if party.get("bloc") not in allowed_blocs:
                raise PublicationError(
                    f"Senate composition party {party_code} uses an unsupported parliamentary bloc"
                )
            colour = str(party.get("party_colour") or "")
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", colour):
                raise PublicationError(
                    f"Senate composition party {party_code} requires a six-digit colour"
                )
        if not str(document.get("source_url") or "").startswith("https://www.aph.gov.au/"):
            raise PublicationError(
                "The Senate composition must cite its official Parliament of Australia source"
            )
        return document

    @property
    def composition_snapshot(self) -> dict[str, Any]:
        document = self._composition_document
        return {
            "snapshot_id": document["snapshot_id"],
            "snapshot_as_at": str(document["snapshot_as_at"]),
            "parliament_number": document["parliament_number"],
            "seat_count": document["seat_count"],
            "source_revision_id": document["source_revision_id"],
            "source_url": document["source_url"],
            "contract_sha256": self.composition_contract_sha256,
        }

    def _definition(self, feed_id: str) -> FeedDefinition:
        try:
            return FEEDS[feed_id]
        except KeyError as exc:
            raise PublicationError(f"Unknown visualisation feed: {feed_id}") from exc

    def _database(self) -> Path:
        return self.explorer._database()

    def _connect(self, database: Path | None = None) -> duckdb.DuckDBPyConnection:
        return self.explorer._connect(database)

    def _effective_filters(
        self,
        connection: duckdb.DuckDBPyConnection,
        requested: PublicationFilters,
    ) -> PublicationFilters:
        election_id = requested.election_id
        if not election_id:
            latest = connection.execute(
                """SELECT election_id FROM core.election
                   WHERE record_status='active'
                   ORDER BY election_date DESC, election_id LIMIT 1"""
            ).fetchone()
            election_id = latest[0] if latest else None
        if not election_id:
            raise PublicationError("No active election is available for publication")
        exists = connection.execute(
            "SELECT count(*) FROM core.election WHERE election_id=? AND record_status='active'",
            [election_id],
        ).fetchone()[0]
        if not exists:
            raise PublicationError(f"The selected election is not active: {election_id}")
        if requested.state and requested.state not in STANDARD_STATES:
            raise PublicationError(f"Unsupported state or territory: {requested.state}")
        return PublicationFilters(election_id, requested.state, requested.contest_id)

    @staticmethod
    def _explorer_filters(
        filters: PublicationFilters,
        *,
        chamber_id: str | None = None,
        reporting_level: str | None = None,
    ) -> ExplorerFilters:
        return ExplorerFilters(
            election_id=filters.election_id,
            chamber_id=chamber_id,
            state=filters.state,
            contest_id=filters.contest_id,
            reporting_level=reporting_level,
        )

    def _house_candidate_results(self, filters: PublicationFilters) -> QuerySpec:
        base = self.explorer._results_spec(
            self._explorer_filters(
                filters, chamber_id="chamber_house", reporting_level="contest"
            )
        )
        return QuerySpec(
            f"""SELECT base.*, ca.incumbent_status
                FROM ({base.sql}) base
                LEFT JOIN core.candidacy ca
                  ON CAST(ca.candidacy_id AS VARCHAR)=base.subject_id
                 AND ca.record_status='active'
                WHERE result_type IN ('first_preference','tcp','tpp')
                  AND vote_type='total'""",
            base.params,
            "state, contest_name, result_type, votes DESC NULLS LAST, subject_name",
        )

    def _declared_members(self, filters: PublicationFilters) -> QuerySpec:
        return self.explorer._outcomes_spec(self._explorer_filters(filters))

    def _turnout_informality(self, filters: PublicationFilters) -> QuerySpec:
        return self.explorer._participation_spec(self._explorer_filters(filters))

    def _senate_count_progress(self, filters: PublicationFilters) -> QuerySpec:
        return self.explorer._count_totals_spec(
            self._explorer_filters(filters, chamber_id="chamber_senate")
        )

    def _senate_count_movements(self, filters: PublicationFilters) -> QuerySpec:
        conditions = ["e.election_id=?", "ch.chamber_id='chamber_senate'"]
        params: list[Any] = [filters.election_id]
        if filters.state:
            conditions.append(f"{STATE_SQL}=?")
            params.append(filters.state)
        if filters.contest_id:
            conditions.append("c.contest_id=?")
            params.append(filters.contest_id)
        where = " AND ".join(conditions)
        return QuerySpec(
            f"""SELECT e.election_id, e.election_name, e.election_date,
                       ch.chamber_id, ch.chamber_name,
                       c.contest_id, c.contest_name, {STATE_SQL} AS state,
                       cr.round_number, cr.round_label, cr.action_type,
                       cr.quota_value, cr.transfer_value, cr.remarks,
                       CAST(pt.from_candidacy_id AS VARCHAR) AS from_candidacy_id,
                       from_ca.ballot_name AS from_candidate_name,
                       COALESCE(from_party.abbreviation, from_party.short_name,
                                from_party.party_name,
                                from_ca.official_party_abbreviation,
                                from_ca.official_party_name) AS from_party_name,
                       from_party.colour_hex AS from_party_colour,
                       CAST(pt.to_candidacy_id AS VARCHAR) AS to_candidacy_id,
                       CASE WHEN pt.exhausted THEN 'Exhausted'
                            ELSE to_ca.ballot_name END AS to_candidate_name,
                       COALESCE(to_party.abbreviation, to_party.short_name,
                                to_party.party_name,
                                to_ca.official_party_abbreviation,
                                to_ca.official_party_name) AS to_party_name,
                       to_party.colour_hex AS to_party_colour,
                       pt.papers_value, pt.votes_value, pt.exhausted,
                       pt.value_status, pt.source_revision_id
                FROM "count".preference_transfer pt
                JOIN provenance.source_file_revision sr
                  ON sr.source_revision_id=pt.source_revision_id
                 AND sr.record_status='active'
                JOIN "count".count_round cr USING (count_round_id)
                JOIN core.contest c USING (contest_id)
                JOIN core.election_chamber ec USING (election_chamber_id)
                JOIN core.election e USING (election_id)
                JOIN control.chamber ch USING (chamber_id)
                LEFT JOIN core.candidacy from_ca
                  ON from_ca.candidacy_id=pt.from_candidacy_id
                LEFT JOIN sync.party from_party
                  ON from_party.party_id=from_ca.party_id
                LEFT JOIN core.candidacy to_ca
                  ON to_ca.candidacy_id=pt.to_candidacy_id
                LEFT JOIN sync.party to_party
                  ON to_party.party_id=to_ca.party_id
                LEFT JOIN core.contest_constituency_snapshot cs USING (contest_id)
                LEFT JOIN sync.constituency sc
                  ON sc.constituency_id=COALESCE(
                       cs.canonical_constituency_id, c.canonical_constituency_id
                     )
                WHERE {where}""",
            params,
            "state, contest_name, round_number, exhausted, votes_value DESC NULLS LAST, to_candidate_name",
        )

    def _senate_composition(self, filters: PublicationFilters) -> QuerySpec:
        if filters.election_id != "election_fed_2025_05_03_general":
            outcomes = self.explorer._outcomes_spec(
                self._explorer_filters(filters, chamber_id="chamber_senate")
            )
            return QuerySpec(
                f"""WITH elected AS ({outcomes.sql})
                    SELECT
                      election_id,
                      'election_result_' || election_id AS snapshot_id,
                      CAST(election_date AS VARCHAR) AS snapshot_as_at,
                      CAST(CASE election_id
                        WHEN 'election_fed_2010_08_21_general' THEN 43
                        WHEN 'election_fed_2013_09_07_general' THEN 44
                        WHEN 'election_fed_2016_07_02_general' THEN 45
                        WHEN 'election_fed_2019_05_18_general' THEN 46
                        WHEN 'election_fed_2022_05_21_general' THEN 47
                        ELSE NULL
                      END AS INTEGER) AS parliament_number,
                      chamber_id,
                      chamber_name,
                      candidacy_id AS seat_id,
                      COALESCE(person_id, 'candidate_' || candidacy_id) AS person_id,
                      COALESCE(person_name, candidate_name) AS person_name,
                      state,
                      party_id,
                      party_name,
                      party_name AS party_abbreviation,
                      party_colour,
                      CASE
                        WHEN election_id IN (
                          'election_fed_2010_08_21_general',
                          'election_fed_2022_05_21_general'
                        )
                             AND party_id='party_alp' THEN 'government'
                        WHEN election_id IN (
                          'election_fed_2013_09_07_general',
                          'election_fed_2016_07_02_general',
                          'election_fed_2019_05_18_general'
                        ) AND party_id IN (
                          'party_liberal', 'party_nationals', 'party_lnp_qld',
                          'party_country_liberal', 'party_coalition'
                        ) THEN 'government'
                        WHEN election_id IN (
                          'election_fed_2010_08_21_general',
                          'election_fed_2022_05_21_general'
                        )
                             AND party_id IN (
                               'party_liberal', 'party_nationals', 'party_lnp_qld',
                               'party_country_liberal', 'party_coalition'
                             ) THEN 'opposition'
                        WHEN election_id IN (
                          'election_fed_2013_09_07_general',
                          'election_fed_2016_07_02_general',
                          'election_fed_2019_05_18_general'
                        ) AND party_id='party_alp' THEN 'opposition'
                        ELSE 'crossbench'
                      END AS bloc,
                      CAST(NULL AS VARCHAR) AS term_expiry,
                      CASE
                        WHEN election_id='election_fed_2013_09_07_general'
                             AND contest_status='void'
                          THEN 'published_2013_result_later_voided'
                        ELSE 'declared_elected_at_selected_election'
                      END AS membership_basis,
                      source_revision_id,
                      CASE election_id
                        WHEN 'election_fed_2010_08_21_general'
                          THEN 'https://results.aec.gov.au/15508/Website/SenateResultsMenu-15508.htm'
                        WHEN 'election_fed_2013_09_07_general'
                          THEN 'https://results.aec.gov.au/17496/Website/SenateResultsMenu-17496.htm'
                        WHEN 'election_fed_2016_07_02_general'
                          THEN 'https://results.aec.gov.au/20499/Website/SenateResultsMenu-20499.htm'
                        WHEN 'election_fed_2019_05_18_general'
                          THEN 'https://results.aec.gov.au/24310/Website/SenateResultsMenu-24310.htm'
                        WHEN 'election_fed_2022_05_21_general'
                          THEN 'https://results.aec.gov.au/27966/Website/SenateResultsMenu-27966.htm'
                        ELSE NULL
                      END AS source_url,
                      'AEC declared elected outcome' AS source_locator
                    FROM elected
                    WHERE outcome_type='elected'""",
                outcomes.params,
                "CASE bloc WHEN 'government' THEN 1 WHEN 'crossbench' THEN 2 ELSE 3 END, party_name, state, person_name",
            )
        document = self._composition_document
        parties = document["parties"]
        rows: list[tuple[Any, ...]] = []
        for member in document["senators"]:
            party_code = str(member["party"])
            party = parties[party_code]
            rows.append(
                (
                    document["snapshot_id"],
                    str(document["snapshot_as_at"]),
                    int(document["parliament_number"]),
                    document["chamber_id"],
                    document["chamber_name"],
                    f"seat_senate_{member['state'].lower()}_{member['person_id'].removeprefix('person_')}",
                    member["person_id"],
                    member["person_name"],
                    member["state"],
                    party["party_id"],
                    party["party_name"],
                    party_code,
                    party["party_colour"],
                    party["bloc"],
                    str(member["term_expiry"]),
                    member.get("membership_basis", "official_snapshot"),
                    document["source_revision_id"],
                    document["source_url"],
                    f"{document['source_locator']}; senator: {member['person_name']}",
                )
            )
        placeholders = ",".join(
            "(" + ",".join("?" for _ in SENATE_COMPOSITION_FIELDS) + ")"
            for _ in rows
        )
        columns = ",".join(SENATE_COMPOSITION_FIELDS)
        flattened = tuple(value for row in rows for value in row)
        return QuerySpec(
            f"""WITH composition({columns}) AS (VALUES {placeholders})
                SELECT ? AS election_id, composition.*
                FROM composition
                WHERE (? IS NULL OR state=?)""",
            (*flattened, filters.election_id, filters.state, filters.state),
            "CASE bloc WHEN 'government' THEN 1 WHEN 'crossbench' THEN 2 ELSE 3 END, party_name, state, person_name",
        )

    def _senate_group_results(self, filters: PublicationFilters) -> QuerySpec:
        base = self.explorer._results_spec(
            self._explorer_filters(filters, chamber_id="chamber_senate")
        )
        return QuerySpec(
            f"""WITH available AS (
                    SELECT * FROM ({base.sql}) base
                    WHERE result_type IN ('group_total','party_total')
                      AND vote_type='total'
                      AND reporting_level IN ('contest','state')
                      AND state IS NOT NULL
                  ), preferred AS (
                    SELECT * FROM available WHERE result_type='group_total'
                    UNION ALL
                    SELECT * REPLACE ('group_total' AS result_type)
                    FROM available legacy
                    WHERE result_type='party_total'
                      AND NOT EXISTS (
                        SELECT 1 FROM available governed
                        WHERE governed.result_type='group_total'
                          AND governed.state=legacy.state
                      )
                  )
                  SELECT * FROM preferred""",
            base.params,
            "state, reporting_level, reporting_unit, votes DESC NULLS LAST, subject_name",
        )

    def _house_seat_results(self, filters: PublicationFilters) -> QuerySpec:
        outcomes = self.explorer._outcomes_spec(
            self._explorer_filters(filters, chamber_id="chamber_house")
        )
        results = self.explorer._results_spec(
            self._explorer_filters(
                filters, chamber_id="chamber_house", reporting_level="contest"
            )
        )
        participation = self.explorer._participation_spec(
            self._explorer_filters(
                filters, chamber_id="chamber_house", reporting_level="contest"
            )
        )
        return QuerySpec(
            f"""WITH winners AS ({outcomes.sql}),
                       tcp AS (
                         SELECT * FROM ({results.sql}) r
                         WHERE result_type='tcp' AND vote_type='total'
                       ), incumbent_candidates AS (
                         SELECT ca.contest_id, CAST(ca.candidacy_id AS VARCHAR) AS candidacy_id,
                                ca.party_id,
                                COALESCE(p.party_name, p.short_name, p.abbreviation,
                                         ca.official_party_name,
                                         ca.official_party_abbreviation) AS party_name
                         FROM core.candidacy ca
                         LEFT JOIN sync.party p USING (party_id)
                         WHERE ca.record_status='active'
                           AND ca.incumbent_status='incumbent'
                       ), participation AS (
                         SELECT contest_id,
                                max(integer_value) FILTER (
                                  WHERE measure_type='enrolment' AND vote_type='total'
                                ) AS enrolment,
                                max(integer_value) FILTER (
                                  WHERE measure_type='formal_votes' AND vote_type='total'
                                ) AS formal_votes,
                                max(integer_value) FILTER (
                                  WHERE measure_type='informal_votes' AND vote_type='total'
                                ) AS informal_votes,
                                max(integer_value) FILTER (
                                  WHERE measure_type='total_votes' AND vote_type='total'
                                ) AS votes_counted,
                                max(integer_value) FILTER (
                                  WHERE measure_type='turnout' AND vote_type='total'
                                ) AS turnout,
                                max(decimal_value) FILTER (
                                  WHERE measure_type='turnout_percentage' AND vote_type='total'
                                ) AS turnout_percentage,
                                max(decimal_value) FILTER (
                                  WHERE measure_type='informality_percentage' AND vote_type='total'
                                ) AS informality_percentage,
                                string_agg(DISTINCT source_revision_id, '|'
                                  ORDER BY source_revision_id) AS participation_source_revision_ids
                         FROM ({participation.sql}) p
                         GROUP BY contest_id
                       ), seat_rows AS (
                SELECT w.election_id, w.election_name, w.election_date,
                       w.chamber_id, w.chamber_name, w.contest_id,
                       w.contest_name, w.state, w.candidacy_id, w.person_id,
                       w.candidate_name, w.person_name, w.party_id, w.party_name,
                       w.party_colour, w.outcome_type, w.elected_order, w.declared_at,
                       winner.incumbent_status,
                       max(t.votes) FILTER (WHERE t.subject_id=w.candidacy_id) AS tcp_votes,
                       CASE WHEN sum(t.votes) > 0 THEN round(
                         100.0 * max(t.votes) FILTER (WHERE t.subject_id=w.candidacy_id) /
                         sum(t.votes), 4
                       ) END AS tcp_vote_share,
                       max(t.swing) FILTER (WHERE t.subject_id=w.candidacy_id) AS tcp_swing,
                       max(t.party_name) FILTER (WHERE t.subject_id<>w.candidacy_id)
                         AS tcp_opponent_party_name,
                       max(incumbent.party_name) FILTER (
                         WHERE incumbent.candidacy_id<>w.candidacy_id
                       ) AS defeated_incumbent_party_name,
                       CASE WHEN sum(t.votes) > 0 THEN round(
                         100.0 * max(t.votes) FILTER (WHERE t.subject_id<>w.candidacy_id) /
                         sum(t.votes), 4
                       ) END AS opponent_tcp_vote_share,
                       CASE WHEN sum(t.votes) > 0 THEN round(
                         100.0 * (
                           max(t.votes) FILTER (WHERE t.subject_id=w.candidacy_id) -
                           max(t.votes) FILTER (WHERE t.subject_id<>w.candidacy_id)
                         ) / sum(t.votes), 4
                       ) END AS winning_margin_percentage_points,
                       concat_ws('|', w.source_revision_id,
                         string_agg(DISTINCT t.source_revision_id, '|'
                                    ORDER BY t.source_revision_id)) AS result_source_revision_ids
                FROM winners w
                LEFT JOIN tcp t USING (contest_id)
                LEFT JOIN core.candidacy winner
                  ON CAST(winner.candidacy_id AS VARCHAR)=w.candidacy_id
                 AND winner.record_status='active'
                LEFT JOIN incumbent_candidates incumbent USING (contest_id)
                GROUP BY w.election_id, w.election_name, w.election_date,
                         w.chamber_id, w.chamber_name, w.contest_id,
                         w.contest_name, w.state, w.candidacy_id, w.person_id,
                         w.candidate_name, w.person_name, w.party_id, w.party_name,
                         w.party_colour, w.outcome_type, w.elected_order, w.declared_at,
                         winner.incumbent_status, w.source_revision_id
                       )
                SELECT s.*,
                       CASE
                         WHEN s.election_id IN (
                           'election_fed_2010_08_21_general',
                           'election_fed_2022_05_21_general',
                           'election_fed_2025_05_03_general'
                         ) AND s.party_id='party_alp' THEN 'government'
                         WHEN s.election_id IN (
                           'election_fed_2013_09_07_general',
                           'election_fed_2016_07_02_general',
                           'election_fed_2019_05_18_general'
                         ) AND s.party_id IN (
                           'party_liberal', 'party_nationals', 'party_lnp_qld',
                           'party_country_liberal', 'party_coalition'
                         ) THEN 'government'
                         WHEN s.party_id IS NULL THEN 'crossbench'
                         ELSE 'opposition'
                       END AS bloc,
                       CASE
                         WHEN s.incumbent_status='incumbent' THEN 'retained'
                         WHEN s.defeated_incumbent_party_name IS NOT NULL THEN 'gained'
                         ELSE 'new_member'
                       END AS seat_change_type,
                       CASE
                         WHEN s.incumbent_status='incumbent' THEN 'Retained'
                         WHEN s.defeated_incumbent_party_name IS NOT NULL
                           THEN 'Gained from ' || s.defeated_incumbent_party_name
                         ELSE 'New member'
                       END AS seat_change_label,
                       p.enrolment, p.formal_votes, p.informal_votes,
                       COALESCE(p.votes_counted, p.turnout) AS votes_counted,
                       p.turnout, p.turnout_percentage, p.informality_percentage,
                       CASE WHEN p.enrolment > 0 THEN round(
                         100.0 * COALESCE(p.votes_counted, p.turnout) / p.enrolment, 4
                       ) END AS counted_percentage_of_enrolment,
                       concat_ws('|', s.result_source_revision_ids,
                                 p.participation_source_revision_ids) AS source_revision_id
                FROM seat_rows s
                LEFT JOIN participation p USING (contest_id)""",
            (*outcomes.params, *results.params, *participation.params),
            "state, contest_name, elected_order, candidate_name",
        )

    def _house_party_summary(self, filters: PublicationFilters) -> QuerySpec:
        results = self.explorer._results_spec(
            self._explorer_filters(
                filters, chamber_id="chamber_house", reporting_level="contest"
            )
        )
        outcomes = self.explorer._outcomes_spec(
            self._explorer_filters(filters, chamber_id="chamber_house")
        )
        return QuerySpec(
            f"""WITH result_rows AS ({results.sql}), outcome_rows AS ({outcomes.sql}),
                votes AS (
                  SELECT election_id, election_name, election_date,
                         party_id, coalesce(party_name, 'Independent / ungrouped') AS party_name,
                         max(party_colour) AS party_colour,
                         sum(votes) AS first_preference_votes,
                         count(DISTINCT contest_id) AS contests_stood,
                         string_agg(DISTINCT source_revision_id, '|' ORDER BY source_revision_id)
                           AS vote_source_revision_ids
                  FROM result_rows
                  WHERE result_type='first_preference' AND vote_type='total'
                    AND subject_type IN ('candidate','candidacy') AND votes IS NOT NULL
                  GROUP BY election_id, election_name, election_date, party_id,
                           coalesce(party_name, 'Independent / ungrouped')
                ), seats AS (
                  SELECT election_id, party_id,
                         coalesce(party_name, 'Independent / ungrouped') AS party_name,
                         count(DISTINCT contest_id) AS declared_seats,
                         string_agg(DISTINCT source_revision_id, '|' ORDER BY source_revision_id)
                           AS outcome_source_revision_ids
                  FROM outcome_rows
                  WHERE outcome_type='elected'
                  GROUP BY election_id, party_id,
                           coalesce(party_name, 'Independent / ungrouped')
                )
                SELECT v.election_id, v.election_name, v.election_date,
                       v.party_id, v.party_name, v.party_colour,
                       v.first_preference_votes,
                       round(100.0 * v.first_preference_votes /
                         nullif(sum(v.first_preference_votes) OVER (PARTITION BY v.election_id), 0), 4)
                         AS first_preference_vote_share,
                       v.contests_stood, coalesce(s.declared_seats, 0) AS declared_seats,
                       concat_ws('|', v.vote_source_revision_ids,
                                 s.outcome_source_revision_ids) AS source_revision_id
                FROM votes v
                LEFT JOIN seats s
                  ON s.election_id=v.election_id
                 AND coalesce(s.party_id, '')=coalesce(v.party_id, '')
                 AND s.party_name=v.party_name""",
            (*results.params, *outcomes.params),
            "first_preference_votes DESC, party_name",
        )

    def _spec(self, feed_id: str, filters: PublicationFilters) -> QuerySpec:
        definition = self._definition(feed_id)
        return getattr(self, definition.builder)(filters)

    @staticmethod
    def _rows(cursor: duckdb.DuckDBPyConnection) -> tuple[list[str], list[dict[str, Any]], list[dict[str, str]]]:
        columns = [column[0] for column in cursor.description]
        contract = [
            {"name": column[0], "type": _json_type(str(column[1])), "duckdb_type": str(column[1])}
            for column in cursor.description
        ]
        rows = [
            {name: _json_value(value) for name, value in zip(columns, row, strict=True)}
            for row in cursor.fetchall()
        ]
        return columns, rows, contract

    def catalogue(self) -> dict[str, Any]:
        database = self._database()
        identity = self._release_identity_resolver()
        connection = self._connect(database)
        try:
            effective = self._effective_filters(connection, PublicationFilters())
            elections = [
                {
                    "election_id": row[0],
                    "election_name": row[1],
                    "election_date": _json_value(row[2]),
                }
                for row in connection.execute(
                    """SELECT election_id, election_name, election_date
                       FROM core.election WHERE record_status='active'
                       ORDER BY election_date DESC, election_id"""
                ).fetchall()
            ]
            feeds = []
            for definition in FEEDS.values():
                spec = self._spec(definition.feed_id, effective)
                cursor = connection.execute(
                    f"SELECT * FROM ({spec.sql}) base LIMIT 0", list(spec.params)
                )
                contract = [
                    {"name": column[0], "type": _json_type(str(column[1])), "duckdb_type": str(column[1])}
                    for column in cursor.description
                ]
                feeds.append(
                    {
                        "feed_id": definition.feed_id,
                        "feed_version": FEED_VERSION,
                        "title": definition.title,
                        "description": definition.description,
                        "grain": definition.grain,
                        "recommended_for": list(definition.recommended_for),
                        "filters": ["election_id", "state", "contest_id"],
                        "formats": ["json", "csv", "manifest"],
                        "fields": contract,
                        "urls": {
                            "json": f"/api/public/v1/feeds/{definition.feed_id}.json",
                            "csv": f"/api/public/v1/feeds/{definition.feed_id}.csv",
                            "manifest": f"/api/public/v1/feeds/{definition.feed_id}/manifest.json",
                        },
                    }
                )
        finally:
            connection.close()
        return {
            "api_version": "v1",
            "feed_version": FEED_VERSION,
            "read_only": True,
            "default_election_id": effective.election_id,
            "release": identity,
            "supplemental_contracts": {
                "parliamentary_composition": self.composition_snapshot,
            },
            "elections": elections,
            "feeds": feeds,
        }

    def build(self, feed_id: str, requested: PublicationFilters) -> FeedRepresentation:
        definition = self._definition(feed_id)
        database = self._database()
        identity = self._release_identity_resolver()
        connection = self._connect(database)
        try:
            filters = self._effective_filters(connection, requested)
            spec = self._spec(feed_id, filters)
            row_count = int(
                connection.execute(
                    f"SELECT count(*) FROM ({spec.sql}) base", list(spec.params)
                ).fetchone()[0]
            )
            if row_count > self.max_rows:
                raise PublicationTooLargeError(
                    f"The feed contains {row_count:,} rows; narrow the state or contest filters "
                    f"below the {self.max_rows:,}-row publication limit."
                )
            cursor = connection.execute(
                f"SELECT * FROM ({spec.sql}) base ORDER BY {spec.order_by}",
                list(spec.params),
            )
            columns, rows, contract = self._rows(cursor)
        finally:
            connection.close()

        normalised_filters = {
            "election_id": filters.election_id,
            "state": filters.state,
            "contest_id": filters.contest_id,
        }
        release_id = str(identity.get("release_id") or "")
        database_sha256 = str(identity.get("database_sha256") or "")
        publication_basis = {
            "feed_id": feed_id,
            "feed_version": FEED_VERSION,
            "filters": normalised_filters,
            "release_id": release_id,
            "database_sha256": database_sha256,
        }
        if (
            feed_id == "senate_composition"
            and filters.election_id == "election_fed_2025_05_03_general"
        ):
            publication_basis["composition_contract_sha256"] = (
                self.composition_contract_sha256
            )
        publication_id = "publication_" + hashlib.sha256(
            _canonical_json(publication_basis)
        ).hexdigest()[:32]
        data_sha256 = hashlib.sha256(_canonical_json(rows)).hexdigest()
        source_revisions = sorted(
            {
                revision
                for row in rows
                for revision in str(row.get("source_revision_id") or "").split("|")
                if revision
            }
        )
        manifest_core = {
            "api_version": "v1",
            "feed_id": feed_id,
            "feed_version": FEED_VERSION,
            "calculation_version": FEED_VERSION,
            "title": definition.title,
            "description": definition.description,
            "grain": definition.grain,
            "publication_id": publication_id,
            "read_only": True,
            "filters": normalised_filters,
            "release": identity,
            "row_count": row_count,
            "fields": contract,
            "data_sha256": data_sha256,
            "source_revision_ids": source_revisions,
            "source_revision_set_sha256": hashlib.sha256(
                _canonical_json(source_revisions)
            ).hexdigest(),
        }
        if (
            feed_id == "senate_composition"
            and filters.election_id == "election_fed_2025_05_03_general"
        ):
            manifest_core["supplemental_contract"] = self.composition_snapshot
        manifest = {
            **manifest_core,
            "manifest_sha256": hashlib.sha256(_canonical_json(manifest_core)).hexdigest(),
        }
        json_bytes = _canonical_json({"manifest": manifest, "data": rows})

        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow([*PROVENANCE_COLUMNS, *columns])
        provenance = [
            feed_id,
            FEED_VERSION,
            publication_id,
            release_id,
            database_sha256,
        ]
        for row in rows:
            writer.writerow([*provenance, *(_csv_value(row[name]) for name in columns)])
        csv_bytes = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        return FeedRepresentation(
            feed_id,
            publication_id,
            row_count,
            manifest,
            json_bytes,
            csv_bytes,
        )


def safe_feed_filename(feed_id: str, publication_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", f"politica_{feed_id}_{publication_id}.csv")
    return value[:180]
