from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..ids import fact_id
from .transformers import TransformContext, TransformResult, register_transformer


ADAPTER_ID = "adapter_aec_2025_v1"
DATASET_KEY = "house_first_preferences_by_vote_type"
TRANSFORM_VERSION = "1.0.0"

VOTE_FIELDS = {
    "OrdinaryVotes": "ordinary",
    "AbsentVotes": "absent",
    "ProvisionalVotes": "provisional",
    "PrePollVotes": "early",
    "PostalVotes": "postal",
    "TotalVotes": "total",
}


def _integer(value: object, field: str, locator: str) -> int:
    text = str(value or "").strip().replace(",", "")
    if not text:
        raise ValueError(f"{field} is blank at {locator}")
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not numeric at {locator}: {value!r}") from exc
    if parsed != parsed.to_integral_value() or parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer at {locator}: {value!r}")
    return int(parsed)


def _decimal(value: object, field: str, locator: str) -> Decimal:
    text = str(value or "").strip().replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not numeric at {locator}: {value!r}") from exc


def _source_rows(
    context: TransformContext,
    dataset_key: str = DATASET_KEY,
) -> list[tuple[str, str | None, dict]]:
    prefix = context.dataset["virtual_name"]
    if context.dataset.get("sheet"):
        prefix += f"!{context.dataset['sheet']}"
    prefix += "#row="
    rows = context.connection.execute(
        """SELECT source_locator, source_row_hash, source_native_json
           FROM staging.source_record
           WHERE import_run_id=? AND source_revision_id=? AND dataset_key=?
             AND starts_with(source_locator, ?)
           ORDER BY source_row_number""",
        [
            context.import_run_id,
            context.source_revision_id,
            dataset_key,
            prefix,
        ],
    ).fetchall()
    return [(locator, row_hash, json.loads(payload)) for locator, row_hash, payload in rows]


def _candidate_index(context: TransformContext) -> dict[tuple[str, str], tuple[str, object]]:
    election_id = context.job.get("election_id")
    if not election_id:
        raise ValueError("An existing election must be selected for this result transformer")
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
    event_match = re.fullmatch(
        r"HouseFirstPrefsByCandidateByVoteTypeDownload-(\d+)\.csv",
        filename,
        flags=re.IGNORECASE,
    )
    if event_match is None:
        raise ValueError("The AEC source filename does not contain a valid numeric event ID")
    if str(election[1] or "") != event_match.group(1):
        raise ValueError(
            f"Source event {event_match.group(1)} does not match the selected election's "
            f"official event ID {election[1]!r}"
        )
    rows = context.connection.execute(
        """SELECT contest.official_contest_id, candidacy.official_candidate_id,
                  contest.contest_id, candidacy.candidacy_id
           FROM core.election_chamber chamber
           JOIN core.contest contest
             ON contest.election_chamber_id=chamber.election_chamber_id
           JOIN core.candidacy candidacy ON candidacy.contest_id=contest.contest_id
           WHERE chamber.election_id=? AND chamber.chamber_id='chamber_house'
             AND chamber.record_status='active' AND contest.record_status='active'
             AND candidacy.record_status='active'""",
        [election_id],
    ).fetchall()
    return {(str(division), str(candidate)): (contest_id, candidacy_id)
            for division, candidate, contest_id, candidacy_id in rows}


def _revision_family(context: TransformContext) -> tuple[str, set[str]]:
    row = context.connection.execute(
        """SELECT source_file_id FROM provenance.source_file_revision
           WHERE source_revision_id=?""",
        [context.source_revision_id],
    ).fetchone()
    if row is None:
        raise ValueError("The registered source revision is missing from provenance")
    source_file_id = row[0]
    prior = {
        item[0]
        for item in context.connection.execute(
            """SELECT source_revision_id FROM provenance.source_file_revision
               WHERE source_file_id=? AND source_revision_id<>?""",
            [source_file_id, context.source_revision_id],
        ).fetchall()
    }
    return source_file_id, prior


