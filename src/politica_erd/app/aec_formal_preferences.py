from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile

from ..ids import deterministic_uuid
from .aec_house_summaries import _event_id
from .aec_individual import _revision_family
from .aec_senate_summaries import _senate_index
from .references import normalise
from .transformers import TransformContext, TransformResult, register_transformer


ADAPTER_ID = "adapter_aec_2025_v1"
TRANSFORM_VERSION = "1.0.0"
METADATA_COLUMNS = (
    "State",
    "Division",
    "Vote Collection Point Name",
    "Vote Collection Point ID",
    "Batch No",
    "Paper No",
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _header(context: TransformContext) -> list[str]:
    return [str(value).strip() for value in context.dataset.get("headers") or []]


def _mapping(
    context: TransformContext,
    *,
    state: str,
    contest_id: str,
    headers: list[str],
) -> list[tuple[str, str, object]]:
    if tuple(headers[:6]) != METADATA_COLUMNS:
        raise ValueError(
            f"Unexpected {state} formal-preference metadata columns: {headers[:6]}"
        )
    groups = {
        str(code or "").strip().upper(): identifier
        for code, identifier in context.connection.execute(
            """SELECT official_group_id, ballot_group_id
               FROM core.ballot_group
               WHERE contest_id=? AND record_status='active'""",
            [contest_id],
        ).fetchall()
        if str(code or "").strip()
    }
    candidate_rows = context.connection.execute(
        """SELECT candidacy.candidacy_id, candidacy.ballot_given_names,
                  candidacy.ballot_family_name, candidacy.ballot_name,
                  ballot_group.official_group_id
           FROM core.candidacy candidacy
           LEFT JOIN core.ballot_group_membership membership
             ON membership.candidacy_id=candidacy.candidacy_id
           LEFT JOIN core.ballot_group ballot_group
             ON ballot_group.ballot_group_id=membership.ballot_group_id
           WHERE candidacy.contest_id=? AND candidacy.record_status='active'""",
        [contest_id],
    ).fetchall()
    candidates: dict[tuple[str, str], list[object]] = {}
    expected_candidates: set[object] = set()
    for identifier, given, family, ballot_name, group_code in candidate_rows:
        expected_candidates.add(identifier)
        code = str(group_code or "UG").strip().upper() or "UG"
        for label in (
            f"{family or ''} {given or ''}",
            f"{given or ''} {family or ''}",
            ballot_name,
        ):
            key = (code, normalise(label))
            if key[1]:
                candidates.setdefault(key, [])
                if identifier not in candidates[key]:
                    candidates[key].append(identifier)

    result: list[tuple[str, str, object]] = []
    seen_group_columns: set[str] = set()
    seen_candidates: set[object] = set()
    for header in headers[6:]:
        if ":" not in header:
            raise ValueError(f"Unexpected formal-preference column in {state}: {header}")
        group_code, label = header.split(":", 1)
        group_code = group_code.strip().upper()
        if (
            group_code != "UG"
            and group_code in groups
            and group_code not in seen_group_columns
        ):
            result.append((header, "ballot_group", groups[group_code]))
            seen_group_columns.add(group_code)
            continue
        matches = candidates.get((group_code, normalise(label)), [])
        if len(matches) != 1 or matches[0] in seen_candidates:
            raise ValueError(
                f"Formal-preference candidate column does not resolve uniquely in {state}: {header}"
            )
        seen_candidates.add(matches[0])
        result.append((header, "candidacy", matches[0]))

    if seen_candidates != expected_candidates:
        raise ValueError(
            f"The {state} formal-preference header does not cover every governed candidate"
        )
    expected_groups = set(groups) - {"UG"}
    if seen_group_columns != expected_groups:
        raise ValueError(
            f"The {state} formal-preference header does not cover every governed above-the-line group"
        )
    return result


def _extract_source(context: TransformContext, destination: Path) -> None:
    member = context.dataset.get("member")
    if member:
        with zipfile.ZipFile(context.source_container) as archive:
            info = archive.getinfo(member)
            if info.is_dir():
                raise ValueError("The selected formal-preference ZIP member is a directory")
            with archive.open(info) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=4 * 1024**2)
    else:
        shutil.copy2(context.source_container, destination)


