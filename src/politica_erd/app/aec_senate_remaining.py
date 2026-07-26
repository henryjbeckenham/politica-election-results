from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import re

from ..ids import deterministic_uuid, fact_id
from .aec_house_summaries import (
    _event_id,
    _percentage_matches,
    _publication_phase,
    _replace_vote_results,
)
from .aec_individual import _decimal, _integer, _revision_family, _source_rows
from .aec_senate_summaries import STATES, _lineage, _senate_index
from .references import normalise
from .transformers import TransformContext, TransformResult, register_transformer


ADAPTER_ID = "adapter_aec_2025_v1"
TRANSFORM_VERSION = "1.0.0"

GROUP_VOTES = {
    "OrdinaryVotes": "ordinary",
    "AbsentVotes": "absent",
    "ProvisionalVotes": "provisional",
    "DeclarationPrePollVotes": "early",
    "PostalVotes": "postal",
    "TotalVotes": "total",
}


def _ensure_controlled_value(
    context: TransformContext,
    value_set_name: str,
    value_code: str,
    display_name: str,
    description: str,
) -> None:
    existing = context.connection.execute(
        """SELECT display_name, description, active
           FROM control.controlled_value
           WHERE value_set_name=? AND value_code=?""",
        [value_set_name, value_code],
    ).fetchone()
    if existing:
        if existing != (display_name, description, True):
            raise ValueError(
                f"Controlled value {value_set_name}.{value_code} conflicts with Stage 8"
            )
        return
    next_order = context.connection.execute(
        """SELECT coalesce(max(sort_order), 0) + 1
           FROM control.controlled_value WHERE value_set_name=?""",
        [value_set_name],
    ).fetchone()[0]
    context.connection.execute(
        """INSERT INTO control.controlled_value
           (value_set_name, value_code, display_name, description, sort_order,
            valid_from, valid_to, active)
           VALUES (?, ?, ?, ?, ?, NULL, NULL, TRUE)""",
        [value_set_name, value_code, display_name, description, next_order],
    )


def _ensure_stage8_group_values(context: TransformContext, *, national: bool) -> None:
    _ensure_controlled_value(
        context,
        "subject_type",
        "source_group",
        "Source Group",
        "Authority-defined aggregate group retained without an inferred canonical-party mapping",
    )
    if national:
        _ensure_controlled_value(
            context,
            "reporting_unit_type",
            "national_total",
            "National Total",
            "National aggregate total",
        )


def _ensure_national_senate_unit(context: TransformContext) -> tuple[object, int]:
    code = "senate:national"
    existing = context.connection.execute(
        """SELECT election_reporting_unit_id, official_label, reporting_unit_type
           FROM geography.election_reporting_unit
           WHERE election_id=? AND contest_id IS NULL
             AND official_reporting_unit_code=?""",
        [context.job["election_id"], code],
    ).fetchall()
    if len(existing) > 1:
        raise ValueError("Multiple governed national Senate reporting units already exist")
    if existing:
        if existing[0][1] != "Australia Senate total" or existing[0][2] != "national_total":
            raise ValueError("The governed national Senate reporting unit conflicts with Stage 8")
        return existing[0][0], 0
    identifier = deterministic_uuid(
        "election_reporting_unit", context.job["election_id"], code
    )
    context.connection.execute(
        """INSERT INTO geography.election_reporting_unit VALUES
           (?, ?, NULL, NULL, ?, 'Australia Senate total', 'national',
            'national_total', NULL, NULL, NULL, NULL, NULL, 'official', ?)""",
        [identifier, context.job["election_id"], code, context.source_revision_id],
    )
    return identifier, 1


def _group_fact(
    context: TransformContext,
    *,
    contest_id: str | None,
    unit_id: object | None,
    group_code: str,
    vote_type: str,
    measure_type: str,
    value: int | Decimal,
    locator: str,
    source_field: str,
    row_hash: str | None,
) -> tuple[tuple, tuple]:
    natural = (
        context.job["election_id"],
        contest_id,
        unit_id,
        "source_group",
        group_code,
        "group_total",
        vote_type,
        measure_type,
    )
    identifier = fact_id("vote_result", natural, context.source_revision_id)
    source_locator = f"{locator};field:{source_field}"
    fact = (
        identifier,
        context.job["election_id"],
        contest_id,
        unit_id,
        "source_group",
        None,
        None,
        None,
        group_code,
        "group_total",
        vote_type,
        measure_type,
        int(value) if measure_type == "votes" else None,
        value if measure_type == "vote_share" else None,
        "reported",
        "official_reported" if measure_type == "votes" else "official_calculated",
        _publication_phase(context),
        context.source_revision_id,
        source_locator,
        context.import_run_id,
        "active",
    )
    return fact, _lineage(
        context,
        "results",
        "vote_result",
        identifier,
        source_locator,
        row_hash,
    )


