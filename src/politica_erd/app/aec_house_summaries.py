from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import re

from ..ids import fact_id
from ..tcp_measures import TcpReportedPercentage, classify_tcp_reported_percentages
from .aec_individual import _decimal, _integer, _revision_family, _source_rows
from .transformers import TransformContext, TransformResult, register_transformer


ADAPTER_ID = "adapter_aec_2025_v1"
TRANSFORM_VERSION = "1.0.1"

VOTE_FIELDS = {
    "OrdinaryVotes": "ordinary",
    "AbsentVotes": "absent",
    "ProvisionalVotes": "provisional",
    "PrePollVotes": "early",
    "PostalVotes": "postal",
    "TotalVotes": "total",
}

PARTICIPATION_CONFIG = {
    "GeneralEnrolmentByDivisionDownload": {
        "fields": (("Enrolment", "total", "enrolment", False),),
        "label": "House enrolment",
    },
    "HouseInformalByDivisionDownload": {
        "fields": (
            ("FormalVotes", "total", "formal_votes", False),
            ("InformalVotes", "total", "informal_votes", False),
            ("InformalPercent", "total", "informality_percentage", True),
        ),
        "label": "House informal-vote summary",
    },
    "HouseTurnoutByDivisionDownload": {
        "fields": (
            ("Turnout", "total", "turnout", False),
            ("TurnoutPercentage", "total", "turnout_percentage", True),
        ),
        "label": "House turnout",
    },
    "HouseVotesCountedByDivisionDownload": {
        "fields": tuple(
            (field, vote_type, "total_votes", False)
            for field, vote_type in VOTE_FIELDS.items()
        ),
        "label": "House votes-counted summary",
    },
}


def _publication_phase(context: TransformContext) -> str:
    return context.job.get("configuration", {}).get("publication_phase") or "final"


def _event_id(context: TransformContext, filename_pattern: str) -> str:
    election_id = context.job.get("election_id")
    if not election_id:
        raise ValueError("An existing election must be selected for this AEC transformer")
    election = context.connection.execute(
        """SELECT authority_id, official_event_id FROM core.election
           WHERE election_id=? AND record_status='active'""",
        [election_id],
    ).fetchone()
    if election is None:
        raise ValueError(f"The selected election is not present in the governed database: {election_id}")
    if election[0] != "authority_aec":
        raise ValueError("The AEC transformer can only target an AEC election")
    filename = Path(context.dataset["virtual_name"]).name
    match = re.fullmatch(filename_pattern, filename, flags=re.IGNORECASE)
    if match is None:
        raise ValueError(f"The AEC source filename is not valid for this transformer: {filename}")
    event_id = match.group("event")
    if str(election[1] or "") != event_id:
        raise ValueError(
            f"Source event {event_id} does not match the selected election's "
            f"official event ID {election[1]!r}"
        )
    return event_id


def _house_indexes(
    context: TransformContext,
) -> tuple[dict[str, str], dict[tuple[str, str], tuple[str, object]]]:
    election_id = context.job["election_id"]
    contests: dict[str, str] = {}
    candidates: dict[tuple[str, str], tuple[str, object]] = {}
    rows = context.connection.execute(
        """SELECT contest.official_contest_id, contest.contest_id,
                  candidacy.official_candidate_id, candidacy.candidacy_id
           FROM core.election_chamber chamber
           JOIN core.contest contest
             ON contest.election_chamber_id=chamber.election_chamber_id
           LEFT JOIN core.candidacy candidacy
             ON candidacy.contest_id=contest.contest_id
            AND candidacy.record_status='active'
           WHERE chamber.election_id=? AND chamber.chamber_id='chamber_house'
             AND chamber.record_status='active' AND contest.record_status='active'""",
        [election_id],
    ).fetchall()
    for official_contest, contest_id, official_candidate, candidacy_id in rows:
        if official_contest is None:
            raise ValueError(f"Governed House contest {contest_id} has no official contest ID")
        division = str(official_contest)
        contests[division] = contest_id
        if official_candidate is not None:
            candidates[(division, str(official_candidate))] = (contest_id, candidacy_id)
    if not contests:
        raise ValueError("The selected election has no active governed House contests")
    return contests, candidates


