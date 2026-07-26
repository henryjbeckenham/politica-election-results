from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from ..ids import (
    candidacy_id,
    contest_id,
    deterministic_uuid,
    election_chamber_id,
    election_id,
    fact_id,
)
from .readers import iter_dataset_rows
from .references import ReferenceMatcher, normalise


MODE = "aec_election_bootstrap"
ADAPTER_ID = "adapter_aec_2025_v1"
HOUSE_DATASET = "house_candidates"
SENATE_DATASET = "senate_candidates"
SUPPORTED_DATASETS = {HOUSE_DATASET, SENATE_DATASET}
STATE_NAMES = {
    "ACT": "Australian Capital Territory",
    "NSW": "New South Wales",
    "NT": "Northern Territory",
    "QLD": "Queensland",
    "SA": "South Australia",
    "TAS": "Tasmania",
    "VIC": "Victoria",
    "WA": "Western Australia",
}
PUBLICATION_STATUSES = {"unpublished", "provisional", "final"}
CONTEST_STATUSES = {"scheduled", "nominations_closed", "counting", "declared"}
ELECTION_TYPES = {"general", "periodic", "by_election", "supplementary", "fresh"}

_FILENAME_PATTERNS = {
    HOUSE_DATASET: re.compile(r"^HouseCandidatesDownload-(\d+)\.csv$", re.IGNORECASE),
    SENATE_DATASET: re.compile(r"^SenateCandidatesDownload-(\d+)\.csv$", re.IGNORECASE),
}
_EVENT_PREAMBLE = re.compile(r"\[Event:(\d+)\b", re.IGNORECASE)


class AecBootstrapError(ValueError):
    """A safe, operator-facing rejection of a new-election bootstrap."""


def validate_configuration(configuration: dict) -> dict:
    event_id = str(configuration.get("official_event_id") or "").strip()
    if not re.fullmatch(r"\d+", event_id):
        raise AecBootstrapError("The AEC event number must contain digits only.")
    name = str(configuration.get("election_name") or "").strip()
    if not name:
        raise AecBootstrapError("Election name is required.")
    try:
        election_date = date.fromisoformat(str(configuration.get("election_date") or ""))
    except ValueError as exc:
        raise AecBootstrapError("Election date must be a valid YYYY-MM-DD date.") from exc
    election_type = str(configuration.get("election_type_code") or "general").strip()
    if election_type not in ELECTION_TYPES:
        raise AecBootstrapError(
            "Election type must be general, periodic, by-election, supplementary or fresh."
        )
    publication = str(configuration.get("publication_phase") or "final").strip()
    if publication not in PUBLICATION_STATUSES:
        raise AecBootstrapError("Publication status must be unpublished, provisional or final.")
    contest_status = str(configuration.get("contest_status") or "nominations_closed").strip()
    if contest_status not in CONTEST_STATUSES:
        raise AecBootstrapError(
            "Contest status must be scheduled, nominations closed, counting or declared."
        )
    try:
        state_vacancies = int(configuration.get("senate_state_vacancies", 6))
        territory_vacancies = int(configuration.get("senate_territory_vacancies", 2))
    except (TypeError, ValueError) as exc:
        raise AecBootstrapError("Senate vacancy values must be whole numbers.") from exc
    if not 1 <= state_vacancies <= 24 or not 1 <= territory_vacancies <= 12:
        raise AecBootstrapError("Senate vacancy values are outside the permitted safety range.")
    return {
        **configuration,
        "official_event_id": event_id,
        "election_name": name,
        "election_date": election_date.isoformat(),
        "election_type_code": election_type,
        "publication_phase": publication,
        "contest_status": contest_status,
        "senate_state_vacancies": state_vacancies,
        "senate_territory_vacancies": territory_vacancies,
        "senate_whole_chamber": bool(configuration.get("senate_whole_chamber", False)),
    }