def _transform_group_preferences(
    context: TransformContext, *, state_scoped: bool
) -> TransformResult:
    _ensure_stage8_group_values(context, national=not state_scoped)
    stem = (
        "SenateFirstPrefsByStateByGroupByVoteTypeDownload"
        if state_scoped
        else "SenateFirstPrefsByGroupByVoteTypeDownload"
    )
    dataset_key = (
        "senate_group_preferences_state"
        if state_scoped
        else "senate_group_preferences_national"
    )
    _event_id(context, rf"{stem}-(?P<event>\d+)\.csv")
    rows = _source_rows(context, dataset_key)
    if not rows:
        raise ValueError("The staged Senate group file contains no source rows")
    contests, _ = _senate_index(context)

    records: list[dict] = []
    seen: set[tuple[str | None, str]] = set()
    scope_totals: dict[str | None, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for locator, row_hash, row in rows:
        state = str(row.get("StateAb") or "").strip().upper() if state_scoped else None
        group = str(row.get("GroupAb") or "").strip().upper()
        label = str(row.get("GroupNm") or "").strip()
        if state_scoped and state not in contests:
            raise ValueError(f"Unknown Senate StateAb {state!r} at {locator}")
        if not group or not label:
            raise ValueError(f"Blank Senate GroupAb or GroupNm at {locator}")
        grain = (state, group)
        if grain in seen:
            raise ValueError(f"Duplicate Senate group aggregate {grain} at {locator}")
        seen.add(grain)
        values = {
            field: _integer(row.get(field), field, locator) for field in GROUP_VOTES
        }
        if sum(values[field] for field in tuple(GROUP_VOTES)[:-1]) != values["TotalVotes"]:
            raise ValueError(f"Senate group vote-type components do not equal TotalVotes at {locator}")
        for field, value in values.items():
            scope_totals[state][field] += value
        records.append(
            {
                "state": state,
                "group": group,
                "label": label,
                "locator": locator,
                "row_hash": row_hash,
                "row": row,
                "values": values,
            }
        )

    if state_scoped and {record["state"] for record in records} != set(contests):
        raise ValueError("The state Senate group file does not cover every governed contest")

    for record in records:
        for votes_field in GROUP_VOTES:
            percentage_field = votes_field.replace("Votes", "Percentage")
            reported = _decimal(
                record["row"].get(percentage_field),
                percentage_field,
                record["locator"],
            )
            if not _percentage_matches(
                record["values"][votes_field],
                scope_totals[record["state"]][votes_field],
                reported,
            ):
                raise ValueError(
                    f"{percentage_field} does not reconcile within its Senate group scope at "
                    f"{record['locator']}"
                )

    national_unit: object | None = None
    structure_count = 0
    if not state_scoped:
        national_unit, structure_count = _ensure_national_senate_unit(context)
    facts: list[tuple] = []
    lineages: list[tuple] = []
    for record in records:
        contest_id = contests[record["state"]][0] if state_scoped else None
        for votes_field, vote_type in GROUP_VOTES.items():
            fact, lineage = _group_fact(
                context,
                contest_id=contest_id,
                unit_id=national_unit,
                group_code=record["group"],
                vote_type=vote_type,
                measure_type="votes",
                value=record["values"][votes_field],
                locator=record["locator"],
                source_field=votes_field,
                row_hash=record["row_hash"],
            )
            facts.append(fact)
            lineages.append(lineage)
            percentage_field = votes_field.replace("Votes", "Percentage")
            fact, lineage = _group_fact(
                context,
                contest_id=contest_id,
                unit_id=national_unit,
                group_code=record["group"],
                vote_type=vote_type,
                measure_type="vote_share",
                value=_decimal(
                    record["row"].get(percentage_field),
                    percentage_field,
                    record["locator"],
                ),
                locator=record["locator"],
                source_field=percentage_field,
                row_hash=record["row_hash"],
            )
            facts.append(fact)
            lineages.append(lineage)

    _, prior = _revision_family(context)
    superseded = _replace_vote_results(context, facts, lineages, prior)
    label = "state" if state_scoped else "national"
    return TransformResult(
        inserted_rows=len(facts) + structure_count,
        source_rows=len(rows),
        notes=(
            f"Inserted {len(facts):,} {label} Senate source-group facts and "
            f"{structure_count:,} reporting unit; superseded {superseded:,} prior facts."
        ),
    )


def transform_group_preferences_national(context: TransformContext) -> TransformResult:
    return _transform_group_preferences(context, state_scoped=False)


def transform_group_preferences_state(context: TransformContext) -> TransformResult:
    return _transform_group_preferences(context, state_scoped=True)


def _signed_integer(value: object, field: str, locator: str) -> int:
    parsed = _decimal(value, field, locator)
    if parsed != parsed.to_integral_value():
        raise ValueError(f"{field} must be an integer at {locator}: {value!r}")
    return int(parsed)


def _nonnegative_decimal(value: object, field: str, locator: str) -> Decimal:
    parsed = _decimal(value, field, locator)
    if parsed < 0:
        raise ValueError(f"{field} must be non-negative at {locator}: {value!r}")
    return parsed


def transform_senate_distribution(context: TransformContext) -> TransformResult:
    filename = Path(context.dataset["virtual_name"]).name
    match = re.fullmatch(
        r"SenateStateDOPDownload-(?P<event>\d+)-(?P<state>ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\.csv",
        filename,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"Invalid Senate DOP member filename: {filename}")
    _event_id(
        context,
        r"SenateStateDOPDownload-(?P<event>\d+)-(?:ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\.csv",
    )
    state = match.group("state").upper()
    rows = _source_rows(context, "senate_distribution")
    if not rows:
        raise ValueError("The staged Senate DOP member contains no source rows")
    contests, candidates = _senate_index(context)
    contest_id, vacancies = contests[state]

    name_index: dict[str, list[object]] = defaultdict(list)
    expected_candidates: set[object] = set()
    for (candidate_state, _), candidate in candidates.items():
        if candidate_state != state:
            continue
        expected_candidates.add(candidate["candidacy_id"])
        for label in (
            f"{candidate['given']} {candidate['family']}",
            f"{candidate['family']} {candidate['given']}",
            candidate["name"],
        ):
            key = normalise(label)
            if key and candidate["candidacy_id"] not in name_index[key]:
                name_index[key].append(candidate["candidacy_id"])

    by_count: dict[int, list[dict]] = defaultdict(list)
    seen_positions: set[tuple[int, int]] = set()
    invariants: set[tuple[int, int, Decimal]] = set()
    for locator, row_hash, row in rows:
        row_state = str(row.get("State") or "").strip().upper()
        if row_state != state:
            raise ValueError(f"Senate DOP state {row_state!r} conflicts with {state} at {locator}")
        count_number = _integer(row.get("Count"), "Count", locator)
        position = _integer(row.get("Ballot Position"), "Ballot Position", locator)
        if count_number < 1 or (count_number, position) in seen_positions:
            raise ValueError(f"Duplicate or invalid Senate DOP count/position at {locator}")
        seen_positions.add((count_number, position))
        source_vacancies = _integer(row.get("No Of Vacancies"), "No Of Vacancies", locator)
        formal_papers = _integer(row.get("Total Formal Papers"), "Total Formal Papers", locator)
        quota = _nonnegative_decimal(row.get("Quota"), "Quota", locator)
        invariants.add((source_vacancies, formal_papers, quota))
        candidate_matches = name_index.get(
            normalise(f"{row.get('GivenNm') or ''} {row.get('Surname') or ''}"), []
        )
        surname = str(row.get("Surname") or "").strip()
        pseudo = normalise(surname) in {"exhausted", "gain loss"}
        if len(candidate_matches) > 1 or (not pseudo and len(candidate_matches) != 1):
            raise ValueError(f"Senate DOP candidate does not resolve uniquely at {locator}")
        by_count[count_number].append(
            {
                "position": position,
                "candidate": candidate_matches[0] if candidate_matches else None,
                "pseudo": normalise(surname) if pseudo else None,
                "papers": _signed_integer(row.get("Papers"), "Papers", locator),
                "transferred": _decimal(row.get("VoteTransferred"), "VoteTransferred", locator),
                "progressive": _nonnegative_decimal(
                    row.get("ProgressiveVoteTotal"), "ProgressiveVoteTotal", locator
                ),
                "transfer_value": _nonnegative_decimal(
                    row.get("Transfer Value"), "Transfer Value", locator
                ),
                "status": str(row.get("Status") or "").strip().casefold() or "continuing",
                "comment": str(row.get("Comment") or "").strip(),
                "locator": locator,
                "row_hash": row_hash,
            }
        )

    if len(invariants) != 1:
        raise ValueError("Senate DOP vacancies, formal papers or quota change within one member")
    source_vacancies, formal_papers, _ = next(iter(invariants))
    if source_vacancies != vacancies:
        raise ValueError(
            f"Senate DOP reports {source_vacancies} vacancies for {state}; governed contest has {vacancies}"
        )
    expected_counts = set(range(1, max(by_count) + 1))
    if set(by_count) != expected_counts:
        raise ValueError("Senate DOP count numbers are not contiguous from 1")
    expected_positions = {item["position"] for item in by_count[1]}
    round_metadata: dict[int, dict] = {}
    for count_number, records in by_count.items():
        positions = {item["position"] for item in records}
        observed_candidates = {item["candidate"] for item in records if item["candidate"]}
        observed_pseudo = {item["pseudo"] for item in records if item["pseudo"]}
        if (
            positions != expected_positions
            or observed_candidates != expected_candidates
            or observed_pseudo != {"exhausted", "gain loss"}
        ):
            raise ValueError(f"Senate DOP count {count_number} has incomplete candidate rows")
        comments = {item["comment"] for item in records if item["comment"]}
        transfer_values = {
            item["transfer_value"]
            for item in records
            if item["candidate"] is not None and item["transfer_value"] != 0
        }
        if len(comments) > 1 or len(transfer_values) > 1:
            raise ValueError(
                f"Senate DOP count {count_number} has conflicting round metadata"
            )
        transfer_value = next(iter(transfer_values), Decimal(0))
        source = next(
            (
                item
                for item in records
                if item["candidate"] is not None
                and item["transfer_value"] == transfer_value
                and transfer_value != 0
            ),
            next(item for item in records if item["candidate"] is not None),
        )
        round_metadata[count_number] = {
            "comment": next(iter(comments), ""),
            "transfer_value": transfer_value,
            "source": source,
        }
    first_progressive = sum(
        item["progressive"] for item in by_count[1] if item["candidate"] is not None
    )
    if first_progressive != formal_papers:
        raise ValueError(
            f"Senate DOP first-count candidate totals {first_progressive} do not equal "
            f"{formal_papers} formal papers"
        )
    governed_formal = context.connection.execute(
        """SELECT coalesce(sum(integer_value), 0), count(*)
           FROM results.vote_result
           WHERE contest_id=? AND election_reporting_unit_id IS NULL
             AND result_type='first_preference' AND vote_type='total'
             AND measure_type='votes' AND record_status='active'""",
        [contest_id],
    ).fetchone()
    if int(governed_formal[1]) <= 0:
        raise ValueError(
            f"The {state} DOP requires governed state first-preference totals"
        )
    if int(governed_formal[0]) != formal_papers:
        raise ValueError(
            f"The {state} DOP reports {formal_papers:,} formal papers; governed state "
            f"first preferences report {int(governed_formal[0]):,}"
        )
    final_elected = {
        item["candidate"]
        for item in by_count[max(by_count)]
        if item["candidate"] is not None and item["status"] == "elected"
    }
    if len(final_elected) != vacancies:
        raise ValueError(
            f"Senate DOP final count identifies {len(final_elected)} elected candidates; "
            f"expected {vacancies}"
        )

    _, prior = _revision_family(context)
    conflicting = context.connection.execute(
        """SELECT DISTINCT round.source_revision_id
           FROM "count".count_round round
           JOIN provenance.source_file_revision revision
             ON revision.source_revision_id=round.source_revision_id
           WHERE round.contest_id=? AND revision.record_status='active'
             AND round.source_revision_id<>?""",
        [contest_id, context.source_revision_id],
    ).fetchall()
    if any(row[0] not in prior for row in conflicting):
        raise ValueError("An active Senate DOP source from another logical file already occupies this contest")

    round_rows: list[tuple] = []
    total_rows: list[tuple] = []
    transfer_rows: list[tuple] = []
    lineages: list[tuple] = []
    for count_number, records in sorted(by_count.items()):
        metadata = round_metadata[count_number]
        source = metadata["source"]
        comment = metadata["comment"]
        action_comment = (
            "" if count_number == 1 else round_metadata[count_number - 1]["comment"]
        ).casefold()
        if count_number == 1:
            action = "first_preferences"
        elif "surplus" in action_comment:
            action = "surplus_distribution"
        elif "exclud" in action_comment or "exclusion" in action_comment:
            excluded = re.search(r"exclusion of\s+(\d+)\s+candidate", action_comment)
            action = (
                "bulk_exclusion"
                if excluded and int(excluded.group(1)) > 1
                else "exclusion"
            )
        else:
            action = "transfer"
        round_id = fact_id(
            "count_round", [contest_id, count_number], context.source_revision_id
        )
        round_rows.append(
            (
                round_id,
                contest_id,
                count_number,
                f"Count {count_number}",
                action,
                next(iter(invariants))[2],
                metadata["transfer_value"],
                "AEC Senate distribution adjustments",
                json.dumps(
                    {
                        item["pseudo"].replace(" ", "_"): {
                            "papers": item["papers"],
                            "votes_transferred": str(item["transferred"]),
                            "progressive_total": str(item["progressive"]),
                            "transfer_value": str(item["transfer_value"]),
                        }
                        for item in records
                        if item["pseudo"] is not None
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                comment or None,
                _publication_phase(context),
                context.source_revision_id,
                source["locator"],
            )
        )
        lineages.append(
            _lineage(
                context,
                "count",
                "count_round",
                round_id,
                source["locator"],
                source["row_hash"],
            )
        )
        for record in records:
            candidate_id = record["candidate"]
            if candidate_id is None:
                if record["pseudo"] == "exhausted" and record["transferred"] != 0:
                    transfer_id = fact_id(
                        "preference_transfer",
                        [round_id, "exhausted", record["locator"]],
                        context.source_revision_id,
                    )
                    transfer_rows.append(
                        (
                            transfer_id,
                            round_id,
                            None,
                            None,
                            record["papers"] if record["papers"] >= 0 else None,
                            record["transferred"],
                            True,
                            "reported",
                            context.source_revision_id,
                            record["locator"],
                        )
                    )
                    lineages.append(
                        _lineage(
                            context,
                            "count",
                            "preference_transfer",
                            transfer_id,
                            record["locator"],
                            record["row_hash"],
                        )
                    )
                continue
            total_id = fact_id(
                "count_candidate_total",
                [round_id, candidate_id],
                context.source_revision_id,
            )
            total_rows.append(
                (
                    total_id,
                    round_id,
                    candidate_id,
                    record["papers"] if record["papers"] >= 0 else None,
                    record["transferred"],
                    record["progressive"],
                    record["status"],
                    "reported",
                    context.source_revision_id,
                    record["locator"],
                )
            )
            lineages.append(
                _lineage(
                    context,
                    "count",
                    "count_candidate_total",
                    total_id,
                    record["locator"],
                    record["row_hash"],
                )
            )
            if record["transferred"] != 0:
                transfer_id = fact_id(
                    "preference_transfer",
                    [round_id, candidate_id, record["locator"]],
                    context.source_revision_id,
                )
                transfer_rows.append(
                    (
                        transfer_id,
                        round_id,
                        None,
                        candidate_id,
                        record["papers"] if record["papers"] >= 0 else None,
                        record["transferred"],
                        False,
                        "reported",
                        context.source_revision_id,
                        record["locator"],
                    )
                )
                lineages.append(
                    _lineage(
                        context,
                        "count",
                        "preference_transfer",
                        transfer_id,
                        record["locator"],
                        record["row_hash"],
                    )
                )

    context.connection.executemany(
        'INSERT INTO "count".count_round VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        round_rows,
    )
    context.connection.executemany(
        'INSERT INTO "count".count_candidate_total VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        total_rows,
    )
    context.connection.executemany(
        'INSERT INTO "count".preference_transfer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        transfer_rows,
    )
    context.connection.executemany(
        "INSERT INTO provenance.row_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        lineages,
    )
    return TransformResult(
        inserted_rows=len(round_rows) + len(total_rows) + len(transfer_rows),
        source_rows=len(rows),
        notes=(
            f"Inserted {len(round_rows):,} {state} Senate count rounds, "
            f"{len(total_rows):,} candidate totals and {len(transfer_rows):,} transfers. "
            "Prior revisions remain immutable and are excluded by active source status."
        ),
    )


register_transformer(
    ADAPTER_ID,
    "senate_group_preferences_national",
    TRANSFORM_VERSION,
    transform_group_preferences_national,
)
register_transformer(
    ADAPTER_ID,
    "senate_group_preferences_state",
    TRANSFORM_VERSION,
    transform_group_preferences_state,
)
register_transformer(
    ADAPTER_ID,
    "senate_distribution",
    TRANSFORM_VERSION,
    transform_senate_distribution,
)