def _complete_contests(
    contests: dict[str, str], seen: set[str], label: str
) -> None:
    missing = sorted(set(contests) - seen, key=lambda value: (len(value), value))
    if not missing:
        return
    sample = ", ".join(missing[:8])
    suffix = "" if len(missing) <= 8 else f" (+{len(missing) - 8} more)"
    raise ValueError(
        f"The source is not a complete {label} revision; governed DivisionID rows are "
        f"missing: {sample}{suffix}"
    )


def _percentage_matches(numerator: int, denominator: int, reported: Decimal) -> bool:
    if denominator == 0:
        return reported == 0
    expected = (Decimal(numerator) * Decimal(100)) / Decimal(denominator)
    return abs(expected - reported) <= Decimal("0.011")


def _vote_key(row: tuple) -> tuple:
    return (
        row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8],
        row[9], row[10], row[11],
    )


def _replace_vote_results(
    context: TransformContext,
    facts: list[tuple],
    lineage: list[tuple],
    prior_revisions: set[str],
) -> int:
    new_keys = {_vote_key(row) for row in facts}
    existing = context.connection.execute(
        """SELECT election_id, contest_id, election_reporting_unit_id, subject_type,
                  candidacy_id, ballot_group_id, party_id, question_option_code,
                  result_type, vote_type, measure_type, source_revision_id
           FROM results.vote_result
           WHERE election_id=? AND record_status='active'""",
        [context.job["election_id"]],
    ).fetchall()
    conflicts = [
        row for row in existing
        if tuple(row[:11]) in new_keys
        and row[11] not in prior_revisions
        and row[11] != context.source_revision_id
    ]
    if conflicts:
        raise ValueError(
            "Active results already occupy canonical grains from a different logical source"
        )
    superseded = 0
    if prior_revisions:
        placeholders = ",".join("?" for _ in prior_revisions)
        superseded = len(
            context.connection.execute(
                f"""UPDATE results.vote_result SET record_status='superseded'
                     WHERE record_status='active' AND source_revision_id IN ({placeholders})
                     RETURNING vote_result_id""",
                sorted(prior_revisions),
            ).fetchall()
        )
    context.connection.executemany(
        "INSERT INTO results.vote_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        facts,
    )
    context.connection.executemany(
        "INSERT INTO provenance.row_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        lineage,
    )
    return superseded


def _vote_fact(
    context: TransformContext,
    *,
    contest_id: str,
    subject_type: str,
    subject_id: object | str,
    result_type: str,
    vote_type: str,
    measure_type: str,
    integer_value: int | None,
    decimal_value: Decimal | None,
    locator: str,
    source_field: str,
    row_hash: str | None,
    value_basis: str | None = None,
    election_reporting_unit_id: object | None = None,
) -> tuple[tuple, tuple]:
    natural = (
        context.job["election_id"], contest_id, election_reporting_unit_id,
        subject_type, subject_id,
        result_type, vote_type, measure_type,
    )
    identifier = fact_id("vote_result", natural, context.source_revision_id)
    source_locator = f"{locator};field:{source_field}"
    fact = (
        identifier,
        context.job["election_id"],
        contest_id,
        election_reporting_unit_id,
        subject_type,
        subject_id if subject_type == "candidacy" else None,
        subject_id if subject_type == "ballot_group" else None,
        subject_id if subject_type == "party" else None,
        None,
        result_type,
        vote_type,
        measure_type,
        integer_value,
        decimal_value,
        "reported",
        value_basis or ("official_reported" if integer_value is not None else "official_calculated"),
        _publication_phase(context),
        context.source_revision_id,
        source_locator,
        context.import_run_id,
        "active",
    )
    lineage_id = fact_id(
        "row_lineage", ["results", "vote_result", str(identifier), source_locator],
        context.source_revision_id,
    )
    lineage = (
        lineage_id, "results", "vote_result", str(identifier),
        context.source_revision_id, source_locator, context.import_run_id,
        context.transform_run_id, row_hash,
    )
    return fact, lineage