def transform_house_first_preferences(context: TransformContext) -> TransformResult:
    """Replace one complete AEC House first-preference vote-type revision.

    This route intentionally targets an election whose contests and candidacies
    already exist. Official DivisionID and CandidateID keys are resolved against
    those governed records; labels never create identities silently.
    """

    source_rows = _source_rows(context)
    if not source_rows:
        raise ValueError("The staged dataset contains no source rows")
    candidate_index = _candidate_index(context)
    election_id = context.job["election_id"]
    _, prior_revisions = _revision_family(context)

    facts: list[tuple] = []
    lineage: list[tuple] = []
    natural_keys: set[tuple] = set()
    unresolved: list[str] = []
    skipped_informal = 0
    seen_candidates: set[tuple[str, str]] = set()

    for locator, row_hash, row in source_rows:
        division_id = str(row.get("DivisionID") or "").strip()
        candidate_id = str(row.get("CandidateID") or "").strip()
        if candidate_id == "999" or str(row.get("Surname") or "").strip().casefold() == "informal":
            skipped_informal += 1
            continue
        source_candidate = (division_id, candidate_id)
        if source_candidate in seen_candidates:
            raise ValueError(
                f"Duplicate DivisionID/CandidateID row in the source: {division_id}/{candidate_id}"
            )
        seen_candidates.add(source_candidate)
        governed = candidate_index.get(source_candidate)
        if governed is None:
            unresolved.append(f"{division_id}/{candidate_id} at {locator}")
            continue
        contest_id, candidacy_id = governed

        component_total = sum(
            _integer(row.get(field), field, locator)
            for field in (
                "OrdinaryVotes",
                "AbsentVotes",
                "ProvisionalVotes",
                "PrePollVotes",
                "PostalVotes",
            )
        )
        reported_total = _integer(row.get("TotalVotes"), "TotalVotes", locator)
        if component_total != reported_total:
            raise ValueError(
                f"Vote-type components total {component_total:,}, but TotalVotes is "
                f"{reported_total:,} at {locator}"
            )

        measures: list[tuple[str, str, int | None, Decimal | None]] = [
            (field, vote_type, _integer(row.get(field), field, locator), None)
            for field, vote_type in VOTE_FIELDS.items()
        ]
        if str(row.get("Swing") or "").strip():
            measures.append(("Swing", "total", None, _decimal(row["Swing"], "Swing", locator)))

        for source_field, vote_type, integer_value, decimal_value in measures:
            measure_type = "votes" if integer_value is not None else "swing"
            natural = (
                election_id,
                contest_id,
                None,
                "candidacy",
                candidacy_id,
                "first_preference",
                vote_type,
                measure_type,
            )
            if natural in natural_keys:
                raise ValueError(f"Duplicate canonical result grain generated at {locator}")
            natural_keys.add(natural)
            result_id = fact_id("vote_result", natural, context.source_revision_id)
            source_locator = f"{locator};field:{source_field}"
            facts.append(
                (
                    result_id,
                    election_id,
                    contest_id,
                    None,
                    "candidacy",
                    candidacy_id,
                    None,
                    None,
                    None,
                    "first_preference",
                    vote_type,
                    measure_type,
                    integer_value,
                    decimal_value,
                    "reported",
                    "official_reported" if integer_value is not None else "official_calculated",
                    context.job.get("configuration", {}).get("publication_phase") or "final",
                    context.source_revision_id,
                    source_locator,
                    context.import_run_id,
                    "active",
                )
            )
            lineage_id = fact_id(
                "row_lineage",
                ["results", "vote_result", str(result_id), source_locator],
                context.source_revision_id,
            )
            lineage.append(
                (
                    lineage_id,
                    "results",
                    "vote_result",
                    str(result_id),
                    context.source_revision_id,
                    source_locator,
                    context.import_run_id,
                    context.transform_run_id,
                    row_hash,
                )
            )

    if unresolved:
        sample = ", ".join(unresolved[:8])
        suffix = "" if len(unresolved) <= 8 else f" (+{len(unresolved) - 8} more)"
        raise ValueError(
            "Official candidate keys do not resolve in the selected election: " + sample + suffix
        )
    missing_candidates = sorted(set(candidate_index) - seen_candidates)
    if missing_candidates:
        sample = ", ".join("/".join(item) for item in missing_candidates[:8])
        suffix = (
            ""
            if len(missing_candidates) <= 8
            else f" (+{len(missing_candidates) - 8} more)"
        )
        raise ValueError(
            "The source is not a complete House candidate result revision; governed "
            "DivisionID/CandidateID rows are missing: " + sample + suffix
        )
    if not facts:
        raise ValueError("No canonical candidate results were produced")

    existing_rows = context.connection.execute(
        """SELECT contest_id, candidacy_id, vote_type, measure_type, source_revision_id
           FROM results.vote_result
           WHERE election_id=? AND result_type='first_preference'
             AND subject_type='candidacy' AND election_reporting_unit_id IS NULL
             AND record_status='active'""",
        [election_id],
    ).fetchall()
    new_keys = {(item[2], item[5], item[10], item[11]) for item in facts}
    conflicts = [
        (contest, str(candidacy), vote_type, measure, revision)
        for contest, candidacy, vote_type, measure, revision in existing_rows
        if (contest, candidacy, vote_type, measure) in new_keys
        and revision not in prior_revisions
    ]
    if conflicts:
        sample = ", ".join("/".join(map(str, row[:4])) for row in conflicts[:5])
        raise ValueError(
            "Active results already occupy canonical grains from a different logical source: "
            + sample
        )

    superseded = 0
    if prior_revisions:
        placeholders = ",".join("?" for _ in prior_revisions)
        superseded = context.connection.execute(
            f"""UPDATE results.vote_result SET record_status='superseded'
                 WHERE record_status='active' AND source_revision_id IN ({placeholders})
                 RETURNING vote_result_id""",
            sorted(prior_revisions),
        ).fetchall()
        superseded = len(superseded)

    context.connection.executemany(
        "INSERT INTO results.vote_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        facts,
    )
    context.connection.executemany(
        "INSERT INTO provenance.row_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        lineage,
    )
    return TransformResult(
        inserted_rows=len(facts),
        notes=(
            f"Inserted {len(facts):,} active House first-preference observations; "
            f"superseded {superseded:,} observations from prior revisions; "
            f"ignored {skipped_informal:,} source informal-summary rows."
        ),
    )


register_transformer(
    ADAPTER_ID,
    DATASET_KEY,
    TRANSFORM_VERSION,
    transform_house_first_preferences,
)
