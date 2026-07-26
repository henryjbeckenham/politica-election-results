from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import re

from ..ids import deterministic_uuid, fact_id
from .aec_house_summaries import (
    _event_id,
    _percentage_matches,
    _publication_phase,
    _replace_participation,
    _replace_vote_results,
    _vote_fact,
)
from .aec_individual import _decimal, _integer, _revision_family, _source_rows
from .references import normalise
from .transformers import TransformContext, TransformResult, register_transformer


ADAPTER_ID = "adapter_aec_2025_v1"
TRANSFORM_VERSION = "1.0.0"
STATES = {"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"}
VOTES = {
    "OrdinaryVotes": "ordinary",
    "AbsentVotes": "absent",
    "ProvisionalVotes": "provisional",
    "PrePollVotes": "early",
    "PostalVotes": "postal",
    "TotalVotes": "total",
}
STATE_FILES = {
    "GeneralEnrolmentByStateDownload": (
        "enrolment_state",
        (("Enrolment", "total", "enrolment", False),),
    ),
    "SenateInformalByStateDownload": (
        "senate_participation",
        (
            ("FormalVotes", "total", "formal_votes", False),
            ("InformalVotes", "total", "informal_votes", False),
            ("InformalPercent", "total", "informality_percentage", True),
        ),
    ),
    "SenateTurnoutByStateDownload": (
        "senate_participation",
        (
            ("Turnout", "total", "turnout", False),
            ("TurnoutPercentage", "total", "turnout_percentage", True),
        ),
    ),
    "SenateVotesCountedByStateDownload": (
        "senate_participation",
        tuple((field, vote_type, "total_votes", False) for field, vote_type in VOTES.items()),
    ),
}
DIVISION_FILES = {
    "SenateInformalByDivisionDownload": (
        ("FormalVotes", "total", "formal_votes", False),
        ("InformalVotes", "total", "informal_votes", False),
        ("InformalPercent", "total", "informality_percentage", True),
    ),
    "SenateTurnoutByDivisionDownload": (
        ("Turnout", "total", "turnout", False),
        ("TurnoutPercentage", "total", "turnout_percentage", True),
    ),
    "SenateVotesCountedByDivisionDownload": tuple(
        (field, vote_type, "total_votes", False) for field, vote_type in VOTES.items()
    ),
}


def _lineage(context: TransformContext, schema: str, table: str, identifier: object,
             locator: str, row_hash: str | None) -> tuple:
    lineage_id = fact_id(
        "row_lineage", [schema, table, str(identifier), locator], context.source_revision_id
    )
    return (
        lineage_id, schema, table, str(identifier), context.source_revision_id,
        locator, context.import_run_id, context.transform_run_id, row_hash,
    )


def _senate_index(context: TransformContext) -> tuple[dict[str, tuple[str, int]], dict[tuple[str, str], dict]]:
    rows = context.connection.execute(
        """SELECT contest.official_contest_id, contest.contest_id, contest.vacancies,
                  candidacy.official_candidate_id, candidacy.candidacy_id,
                  candidacy.party_id, candidacy.ballot_given_names,
                  candidacy.ballot_family_name, candidacy.ballot_name
           FROM core.election_chamber chamber
           JOIN core.contest contest ON contest.election_chamber_id=chamber.election_chamber_id
           LEFT JOIN core.candidacy candidacy ON candidacy.contest_id=contest.contest_id
                AND candidacy.record_status='active'
           WHERE chamber.election_id=? AND chamber.chamber_id='chamber_senate'
             AND chamber.record_status='active' AND contest.record_status='active'""",
        [context.job.get("election_id")],
    ).fetchall()
    contests: dict[str, tuple[str, int]] = {}
    candidates: dict[tuple[str, str], dict] = {}
    for state_value, contest_id, vacancies, candidate_id, candidacy_id, party_id, given, family, name in rows:
        state = str(state_value or "").strip().upper()
        if state not in STATES:
            raise ValueError(f"Invalid governed Senate state code: {state!r}")
        value = (str(contest_id), int(vacancies))
        if state in contests and contests[state] != value:
            raise ValueError(f"Multiple active Senate contests use {state}")
        contests[state] = value
        if candidate_id is not None:
            key = (state, str(candidate_id))
            if key in candidates:
                raise ValueError(f"Duplicate governed Senate CandidateID {state}/{candidate_id}")
            candidates[key] = {
                "contest_id": str(contest_id), "candidacy_id": candidacy_id,
                "party_id": party_id, "given": str(given or "").strip(),
                "family": str(family or "").strip(), "name": str(name or "").strip(),
            }
    if not contests or not candidates:
        raise ValueError("The selected election has no governed Senate contests or candidates")
    return contests, candidates