def governed_election_id(configuration: dict) -> str:
    configured = validate_configuration(configuration)
    identifier = election_id(
        "fed", configured["election_date"], configured["election_type_code"]
    )
    # More than one by-election/supplementary/fresh poll can occur on the same
    # day. The official event number prevents those legitimate events from
    # colliding while retaining the established general-election ID shape.
    if configured["election_type_code"] in {"by_election", "supplementary", "fresh"}:
        identifier += f"_{configured['official_event_id']}"
    return identifier


def _selection(dataset: dict) -> dict:
    return dataset.get("detection", {}).get("selection") or {}


def _candidate_datasets(job: dict) -> dict[str, dict]:
    selected: dict[str, dict] = {}
    for dataset in job.get("datasets", []):
        selection = _selection(dataset)
        key = selection.get("dataset_key")
        if selection.get("adapter_id") != ADAPTER_ID or key not in SUPPORTED_DATASETS:
            raise AecBootstrapError(
                "A new AEC election may contain only one House Candidates file and/or one "
                "Senate Candidates file. Other result files must be ingested after registration."
            )
        if key in selected:
            raise AecBootstrapError(f"More than one {key.replace('_', ' ')} dataset was supplied.")
        if dataset.get("format") != "csv":
            raise AecBootstrapError("Stage 6 accepts the official AEC candidate CSV files only.")
        selected[key] = dataset
    if not selected:
        raise AecBootstrapError("Supply at least one official AEC candidate file.")
    return selected


def _upload(job: dict, dataset: dict) -> dict:
    return next(item for item in job["uploads"] if item["upload_id"] == dataset["upload_id"])


def _container(job_directory: Path, job: dict, dataset: dict) -> Path:
    return job_directory / "uploads" / _upload(job, dataset)["stored_name"]


def _first_csv_line(job_directory: Path, job: dict, dataset: dict) -> str:
    container = _container(job_directory, job, dataset)
    member = dataset.get("member")
    if member:
        with zipfile.ZipFile(container) as archive:
            raw = archive.read(member).splitlines()[0] if archive.getinfo(member).file_size else b""
    else:
        with container.open("rb") as handle:
            raw = handle.readline()
    try:
        return raw.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        return raw.decode("cp1252").strip()


def _event_number(job_directory: Path, job: dict, dataset: dict, key: str) -> str:
    filename = Path(dataset["virtual_name"]).name
    match = _FILENAME_PATTERNS[key].fullmatch(filename)
    if not match:
        expected = (
            "HouseCandidatesDownload-EVENT.csv"
            if key == HOUSE_DATASET
            else "SenateCandidatesDownload-EVENT.csv"
        )
        raise AecBootstrapError(f"{filename!r} is not an official candidate filename; expected {expected}.")
    filename_event = match.group(1)
    preamble = _first_csv_line(job_directory, job, dataset)
    preamble_match = _EVENT_PREAMBLE.search(preamble)
    if not preamble_match:
        raise AecBootstrapError(f"{filename!r} has no AEC [Event:…] source preamble.")
    if preamble_match.group(1) != filename_event:
        raise AecBootstrapError(
            f"{filename!r} names event {filename_event}, but its source preamble names "
            f"event {preamble_match.group(1)}."
        )
    return filename_event


def _clean(value: object) -> str:
    return str(value or "").strip()


def _yes(value: object) -> bool:
    return _clean(value).casefold() in {"y", "yes", "true", "1"}


def _lookup(matcher: ReferenceMatcher, entity_type: str, *values: object) -> tuple[str | None, str]:
    candidates: set[str] = set()
    supplied = False
    for value in values:
        token = normalise(value)
        if not token:
            continue
        supplied = True
        candidates.update(matcher.lookups[entity_type].get(token, set()))
    if len(candidates) == 1:
        return next(iter(candidates)), "matched"
    if len(candidates) > 1:
        return None, "conflict"
    return (None, "unmatched") if supplied else (None, "not_applicable")