def transform_house_tcp_by_vote_type(context: TransformContext) -> TransformResult:
    dataset_key = "house_tcp_by_vote_type"
    _event_id(
        context,
        r"HouseTcpByCandidateByVoteTypeDownload-(?P<event>\d+)\.csv",
    )
    rows = _source_rows(context, dataset_key)
    if not rows:
        raise ValueError("The staged dataset contains no source rows")
    contests, candidates = _house_indexes(context)
    by_contest: dict[str, set[str]] = defaultdict(set)
    seen_source_keys: set[tuple[str, str]] = set()
    staged_rows: list[tuple[str, str | None, dict, str, object, Decimal]] = []
    reported_by_division: dict[str, list[TcpReportedPercentage]] = defaultdict(list)
    facts: list[tuple] = []
    lineage: list[tuple] = []
    natural_keys: set[tuple] = set()
    unresolved: list[str] = []

    for locator, row_hash, row in rows:
        division = str(row.get("DivisionID") or "").strip()
        candidate = str(row.get("CandidateID") or "").strip()
        source_key = (division, candidate)
        if source_key in seen_source_keys:
            raise ValueError(f"Duplicate DivisionID/CandidateID row in the source: {division}/{candidate}")
        seen_source_keys.add(source_key)
        governed = candidates.get(source_key)
        if governed is None:
            unresolved.append(f"{division}/{candidate} at {locator}")
            continue
        contest_id, candidacy_id = governed
        by_contest[division].add(candidate)
        component_total = sum(
            _integer(row.get(field), field, locator)
            for field in ("OrdinaryVotes", "AbsentVotes", "ProvisionalVotes", "PrePollVotes", "PostalVotes")
        )
        total = _integer(row.get("TotalVotes"), "TotalVotes", locator)
        if component_total != total:
            raise ValueError(
                f"Vote-type components total {component_total:,}, but TotalVotes is {total:,} at {locator}"
            )
        reported = _decimal(row.get("Swing"), "Swing", locator)
        reported_by_division[division].append(
            TcpReportedPercentage(reported, total)
        )
        staged_rows.append(
            (locator, row_hash, row, contest_id, candidacy_id, reported)
        )

    if unresolved:
        sample = ", ".join(unresolved[:8])
        suffix = "" if len(unresolved) <= 8 else f" (+{len(unresolved) - 8} more)"
        raise ValueError("Official candidate keys do not resolve in the selected election: " + sample + suffix)
    _complete_contests(contests, set(by_contest), "House two-candidate-preferred")
    invalid = sorted(division for division, values in by_contest.items() if len(values) != 2)
    if invalid:
        raise ValueError(
            "Each governed House contest must contain exactly two TCP candidates; invalid DivisionID rows: "
            + ", ".join(invalid[:8])
        )
    measure_by_division = {
        division: classify_tcp_reported_percentages(
            values,
            context=f"House TCP DivisionID {division}",
        )
        for division, values in reported_by_division.items()
    }

    for locator, row_hash, row, contest_id, candidacy_id, reported in staged_rows:
        division = str(row.get("DivisionID") or "").strip()
        measures: list[tuple[str, str, int | None, Decimal | None]] = [
            (field, vote_type, _integer(row.get(field), field, locator), None)
            for field, vote_type in VOTE_FIELDS.items()
        ]
        measures.append(("Swing", "total", None, reported))
        for field, vote_type, integer_value, decimal_value in measures:
            measure_type = (
                "votes" if integer_value is not None else measure_by_division[division]
            )
            grain = (contest_id, candidacy_id, vote_type, measure_type)
            if grain in natural_keys:
                raise ValueError(f"Duplicate canonical result grain generated at {locator}")
            natural_keys.add(grain)
            fact, line = _vote_fact(
                context,
                contest_id=contest_id,
                subject_type="candidacy",
                subject_id=candidacy_id,
                result_type="tcp",
                vote_type=vote_type,
                measure_type=measure_type,
                integer_value=integer_value,
                decimal_value=decimal_value,
                locator=locator,
                source_field=field,
                row_hash=row_hash,
            )
            facts.append(fact)
            lineage.append(line)

    _, prior = _revision_family(context)
    superseded = _replace_vote_results(context, facts, lineage, prior)
    return TransformResult(
        inserted_rows=len(facts),
        notes=(
            f"Inserted {len(facts):,} active House two-candidate-preferred observations; "
            f"superseded {superseded:,} observations from prior revisions."
        ),
    )