def _house_divisions(context: TransformContext) -> dict[str, tuple[str, str | None]]:
    rows = context.connection.execute(
        """SELECT contest.official_contest_id, contest.contest_name, reference.state_territory
           FROM core.election_chamber chamber
           JOIN core.contest contest ON contest.election_chamber_id=chamber.election_chamber_id
           LEFT JOIN sync.constituency reference
             ON reference.constituency_id=contest.canonical_constituency_id
           WHERE chamber.election_id=? AND chamber.chamber_id='chamber_house'
             AND chamber.record_status='active' AND contest.record_status='active'""",
        [context.job["election_id"]],
    ).fetchall()
    result: dict[str, tuple[str, str | None]] = {}
    for code, name, state in rows:
        key = str(code or "").strip()
        if not key or key in result:
            raise ValueError("A governed House division has a missing or duplicate DivisionID")
        result[key] = (str(name or "").strip(), str(state or "").strip().upper() or None)
    return result


def _division_identities(records: list[dict]) -> dict[str, tuple[str, str, str, str | None]]:
    result: dict[str, tuple[str, str, str, str | None]] = {}
    for record in records:
        identity = (record["division_name"], record["state"], record["locator"], record["row_hash"])
        previous = result.setdefault(record["division"], identity)
        if normalise(previous[0]) != normalise(identity[0]) or previous[1] != identity[1]:
            raise ValueError(f"DivisionID {record['division']} has inconsistent identity values")
    return result


def _validate_divisions(context: TransformContext, identities: dict[str, tuple[str, str, str, str | None]]) -> None:
    governed = _house_divisions(context)
    if governed and set(governed) != set(identities):
        missing = sorted(set(governed) - set(identities))
        extra = sorted(set(identities) - set(governed))
        raise ValueError(f"Senate division scope is incomplete; missing={missing[:8]}, unexpected={extra[:8]}")
    for division, (name, state, locator, _) in identities.items():
        if division not in governed:
            continue
        governed_name, governed_state = governed[division]
        # Constituency reference rows may store a full state name rather than
        # the AEC abbreviation. DivisionID plus the governed contest name are
        # the stable cross-file identity; StateAb is validated against the
        # Senate contest separately.
        if normalise(name) != normalise(governed_name):
            raise ValueError(f"Division identity does not match the governed register at {locator}")


def _ensure_division_units(context: TransformContext, identities: dict,
                           contests: dict[str, tuple[str, int]]) -> tuple[dict[tuple[str, str], object], int, list[tuple]]:
    units: dict[tuple[str, str], object] = {}
    inserted = 0
    lineages: list[tuple] = []
    for division, (name, state, locator, row_hash) in sorted(identities.items()):
        contest_id = contests[state][0]
        existing = context.connection.execute(
            """SELECT election_reporting_unit_id, official_label, source_reporting_unit_type
               FROM geography.election_reporting_unit
               WHERE election_id=? AND contest_id=? AND official_reporting_unit_code=?""",
            [context.job["election_id"], contest_id, division],
        ).fetchone()
        if existing:
            if normalise(existing[1]) != normalise(name) or existing[2] != "division":
                raise ValueError(f"Existing Senate reporting unit conflicts for DivisionID {division}")
            units[(state, division)] = existing[0]
            continue
        canonical = context.connection.execute(
            """SELECT reporting_unit_id FROM geography.reporting_unit
               WHERE authority_id='authority_aec' AND official_reporting_unit_code=?
                 AND record_status='active' ORDER BY valid_from DESC NULLS LAST LIMIT 1""",
            [f"division:{division}"],
        ).fetchone()
        unit_id = deterministic_uuid(
            "election_reporting_unit", context.job["election_id"], contest_id, f"division:{division}"
        )
        context.connection.execute(
            """INSERT INTO geography.election_reporting_unit VALUES
               (?, ?, ?, ?, ?, ?, 'division', 'district_total', NULL, NULL, NULL,
                NULL, NULL, ?, ?)""",
            [unit_id, context.job["election_id"], contest_id,
             canonical[0] if canonical else None, division, name,
             "matched" if canonical else "unmatched", context.source_revision_id],
        )
        units[(state, division)] = unit_id
        inserted += 1
        lineages.append(_lineage(context, "geography", "election_reporting_unit", unit_id, locator, row_hash))
    return units, inserted, lineages