def _source_locator(dataset: dict, row_number: int) -> str:
    locator = dataset["virtual_name"]
    if dataset.get("sheet"):
        locator += f"!{dataset['sheet']}"
    return f"{locator}#row={row_number}"


def _row_hash(row: dict) -> str:
    native_json = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(native_json.encode("utf-8")).hexdigest()


def reference_snapshot(connection: duckdb.DuckDBPyConnection) -> dict:
    definitions = {
        "people": ("sync.person", "person_id"),
        "parties": ("sync.party", "party_id"),
        "constituencies": ("sync.constituency", "constituency_id"),
    }
    snapshot = {}
    for label, (table, key) in definitions.items():
        rows = connection.execute(f"SELECT * FROM {table} ORDER BY {key}").fetchall()
        payload = json.dumps(rows, default=str, ensure_ascii=False, separators=(",", ":"))
        snapshot[label] = {
            "row_count": len(rows),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }
    return snapshot


def _load_and_validate_rows(
    job_directory: Path, job: dict, matcher: ReferenceMatcher
) -> tuple[dict[str, list[dict]], dict]:
    configuration = validate_configuration(job["configuration"])
    datasets = _candidate_datasets(job)
    expected_event = configuration["official_event_id"]
    rows_by_kind: dict[str, list[dict]] = {}
    candidate_locations: dict[str, tuple[str, str]] = {}
    division_identity: dict[str, tuple[str, str]] = {}
    matches = defaultdict(int)
    matched_ids: dict[str, set[str]] = defaultdict(set)

    for key, dataset in datasets.items():
        observed_event = _event_number(job_directory, job, dataset, key)
        if observed_event != expected_event:
            raise AecBootstrapError(
                f"{Path(dataset['virtual_name']).name} belongs to AEC event {observed_event}, "
                f"not the configured event {expected_event}."
            )
        source_rows: list[dict] = []
        for row_number, row in iter_dataset_rows(_container(job_directory, job, dataset), dataset):
            state = _clean(row.get("StateAb")).upper()
            candidate_key = _clean(row.get("CandidateID"))
            given = _clean(row.get("GivenNm"))
            family = _clean(row.get("Surname"))
            if state not in STATE_NAMES:
                raise AecBootstrapError(
                    f"{dataset['virtual_name']} row {row_number} has invalid StateAb {state!r}."
                )
            if not candidate_key or not (given or family):
                raise AecBootstrapError(
                    f"{dataset['virtual_name']} row {row_number} lacks a candidate ID or name."
                )
            if key == HOUSE_DATASET:
                contest_code = _clean(row.get("DivisionID"))
                contest_name = _clean(row.get("DivisionNm"))
                if not contest_code.isdigit() or not contest_name:
                    raise AecBootstrapError(
                        f"{dataset['virtual_name']} row {row_number} has an invalid division ID or name."
                    )
                identity = (contest_name, state)
                prior = division_identity.setdefault(contest_code, identity)
                if prior != identity:
                    raise AecBootstrapError(
                        f"Division {contest_code} has inconsistent name or state values in the source."
                    )
            else:
                contest_code = state
                contest_name = STATE_NAMES[state]
            prior_location = candidate_locations.setdefault(candidate_key, (key, contest_code))
            if prior_location != (key, contest_code):
                raise AecBootstrapError(
                    f"CandidateID {candidate_key} appears in more than one contest."
                )
            if any(item["candidate_key"] == candidate_key for item in source_rows):
                raise AecBootstrapError(
                    f"CandidateID {candidate_key} is duplicated in {dataset['virtual_name']}."
                )

            person_id, person_status = _lookup(matcher, "person", f"{given} {family}".strip())
            party_id, party_status = _lookup(
                matcher, "party", row.get("PartyNm"), row.get("PartyAb")
            )
            if key == HOUSE_DATASET:
                constituency_id, constituency_status = _lookup(
                    matcher, "constituency", contest_code
                )
            else:
                constituency_id, constituency_status = None, "not_applicable"
            for entity, identifier, status in (
                ("people", person_id, person_status),
                ("parties", party_id, party_status),
                ("constituencies", constituency_id, constituency_status),
            ):
                matches[f"{entity}_{status}"] += 1
                if identifier:
                    matched_ids[entity].add(identifier)
            source_rows.append(
                {
                    "row_number": row_number,
                    "row": row,
                    "locator": _source_locator(dataset, row_number),
                    "row_hash": _row_hash(row),
                    "state": state,
                    "contest_code": contest_code,
                    "contest_name": contest_name,
                    "candidate_key": candidate_key,
                    "given": given,
                    "family": family,
                    "person_id": person_id,
                    "person_status": person_status,
                    "party_id": party_id,
                    "party_status": party_status,
                    "constituency_id": constituency_id,
                    "constituency_status": constituency_status,
                }
            )
        if not source_rows:
            raise AecBootstrapError(f"{dataset['virtual_name']} contains no candidate rows.")
        rows_by_kind[key] = source_rows

    house_contests = len({item["contest_code"] for item in rows_by_kind.get(HOUSE_DATASET, [])})
    senate_contests = len({item["contest_code"] for item in rows_by_kind.get(SENATE_DATASET, [])})
    preview = {
        "status": "PASS",
        "official_event_id": expected_event,
        "election_id": governed_election_id(configuration),
        "election_name": configuration["election_name"],
        "election_date": configuration["election_date"],
        "election_type_code": configuration["election_type_code"],
        "publication_status": configuration["publication_phase"],
        "contest_status": configuration["contest_status"],
        "chambers": [
            chamber
            for chamber, key in (("House", HOUSE_DATASET), ("Senate", SENATE_DATASET))
            if key in rows_by_kind
        ],
        "house_contests": house_contests,
        "senate_contests": senate_contests,
        "house_candidates": len(rows_by_kind.get(HOUSE_DATASET, [])),
        "senate_candidates": len(rows_by_kind.get(SENATE_DATASET, [])),
        "total_contests": house_contests + senate_contests,
        "total_candidates": sum(len(rows) for rows in rows_by_kind.values()),
        "reference_matches": dict(sorted(matches.items())),
        "unique_reference_matches": {
            key: len(values) for key, values in sorted(matched_ids.items())
        },
        "warnings": [
            "Unmatched People, Parties or Constituencies remain explicitly unmatched; "
            "Stage 6 never creates or edits Grand Database records."
        ],
    }
    return rows_by_kind, preview