def transform_house_tpp_division(context: TransformContext) -> TransformResult:
    dataset_key = "house_tpp_division"
    _event_id(context, r"HouseTppByDivisionDownload-(?P<event>\d+)\.csv")
    rows = _source_rows(context, dataset_key)
    if not rows:
        raise ValueError("The staged dataset contains no source rows")
    contests, _ = _house_indexes(context)
    required_parties = {"party_alp", "party_coalition"}
    observed_parties = {
        row[0]
        for row in context.connection.execute(
            "SELECT party_id FROM sync.party WHERE party_id IN ('party_alp', 'party_coalition') AND record_status='active'"
        ).fetchall()
    }
    if observed_parties != required_parties:
        raise ValueError("The governed ALP and Coalition party records are required for TPP results")
    seen: set[str] = set()
    facts: list[tuple] = []
    lineage: list[tuple] = []
    for locator, row_hash, row in rows:
        division = str(row.get("DivisionID") or "").strip()
        if division in seen:
            raise ValueError(f"Duplicate DivisionID row in the source: {division}")
        seen.add(division)
        contest_id = contests.get(division)
        if contest_id is None:
            raise ValueError(f"Official DivisionID does not resolve in the selected election: {division} at {locator}")
        alp_votes = _integer(row.get("Australian Labor Party Votes"), "Australian Labor Party Votes", locator)
        coalition_votes = _integer(row.get("Liberal/National Coalition Votes"), "Liberal/National Coalition Votes", locator)
        total = _integer(row.get("TotalVotes"), "TotalVotes", locator)
        if alp_votes + coalition_votes != total:
            raise ValueError(f"ALP and Coalition TPP votes do not reconcile to TotalVotes at {locator}")
        alp_share = _decimal(row.get("Australian Labor Party Percentage"), "Australian Labor Party Percentage", locator)
        coalition_share = _decimal(row.get("Liberal/National Coalition Percentage"), "Liberal/National Coalition Percentage", locator)
        if not _percentage_matches(alp_votes, total, alp_share) or not _percentage_matches(coalition_votes, total, coalition_share):
            raise ValueError(f"TPP vote percentages do not reconcile to votes at {locator}")
        values = (
            ("party_alp", "Australian Labor Party Votes", "votes", alp_votes, None),
            ("party_alp", "Australian Labor Party Percentage", "vote_share", None, alp_share),
            ("party_coalition", "Liberal/National Coalition Votes", "votes", coalition_votes, None),
            ("party_coalition", "Liberal/National Coalition Percentage", "vote_share", None, coalition_share),
        )
        for party_id, field, measure, integer_value, decimal_value in values:
            fact, line = _vote_fact(
                context,
                contest_id=contest_id,
                subject_type="party",
                subject_id=party_id,
                result_type="tpp",
                vote_type="total",
                measure_type=measure,
                integer_value=integer_value,
                decimal_value=decimal_value,
                locator=locator,
                source_field=field,
                row_hash=row_hash,
                value_basis="official_calculated",
            )
            facts.append(fact)
            lineage.append(line)
        if str(row.get("Swing") or "").strip():
            swing_party = "party_alp" if str(row.get("PartyAb") or "").upper() == "ALP" else "party_coalition"
            fact, line = _vote_fact(
                context,
                contest_id=contest_id,
                subject_type="party",
                subject_id=swing_party,
                result_type="tpp",
                vote_type="total",
                measure_type="swing",
                integer_value=None,
                decimal_value=_decimal(row["Swing"], "Swing", locator),
                locator=locator,
                source_field="Swing",
                row_hash=row_hash,
                value_basis="official_calculated",
            )
            facts.append(fact)
            lineage.append(line)
    _complete_contests(contests, seen, "House two-party-preferred")
    _, prior = _revision_family(context)
    superseded = _replace_vote_results(context, facts, lineage, prior)
    return TransformResult(
        inserted_rows=len(facts),
        notes=(
            f"Inserted {len(facts):,} active division-level House two-party-preferred observations; "
            f"superseded {superseded:,} observations from prior revisions."
        ),
    )


def _participation_key(row: tuple) -> tuple:
    return tuple(row[1:6])