def _preference_rows(context: TransformContext, division_scoped: bool):
    key = "senate_first_preferences_division" if division_scoped else "senate_first_preferences_state"
    rows = _source_rows(context, key)
    if not rows:
        raise ValueError("The staged dataset contains no source rows")
    contests, candidates = _senate_index(context)
    candidates_by_state: dict[str, set[str]] = defaultdict(set)
    for state, candidate in candidates:
        candidates_by_state[state].add(candidate)
    records: list[dict] = []
    assignments: dict[tuple[str, str], tuple[str, int, str, str | None]] = {}
    codes_by_state: dict[str, set[str]] = defaultdict(set)
    labels: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    candidates_by_scope: dict[tuple, set[str]] = defaultdict(set)
    groups_by_scope: dict[tuple, set[str]] = defaultdict(set)
    seen: set[tuple] = set()
    for locator, row_hash, row in rows:
        state = str(row.get("StateAb") or "").strip().upper()
        group = str(row.get("Group") or "").strip().upper()
        candidate = str(row.get("CandidateID") or "").strip()
        position = _integer(row.get("BallotPosition"), "BallotPosition", locator)
        division = str(row.get("DivisionID") or "").strip() if division_scoped else None
        division_name = str(row.get("DivisionNm") or "").strip() if division_scoped else None
        if state not in contests or not group:
            raise ValueError(f"Unrecognised Senate state or group at {locator}")
        if division_scoped and (not division or not division.isdigit() or not division_name):
            raise ValueError(f"Invalid DivisionID or DivisionNm at {locator}")
        scope = (state, division) if division_scoped else (state,)
        is_group = position == 0
        grain = (*scope, "group" if is_group else "candidate", group if is_group else candidate)
        if grain in seen:
            raise ValueError(f"Duplicate Senate first-preference source grain at {locator}")
        seen.add(grain)
        if is_group:
            if group == "UG":
                raise ValueError(f"Ungrouped candidates cannot have an above-the-line row at {locator}")
            groups_by_scope[scope].add(group)
            label = str(row.get("PartyName") or row.get("CandidateDetails") or group).strip()
            old = labels.setdefault((state, group), (label, locator, row_hash))
            if normalise(old[0]) != normalise(label):
                raise ValueError(f"Group label changes for {state}/{group}")
        else:
            if (state, candidate) not in candidates:
                raise ValueError(f"CandidateID does not resolve: {state}/{candidate} at {locator}")
            assignment = (group, position, locator, row_hash)
            old = assignments.setdefault((state, candidate), assignment)
            if old[:2] != assignment[:2]:
                raise ValueError(f"Candidate group or position changes for {state}/{candidate}")
            candidates_by_scope[scope].add(candidate)
        components = sum(_integer(row.get(field), field, locator) for field in tuple(VOTES)[:-1])
        if components != _integer(row.get("TotalVotes"), "TotalVotes", locator):
            raise ValueError(f"Vote-type components do not equal TotalVotes at {locator}")
        codes_by_state[state].add(group)
        records.append({
            "state": state, "group": group, "candidate": candidate,
            "position": position, "is_group": is_group, "division": division,
            "division_name": division_name, "locator": locator, "row_hash": row_hash, "row": row,
        })
    if {record["state"] for record in records} != set(contests):
        raise ValueError("The Senate first-preference file does not cover every governed state contest")
    for scope, observed in candidates_by_scope.items():
        state = scope[0]
        if observed != candidates_by_state[state]:
            raise ValueError(f"The Senate candidate result scope {scope} is incomplete")
        if groups_by_scope[scope] != codes_by_state[state] - {"UG"}:
            raise ValueError(f"The above-the-line group rows are incomplete for {scope}")
    expected_scopes = len(contests) if not division_scoped else len(_division_identities(records))
    if len(candidates_by_scope) != expected_scopes:
        raise ValueError("The Senate first-preference file contains an incomplete scope")
    for state, codes in codes_by_state.items():
        for group in codes:
            assigned = [item for key, item in assignments.items() if key[0] == state and item[0] == group]
            if not assigned:
                raise ValueError(f"Senate group {state}/{group} has no governed candidates")
            if group == "UG":
                labels[(state, group)] = ("Ungrouped", assigned[0][2], assigned[0][3])
    return records, contests, candidates, assignments, codes_by_state, labels