def inspect_aec_bootstrap(
    database: Path,
    job_directory: Path,
    job: dict,
) -> dict:
    configuration = validate_configuration(job["configuration"])
    identifier = governed_election_id(configuration)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        duplicate_event = connection.execute(
            "SELECT election_id, election_name FROM core.election WHERE authority_id='authority_aec' AND official_event_id=?",
            [configuration["official_event_id"]],
        ).fetchone()
        if duplicate_event:
            raise AecBootstrapError(
                f"AEC event {configuration['official_event_id']} is already registered as "
                f"{duplicate_event[1]} ({duplicate_event[0]})."
            )
        duplicate_identifier = connection.execute(
            "SELECT official_event_id, election_name FROM core.election WHERE election_id=?",
            [identifier],
        ).fetchone()
        if duplicate_identifier:
            raise AecBootstrapError(
                f"The governed election ID {identifier} is already used by {duplicate_identifier[1]}."
            )
        election_type_exists = connection.execute(
            "SELECT count(*) FROM control.election_type WHERE election_type_id=? AND active",
            [f"election_type_{configuration['election_type_code']}"],
        ).fetchone()[0]
        if election_type_exists != 1:
            raise AecBootstrapError("The selected election type is not active in this database.")
        matcher = ReferenceMatcher(connection, "authority_aec")
        _, preview = _load_and_validate_rows(job_directory, job, matcher)
        preview["reference_snapshot"] = reference_snapshot(connection)
        preview["reference_counts"] = {
            key: value["row_count"]
            for key, value in preview["reference_snapshot"].items()
        }
        return preview
    finally:
        connection.close()


