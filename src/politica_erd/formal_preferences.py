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

from .aec import normalise_label
from .db import bulk_insert
from .ids import deterministic_uuid

if TYPE_CHECKING:
    from .import_2025 import ImportContext


FORMAL_PREFERENCE_SOURCES = {
    "NT": "senate_formal_preferences_nt",
    "ACT": "senate_formal_preferences_act",
    "TAS": "senate_formal_preferences_tas",
    "SA": "senate_formal_preferences_sa",
    "WA": "senate_formal_preferences_wa",
    "QLD": "senate_formal_preferences_qld",
    "VIC": "senate_formal_preferences_vic",
    "NSW": "senate_formal_preferences_nsw",
}

METADATA_COLUMNS = (
    "State",
    "Division",
    "Vote Collection Point Name",
    "Vote Collection Point ID",
    "Batch No",
    "Paper No",
)

COMPACT_METADATA_COLUMNS = (
    "ElectorateNm",
    "VoteCollectionPointNm",
    "VoteCollectionPointId",
    "BatchNo",
    "PaperNo",
    "Preferences",
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_header(path: Path) -> tuple[str, list[str]]:
    with zipfile.ZipFile(path) as archive:
        members = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
        if len(members) != 1:
            raise ValueError(f"Expected one formal-preference CSV in {path.name}; found {members}")
        member = members[0]
        with archive.open(member) as raw:
            line = raw.readline().decode("utf-8-sig")
    return member, [header.strip() for header in next(csv.reader([line]))]


def _candidate_columns(context: "ImportContext") -> dict[tuple[str, str, str], object]:
    candidate_names: dict[tuple[str, str], set[str]] = {}
    for source_row in context.rows("senate_candidates"):
        data = source_row.data
        candidate_names[(data["StateAb"], data["CandidateID"])] = {
            normalise_label(f"{data['Surname']} {data['GivenNm']}"),
            normalise_label(f"{data['GivenNm']} {data['Surname']}"),
        }
    candidate_groups: dict[tuple[str, str], str] = {}
    for source_row in context.rows("senate_fp_state_vote_type"):
        data = source_row.data
        if data["BallotPosition"] != "0":
            group_code = data.get("Group") or data.get("Ticket")
            if not group_code:
                raise ValueError(
                    "A Senate candidate row has no Group or Ticket code: "
                    f"{source_row.locator}"
                )
            candidate_groups[(data["StateAb"], data["CandidateID"])] = group_code
    lookup: dict[tuple[str, str, str], object] = {}
    for (state, candidate_id), names in candidate_names.items():
        group = candidate_groups[(state, candidate_id)]
        candidacy = context.candidacies[("senate", state, candidate_id)]
        for name in names:
            lookup[(state, group, name)] = candidacy
    return lookup


def _column_mapping(
    context: "ImportContext",
    state: str,
    headers: list[str],
    candidate_lookup: dict[tuple[str, str, str], object],
) -> list[tuple[str, str, object]]:
    if tuple(headers[:6]) != METADATA_COLUMNS:
        raise ValueError(f"Unexpected {state} formal-preference metadata columns: {headers[:6]}")
    rows: list[tuple[str, str, object]] = []
    seen_group_columns: set[str] = set()
    for header in headers[6:]:
        if ":" not in header:
            raise ValueError(f"Unexpected formal-preference column in {state}: {header}")
        group_code, label = header.split(":", 1)
        ballot_group = context.ballot_groups.get((state, group_code))
        if group_code != "UG" and ballot_group is not None and group_code not in seen_group_columns:
            rows.append((header, "ballot_group", ballot_group))
            seen_group_columns.add(group_code)
            continue
        candidacy = candidate_lookup.get((state, group_code, normalise_label(label)))
        if candidacy is not None:
            rows.append((header, "candidacy", candidacy))
            continue
        raise ValueError(f"Unmapped formal-preference column in {state}: {header}")
    return rows


def _alpha_group_column_number(group_code: str) -> int:
    if not group_code.isalpha() or group_code == "UG":
        raise ValueError(f"Unsupported grouped Senate column code: {group_code!r}")
    value = 0
    for character in group_code.upper():
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def _compact_column_mapping(
    context: "ImportContext",
    state: str,
) -> list[tuple[int, str, object]]:
    """Map the position-based 2016 preference vector to governed targets.

    The 2016 archive stores one comma-separated vector rather than named
    preference columns. The separately published candidate-information archive
    supplies the ballot order: grouped above-the-line columns first, followed
    by every Senate candidate in its published row order.
    """

    candidate_ids_by_name: dict[str, list[str]] = {}
    for source_row in context.rows("senate_candidates"):
        data = source_row.data
        if data["StateAb"] != state:
            continue
        key = normalise_label(f"{data['Surname']} {data['GivenNm']}")
        candidate_ids_by_name.setdefault(key, []).append(data["CandidateID"])

    information_rows = [
        source_row
        for source_row in context.rows("senate_formal_candidate_information")
        if source_row.data.get("nom_ty") == "S"
        and source_row.data.get("state_ab") == state
    ]
    if not information_rows:
        raise ValueError(f"No 2016 formal-preference candidate information exists for {state}.")

    group_codes: list[str] = []
    seen_groups: set[str] = set()
    for source_row in information_rows:
        group_code = source_row.data.get("ticket", "").strip()
        if group_code and group_code != "UG" and group_code not in seen_groups:
            group_codes.append(group_code)
            seen_groups.add(group_code)
    expected_group_order = sorted(group_codes, key=_alpha_group_column_number)
    if group_codes != expected_group_order:
        raise ValueError(
            f"The {state} candidate-information group order is not ballot order: {group_codes}"
        )

    mapping: list[tuple[int, str, object]] = []
    for group_code in group_codes:
        ballot_group = context.ballot_groups.get((state, group_code))
        if ballot_group is None:
            raise ValueError(
                f"The {state} formal-preference group {group_code} has no governed ballot group."
            )
        mapping.append((len(mapping) + 1, "ballot_group", ballot_group))

    mapped_candidate_ids: set[str] = set()
    for source_row in information_rows:
        data = source_row.data
        name = normalise_label(f"{data.get('surname', '')} {data.get('ballot_given_nm', '')}")
        matches = candidate_ids_by_name.get(name, [])
        if len(matches) != 1:
            raise ValueError(
                f"The {state} 2016 candidate-information row is not uniquely mapped: "
                f"{source_row.locator} ({name!r}, matches={matches})"
            )
        candidate_id = matches[0]
        if candidate_id in mapped_candidate_ids:
            raise ValueError(
                f"The {state} candidate {candidate_id} appears twice in candidate information."
            )
        mapped_candidate_ids.add(candidate_id)
        candidacy = context.candidacies[("senate", state, candidate_id)]
        mapping.append((len(mapping) + 1, "candidacy", candidacy))

    expected_candidate_ids = {
        candidate_id
        for name_matches in candidate_ids_by_name.values()
        for candidate_id in name_matches
    }
    if mapped_candidate_ids != expected_candidate_ids:
        raise ValueError(
            f"The {state} candidate-information archive does not cover the Senate ballot exactly."
        )
    return mapping


def _extract_member(archive_path: Path, member: str, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive, archive.open(member) as source, destination.open("wb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)


def _write_state_parquet(
    context: "ImportContext",
    state: str,
    source_key: str,
    csv_path: Path,
    member: str,
    mapping: list[tuple[str, str, object]],
    destination: Path,
) -> None:
    connection = context.connection
    destination.mkdir(parents=True, exist_ok=True)
    connection.execute("DROP TABLE IF EXISTS formal_column_map")
    connection.execute(
        "CREATE TEMP TABLE formal_column_map (source_column VARCHAR, target_kind VARCHAR, target_id UUID)"
    )
    bulk_insert(connection, "INSERT INTO formal_column_map", mapping)

    preference_columns = ", ".join(_quote_identifier(row[0]) for row in mapping)
    source = context.source_by_key[source_key]
    revision = context.revision_by_key[source_key]
    dataset_id = deterministic_uuid(
        "ballot_dataset", context.senate_contests[state], revision
    )
    contest = context.senate_contests[state]
    escaped_csv = str(csv_path).replace("'", "''")
    member_literal = _quote_literal(member)
    dataset_literal = _quote_literal(dataset_id)
    revision_literal = _quote_literal(revision)
    contest_literal = _quote_literal(contest)

    expected = source["row_count"]
    chunk_sizes = getattr(context, "formal_preference_chunk_sizes", {})
    chunk_size = int(
        chunk_sizes.get(
            state,
            getattr(context, "formal_preference_chunk_size", 500000),
        )
    )
    if chunk_size < 1:
        raise ValueError("formal_preference_chunk_size must be positive")
    for chunk_index, first_row in enumerate(range(1, expected + 1, chunk_size)):
        last_row = min(expected, first_row + chunk_size - 1)
        expected_chunk_rows = last_row - first_row + 1
        partition = destination / f"storage_partition={chunk_index}"
        output = partition / "part-0.parquet"
        if output.exists():
            try:
                observed_chunk_rows = connection.execute(
                    "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
                    [str(output)],
                ).fetchone()[0]
            except (duckdb.Error, OSError):
                observed_chunk_rows = -1
            if observed_chunk_rows == expected_chunk_rows:
                continue
            shutil.rmtree(partition)
        partition.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_suffix(output.suffix + ".writing")
        temporary_output.unlink(missing_ok=True)
        escaped_output = str(temporary_output).replace("'", "''")

        sql = f"""
        COPY (
          WITH raw AS (
        SELECT row_number() OVER ()::BIGINT AS source_row_ordinal, *
        FROM read_csv(
          '{escaped_csv}', header=true, all_varchar=true, null_padding=true,
          strict_mode=false, parallel=true, sample_size=-1
        )
      ),
      filtered AS (
        SELECT * FROM raw
        WHERE source_row_ordinal BETWEEN {first_row} AND {last_row}
      ),
      unpivoted AS (
        SELECT source_row_ordinal,
               {_quote_identifier('State')} AS state_code,
               {_quote_identifier('Division')} AS division_name,
               {_quote_identifier('Vote Collection Point Name')} AS collection_point_name,
               {_quote_identifier('Vote Collection Point ID')} AS collection_point_id,
               {_quote_identifier('Batch No')} AS batch_number,
               {_quote_identifier('Paper No')} AS paper_number,
               source_column, source_marking
        FROM filtered
        UNPIVOT (source_marking FOR source_column IN ({preference_columns}))
      ),
      marks AS (
        SELECT u.*, m.target_kind, m.target_id,
               try_cast(trim(u.source_marking) AS INTEGER) AS preference_rank
        FROM unpivoted u
        JOIN formal_column_map m USING (source_column)
        WHERE try_cast(trim(u.source_marking) AS INTEGER) > 0
      ),
      mark_counts AS (
        SELECT *, count(*) OVER (
          PARTITION BY source_row_ordinal, target_kind, preference_rank
        ) AS occurrence_count
        FROM marks
      ),
      unique_ranks AS (
        SELECT DISTINCT source_row_ordinal, target_kind, preference_rank
        FROM mark_counts
        WHERE occurrence_count = 1
      ),
      ordered_ranks AS (
        SELECT *, row_number() OVER (
          PARTITION BY source_row_ordinal, target_kind ORDER BY preference_rank
        ) AS rank_ordinal
        FROM unique_ranks
      ),
      sequence_lengths AS (
        SELECT source_row_ordinal, target_kind,
               coalesce(
                 min(rank_ordinal - 1) FILTER (WHERE preference_rank <> rank_ordinal),
                 count(*)
               )::INTEGER AS sequence_length
        FROM ordered_ranks
        GROUP BY source_row_ordinal, target_kind
      ),
      selected AS (
        SELECT source_row_ordinal,
               coalesce(max(sequence_length) FILTER (WHERE target_kind='candidacy'), 0)::INTEGER AS candidate_length,
               coalesce(max(sequence_length) FILTER (WHERE target_kind='ballot_group'), 0)::INTEGER AS group_length
        FROM sequence_lengths
        GROUP BY source_row_ordinal
      ),
      choice AS (
        SELECT source_row_ordinal,
               CASE WHEN candidate_length >= 6 THEN 'candidacy' ELSE 'ballot_group' END AS chosen_kind,
               CASE WHEN candidate_length >= 6 THEN candidate_length ELSE group_length END AS chosen_length
        FROM selected
        WHERE candidate_length >= 6 OR group_length >= 1
      ),
      paths AS (
        SELECT mc.source_row_ordinal + 1 AS source_row_number,
               first(mc.state_code) AS state_code,
               first(mc.division_name) AS division_name,
               first(mc.collection_point_name) AS collection_point_name,
               first(mc.collection_point_id) AS collection_point_id,
               first(mc.batch_number) AS batch_number,
               first(mc.paper_number) AS paper_number,
               c.chosen_kind,
               c.chosen_length,
               list(
                 struct_pack(
                   preference_rank := mc.preference_rank::INTEGER,
                   target_kind := mc.target_kind,
                   target_id := mc.target_id,
                   source_marking := mc.source_marking
                 ) ORDER BY mc.preference_rank
               ) AS preferences
        FROM mark_counts mc
        JOIN choice c USING (source_row_ordinal)
        WHERE mc.target_kind = c.chosen_kind
          AND mc.occurrence_count = 1
          AND mc.preference_rank <= c.chosen_length
        GROUP BY mc.source_row_ordinal, c.chosen_kind, c.chosen_length
      )
      SELECT {dataset_literal}::UUID AS ballot_dataset_id,
             {revision_literal}::VARCHAR AS source_revision_id,
             {member_literal}::VARCHAR AS source_member,
             source_row_number,
             'member:' || {member_literal} || ';row:' || source_row_number::VARCHAR AS source_row_locator,
             state_code,
             division_name,
             collection_point_name,
             collection_point_id,
             batch_number,
             paper_number,
             lower(state_code) || '|' || collection_point_id || '|' || batch_number || '|' || paper_number AS anonymous_source_key,
             {contest_literal}::VARCHAR AS contest_id,
             CASE WHEN chosen_kind='candidacy' THEN 'below_the_line' ELSE 'above_the_line' END AS ballot_type,
             chosen_kind='ballot_group' AS above_the_line,
             chosen_length::INTEGER AS preference_count,
             preferences
      FROM paths
      ORDER BY source_row_number
    ) TO '{escaped_output}' (
      FORMAT PARQUET,
      COMPRESSION ZSTD,
      ROW_GROUP_SIZE 100000
    )
    """
        connection.execute(sql)
        observed_chunk_rows = connection.execute(
            "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
            [str(temporary_output)],
        ).fetchone()[0]
        if observed_chunk_rows != expected_chunk_rows:
            raise ValueError(
                f"{state} formal ballot shard {chunk_index} produced "
                f"{observed_chunk_rows:,} rows; expected {expected_chunk_rows:,}"
            )
        os.replace(temporary_output, output)

    state_glob = str(destination / "**" / "*.parquet").replace("'", "''")
    observed = connection.execute(
        f"SELECT count(*) FROM read_parquet('{state_glob}', hive_partitioning=false)"
    ).fetchone()[0]
    if observed != expected:
        raise ValueError(
            f"{state} formal ballot transformation produced {observed:,} rows; expected {expected:,}"
        )


def _write_compact_state_parquet(
    context: "ImportContext",
    state: str,
    source_key: str,
    csv_path: Path,
    member: str,
    mapping: list[tuple[int, str, object]],
    destination: Path,
) -> None:
    """Transform the position-based 2016 ballot archive in one atomic scan."""

    connection = context.connection
    connection.execute("DROP TABLE IF EXISTS compact_formal_column_map")
    connection.execute(
        "CREATE TEMP TABLE compact_formal_column_map "
        "(source_ordinal INTEGER, target_kind VARCHAR, target_id UUID)"
    )
    bulk_insert(connection, "INSERT INTO compact_formal_column_map", mapping)

    source = context.source_by_key[source_key]
    revision = context.revision_by_key[source_key]
    expected = int(source["row_count"]) - 1
    if expected < 1:
        raise ValueError(f"The {state} compact formal-preference archive has no ballots.")
    dataset_id = deterministic_uuid(
        "ballot_dataset", context.senate_contests[state], revision
    )
    contest = context.senate_contests[state]
    chunk_sizes = getattr(context, "formal_preference_chunk_sizes", {})
    chunk_size = int(
        chunk_sizes.get(
            state,
            getattr(context, "formal_preference_chunk_size", 500000),
        )
    )
    if chunk_size < 1:
        raise ValueError("formal_preference_chunk_size must be positive")

    escaped_csv = str(csv_path).replace("'", "''")
    state_literal = _quote_literal(state)
    member_literal = _quote_literal(member)
    dataset_literal = _quote_literal(dataset_id)
    revision_literal = _quote_literal(revision)
    contest_literal = _quote_literal(contest)
    temporary_destination = destination.with_name(destination.name + ".writing")
    if temporary_destination.exists():
        shutil.rmtree(temporary_destination)
    if destination.exists():
        shutil.rmtree(destination)
    temporary_destination.parent.mkdir(parents=True, exist_ok=True)
    escaped_destination = str(temporary_destination).replace("'", "''")

    sql = f"""
    COPY (
      WITH raw AS (
        SELECT row_number() OVER ()::BIGINT AS raw_ordinal, *
        FROM read_csv(
          '{escaped_csv}', header=true, all_varchar=true, null_padding=true,
          strict_mode=false, parallel=false, sample_size=-1
        )
      ),
      filtered AS (
        SELECT raw_ordinal,
               raw_ordinal - 1 AS ballot_ordinal,
               {_quote_identifier('ElectorateNm')} AS division_name,
               {_quote_identifier('VoteCollectionPointNm')} AS collection_point_name,
               {_quote_identifier('VoteCollectionPointId')} AS collection_point_id,
               {_quote_identifier('BatchNo')} AS batch_number,
               {_quote_identifier('PaperNo')} AS paper_number,
               {_quote_identifier('Preferences')} AS preference_vector
        FROM raw
        WHERE {_quote_identifier('ElectorateNm')} <> '------------'
      ),
      marks AS (
        SELECT f.*, m.target_kind, m.target_id,
               CASE
                 WHEN trim(u.source_marking) IN ('/', '*') THEN 1
                 ELSE try_cast(trim(u.source_marking) AS INTEGER)
               END AS preference_rank,
               u.source_marking
        FROM filtered f,
             UNNEST(string_split(f.preference_vector, ',')) WITH ORDINALITY
               AS u(source_marking, source_ordinal)
        JOIN compact_formal_column_map m
          ON m.source_ordinal = u.source_ordinal
        WHERE CASE
                WHEN trim(u.source_marking) IN ('/', '*') THEN 1
                ELSE try_cast(trim(u.source_marking) AS INTEGER)
              END > 0
      ),
      mark_counts AS (
        SELECT *, count(*) OVER (
          PARTITION BY ballot_ordinal, target_kind, preference_rank
        ) AS occurrence_count
        FROM marks
      ),
      unique_ranks AS (
        SELECT DISTINCT ballot_ordinal, target_kind, preference_rank
        FROM mark_counts
        WHERE occurrence_count = 1
      ),
      ordered_ranks AS (
        SELECT *, row_number() OVER (
          PARTITION BY ballot_ordinal, target_kind ORDER BY preference_rank
        ) AS rank_ordinal
        FROM unique_ranks
      ),
      sequence_lengths AS (
        SELECT ballot_ordinal, target_kind,
               coalesce(
                 min(rank_ordinal - 1) FILTER (WHERE preference_rank <> rank_ordinal),
                 count(*)
               )::INTEGER AS sequence_length
        FROM ordered_ranks
        GROUP BY ballot_ordinal, target_kind
      ),
      selected AS (
        SELECT ballot_ordinal,
               coalesce(max(sequence_length) FILTER (WHERE target_kind='candidacy'), 0)::INTEGER AS candidate_length,
               coalesce(max(sequence_length) FILTER (WHERE target_kind='ballot_group'), 0)::INTEGER AS group_length
        FROM sequence_lengths
        GROUP BY ballot_ordinal
      ),
      choice AS (
        SELECT ballot_ordinal,
               CASE WHEN candidate_length >= 6 THEN 'candidacy' ELSE 'ballot_group' END AS chosen_kind,
               CASE WHEN candidate_length >= 6 THEN candidate_length ELSE group_length END AS chosen_length
        FROM selected
        WHERE candidate_length >= 6 OR group_length >= 1
      ),
      paths AS (
        SELECT mc.ballot_ordinal,
               first(mc.raw_ordinal) AS raw_ordinal,
               first(mc.division_name) AS division_name,
               first(mc.collection_point_name) AS collection_point_name,
               first(mc.collection_point_id) AS collection_point_id,
               first(mc.batch_number) AS batch_number,
               first(mc.paper_number) AS paper_number,
               c.chosen_kind,
               c.chosen_length,
               list(
                 struct_pack(
                   preference_rank := mc.preference_rank::INTEGER,
                   target_kind := mc.target_kind,
                   target_id := mc.target_id,
                   source_marking := mc.source_marking
                 ) ORDER BY mc.preference_rank
               ) AS preferences
        FROM mark_counts mc
        JOIN choice c USING (ballot_ordinal)
        WHERE mc.target_kind = c.chosen_kind
          AND mc.occurrence_count = 1
          AND mc.preference_rank <= c.chosen_length
        GROUP BY mc.ballot_ordinal, c.chosen_kind, c.chosen_length
      )
      SELECT {dataset_literal}::UUID AS ballot_dataset_id,
             {revision_literal}::VARCHAR AS source_revision_id,
             {member_literal}::VARCHAR AS source_member,
             raw_ordinal + 1 AS source_row_number,
             'member:' || {member_literal} || ';row:' || (raw_ordinal + 1)::VARCHAR AS source_row_locator,
             {state_literal}::VARCHAR AS state_code,
             division_name,
             collection_point_name,
             collection_point_id,
             batch_number,
             paper_number,
             lower({state_literal}) || '|' || collection_point_id || '|' || batch_number || '|' || paper_number AS anonymous_source_key,
             {contest_literal}::VARCHAR AS contest_id,
             CASE WHEN chosen_kind='candidacy' THEN 'below_the_line' ELSE 'above_the_line' END AS ballot_type,
             chosen_kind='ballot_group' AS above_the_line,
             chosen_length::INTEGER AS preference_count,
             preferences,
             floor((ballot_ordinal - 1) / {chunk_size})::INTEGER AS storage_partition
      FROM paths
      ORDER BY ballot_ordinal
    ) TO '{escaped_destination}' (
      FORMAT PARQUET,
      PARTITION_BY (storage_partition),
      COMPRESSION ZSTD,
      ROW_GROUP_SIZE 100000
    )
    """
    connection.execute(sql)

    observed = connection.execute(
        "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
        [str(temporary_destination / "**" / "*.parquet")],
    ).fetchone()[0]
    if observed != expected:
        raise ValueError(
            f"{state} compact formal ballot transformation produced "
            f"{observed:,} rows; expected {expected:,}"
        )
    for partition_index, first_row in enumerate(range(1, expected + 1, chunk_size)):
        expected_rows = min(chunk_size, expected - first_row + 1)
        partition = temporary_destination / f"storage_partition={partition_index}"
        files = sorted(partition.glob("*.parquet"))
        if len(files) != 1:
            raise ValueError(
                f"{state} compact ballot partition {partition_index} has {len(files)} files."
            )
        observed_rows = connection.execute(
            "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
            [str(files[0])],
        ).fetchone()[0]
        if observed_rows != expected_rows:
            raise ValueError(
                f"{state} compact ballot partition {partition_index} produced "
                f"{observed_rows:,} rows; expected {expected_rows:,}"
            )
        files[0].replace(partition / "part-0.parquet")
    os.replace(temporary_destination, destination)


def _write_compact_state_parquet_chunked(
    context: "ImportContext",
    state: str,
    source_key: str,
    csv_path: Path,
    member: str,
    mapping: list[tuple[int, str, object]],
    destination: Path,
) -> None:
    """Transform compact 2016 vectors through bounded raw Parquet shards."""

    connection = context.connection
    connection.execute("DROP TABLE IF EXISTS compact_formal_column_map")
    connection.execute(
        "CREATE TEMP TABLE compact_formal_column_map "
        "(source_ordinal INTEGER, target_kind VARCHAR, target_id UUID)"
    )
    bulk_insert(connection, "INSERT INTO compact_formal_column_map", mapping)

    source = context.source_by_key[source_key]
    revision = context.revision_by_key[source_key]
    expected = int(source["row_count"]) - 1
    dataset_id = deterministic_uuid(
        "ballot_dataset", context.senate_contests[state], revision
    )
    contest = context.senate_contests[state]
    chunk_sizes = getattr(context, "formal_preference_chunk_sizes", {})
    chunk_size = int(
        chunk_sizes.get(
            state,
            getattr(context, "formal_preference_chunk_size", 500000),
        )
    )
    if expected < 1 or chunk_size < 1:
        raise ValueError(f"Invalid {state} compact ballot count or chunk size.")

    escaped_csv = str(csv_path).replace("'", "''")
    raw_root = csv_path.parent / f"{state.lower()}_raw_partitions"
    if raw_root.exists():
        shutil.rmtree(raw_root)
    escaped_raw_root = str(raw_root).replace("'", "''")
    connection.execute(
        f"""
        COPY (
          WITH raw AS (
            SELECT row_number() OVER ()::BIGINT AS raw_ordinal, *
            FROM read_csv(
              '{escaped_csv}', header=true, all_varchar=true, null_padding=true,
              strict_mode=false, parallel=false, sample_size=-1
            )
          )
          SELECT raw_ordinal,
                 raw_ordinal - 1 AS ballot_ordinal,
                 {_quote_identifier('ElectorateNm')} AS division_name,
                 {_quote_identifier('VoteCollectionPointNm')} AS collection_point_name,
                 {_quote_identifier('VoteCollectionPointId')} AS collection_point_id,
                 {_quote_identifier('BatchNo')} AS batch_number,
                 {_quote_identifier('PaperNo')} AS paper_number,
                 {_quote_identifier('Preferences')} AS preference_vector,
                 floor((raw_ordinal - 2) / {chunk_size})::INTEGER AS storage_partition
          FROM raw
          WHERE {_quote_identifier('ElectorateNm')} <> '------------'
          ORDER BY raw_ordinal
        ) TO '{escaped_raw_root}' (
          FORMAT PARQUET,
          PARTITION_BY (storage_partition),
          COMPRESSION ZSTD,
          ROW_GROUP_SIZE 100000
        )
        """
    )
    raw_count = connection.execute(
        "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
        [str(raw_root / "**" / "*.parquet")],
    ).fetchone()[0]
    if raw_count != expected:
        raise ValueError(
            f"{state} compact raw partitioning produced {raw_count:,} rows; "
            f"expected {expected:,}"
        )

    temporary_destination = destination.with_name(destination.name + ".writing")
    if temporary_destination.exists():
        shutil.rmtree(temporary_destination)
    if destination.exists():
        shutil.rmtree(destination)
    temporary_destination.mkdir(parents=True, exist_ok=True)

    state_literal = _quote_literal(state)
    member_literal = _quote_literal(member)
    dataset_literal = _quote_literal(dataset_id)
    revision_literal = _quote_literal(revision)
    contest_literal = _quote_literal(contest)
    try:
        for partition_index, first_row in enumerate(
            range(1, expected + 1, chunk_size)
        ):
            expected_rows = min(chunk_size, expected - first_row + 1)
            raw_partition = raw_root / f"storage_partition={partition_index}"
            raw_files = sorted(raw_partition.glob("*.parquet"))
            if len(raw_files) != 1:
                raise ValueError(
                    f"{state} raw ballot partition {partition_index} has "
                    f"{len(raw_files)} files."
                )
            escaped_raw = str(raw_files[0]).replace("'", "''")
            partition = temporary_destination / f"storage_partition={partition_index}"
            partition.mkdir(parents=True, exist_ok=True)
            output = partition / "part-0.parquet"
            writing = output.with_suffix(output.suffix + ".writing")
            escaped_output = str(writing).replace("'", "''")
            connection.execute(
                f"""
                COPY (
                  WITH filtered AS (
                    SELECT * FROM read_parquet(
                      '{escaped_raw}', hive_partitioning=false
                    )
                  ),
                  marks AS (
                    SELECT f.*, m.target_kind, m.target_id,
                           CASE
                             WHEN trim(u.source_marking) IN ('/', '*') THEN 1
                             ELSE try_cast(trim(u.source_marking) AS INTEGER)
                           END AS preference_rank,
                           u.source_marking
                    FROM filtered f,
                         UNNEST(string_split(f.preference_vector, ',')) WITH ORDINALITY
                           AS u(source_marking, source_ordinal)
                    JOIN compact_formal_column_map m
                      ON m.source_ordinal = u.source_ordinal
                    WHERE CASE
                            WHEN trim(u.source_marking) IN ('/', '*') THEN 1
                            ELSE try_cast(trim(u.source_marking) AS INTEGER)
                          END > 0
                  ),
                  mark_counts AS (
                    SELECT *, count(*) OVER (
                      PARTITION BY ballot_ordinal, target_kind, preference_rank
                    ) AS occurrence_count
                    FROM marks
                  ),
                  unique_ranks AS (
                    SELECT DISTINCT ballot_ordinal, target_kind, preference_rank
                    FROM mark_counts
                    WHERE occurrence_count = 1
                  ),
                  ordered_ranks AS (
                    SELECT *, row_number() OVER (
                      PARTITION BY ballot_ordinal, target_kind ORDER BY preference_rank
                    ) AS rank_ordinal
                    FROM unique_ranks
                  ),
                  sequence_lengths AS (
                    SELECT ballot_ordinal, target_kind,
                           coalesce(
                             min(rank_ordinal - 1) FILTER (
                               WHERE preference_rank <> rank_ordinal
                             ),
                             count(*)
                           )::INTEGER AS sequence_length
                    FROM ordered_ranks
                    GROUP BY ballot_ordinal, target_kind
                  ),
                  selected AS (
                    SELECT ballot_ordinal,
                           coalesce(max(sequence_length) FILTER (
                             WHERE target_kind='candidacy'
                           ), 0)::INTEGER AS candidate_length,
                           coalesce(max(sequence_length) FILTER (
                             WHERE target_kind='ballot_group'
                           ), 0)::INTEGER AS group_length
                    FROM sequence_lengths
                    GROUP BY ballot_ordinal
                  ),
                  choice AS (
                    SELECT ballot_ordinal,
                           CASE WHEN candidate_length >= 6
                             THEN 'candidacy' ELSE 'ballot_group' END AS chosen_kind,
                           CASE WHEN candidate_length >= 6
                             THEN candidate_length ELSE group_length END AS chosen_length
                    FROM selected
                    WHERE candidate_length >= 6 OR group_length >= 1
                  ),
                  paths AS (
                    SELECT mc.ballot_ordinal,
                           first(mc.raw_ordinal) AS raw_ordinal,
                           first(mc.division_name) AS division_name,
                           first(mc.collection_point_name) AS collection_point_name,
                           first(mc.collection_point_id) AS collection_point_id,
                           first(mc.batch_number) AS batch_number,
                           first(mc.paper_number) AS paper_number,
                           c.chosen_kind,
                           c.chosen_length,
                           list(
                             struct_pack(
                               preference_rank := mc.preference_rank::INTEGER,
                               target_kind := mc.target_kind,
                               target_id := mc.target_id,
                               source_marking := mc.source_marking
                             ) ORDER BY mc.preference_rank
                           ) AS preferences
                    FROM mark_counts mc
                    JOIN choice c USING (ballot_ordinal)
                    WHERE mc.target_kind = c.chosen_kind
                      AND mc.occurrence_count = 1
                      AND mc.preference_rank <= c.chosen_length
                    GROUP BY mc.ballot_ordinal, c.chosen_kind, c.chosen_length
                  )
                  SELECT {dataset_literal}::UUID AS ballot_dataset_id,
                         {revision_literal}::VARCHAR AS source_revision_id,
                         {member_literal}::VARCHAR AS source_member,
                         raw_ordinal + 1 AS source_row_number,
                         'member:' || {member_literal} || ';row:' ||
                           (raw_ordinal + 1)::VARCHAR AS source_row_locator,
                         {state_literal}::VARCHAR AS state_code,
                         division_name,
                         collection_point_name,
                         collection_point_id,
                         batch_number,
                         paper_number,
                         lower({state_literal}) || '|' || collection_point_id || '|' ||
                           batch_number || '|' || paper_number AS anonymous_source_key,
                         {contest_literal}::VARCHAR AS contest_id,
                         CASE WHEN chosen_kind='candidacy'
                           THEN 'below_the_line' ELSE 'above_the_line' END AS ballot_type,
                         chosen_kind='ballot_group' AS above_the_line,
                         chosen_length::INTEGER AS preference_count,
                         preferences
                  FROM paths
                  ORDER BY ballot_ordinal
                ) TO '{escaped_output}' (
                  FORMAT PARQUET,
                  COMPRESSION ZSTD,
                  ROW_GROUP_SIZE 100000
                )
                """
            )
            observed_rows = connection.execute(
                "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
                [str(writing)],
            ).fetchone()[0]
            if observed_rows != expected_rows:
                raise ValueError(
                    f"{state} compact ballot partition {partition_index} produced "
                    f"{observed_rows:,} rows; expected {expected_rows:,}"
                )
            os.replace(writing, output)

        observed = connection.execute(
            "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
            [str(temporary_destination / "**" / "*.parquet")],
        ).fetchone()[0]
        if observed != expected:
            raise ValueError(
                f"{state} compact formal ballot transformation produced "
                f"{observed:,} rows; expected {expected:,}"
            )
        os.replace(temporary_destination, destination)
    finally:
        shutil.rmtree(raw_root, ignore_errors=True)


def _replace_ballot_views(context: "ImportContext", parquet_root: Path) -> None:
    connection = context.connection
    for name in ("ballot_preference", "ballot"):
        row = connection.execute(
            """SELECT table_type FROM information_schema.tables
               WHERE table_schema='ballot' AND table_name=?""",
            [name],
        ).fetchone()
        if row:
            connection.execute(
                f"DROP {'VIEW' if row[0] == 'VIEW' else 'TABLE'} ballot.{name}"
            )
    # Every imported election retains its own immutable Parquet tree.  The
    # canonical ballot views span every installed AEC election rather than
    # being replaced with the most recently imported year only.
    relative_glob = "data/parquet/aec_*/formal_preferences/**/*.parquet"
    connection.execute(
        f"""
        CREATE VIEW ballot.ballot AS
        SELECT control.uuid5_name(
                 'ballot|' || lower(ballot_dataset_id::VARCHAR) || '|' || lower(anonymous_source_key)
               ) AS ballot_id,
               ballot_dataset_id,
               anonymous_source_key,
               contest_id,
               'vote_collection_point'::VARCHAR AS ballot_channel,
               ballot_type,
               'formal'::VARCHAR AS formality_status,
               above_the_line,
               preference_count,
               source_row_locator
        FROM read_parquet(
          '{relative_glob}',
          hive_partitioning=false,
          union_by_name=true,
          filename=true
        )
        WHERE filename NOT LIKE '%.writing/%'
          AND filename NOT LIKE '%.parquet.writing'
        """
    )
    connection.execute(
        f"""
        CREATE VIEW ballot.ballot_preference AS
        WITH expanded AS (
          SELECT control.uuid5_name(
                   'ballot|' || lower(ballot_dataset_id::VARCHAR) || '|' || lower(anonymous_source_key)
                 ) AS ballot_id,
                 unnest(preferences) AS preference
          FROM read_parquet(
            '{relative_glob}',
            hive_partitioning=false,
            union_by_name=true,
            filename=true
          )
          WHERE filename NOT LIKE '%.writing/%'
            AND filename NOT LIKE '%.parquet.writing'
        )
        SELECT control.uuid5_name(
                 'ballot_preference|' || lower(ballot_id::VARCHAR) || '|' ||
                 preference.preference_rank::VARCHAR
               ) AS ballot_preference_id,
               ballot_id,
               preference.preference_rank::INTEGER AS preference_rank,
               CASE WHEN preference.target_kind='candidacy' THEN preference.target_id END AS candidacy_id,
               CASE WHEN preference.target_kind='ballot_group' THEN preference.target_id END AS ballot_group_id,
               preference.source_marking::VARCHAR AS source_marking
        FROM expanded
        """
    )


def import_formal_preferences(context: "ImportContext") -> dict:
    connection = context.connection
    election_year = int(getattr(context, "election_year", 2025))
    election_identifier = str(
        getattr(context, "election_id", "election_fed_2025_05_03_general")
    )
    senate_chamber = str(
        getattr(
            context,
            "senate_chamber_id",
            f"election_chamber_{election_identifier.removeprefix('election_')}_senate",
        )
    )
    parquet_root = (
        context.project_root
        / "data"
        / "parquet"
        / f"aec_{election_year}"
        / "formal_preferences"
    )
    parquet_root.mkdir(parents=True, exist_ok=True)
    candidate_lookup = None
    dataset_rows: list[tuple] = []

    for state, source_key in FORMAL_PREFERENCE_SOURCES.items():
        archive_path = context.source_path(source_key)
        member, headers = _archive_header(archive_path)
        compact_format = tuple(headers) == COMPACT_METADATA_COLUMNS
        if compact_format:
            mapping = _compact_column_mapping(context, state)
            expected_rows = int(context.source_by_key[source_key]["row_count"]) - 1
        else:
            if candidate_lookup is None:
                candidate_lookup = _candidate_columns(context)
            mapping = _column_mapping(context, state, headers, candidate_lookup)
            expected_rows = int(context.source_by_key[source_key]["row_count"])
        source = context.source_by_key[source_key]
        revision = context.revision_by_key[source_key]
        dataset_id = deterministic_uuid(
            "ballot_dataset", context.senate_contests[state], revision
        )
        dataset_rows.append(
            (
                dataset_id,
                senate_chamber,
                context.senate_contests[state],
                revision,
                f"{state} formal Senate ballot papers",
                "mixed_vote_collection_points",
                "AEC batch and paper numbers retained only as anonymous source coordinates",
                "No elector identity is present in the official archive.",
                f"aec_{election_year}_formal_preferences_v1",
                expected_rows,
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
            state_glob = str(state_root / "**" / "*.parquet")
            try:
                existing_rows = connection.execute(
                    "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
                    [state_glob],
                ).fetchone()[0]
            except (duckdb.Error, OSError):
                existing_rows = -1
            if existing_rows == expected_rows:
                print(f"      reusing validated {state} formal preferences", flush=True)
                continue
            shutil.rmtree(state_root)
        print(f"      transforming {state} formal preferences", flush=True)
        with tempfile.TemporaryDirectory(prefix=f"politica-formal-{state.lower()}-") as temporary:
            csv_path = Path(temporary) / Path(member).name
            _extract_member(archive_path, member, csv_path)
            if compact_format:
                _write_compact_state_parquet_chunked(
                    context, state, source_key, csv_path, member, mapping, state_root
                )
            else:
                _write_state_parquet(
                    context, state, source_key, csv_path, member, mapping, state_root
                )

    bulk_insert(connection, "INSERT INTO ballot.ballot_dataset", dataset_rows)
    _replace_ballot_views(context, parquet_root)

    files = []
    for path in sorted(parquet_root.rglob("*.parquet")):
        files.append(
            {
                "path": str(path.relative_to(context.project_root)),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    total_ballots, total_preferences, atl_ballots, btl_ballots = connection.execute(
        f"""
        SELECT count(*), sum(preference_count),
               count(*) FILTER (WHERE above_the_line),
               count(*) FILTER (WHERE NOT above_the_line)
        FROM read_parquet(
          '{(parquet_root / '**' / '*.parquet').as_posix().replace("'", "''")}',
          hive_partitioning=false, union_by_name=true
        )
        """
    ).fetchone()
    manifest = {
        "format": "partitioned_parquet_with_ordered_preference_paths",
        "state_count": len(FORMAL_PREFERENCE_SOURCES),
        "ballot_count": total_ballots,
        "preference_count": total_preferences,
        "above_the_line_ballot_count": atl_ballots,
        "below_the_line_ballot_count": btl_ballots,
        "file_count": len(files),
        "files": files,
    }
    if "senate_formal_candidate_information" in context.revision_by_key:
        manifest["mapping_source_revision_id"] = context.revision_by_key[
            "senate_formal_candidate_information"
        ]
    manifest_path = (
        context.project_root
        / "data"
        / "manifests"
        / f"aec_{election_year}_formal_preferences.json"
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