def _column(group: str, codes: set[str]) -> int:
    if group == "UG":
        return max(21, max((ord(code) - 64 for code in codes if len(code) == 1), default=0) + 1)
    if len(group) != 1 or not group.isalpha():
        raise ValueError(f"Unsupported Senate group code {group!r}")
    return ord(group) - 64


def _ensure_ballot_structure(context: TransformContext, contests: dict, candidates: dict,
                             assignments: dict, codes_by_state: dict, labels: dict):
    groups: dict[tuple[str, str], object] = {}
    inserted = 0
    lineages: list[tuple] = []
    for state, codes in sorted(codes_by_state.items()):
        contest_id = contests[state][0]
        for group in sorted(codes, key=lambda value: (value == "UG", value)):
            label, locator, row_hash = labels[(state, group)]
            party_ids = {
                candidates[key]["party_id"] for key, assignment in assignments.items()
                if key[0] == state and assignment[0] == group and candidates[key]["party_id"]
            }
            existing = context.connection.execute(
                """SELECT ballot_group_id, ungrouped FROM core.ballot_group
                   WHERE contest_id=? AND official_group_id=? AND record_status='active'""",
                [contest_id, group],
            ).fetchone()
            if existing:
                if bool(existing[1]) != (group == "UG"):
                    raise ValueError(f"Existing ballot group conflicts for {state}/{group}")
                group_id = existing[0]
            else:
                group_id = deterministic_uuid("ballot_group", contest_id, group)
                context.connection.execute(
                    "INSERT INTO core.ballot_group VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
                    [group_id, contest_id, group, group, label,
                     next(iter(party_ids)) if len(party_ids) == 1 else None,
                     "ungrouped" if group == "UG" else "above_the_line",
                     group == "UG", _publication_phase(context)],
                )
                inserted += 1
                lineages.append(_lineage(context, "core", "ballot_group", group_id, locator, row_hash))
            groups[(state, group)] = group_id
            if group != "UG":
                position = context.connection.execute(
                    "SELECT ballot_position_id FROM core.ballot_position WHERE contest_id=? AND ballot_group_id=?",
                    [contest_id, group_id],
                ).fetchone()
                if not position:
                    position_id = deterministic_uuid("ballot_position", contest_id, group_id)
                    context.connection.execute(
                        "INSERT INTO core.ballot_position VALUES (?, ?, NULL, ?, ?, 1, 'senate_group_column', ?)",
                        [position_id, contest_id, group_id, _column(group, codes), context.source_revision_id],
                    )
                    inserted += 1
                    lineages.append(_lineage(context, "core", "ballot_position", position_id, locator, row_hash))
    for (state, candidate), (group, position, locator, row_hash) in sorted(assignments.items()):
        contest_id = contests[state][0]
        candidacy_id = candidates[(state, candidate)]["candidacy_id"]
        group_id = groups[(state, group)]
        membership = context.connection.execute(
            """SELECT ballot_group_id, group_position FROM core.ballot_group_membership
               WHERE candidacy_id=?""", [candidacy_id]
        ).fetchone()
        if membership and (membership[0] != group_id or int(membership[1]) != position):
            raise ValueError(f"Existing ballot membership conflicts for {state}/{candidate}")
        if not membership:
            membership_id = deterministic_uuid("ballot_group_membership", group_id, candidacy_id)
            context.connection.execute(
                "INSERT INTO core.ballot_group_membership VALUES (?, ?, ?, ?, 'candidate')",
                [membership_id, group_id, candidacy_id, position],
            )
            inserted += 1
            lineages.append(_lineage(context, "core", "ballot_group_membership", membership_id, locator, row_hash))
        ballot_position = context.connection.execute(
            "SELECT ballot_position_id, position_number FROM core.ballot_position WHERE contest_id=? AND candidacy_id=?",
            [contest_id, candidacy_id],
        ).fetchone()
        if ballot_position and int(ballot_position[1]) != position:
            raise ValueError(f"Existing ballot position conflicts for {state}/{candidate}")
        if not ballot_position:
            position_id = deterministic_uuid("ballot_position", contest_id, candidacy_id)
            context.connection.execute(
                "INSERT INTO core.ballot_position VALUES (?, ?, ?, NULL, ?, ?, 'senate_candidate_column', ?)",
                [position_id, contest_id, candidacy_id, _column(group, codes_by_state[state]),
                 position, context.source_revision_id],
            )
            inserted += 1
            lineages.append(_lineage(context, "core", "ballot_position", position_id, locator, row_hash))
    return groups, inserted, lineages