def _replace_participation(
    context: TransformContext,
    facts: list[tuple],
    lineage: list[tuple],
    prior_revisions: set[str],
) -> int:
    new_keys = {_participation_key(row) for row in facts}
    existing = context.connection.execute(
        """SELECT election_id, contest_id, election_reporting_unit_id, vote_type,
                  measure_type, source_revision_id
           FROM results.participation_result
           WHERE election_id=? AND record_status='active'""",
        [context.job["election_id"]],
    ).fetchall()
    conflicts = [
        row for row in existing
        if tuple(row[:5]) in new_keys
        and row[5] not in prior_revisions
        and row[5] != context.source_revision_id
    ]
    if conflicts:
        raise ValueError(
            "Active participation results already occupy canonical grains from a different logical source"
        )
    superseded = 0
    if prior_revisions:
        placeholders = ",".join("?" for _ in prior_revisions)
        superseded = len(
            context.connection.execute(
                f"""UPDATE results.participation_result SET record_status='superseded'
                     WHERE record_status='active' AND source_revision_id IN ({placeholders})
                     RETURNING participation_result_id""",
                sorted(prior_revisions),
            ).fetchall()
        )
    context.connection.executemany(
        "INSERT INTO results.participation_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        facts,
    )
    context.connection.executemany(
        "INSERT INTO provenance.row_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        lineage,
    )
    return superseded


def _validate_participation_row(prefix: str, row: dict, locator: str) -> None:
    if prefix == "GeneralEnrolmentByDivisionDownload":
        close = _integer(row.get("CloseOfRollsEnrolment"), "CloseOfRollsEnrolment", locator)
        additions = _integer(row.get("NotebookRollAdditions"), "NotebookRollAdditions", locator)
        deletions = _integer(row.get("NotebookRollDeletions"), "NotebookRollDeletions", locator)
        reinstatements = sum(
            _integer(row.get(field), field, locator)
            for field in ("ReinstatementsPostal", "ReinstatementsPrePoll", "ReinstatementsAbsent", "ReinstatementsProvisional")
        )
        enrolment = _integer(row.get("Enrolment"), "Enrolment", locator)
        if close + additions - deletions + reinstatements != enrolment:
            raise ValueError(f"Enrolment components do not reconcile at {locator}")
    elif prefix == "HouseInformalByDivisionDownload":
        formal = _integer(row.get("FormalVotes"), "FormalVotes", locator)
        informal = _integer(row.get("InformalVotes"), "InformalVotes", locator)
        total = _integer(row.get("TotalVotes"), "TotalVotes", locator)
        percent = _decimal(row.get("InformalPercent"), "InformalPercent", locator)
        if formal + informal != total:
            raise ValueError(f"FormalVotes plus InformalVotes does not equal TotalVotes at {locator}")
        if not _percentage_matches(informal, total, percent):
            raise ValueError(f"InformalPercent does not reconcile to InformalVotes at {locator}")
    elif prefix == "HouseTurnoutByDivisionDownload":
        enrolment = _integer(row.get("Enrolment"), "Enrolment", locator)
        turnout = _integer(row.get("Turnout"), "Turnout", locator)
        percent = _decimal(row.get("TurnoutPercentage"), "TurnoutPercentage", locator)
        if turnout > enrolment:
            raise ValueError(f"Turnout exceeds enrolment at {locator}")
        if not _percentage_matches(turnout, enrolment, percent):
            raise ValueError(f"TurnoutPercentage does not reconcile to Turnout at {locator}")
    elif prefix == "HouseVotesCountedByDivisionDownload":
        components = sum(
            _integer(row.get(field), field, locator)
            for field in ("OrdinaryVotes", "AbsentVotes", "ProvisionalVotes", "PrePollVotes", "PostalVotes")
        )
        total = _integer(row.get("TotalVotes"), "TotalVotes", locator)
        enrolment = _integer(row.get("Enrolment"), "Enrolment", locator)
        percent = _decimal(row.get("TotalPercentage"), "TotalPercentage", locator)
        if components != total:
            raise ValueError(f"Vote-type components do not equal TotalVotes at {locator}")
        if not _percentage_matches(total, enrolment, percent):
            raise ValueError(f"TotalPercentage does not reconcile to TotalVotes at {locator}")