def _lineage_row(
    *,
    target_schema: str,
    target_table: str,
    target_id: object,
    source_revision_id: str,
    locator: str,
    import_run_id: str,
    transform_run_id: str,
    row_hash: str,
) -> tuple:
    identifier = fact_id(
        "row_lineage",
        [target_schema, target_table, str(target_id), locator],
        source_revision_id,
    )
    return (
        identifier,
        target_schema,
        target_table,
        str(target_id),
        source_revision_id,
        locator,
        import_run_id,
        transform_run_id,
        row_hash,
    )


def bootstrap_aec_election(
    connection: duckdb.DuckDBPyConnection,
    *,
    job_directory: Path,
    job: dict,
    import_run_id: str,
    source_revision_by_upload: dict[str, str],
    transform_run_by_dataset: dict[str, str],
) -> dict:
    """Create one new AEC election atomically in an isolated working database."""

    configuration = validate_configuration(job["configuration"])
    identifier = governed_election_id(configuration)
    matcher = ReferenceMatcher(connection, "authority_aec")
    rows_by_kind, preview = _load_and_validate_rows(job_directory, job, matcher)
    datasets = _candidate_datasets(job)
    now = datetime.now(timezone.utc)
    reference_snapshot_before = reference_snapshot(connection)
    reference_before = {
        key: value["row_count"] for key, value in reference_snapshot_before.items()
    }
    if connection.execute(
        "SELECT count(*) FROM core.election WHERE election_id=? OR (authority_id='authority_aec' AND official_event_id=?)",
        [identifier, configuration["official_event_id"]],
    ).fetchone()[0]:
        raise AecBootstrapError("The election was registered after this preview; start a new job.")

    dataset_counts: dict[str, int] = {dataset["dataset_id"]: 0 for dataset in datasets.values()}
    lineage: list[tuple] = []
    event = configuration["official_event_id"]
    publication = configuration["publication_phase"]
    contest_status = configuration["contest_status"]
    election_date_value = configuration["election_date"]
    election_type_id = f"election_type_{configuration['election_type_code']}"
    first_key = HOUSE_DATASET if HOUSE_DATASET in datasets else SENATE_DATASET
    first_dataset = datasets[first_key]
    first_row = rows_by_kind[first_key][0]
    first_revision = source_revision_by_upload[first_dataset["upload_id"]]
    first_transform = transform_run_by_dataset[first_dataset["dataset_id"]]

    connection.execute("BEGIN TRANSACTION")
    try:
        if connection.execute(
            "SELECT count(*) FROM control.election_type WHERE election_type_id=? AND active",
            [election_type_id],
        ).fetchone()[0] != 1:
            raise AecBootstrapError("The selected election type is unavailable.")

        connection.execute(
            """INSERT INTO core.election VALUES
               (?, ?, ?, ?, ?, 'jurisdiction_aus_federal', 'authority_aec', ?, ?, ?,
                NULL, 'active', ?, ?)""",
            [
                identifier,
                event,
                configuration["election_name"],
                election_date_value,
                int(election_date_value[:4]),
                election_type_id,
                publication,
                contest_status,
                now,
                now,
            ],
        )
        dataset_counts[first_dataset["dataset_id"]] += 1
        lineage.append(
            _lineage_row(
                target_schema="core",
                target_table="election",
                target_id=identifier,
                source_revision_id=first_revision,
                locator=first_row["locator"],
                import_run_id=import_run_id,
                transform_run_id=first_transform,
                row_hash=first_row["row_hash"],
            )
        )
        key_date_id = f"election_key_date_{identifier.removeprefix('election_')}_polling_day"
        connection.execute(
            "INSERT INTO core.election_key_date VALUES (?, ?, 'polling_day', ?, 'official', ?)",
            [key_date_id, identifier, election_date_value, first_revision],
        )
        dataset_counts[first_dataset["dataset_id"]] += 1
        lineage.append(
            _lineage_row(
                target_schema="core",
                target_table="election_key_date",
                target_id=key_date_id,
                source_revision_id=first_revision,
                locator=first_row["locator"],
                import_run_id=import_run_id,
                transform_run_id=first_transform,
                row_hash=first_row["row_hash"],
            )
        )

        for key, chamber_code, chamber_id, system_code, system_name in (
            (HOUSE_DATASET, "house", "chamber_house", "irv", "Instant-runoff voting"),
            (SENATE_DATASET, "senate", "chamber_senate", "stv", "Single transferable vote"),
        ):
            if key not in datasets:
                continue
            dataset = datasets[key]
            rows = rows_by_kind[key]
            revision = source_revision_by_upload[dataset["upload_id"]]
            transform_run = transform_run_by_dataset[dataset["dataset_id"]]
            system_id = f"electoral_system_federal_{chamber_code}_{system_code}_{event}"
            connection.execute(
                """INSERT INTO control.electoral_system_version VALUES
                   (?, ?, ?, ?, 'jurisdiction_aus_federal', ?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    system_id,
                    system_code,
                    system_name,
                    f"AEC event {event}",
                    election_date_value,
                    1 if key == HOUSE_DATASET else None,
                    "full preferential" if key == HOUSE_DATASET else "optional preferential",
                    None if key == HOUSE_DATASET else "Droop quota",
                    "sequential exclusion and transfer" if key == HOUSE_DATASET else "inclusive Gregory method",
                    "AEC statutory rules",
                    None,
                    (
                        f"Operator-confirmed federal {system_code.upper()} system assumption "
                        f"for Stage 6 bootstrap of AEC event {event}; the candidate file "
                        "establishes the chamber and candidacies, not the statutory rules."
                    ),
                ],
            )
            dataset_counts[dataset["dataset_id"]] += 1

            grouped: dict[str, list[dict]] = defaultdict(list)
            for item in rows:
                grouped[item["contest_code"]].append(item)
            if key == HOUSE_DATASET:
                vacancies = len(grouped)
                whole_chamber = True
            else:
                vacancies = sum(
                    configuration["senate_territory_vacancies"]
                    if state in {"ACT", "NT"}
                    else configuration["senate_state_vacancies"]
                    for state in grouped
                )
                whole_chamber = configuration["senate_whole_chamber"]
            chamber_identifier = election_chamber_id(identifier, chamber_code)
            connection.execute(
                "INSERT INTO core.election_chamber VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
                [
                    chamber_identifier,
                    identifier,
                    chamber_id,
                    system_id,
                    vacancies,
                    whole_chamber,
                    publication,
                ],
            )
            dataset_counts[dataset["dataset_id"]] += 1
            lineage.append(
                _lineage_row(
                    target_schema="core",
                    target_table="election_chamber",
                    target_id=chamber_identifier,
                    source_revision_id=revision,
                    locator=rows[0]["locator"],
                    import_run_id=import_run_id,
                    transform_run_id=transform_run,
                    row_hash=rows[0]["row_hash"],
                )
            )

            for contest_code, contest_rows in sorted(grouped.items()):
                first = contest_rows[0]
                contest_name = first["contest_name"]
                contest_identifier = contest_id(
                    identifier, chamber_code, contest_code, contest_name
                )
                contest_vacancies = (
                    1
                    if key == HOUSE_DATASET
                    else configuration["senate_territory_vacancies"]
                    if contest_code in {"ACT", "NT"}
                    else configuration["senate_state_vacancies"]
                )
                connection.execute(
                    """INSERT INTO core.contest VALUES
                       (?, ?, ?, ?, ?, ?, ?, ?, FALSE, NULL, ?, 'active')""",
                    [
                        contest_identifier,
                        chamber_identifier,
                        first["constituency_id"],
                        contest_code,
                        contest_name,
                        contest_vacancies,
                        system_id,
                        contest_status,
                        publication,
                    ],
                )
                snapshot_id = f"snapshot_{contest_identifier.removeprefix('contest_')}"
                connection.execute(
                    """INSERT INTO core.contest_constituency_snapshot VALUES
                       (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)""",
                    [
                        snapshot_id,
                        contest_identifier,
                        first["constituency_id"],
                        contest_code,
                        contest_name,
                        "federal_lower_house_division"
                        if key == HOUSE_DATASET
                        else "federal_upper_house_state_contest",
                        revision,
                        first["locator"],
                        first["constituency_status"]
                        if key == HOUSE_DATASET
                        else "not_applicable",
                    ],
                )
                dataset_counts[dataset["dataset_id"]] += 2
                for target_table, target_id in (
                    ("contest", contest_identifier),
                    ("contest_constituency_snapshot", snapshot_id),
                ):
                    lineage.append(
                        _lineage_row(
                            target_schema="core",
                            target_table=target_table,
                            target_id=target_id,
                            source_revision_id=revision,
                            locator=first["locator"],
                            import_run_id=import_run_id,
                            transform_run_id=transform_run,
                            row_hash=first["row_hash"],
                        )
                    )
                for item in contest_rows:
                    row = item["row"]
                    candidate_identifier = candidacy_id(
                        contest_identifier, item["candidate_key"]
                    )
                    connection.execute(
                        """INSERT INTO core.candidacy VALUES
                           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'nominated', ?, ?, 'active')""",
                        [
                            candidate_identifier,
                            contest_identifier,
                            item["person_id"],
                            item["party_id"],
                            item["candidate_key"],
                            f"{item['given']} {item['family']}".strip(),
                            item["given"] or None,
                            item["family"] or None,
                            _clean(row.get("PartyNm")) or None,
                            _clean(row.get("PartyAb")) or None,
                            "incumbent" if _yes(row.get("HistoricElected")) else "not_incumbent",
                            item["person_status"],
                            publication,
                        ],
                    )
                    dataset_counts[dataset["dataset_id"]] += 1
                    lineage.append(
                        _lineage_row(
                            target_schema="core",
                            target_table="candidacy",
                            target_id=candidate_identifier,
                            source_revision_id=revision,
                            locator=item["locator"],
                            import_run_id=import_run_id,
                            transform_run_id=transform_run,
                            row_hash=item["row_hash"],
                        )
                    )

        if lineage:
            connection.executemany(
                "INSERT INTO provenance.row_lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                lineage,
            )
        for dataset in datasets.values():
            dataset_id = dataset["dataset_id"]
            output_count = dataset_counts[dataset_id]
            connection.execute(
                """UPDATE provenance.transform_run
                   SET completed_at=?, output_row_count=?, output_hash=?, transform_status='completed'
                   WHERE transform_run_id=?""",
                [
                    now,
                    output_count,
                    hashlib.sha256(
                        f"{dataset_id}:{output_count}:{identifier}".encode("utf-8")
                    ).hexdigest(),
                    transform_run_by_dataset[dataset_id],
                ],
            )
        reference_snapshot_after = reference_snapshot(connection)
        reference_after = {
            key: value["row_count"] for key, value in reference_snapshot_after.items()
        }
        if reference_snapshot_after != reference_snapshot_before:
            raise RuntimeError("The read-only Grand Database reference snapshot changed during bootstrap.")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    return {
        **preview,
        "status": "PASS",
        "inserted_rows": sum(dataset_counts.values()),
        "lineage_rows": len(lineage),
        "dataset_inserted_rows": dataset_counts,
        "reference_snapshot_before": reference_snapshot_before,
        "reference_snapshot_after": reference_snapshot_after,
        "reference_counts_before": reference_before,
        "reference_counts_after": reference_after,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