def _transform_preferences(context: TransformContext, division_scoped: bool) -> TransformResult:
    stem = "SenateFirstPrefsByDivisionByVoteTypeDownload" if division_scoped else "SenateFirstPrefsByStateByVoteTypeDownload"
    _event_id(context, rf"{stem}-(?P<event>\d+)\.csv")
    records, contests, candidates, assignments, codes, labels = _preference_rows(context, division_scoped)
    inserted_structure = 0
    lineages: list[tuple] = []
    units: dict[tuple[str, str], object] = {}
    if division_scoped:
        identities = _division_identities(records)
        _validate_divisions(context, identities)
        units, count, lines = _ensure_division_units(context, identities, contests)
        inserted_structure += count
        lineages.extend(lines)
    groups, count, lines = _ensure_ballot_structure(context, contests, candidates, assignments, codes, labels)
    inserted_structure += count
    lineages.extend(lines)
    facts: list[tuple] = []
    grains: set[tuple] = set()
    for record in records:
        contest_id = contests[record["state"]][0]
        unit = units[(record["state"], record["division"])] if division_scoped else None
        subject_type = "ballot_group" if record["is_group"] else "candidacy"
        subject_id = groups[(record["state"], record["group"])] if record["is_group"] else candidates[(record["state"], record["candidate"])]["candidacy_id"]
        for field, vote_type in VOTES.items():
            grain = (contest_id, unit, subject_type, subject_id, vote_type)
            if grain in grains:
                raise ValueError(f"Duplicate canonical Senate result grain at {record['locator']}")
            grains.add(grain)
            fact, lineage = _vote_fact(
                context, contest_id=contest_id, subject_type=subject_type,
                subject_id=subject_id, result_type="first_preference", vote_type=vote_type,
                measure_type="votes", integer_value=_integer(record["row"].get(field), field, record["locator"]),
                decimal_value=None, locator=record["locator"], source_field=field,
                row_hash=record["row_hash"], election_reporting_unit_id=unit,
            )
            facts.append(fact)
            lineages.append(lineage)
    _, prior = _revision_family(context)
    superseded = _replace_vote_results(context, facts, lineages, prior)
    return TransformResult(
        inserted_rows=len(facts) + inserted_structure,
        notes=f"Inserted {len(facts):,} Senate first-preference facts and {inserted_structure:,} structure rows; superseded {superseded:,} prior facts.",
    )


def transform_preferences_state(context: TransformContext) -> TransformResult:
    return _transform_preferences(context, False)