def transform_house_participation(context: TransformContext) -> TransformResult:
    dataset_key = context.dataset["detection"]["selection"]["dataset_key"]
    filename = Path(context.dataset["virtual_name"]).name
    match = re.fullmatch(
        r"(?P<prefix>GeneralEnrolmentByDivisionDownload|HouseInformalByDivisionDownload|HouseTurnoutByDivisionDownload|HouseVotesCountedByDivisionDownload)-(?P<event>\d+)\.csv",
        filename,
        flags=re.IGNORECASE,
    )
    if match is None:
        raise ValueError(f"The AEC source filename is not valid for a House participation transformer: {filename}")
    prefix = next(
        key for key in PARTICIPATION_CONFIG
        if key.casefold() == match.group("prefix").casefold()
    )
    expected_key = "enrolment_division" if prefix == "GeneralEnrolmentByDivisionDownload" else "house_participation"
    if dataset_key != expected_key:
        raise ValueError("The detected dataset key does not match the participation source filename")
    _event_id(
        context,
        rf"{re.escape(prefix)}-(?P<event>\d+)\.csv",
    )
    rows = _source_rows(context, dataset_key)
    if not rows:
        raise ValueError("The staged dataset contains no source rows")
    contests, _ = _house_indexes(context)
    seen: set[str] = set()
    facts: list[tuple] = []
    lineage: list[tuple] = []
    for locator, row_hash, row in rows:
        division = str(row.get("DivisionID") or "").strip()
        if division in seen:
            raise ValueError(f"Duplicate DivisionID row in the source: {division}")
        seen.add(division)
        contest_id = contests.get(division)
        if contest_id is None:
            raise ValueError(f"Official DivisionID does not resolve in the selected election: {division} at {locator}")
        _validate_participation_row(prefix, row, locator)
        for field, vote_type, measure_type, decimal_measure in PARTICIPATION_CONFIG[prefix]["fields"]:
            value = row.get(field)
            if str(value or "").strip() == "":
                raise ValueError(f"{field} is blank at {locator}")
            integer_value = None if decimal_measure else _integer(value, field, locator)
            decimal_value = _decimal(value, field, locator) if decimal_measure else None
            natural = (
                context.job["election_id"], contest_id, None, vote_type, measure_type,
            )
            identifier = fact_id("participation_result", natural, context.source_revision_id)
            source_locator = f"{locator};field:{field}"
            facts.append(
                (
                    identifier, context.job["election_id"], contest_id, None,
                    vote_type, measure_type, integer_value, decimal_value, "reported",
                    "official_calculated" if decimal_measure else "official_reported",
                    _publication_phase(context), context.source_revision_id, source_locator,
                    context.import_run_id, "active",
                )
            )
            lineage_id = fact_id(
                "row_lineage", ["results", "participation_result", str(identifier), source_locator],
                context.source_revision_id,
            )
            lineage.append(
                (
                    lineage_id, "results", "participation_result", str(identifier),
                    context.source_revision_id, source_locator, context.import_run_id,
                    context.transform_run_id, row_hash,
                )
            )
    _complete_contests(contests, seen, PARTICIPATION_CONFIG[prefix]["label"])
    _, prior = _revision_family(context)
    superseded = _replace_participation(context, facts, lineage, prior)
    return TransformResult(
        inserted_rows=len(facts),
        notes=(
            f"Inserted {len(facts):,} active {PARTICIPATION_CONFIG[prefix]['label']} observations; "
            f"superseded {superseded:,} observations from prior revisions."
        ),
    )