def _write_raw_parquet(
    context: TransformContext, csv_path: Path, raw_parquet: Path
) -> int:
    escaped_csv = str(csv_path).replace("'", "''")
    escaped_raw = str(raw_parquet).replace("'", "''")
    context.connection.execute(
        f"""
        COPY (
          SELECT row_number() OVER ()::BIGINT AS source_row_ordinal, *
          FROM read_csv(
            '{escaped_csv}', header=true, all_varchar=true, delim=',',
            null_padding=true, strict_mode=false, parallel=false, sample_size=-1
          )
        ) TO '{escaped_raw}' (
          FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000
        )
        """
    )
    return int(
        context.connection.execute(
            "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
            [str(raw_parquet)],
        ).fetchone()[0]
    )


def _write_ballot_shards(
    context: TransformContext,
    *,
    state: str,
    contest_id: str,
    dataset_id: object,
    source_member: str,
    raw_parquet: Path,
    mapping: list[tuple[str, str, object]],
    expected_rows: int,
    destination: Path,
) -> tuple[int, int]:
    context.connection.execute("DROP TABLE IF EXISTS formal_column_map")
    context.connection.execute(
        """CREATE TEMP TABLE formal_column_map
           (source_column VARCHAR, target_kind VARCHAR, target_id UUID)"""
    )
    context.connection.executemany(
        "INSERT INTO formal_column_map VALUES (?, ?, ?)", mapping
    )
    preference_columns = ", ".join(_quote_identifier(row[0]) for row in mapping)
    raw_literal = _quote_literal(raw_parquet)
    dataset_literal = _quote_literal(dataset_id)
    revision_literal = _quote_literal(context.source_revision_id)
    contest_literal = _quote_literal(contest_id)
    member_literal = _quote_literal(source_member)
    chunk_size = 250_000
    for chunk_index, first_row in enumerate(range(1, expected_rows + 1, chunk_size)):
        last_row = min(expected_rows, first_row + chunk_size - 1)
        partition = destination / f"storage_partition={chunk_index}"
        partition.mkdir(parents=True, exist_ok=False)
        output = partition / "part-0.parquet"
        escaped_output = str(output).replace("'", "''")
        context.connection.execute(
            f"""
            COPY (
              WITH filtered AS (
                SELECT * FROM read_parquet({raw_literal}, hive_partitioning=false)
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
                SELECT source.*, mapping.target_kind, mapping.target_id,
                       try_cast(trim(source.source_marking) AS INTEGER) AS preference_rank
                FROM unpivoted source
                JOIN formal_column_map mapping USING (source_column)
                WHERE try_cast(trim(source.source_marking) AS INTEGER) > 0
              ),
              mark_counts AS (
                SELECT *, count(*) OVER (
                  PARTITION BY source_row_ordinal, target_kind, preference_rank
                ) AS occurrence_count
                FROM marks
              ),
              unique_ranks AS (
                SELECT DISTINCT source_row_ordinal, target_kind, preference_rank
                FROM mark_counts WHERE occurrence_count=1
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
                         min(rank_ordinal - 1) FILTER (WHERE preference_rank<>rank_ordinal),
                         count(*)
                       )::INTEGER AS sequence_length
                FROM ordered_ranks GROUP BY source_row_ordinal, target_kind
              ),
              selected AS (
                SELECT source_row_ordinal,
                       coalesce(max(sequence_length) FILTER (WHERE target_kind='candidacy'), 0)::INTEGER AS candidate_length,
                       coalesce(max(sequence_length) FILTER (WHERE target_kind='ballot_group'), 0)::INTEGER AS group_length
                FROM sequence_lengths GROUP BY source_row_ordinal
              ),
              choice AS (
                SELECT source_row_ordinal,
                       CASE WHEN candidate_length>=6 THEN 'candidacy' ELSE 'ballot_group' END AS chosen_kind,
                       CASE WHEN candidate_length>=6 THEN candidate_length ELSE group_length END AS chosen_length
                FROM selected WHERE candidate_length>=6 OR group_length>=1
              ),
              paths AS (
                SELECT marks.source_row_ordinal AS source_row_number,
                       first(marks.state_code) AS state_code,
                       first(marks.division_name) AS division_name,
                       first(marks.collection_point_name) AS collection_point_name,
                       first(marks.collection_point_id) AS collection_point_id,
                       first(marks.batch_number) AS batch_number,
                       first(marks.paper_number) AS paper_number,
                       choice.chosen_kind, choice.chosen_length,
                       list(
                         struct_pack(
                           preference_rank := marks.preference_rank::INTEGER,
                           target_kind := marks.target_kind,
                           target_id := marks.target_id,
                           source_marking := marks.source_marking
                         ) ORDER BY marks.preference_rank
                       ) AS preferences
                FROM mark_counts marks JOIN choice USING (source_row_ordinal)
                WHERE marks.target_kind=choice.chosen_kind
                  AND marks.occurrence_count=1
                  AND marks.preference_rank<=choice.chosen_length
                GROUP BY marks.source_row_ordinal, choice.chosen_kind, choice.chosen_length
              )
              SELECT {dataset_literal}::UUID AS ballot_dataset_id,
                     {revision_literal}::VARCHAR AS source_revision_id,
                     {member_literal}::VARCHAR AS source_member,
                     source_row_number,
                     'member:' || {member_literal} || ';row:' || source_row_number::VARCHAR AS source_row_locator,
                     state_code, division_name, collection_point_name,
                     collection_point_id, batch_number, paper_number,
                     lower(state_code) || '|' || lower(division_name) || '|' ||
                       collection_point_id || '|' || batch_number || '|' || paper_number AS anonymous_source_key,
                     {contest_literal}::VARCHAR AS contest_id,
                     CASE WHEN chosen_kind='candidacy' THEN 'below_the_line' ELSE 'above_the_line' END AS ballot_type,
                     chosen_kind='ballot_group' AS above_the_line,
                     chosen_length::INTEGER AS preference_count,
                     preferences
              FROM paths ORDER BY source_row_number
            ) TO '{escaped_output}' (
              FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000
            )
            """
        )
        observed = context.connection.execute(
            "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
            [str(output)],
        ).fetchone()[0]
        expected_chunk = last_row - first_row + 1
        if observed != expected_chunk:
            raise ValueError(
                f"{state} formal ballot shard {chunk_index} produced {observed:,} rows; "
                f"expected {expected_chunk:,}"
            )
    glob = str(destination / "**" / "*.parquet")
    ballots, preferences = context.connection.execute(
        """SELECT count(*), coalesce(sum(preference_count), 0)
           FROM read_parquet(?, hive_partitioning=false, union_by_name=true)""",
        [glob],
    ).fetchone()
    if int(ballots) != expected_rows:
        raise ValueError(
            f"{state} formal ballot transformation produced {ballots:,} rows; "
            f"expected {expected_rows:,}"
        )
    return int(ballots), int(preferences)


