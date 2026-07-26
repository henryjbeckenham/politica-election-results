from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from .db import bulk_insert
from .formal_preferences import _replace_ballot_views
from .ids import deterministic_uuid

if TYPE_CHECKING:
    from .import_2010 import ImportContext


BTL_PREFERENCE_SOURCES = {
    "ACT": "senate_btl_preferences_act",
    "NT": "senate_btl_preferences_nt",
    "TAS": "senate_btl_preferences_tas",
    "SA": "senate_btl_preferences_sa",
    "WA": "senate_btl_preferences_wa",
    "QLD": "senate_btl_preferences_qld",
    "VIC": "senate_btl_preferences_vic",
    "NSW": "senate_btl_preferences_nsw",
}


def _literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_member(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            name for name in archive.namelist() if name.lower().endswith(".csv")
        )
        if len(members) != 1:
            raise ValueError(
                f"Expected one below-the-line CSV in {path.name}; found {members}."
            )
        member = members[0]
        with archive.open(member) as raw:
            metadata = raw.readline().decode("utf-8-sig").strip()
            headers = next(
                csv.reader([raw.readline().decode("utf-8-sig").strip()])
            )
        if "Event:15508" not in metadata:
            raise ValueError(f"Unexpected 2010 BTL metadata in {path.name}.")
        if headers[:4] != ["CandidateId", "Preference", "Batch", "Paper"]:
            raise ValueError(f"Unexpected 2010 BTL columns in {path.name}: {headers}")
        return member