def transform_preferences_division(context: TransformContext) -> TransformResult:
    return _transform_preferences(context, True)


def _validate_participation(prefix: str, row: dict, locator: str) -> None:
    if prefix == "GeneralEnrolmentByStateDownload":
        total = _integer(row.get("CloseOfRollsEnrolment"), "CloseOfRollsEnrolment", locator)
        total += _integer(row.get("NotebookRollAdditions"), "NotebookRollAdditions", locator)
        total -= _integer(row.get("NotebookRollDeletions"), "NotebookRollDeletions", locator)
        total += sum(_integer(row.get(field), field, locator) for field in (
            "ReinstatementsPostal", "ReinstatementsPrePoll", "ReinstatementsAbsent", "ReinstatementsProvisional"
        ))
        if total != _integer(row.get("Enrolment"), "Enrolment", locator):
            raise ValueError(f"Enrolment components do not reconcile at {locator}")
    elif "Informal" in prefix:
        formal = _integer(row.get("FormalVotes"), "FormalVotes", locator)
        informal = _integer(row.get("InformalVotes"), "InformalVotes", locator)
        total = _integer(row.get("TotalVotes"), "TotalVotes", locator)
        if formal + informal != total or not _percentage_matches(informal, total, _decimal(row.get("InformalPercent"), "InformalPercent", locator)):
            raise ValueError(f"Informal-vote values do not reconcile at {locator}")
    elif "Turnout" in prefix:
        enrolment = _integer(row.get("Enrolment"), "Enrolment", locator)
        turnout = _integer(row.get("Turnout"), "Turnout", locator)
        if turnout > enrolment or not _percentage_matches(turnout, enrolment, _decimal(row.get("TurnoutPercentage"), "TurnoutPercentage", locator)):
            raise ValueError(f"Turnout values do not reconcile at {locator}")
    else:
        total = _integer(row.get("TotalVotes"), "TotalVotes", locator)
        components = sum(_integer(row.get(field), field, locator) for field in tuple(VOTES)[:-1])
        enrolment = _integer(row.get("Enrolment"), "Enrolment", locator)
        if components != total or not _percentage_matches(total, enrolment, _decimal(row.get("TotalPercentage"), "TotalPercentage", locator)):
            raise ValueError(f"Votes-counted values do not reconcile at {locator}")


def _participation_fact(context: TransformContext, contest_id: str, unit: object | None,
                        vote_type: str, measure: str, integer: int | None,
                        decimal: Decimal | None, locator: str, field: str,
                        row_hash: str | None) -> tuple[tuple, tuple]:
    identifier = fact_id(
        "participation_result",
        [context.job["election_id"], contest_id, unit, vote_type, measure],
        context.source_revision_id,
    )
    source_locator = f"{locator};field:{field}"
    fact = (
        identifier, context.job["election_id"], contest_id, unit, vote_type, measure,
        integer, decimal, "reported", "official_calculated" if decimal is not None else "official_reported",
        _publication_phase(context), context.source_revision_id, source_locator,
        context.import_run_id, "active",
    )
    return fact, _lineage(context, "results", "participation_result", identifier, source_locator, row_hash)