def transform_house_elected(context: TransformContext) -> TransformResult:
    dataset_key = "house_elected"
    _event_id(context, r"HouseMembersElectedDownload-(?P<event>\d+)\.csv")
    rows = _source_rows(context, dataset_key)
    if not rows:
        raise ValueError("The staged dataset contains no source rows")
    contests, candidates = _house_indexes(context)
    seen: set[str] = set()
    outcomes: list[tuple] = []
    members: list[tuple] = []
    lineage: list[tuple] = []
    for locator, row_hash, row in rows:
        division = str(row.get("DivisionID") or "").strip()
        candidate = str(row.get("CandidateID") or "").strip()
        if division in seen:
            raise ValueError(f"Duplicate elected-member DivisionID row in the source: {division}")
        seen.add(division)
        governed = candidates.get((division, candidate))
        if governed is None:
            raise ValueError(
                f"Official elected candidate key does not resolve in the selected election: "
                f"{division}/{candidate} at {locator}"
            )
        contest_id, candidacy_id = governed
        outcome_id = fact_id(
            "contest_outcome",
            [contest_id, candidacy_id, "elected"],
            context.source_revision_id,
        )
        outcomes.append(
            (
                outcome_id, contest_id, candidacy_id, "elected", 1, None,
                _publication_phase(context), context.source_revision_id, locator, "active",
            )
        )
        person = context.connection.execute(
            "SELECT person_id FROM core.candidacy WHERE candidacy_id=?",
            [candidacy_id],
        ).fetchone()
        member_id = fact_id("elected_member", [outcome_id], context.source_revision_id)
        members.append(
            (
                member_id, outcome_id, context.job["election_id"], contest_id,
                candidacy_id, person[0] if person else None, 1, "pending",
            )
        )
        for table, identifier in (("contest_outcome", outcome_id), ("elected_member", member_id)):
            lineage_id = fact_id(
                "row_lineage", ["results", table, str(identifier), locator],
                context.source_revision_id,
            )
            lineage.append(
                (
                    lineage_id, "results", table, str(identifier), context.source_revision_id,
                    locator, context.import_run_id, context.transform_run_id, row_hash,
                )
            )
    _complete_contests(contests, seen, "House elected-member")
    _, prior = _revision_family(context)
    new_contests = {row[1] for row in outcomes}
    conflicts = context.connection.execute(
        """SELECT contest_id, source_revision_id FROM results.contest_outcome
           WHERE outcome_type='elected' AND record_status='active'"""
    ).fetchall()
    conflicts = [
        row for row in conflicts
        if row[0] in new_contests
        and row[1] not in prior
        and row[1] != context.source_revision_id
    ]
    if conflicts:
        raise ValueError("Active elected outcomes already exist from a different logical source")
    superseded = 0
    if prior:
        placeholders = ",".join("?" for _ in prior)
        context.connection.execute(
            f"""DELETE FROM provenance.row_lineage
                 WHERE target_schema='results' AND target_table='elected_member'
                   AND source_revision_id IN ({placeholders})""",
            sorted(prior),
        )
        context.connection.execute(
            f"""DELETE FROM results.elected_member
                 WHERE contest_outcome_id IN (
                   SELECT contest_outcome_id FROM results.contest_outcome
                   WHERE source_revision_id IN ({placeholders})
                 )""",
            sorted(prior),
        )
        superseded = len(
            context.connection.execute(
                f"""UPDATE results.contest_outcome SET record_status='superseded'
                     WHERE record_status='active' AND source_revision_id IN ({placeholders})
                     RETURNING contest_outcome_id""",
                sorted(prior),
            ).fetchall()
        )
    context.connection.executemany(
        "INSERT INTO results.contest_outcome VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        outcomes,
    )
    context.connection.executemany(
        "INSERT INTO results.elected_member VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        members,
    )
    context.connection.executemany(
        "INSERT INTO provenance.row_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        lineage,
    )
    return TransformResult(
        inserted_rows=len(outcomes) + len(members),
        notes=(
            f"Inserted {len(outcomes):,} active House elected outcomes and "
            f"{len(members):,} current elected-member records; superseded "
            f"{superseded:,} prior outcomes."
        ),
    )


register_transformer(ADAPTER_ID, "house_tcp_by_vote_type", TRANSFORM_VERSION, transform_house_tcp_by_vote_type)
register_transformer(ADAPTER_ID, "house_tpp_division", TRANSFORM_VERSION, transform_house_tpp_division)
register_transformer(ADAPTER_ID, "enrolment_division", TRANSFORM_VERSION, transform_house_participation)
register_transformer(ADAPTER_ID, "house_participation", TRANSFORM_VERSION, transform_house_participation)
register_transformer(ADAPTER_ID, "house_elected", TRANSFORM_VERSION, transform_house_elected)