def _replace_ballot_views(context: TransformContext) -> None:
    for name in ("ballot_preference", "ballot"):
        row = context.connection.execute(
            """SELECT table_type FROM information_schema.tables
               WHERE table_schema='ballot' AND table_name=?""",
            [name],
        ).fetchone()
        if row:
            context.connection.execute(
                f"DROP {'VIEW' if row[0] == 'VIEW' else 'TABLE'} ballot.{name}"
            )
    parquet_glob = "data/parquet/aec*/formal_preferences/**/*.parquet"
    context.connection.execute(
        f"""
        CREATE VIEW ballot.ballot AS
        SELECT control.uuid5_name(
                 'ballot|' || lower(source.ballot_dataset_id::VARCHAR) || '|' ||
                 lower(source.anonymous_source_key)
               ) AS ballot_id,
               source.ballot_dataset_id,
               source.anonymous_source_key,
               source.contest_id,
               'vote_collection_point'::VARCHAR AS ballot_channel,
               source.ballot_type,
               'formal'::VARCHAR AS formality_status,
               source.above_the_line,
               source.preference_count,
               source.source_row_locator
        FROM read_parquet('{parquet_glob}', hive_partitioning=false, union_by_name=true) source
        JOIN ballot.ballot_dataset dataset USING (ballot_dataset_id)
        WHERE dataset.record_status='active'
        """
    )
    context.connection.execute(
        f"""
        CREATE VIEW ballot.ballot_preference AS
        WITH expanded AS (
          SELECT control.uuid5_name(
                   'ballot|' || lower(source.ballot_dataset_id::VARCHAR) || '|' ||
                   lower(source.anonymous_source_key)
                 ) AS ballot_id,
                 unnest(source.preferences) AS preference
          FROM read_parquet('{parquet_glob}', hive_partitioning=false, union_by_name=true) source
          JOIN ballot.ballot_dataset dataset USING (ballot_dataset_id)
          WHERE dataset.record_status='active'
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


def _write_manifest(context: TransformContext) -> None:
    active = [
        {
            "ballot_dataset_id": str(row[0]),
            "contest_id": row[1],
            "source_revision_id": row[2],
            "row_count": int(row[3] or 0),
        }
        for row in context.connection.execute(
            """SELECT ballot_dataset_id, contest_id, source_revision_id, row_count
               FROM ballot.ballot_dataset WHERE record_status='active'
               ORDER BY contest_id, source_revision_id"""
        ).fetchall()
    ]
    destination = context.work_root / "data/manifests/formal_preferences_stage8.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".tmp-{uuid.uuid4().hex}")
    temporary.write_text(
        json.dumps(
            {
                "format": "partitioned_parquet_with_ordered_preference_paths",
                "view_filter": "active ballot.ballot_dataset rows only",
                "active_datasets": active,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def transform_formal_preferences(context: TransformContext) -> TransformResult:
    filename = Path(context.dataset["virtual_name"]).name
    match = re.fullmatch(
        r"aec-senate-formalpreferences-(?P<event>\d+)-(?P<state>ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\.csv",
        filename,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"Invalid Senate formal-preference member filename: {filename}")
    event_id = _event_id(
        context,
        r"aec-senate-formalpreferences-(?P<event>\d+)-(?:ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\.csv",
    )
    state = match.group("state").upper()
    contests, _ = _senate_index(context)
    contest_id = contests[state][0]
    chamber_id = context.connection.execute(
        """SELECT election_chamber_id FROM core.election_chamber
           WHERE election_id=? AND chamber_id='chamber_senate' AND record_status='active'""",
        [context.job["election_id"]],
    ).fetchone()
    if chamber_id is None:
        raise ValueError("The selected election has no active Senate chamber")
    headers = _header(context)
    mapping = _mapping(
        context, state=state, contest_id=contest_id, headers=headers
    )
    governed_formal_votes = context.connection.execute(
        """SELECT coalesce(sum(integer_value), 0), count(*)
           FROM results.vote_result
           WHERE contest_id=? AND election_reporting_unit_id IS NULL
             AND result_type='first_preference' AND vote_type='total'
             AND measure_type='votes' AND record_status='active'""",
        [contest_id],
    ).fetchone()
    if int(governed_formal_votes[1]) <= 0:
        raise ValueError(
            f"The {state} formal archive requires governed state first-preference totals"
        )
    expected_formal_votes = int(governed_formal_votes[0])
    dataset_id = deterministic_uuid(
        "ballot_dataset", contest_id, context.source_revision_id
    )

    scratch_parent = context.work_root / "data/tmp/formal_preferences"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    parquet_parent = (
        context.work_root
        / "data/parquet"
        / f"aec_{event_id}"
        / "formal_preferences"
        / f"state={state}"
    )
    parquet_parent.mkdir(parents=True, exist_ok=True)
    destination = parquet_parent / f"source_revision_id={context.source_revision_id}"
    for stale in parquet_parent.glob(f".{destination.name}.tmp-*"):
        shutil.rmtree(stale, ignore_errors=True)
    reusable_output: tuple[int, int] | None = None
    if destination.exists():
        existing = context.connection.execute(
            """SELECT count(*), coalesce(sum(preference_count), 0)
               FROM read_parquet(?, hive_partitioning=false, union_by_name=true)""",
            [str(destination / "**/*.parquet")],
        ).fetchone()
        expected_existing = context.connection.execute(
            """SELECT row_count FROM ballot.ballot_dataset
               WHERE ballot_dataset_id=? AND source_revision_id=?""",
            [dataset_id, context.source_revision_id],
        ).fetchone()
        if expected_existing and int(existing[0]) == int(expected_existing[0]):
            _replace_ballot_views(context)
            return TransformResult(
                inserted_rows=int(existing[0]) + int(existing[1]) + 1,
                source_rows=int(existing[0]),
                notes=f"Reused the validated {state} formal-preference Parquet revision.",
            )
        if int(existing[0]) <= 0:
            raise ValueError(
                f"An incomplete {state} formal-preference output already exists in this job"
            )
        reusable_output = (int(existing[0]), int(existing[1]))

    if reusable_output is not None:
        ballots, preferences = reusable_output
        source_rows = ballots
    else:
        with tempfile.TemporaryDirectory(
            prefix=f"politica-formal-{state.lower()}-", dir=scratch_parent
        ) as temporary_value:
            temporary = Path(temporary_value)
            csv_path = temporary / filename
            raw_parquet = temporary / "raw.parquet"
            _extract_source(context, csv_path)
            source_rows = _write_raw_parquet(context, csv_path, raw_parquet)
            if source_rows <= 0:
                raise ValueError(f"The {state} formal-preference archive contains no ballots")
            observed_state = context.connection.execute(
                """SELECT count(DISTINCT upper(trim(State))), min(upper(trim(State)))
                   FROM read_parquet(?, hive_partitioning=false)""",
                [str(raw_parquet)],
            ).fetchone()
            if observed_state != (1, state):
                raise ValueError(
                    f"The formal-preference member reports state {observed_state[1]!r}, expected {state}"
                )
            candidate = parquet_parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
            candidate.mkdir(parents=False)
            try:
                ballots, preferences = _write_ballot_shards(
                    context,
                    state=state,
                    contest_id=contest_id,
                    dataset_id=dataset_id,
                    source_member=filename,
                    raw_parquet=raw_parquet,
                    mapping=mapping,
                    expected_rows=source_rows,
                    destination=candidate,
                )
                os.replace(candidate, destination)
            finally:
                shutil.rmtree(candidate, ignore_errors=True)

    if source_rows != expected_formal_votes:
        raise ValueError(
            f"The {state} formal archive contains {source_rows:,} ballots; governed "
            f"state first preferences report {expected_formal_votes:,} formal votes"
        )

    _, prior = _revision_family(context)
    active_conflicts = context.connection.execute(
        """SELECT source_revision_id FROM ballot.ballot_dataset
           WHERE contest_id=? AND record_status='active' AND source_revision_id<>?""",
        [contest_id, context.source_revision_id],
    ).fetchall()
    if any(row[0] not in prior for row in active_conflicts):
        raise ValueError(
            "An active formal-ballot dataset from another logical source already occupies this contest"
        )
    superseded = 0
    if prior:
        placeholders = ",".join("?" for _ in prior)
        superseded = len(
            context.connection.execute(
                f"""UPDATE ballot.ballot_dataset SET record_status='superseded'
                     WHERE contest_id=? AND record_status='active'
                       AND source_revision_id IN ({placeholders})
                     RETURNING ballot_dataset_id""",
                [contest_id, *sorted(prior)],
            ).fetchall()
        )
    context.connection.execute(
        """INSERT INTO ballot.ballot_dataset VALUES
           (?, ?, ?, ?, ?, 'mixed_vote_collection_points',
            'AEC batch and paper numbers retained only as anonymous source coordinates',
            'No elector identity is present in the official archive.',
            'aec_formal_preferences_v2', ?, 'active')""",
        [
            dataset_id,
            chamber_id[0],
            contest_id,
            context.source_revision_id,
            f"{state} formal Senate ballot papers",
            ballots,
        ],
    )
    _replace_ballot_views(context)
    observed = context.connection.execute(
        """SELECT count(*), coalesce(sum(preference_count), 0)
           FROM ballot.ballot WHERE ballot_dataset_id=?""",
        [dataset_id],
    ).fetchone()
    if (int(observed[0]), int(observed[1])) != (ballots, preferences):
        raise ValueError("The active formal-ballot views do not reconcile to the new Parquet revision")
    _write_manifest(context)
    return TransformResult(
        inserted_rows=ballots + preferences + 1,
        source_rows=source_rows,
        notes=(
            f"Inserted {ballots:,} anonymous {state} formal ballots and "
            f"{preferences:,} counted preference positions; superseded "
            f"{superseded:,} prior ballot dataset."
        ),
    )


register_transformer(
    ADAPTER_ID,
    "senate_formal_preferences",
    TRANSFORM_VERSION,
    transform_formal_preferences,
)