def transform_state_participation(context: TransformContext) -> TransformResult:
    filename = Path(context.dataset["virtual_name"]).name
    match = re.fullmatch(r"(?P<prefix>GeneralEnrolmentByStateDownload|SenateInformalByStateDownload|SenateTurnoutByStateDownload|SenateVotesCountedByStateDownload)-(?P<event>\d+)\.csv", filename, re.I)
    if not match:
        raise ValueError(f"Invalid Senate state summary filename: {filename}")
    prefix = next(value for value in STATE_FILES if value.casefold() == match.group("prefix").casefold())
    dataset_key, fields = STATE_FILES[prefix]
    if context.dataset["detection"]["selection"]["dataset_key"] != dataset_key:
        raise ValueError("Detected dataset key does not match the Senate state source")
    _event_id(context, rf"{re.escape(prefix)}-(?P<event>\d+)\.csv")
    rows = _source_rows(context, dataset_key)
    contests, _ = _senate_index(context)
    seen: set[str] = set()
    facts: list[tuple] = []
    lineages: list[tuple] = []
    for locator, row_hash, row in rows:
        state = str(row.get("StateAb") or "").strip().upper()
        if state in seen or state not in contests:
            raise ValueError(f"Duplicate or unknown StateAb {state!r} at {locator}")
        seen.add(state)
        _validate_participation(prefix, row, locator)
        for field, vote_type, measure, is_decimal in fields:
            value = row.get(field)
            fact, lineage = _participation_fact(
                context, contests[state][0], None, vote_type, measure,
                None if is_decimal else _integer(value, field, locator),
                _decimal(value, field, locator) if is_decimal else None,
                locator, field, row_hash,
            )
            facts.append(fact)
            lineages.append(lineage)
    if seen != set(contests):
        raise ValueError("The Senate state summary does not cover every governed contest")
    _, prior = _revision_family(context)
    superseded = _replace_participation(context, facts, lineages, prior)
    return TransformResult(len(facts), notes=f"Inserted {len(facts):,} Senate state participation facts; superseded {superseded:,} prior facts.")


def transform_division_participation(context: TransformContext) -> TransformResult:
    filename = Path(context.dataset["virtual_name"]).name
    match = re.fullmatch(r"(?P<prefix>SenateInformalByDivisionDownload|SenateTurnoutByDivisionDownload|SenateVotesCountedByDivisionDownload)-(?P<event>\d+)\.csv", filename, re.I)
    if not match:
        raise ValueError(f"Invalid Senate division summary filename: {filename}")
    prefix = next(value for value in DIVISION_FILES if value.casefold() == match.group("prefix").casefold())
    _event_id(context, rf"{re.escape(prefix)}-(?P<event>\d+)\.csv")
    rows = _source_rows(context, "senate_participation_division")
    contests, _ = _senate_index(context)
    records: list[dict] = []
    seen: set[str] = set()
    for locator, row_hash, row in rows:
        division = str(row.get("DivisionID") or "").strip()
        state = str(row.get("StateAb") or "").strip().upper()
        name = str(row.get("DivisionNm") or "").strip()
        if not division.isdigit() or not name or state not in contests or division in seen:
            raise ValueError(f"Duplicate or invalid Senate division row at {locator}")
        seen.add(division)
        _validate_participation(prefix, row, locator)
        records.append({"division": division, "division_name": name, "state": state,
                        "locator": locator, "row_hash": row_hash, "row": row})
    identities = _division_identities(records)
    _validate_divisions(context, identities)
    units, structure_count, lineages = _ensure_division_units(context, identities, contests)
    facts: list[tuple] = []
    for record in records:
        for field, vote_type, measure, is_decimal in DIVISION_FILES[prefix]:
            value = record["row"].get(field)
            fact, lineage = _participation_fact(
                context, contests[record["state"]][0], units[(record["state"], record["division"])],
                vote_type, measure, None if is_decimal else _integer(value, field, record["locator"]),
                _decimal(value, field, record["locator"]) if is_decimal else None,
                record["locator"], field, record["row_hash"],
            )
            facts.append(fact)
            lineages.append(lineage)
    _, prior = _revision_family(context)
    superseded = _replace_participation(context, facts, lineages, prior)
    return TransformResult(len(facts) + structure_count, notes=f"Inserted {len(facts):,} Senate division participation facts and {structure_count:,} reporting units; superseded {superseded:,} prior facts.")