def _extract_member(archive_path: Path, member: str, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(member) as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def _official_non_ticket_votes(context: "ImportContext") -> dict[str, int]:
    values: dict[str, int] = {}
    for source_row in context.rows("senate_gvt_usage_state"):
        values[source_row.data["StateAb"]] = int(source_row.data["NonTicketVotes"])
    if set(values) != set(BTL_PREFERENCE_SOURCES):
        raise ValueError("The official 2010 non-ticket vote totals do not cover all states.")
    return values


def _candidate_map(context: "ImportContext", state: str) -> list[tuple]:
    rows = [
        (candidate_id, candidacy)
        for (chamber, candidate_state, candidate_id), candidacy in context.candidacies.items()
        if chamber == "senate" and candidate_state == state
    ]
    rows.sort(key=lambda row: int(row[0]))
    if not rows:
        raise ValueError(f"No governed Senate candidacies exist for {state}.")
    return rows


def _matrix_ballot_count(
    context: "ImportContext", state: str, source_key: str
) -> int:
    candidate_count = len(_candidate_map(context, state))
    source_rows = int(context.source_by_key[source_key]["row_count"])
    ballot_count, remainder = divmod(source_rows, candidate_count)
    if remainder:
        raise ValueError(
            f"The {state} BTL matrix contains {source_rows:,} rows, which is not "
            f"divisible by its {candidate_count} Senate candidates."
        )
    return ballot_count


def _raw_partitions(
    connection: duckdb.DuckDBPyConnection,
    csv_path: Path,
    raw_root: Path,
) -> list[tuple[int, list[Path]]]:
    """Scan the source matrix once into ballot-safe batch buckets."""

    escaped_csv = str(csv_path).replace("'", "''")
    escaped_root = str(raw_root).replace("'", "''")
    connection.execute(
        f"""
        COPY (
          WITH source AS (
            SELECT row_number() OVER ()::BIGINT + 2 AS source_row_number,
                   CandidateId::VARCHAR AS candidate_id,
                   Preference::VARCHAR AS source_marking,
                   Batch::VARCHAR AS batch_number,
                   Paper::VARCHAR AS paper_number
            FROM read_csv(
              '{escaped_csv}', skip=1, header=true, all_varchar=true,
              null_padding=true, strict_mode=false, parallel=false, sample_size=-1
            )
          )
          SELECT *, floor((try_cast(batch_number AS BIGINT) - 1) / 200)::INTEGER
                    AS raw_partition
          FROM source
          WHERE try_cast(batch_number AS BIGINT) > 0
            AND try_cast(paper_number AS BIGINT) > 0
          ORDER BY try_cast(batch_number AS BIGINT), try_cast(paper_number AS BIGINT),
                   source_row_number
        ) TO '{escaped_root}' (
          FORMAT PARQUET,
          PARTITION_BY (raw_partition),
          COMPRESSION ZSTD,
          ROW_GROUP_SIZE 100000
        )
        """
    )
    result: list[tuple[int, list[Path]]] = []
    for directory in sorted(
        raw_root.glob("raw_partition=*"),
        key=lambda path: int(path.name.split("=", 1)[1]),
    ):
        files = sorted(directory.glob("*.parquet"))
        if not files:
            raise ValueError(f"Empty raw BTL partition: {directory}")
        result.append((int(directory.name.split("=", 1)[1]), files))
    if not result:
        raise ValueError(f"No raw BTL partitions were produced from {csv_path}.")
    return result


def _write_partition(
    context: "ImportContext",
    state: str,
    source_key: str,
    member: str,
    raw_files: list[Path],
    partition_index: int,
    destination: Path,
) -> tuple[int, int]:
    connection = context.connection
    revision = context.revision_by_key[source_key]
    dataset_id = deterministic_uuid(
        "ballot_dataset", context.senate_contests[state], revision
    )
    file_list = ", ".join(_literal(str(path)) for path in raw_files)
    output_directory = destination / f"storage_partition={partition_index}"
    output_directory.mkdir(parents=True, exist_ok=True)
    output = output_directory / "part-0.parquet"
    writing = output.with_suffix(".parquet.writing")
    writing.unlink(missing_ok=True)
    escaped_output = str(writing).replace("'", "''")

    connection.execute(
        f"""
        COPY (
          WITH raw AS (
            SELECT * FROM read_parquet([{file_list}], hive_partitioning=false)
          ),
          joined AS (
            SELECT r.*, m.candidacy_id,
                   try_cast(trim(r.source_marking) AS INTEGER) AS preference_rank
            FROM raw r
            JOIN pre_reform_candidate_map m USING (candidate_id)
          ),
          ballot_rows AS (
            SELECT batch_number, paper_number,
                   min(source_row_number) AS source_row_number
            FROM joined
            GROUP BY batch_number, paper_number
          ),
          marks AS (
            SELECT * FROM joined
            WHERE preference_rank > 0 AND preference_rank < 999
          ),
          mark_counts AS (
            SELECT *, count(*) OVER (
              PARTITION BY batch_number, paper_number, preference_rank
            ) AS occurrence_count
            FROM marks
          ),
          unique_ranks AS (
            SELECT DISTINCT batch_number, paper_number, preference_rank
            FROM mark_counts
            WHERE occurrence_count = 1
          ),
          ordered_ranks AS (
            SELECT *, row_number() OVER (
              PARTITION BY batch_number, paper_number ORDER BY preference_rank
            ) AS rank_ordinal
            FROM unique_ranks
          ),
          sequence_lengths AS (
            SELECT batch_number, paper_number,
                   coalesce(
                     min(rank_ordinal - 1) FILTER (
                       WHERE preference_rank <> rank_ordinal
                     ),
                     count(*)
                   )::INTEGER AS sequence_length
            FROM ordered_ranks
            GROUP BY batch_number, paper_number
          ),
          paths AS (
            SELECT mc.batch_number, mc.paper_number,
                   first(br.source_row_number) AS source_row_number,
                   sl.sequence_length,
                   list(
                     struct_pack(
                       preference_rank := mc.preference_rank::INTEGER,
                       target_kind := 'candidacy'::VARCHAR,
                       target_id := mc.candidacy_id,
                       source_marking := mc.source_marking
                     ) ORDER BY mc.preference_rank
                   ) AS preferences
            FROM mark_counts mc
            JOIN sequence_lengths sl USING (batch_number, paper_number)
            JOIN ballot_rows br USING (batch_number, paper_number)
            WHERE mc.occurrence_count = 1
              AND mc.preference_rank <= sl.sequence_length
            GROUP BY mc.batch_number, mc.paper_number, sl.sequence_length
          )
          SELECT {_literal(dataset_id)}::UUID AS ballot_dataset_id,
                 {_literal(revision)}::VARCHAR AS source_revision_id,
                 {_literal(member)}::VARCHAR AS source_member,
                 source_row_number,
                 'member:' || {_literal(member)} || ';batch:' || batch_number ||
                   ';paper:' || paper_number AS source_row_locator,
                 {_literal(state)}::VARCHAR AS state_code,
                 NULL::VARCHAR AS division_name,
                 'Below the line'::VARCHAR AS collection_point_name,
                 'btl'::VARCHAR AS collection_point_id,
                 batch_number,
                 paper_number,
                 lower({_literal(state)}) || '|btl|' || batch_number || '|' ||
                   paper_number AS anonymous_source_key,
                 {_literal(context.senate_contests[state])}::VARCHAR AS contest_id,
                 'below_the_line'::VARCHAR AS ballot_type,
                 false AS above_the_line,
                 sequence_length AS preference_count,
                 preferences
          FROM paths
          WHERE sequence_length > 0
          ORDER BY try_cast(batch_number AS BIGINT), try_cast(paper_number AS BIGINT)
        ) TO '{escaped_output}' (
          FORMAT PARQUET,
          COMPRESSION ZSTD,
          ROW_GROUP_SIZE 100000
        )
        """
    )
    ballot_count, preference_count = connection.execute(
        "SELECT count(*), sum(preference_count) FROM read_parquet(?, hive_partitioning=false)",
        [str(writing)],
    ).fetchone()
    os.replace(writing, output)
    return int(ballot_count), int(preference_count or 0)


def _write_state(
    context: "ImportContext",
    state: str,
    source_key: str,
    archive_path: Path,
    member: str,
    destination: Path,
    expected_matrix_ballots: int,
) -> tuple[int, int]:
    connection = context.connection
    candidate_map = _candidate_map(context, state)
    source_rows = int(context.source_by_key[source_key]["row_count"])
    if source_rows != expected_matrix_ballots * len(candidate_map):
        raise ValueError(
            f"The {state} BTL matrix has {source_rows:,} source rows; expected "
            f"{expected_matrix_ballots:,} ballots x {len(candidate_map)} candidates."
        )

    connection.execute("DROP TABLE IF EXISTS pre_reform_candidate_map")
    connection.execute(
        "CREATE TEMP TABLE pre_reform_candidate_map "
        "(candidate_id VARCHAR PRIMARY KEY, candidacy_id UUID NOT NULL)"
    )
    bulk_insert(connection, "INSERT INTO pre_reform_candidate_map", candidate_map)

    with tempfile.TemporaryDirectory(prefix=f"politica-2010-btl-{state.lower()}-") as temporary:
        temporary_root = Path(temporary)
        csv_path = temporary_root / Path(member).name
        _extract_member(archive_path, member, csv_path)
        raw_root = temporary_root / "raw"
        partitions = _raw_partitions(connection, csv_path, raw_root)

        temporary_destination = destination.with_name(destination.name + ".writing")
        shutil.rmtree(temporary_destination, ignore_errors=True)
        shutil.rmtree(destination, ignore_errors=True)
        temporary_destination.mkdir(parents=True, exist_ok=True)
        ballot_count = 0
        preference_count = 0
        for partition_index, (_, raw_files) in enumerate(partitions):
            observed_ballots, observed_preferences = _write_partition(
                context,
                state,
                source_key,
                member,
                raw_files,
                partition_index,
                temporary_destination,
            )
            ballot_count += observed_ballots
            preference_count += observed_preferences
        if ballot_count != expected_matrix_ballots:
            raise ValueError(
                f"The {state} BTL transform produced {ballot_count:,} ballots; "
                f"expected {expected_matrix_ballots:,}."
            )
        os.replace(temporary_destination, destination)
    return ballot_count, preference_count


def import_pre_reform_preferences(context: "ImportContext") -> dict:
    """Import the directly marked 2010 BTL papers and activate shared views.

    Group-ticket ballots are represented by their official aggregate vote facts
    plus the separately normalised registered ticket paths. The anonymous
    candidate-by-paper matrices cover only non-ticket formal ballots.
    """

    connection = context.connection
    official_by_state = _official_non_ticket_votes(context)
    represented_by_state = {
        state: _matrix_ballot_count(context, state, source_key)
        for state, source_key in BTL_PREFERENCE_SOURCES.items()
    }
    unavailable_by_state = {
        state: official_by_state[state] - represented_by_state[state]
        for state in BTL_PREFERENCE_SOURCES
    }
    if any(value < 0 for value in unavailable_by_state.values()):
        raise ValueError(
            "A 2010 BTL matrix contains more papers than the official non-ticket total."
        )
    parquet_root = context.project_root / "data" / "parquet" / "aec_2010" / "formal_preferences"
    parquet_root.mkdir(parents=True, exist_ok=True)
    dataset_rows: list[tuple] = []
    state_preference_counts: dict[str, int] = {}

    for state, source_key in BTL_PREFERENCE_SOURCES.items():
        archive_path = context.source_path(source_key)
        member = _archive_member(archive_path)
        revision = context.revision_by_key[source_key]
        expected_ballots = represented_by_state[state]
        dataset_id = deterministic_uuid(
            "ballot_dataset", context.senate_contests[state], revision
        )
        dataset_rows.append(
            (
                dataset_id,
                context.senate_chamber_id,
                context.senate_contests[state],
                revision,
                f"{state} 2010 non-ticket formal Senate ballot papers",
                "below_the_line",
                "AEC batch and paper numbers retained only as anonymous source coordinates",
                (
                    "No elector identity is present; group-ticket ballots exist only as official "
                    f"aggregates. The AEC matrix omits {unavailable_by_state[state]} paper(s) "
                    "included in the official non-ticket aggregate."
                ),
                "aec_2010_btl_preferences_v1",
                expected_ballots,
                "active",
            )
        )
        state_container = parquet_root / f"state={state}"
        state_root = state_container / f"source_revision_id={revision}"
        if state_container.exists():
            for child in state_container.iterdir():
                if child.is_dir() and child != state_root:
                    shutil.rmtree(child)
        existing_files = sorted(state_root.rglob("*.parquet")) if state_root.exists() else []
        if existing_files:
            try:
                existing = connection.execute(
                    "SELECT count(*), sum(preference_count) "
                    "FROM read_parquet(?, hive_partitioning=false)",
                    [str(state_root / "**" / "*.parquet")],
                ).fetchone()
            except (duckdb.Error, OSError):
                existing = (-1, None)
            if existing[0] == expected_ballots:
                print(f"      reusing validated {state} 2010 BTL preferences", flush=True)
                state_preference_counts[state] = int(existing[1] or 0)
                continue
        print(f"      transforming {state} 2010 BTL preferences", flush=True)
        _, preference_count = _write_state(
            context,
            state,
            source_key,
            archive_path,
            member,
            state_root,
            expected_ballots,
        )
        state_preference_counts[state] = preference_count

    for dataset_id, *_ in dataset_rows:
        connection.execute(
            "DELETE FROM ballot.ballot_dataset WHERE ballot_dataset_id=?", [dataset_id]
        )
    bulk_insert(connection, "INSERT INTO ballot.ballot_dataset", dataset_rows)
    _replace_ballot_views(context, parquet_root)

    files = [
        {
            "path": str(path.relative_to(context.project_root)),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(parquet_root.rglob("*.parquet"))
    ]
    ticket_votes = sum(
        int(row.data["TicketVotes"])
        for row in context.rows("senate_gvt_usage_state")
    )
    ballot_count = sum(represented_by_state.values())
    official_non_ticket_vote_count = sum(official_by_state.values())
    unavailable_ballot_count = sum(unavailable_by_state.values())
    preference_count = sum(state_preference_counts.values())
    manifest = {
        "format": "partitioned_parquet_with_ordered_preference_paths",
        "coverage": "official_non_ticket_below_the_line_ballots",
        "state_count": len(BTL_PREFERENCE_SOURCES),
        "ballot_count": ballot_count,
        "preference_count": preference_count,
        "above_the_line_ballot_count": 0,
        "below_the_line_ballot_count": ballot_count,
        "official_non_ticket_vote_count": official_non_ticket_vote_count,
        "unavailable_ballot_count": unavailable_ballot_count,
        "group_ticket_vote_count": ticket_votes,
        "formal_vote_count": ticket_votes + official_non_ticket_vote_count,
        "represented_formal_vote_count": ticket_votes + ballot_count,
        "file_count": len(files),
        "state_ballot_counts": represented_by_state,
        "state_official_non_ticket_vote_counts": official_by_state,
        "state_unavailable_ballot_counts": unavailable_by_state,
        "state_preference_counts": state_preference_counts,
        "files": files,
    }
    manifest_path = context.project_root / "data" / "manifests" / "aec_2010_formal_preferences.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