def transform_elected(context: TransformContext) -> TransformResult:
    _event_id(context, r"SenateSenatorsElectedDownload-(?P<event>\d+)\.csv")
    rows = _source_rows(context, "senate_elected")
    contests, candidates = _senate_index(context)
    names: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for (state, _), candidate in candidates.items():
        name = f"{candidate['given']} {candidate['family']}".strip() or candidate["name"]
        names[(state, normalise(name))].append(candidate)
    orders: dict[str, set[int]] = defaultdict(set)
    outcomes: list[tuple] = []
    members: list[tuple] = []
    lineages: list[tuple] = []
    for locator, row_hash, row in rows:
        state = str(row.get("StateAb") or "").strip().upper()
        name = normalise(f"{row.get('GivenNm') or ''} {row.get('Surname') or ''}")
        matches = names.get((state, name), [])
        order = _integer(row.get("ElectedOrder"), "ElectedOrder", locator)
        if state not in contests or len(matches) != 1 or order in orders[state] or order < 1:
            raise ValueError(f"Elected senator does not resolve uniquely at {locator}")
        orders[state].add(order)
        candidate = matches[0]
        contest_id = contests[state][0]
        outcome_id = fact_id("contest_outcome", [contest_id, candidate["candidacy_id"], "elected"], context.source_revision_id)
        outcomes.append((outcome_id, contest_id, candidate["candidacy_id"], "elected", order, None,
                         _publication_phase(context), context.source_revision_id, locator, "active"))
        person = context.connection.execute("SELECT person_id FROM core.candidacy WHERE candidacy_id=?", [candidate["candidacy_id"]]).fetchone()
        member_id = fact_id("elected_member", [outcome_id], context.source_revision_id)
        members.append((member_id, outcome_id, context.job["election_id"], contest_id,
                        candidate["candidacy_id"], person[0] if person else None, order, "pending"))
        lineages.append(_lineage(context, "results", "contest_outcome", outcome_id, locator, row_hash))
        lineages.append(_lineage(context, "results", "elected_member", member_id, locator, row_hash))
    if set(orders) != set(contests):
        raise ValueError("The elected-senators file does not cover every governed contest")
    for state, (_, vacancies) in contests.items():
        if orders[state] != set(range(1, vacancies + 1)):
            raise ValueError(f"Elected orders for {state} do not reconcile to {vacancies} vacancies")
    _, prior = _revision_family(context)
    contest_ids = {row[1] for row in outcomes}
    conflicts = context.connection.execute(
        "SELECT contest_id, source_revision_id FROM results.contest_outcome WHERE outcome_type='elected' AND record_status='active'"
    ).fetchall()
    if any(row[0] in contest_ids and row[1] not in prior and row[1] != context.source_revision_id for row in conflicts):
        raise ValueError("Active Senate elected outcomes exist from a different logical source")
    superseded = 0
    if prior:
        placeholders = ",".join("?" for _ in prior)
        context.connection.execute(f"DELETE FROM provenance.row_lineage WHERE target_schema='results' AND target_table='elected_member' AND source_revision_id IN ({placeholders})", sorted(prior))
        context.connection.execute(f"DELETE FROM results.elected_member WHERE contest_outcome_id IN (SELECT contest_outcome_id FROM results.contest_outcome WHERE source_revision_id IN ({placeholders}))", sorted(prior))
        superseded = len(context.connection.execute(
            f"UPDATE results.contest_outcome SET record_status='superseded' WHERE record_status='active' AND source_revision_id IN ({placeholders}) RETURNING contest_outcome_id",
            sorted(prior),
        ).fetchall())
    context.connection.executemany("INSERT INTO results.contest_outcome VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", outcomes)
    context.connection.executemany("INSERT INTO results.elected_member VALUES (?, ?, ?, ?, ?, ?, ?, ?)", members)
    context.connection.executemany("INSERT INTO provenance.row_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", lineages)
    return TransformResult(len(outcomes) + len(members), notes=f"Inserted {len(outcomes):,} Senate outcomes and current members; superseded {superseded:,} prior outcomes.")


register_transformer(ADAPTER_ID, "senate_first_preferences_state", TRANSFORM_VERSION, transform_preferences_state)
register_transformer(ADAPTER_ID, "senate_first_preferences_division", TRANSFORM_VERSION, transform_preferences_division)
register_transformer(ADAPTER_ID, "senate_elected", TRANSFORM_VERSION, transform_elected)
register_transformer(ADAPTER_ID, "enrolment_state", TRANSFORM_VERSION, transform_state_participation)
register_transformer(ADAPTER_ID, "senate_participation", TRANSFORM_VERSION, transform_state_participation)
register_transformer(ADAPTER_ID, "senate_participation_division", TRANSFORM_VERSION, transform_division_participation)
