from __future__ import annotations

from collections.abc import Iterable, Sequence

import duckdb


def bulk_insert(
    connection: duckdb.DuckDBPyConnection,
    insert_prefix: str,
    rows: Iterable[Sequence],
    batch_size: int = 500,
) -> int:
    materialised = list(rows)
    for start in range(0, len(materialised), batch_size):
        batch = materialised[start : start + batch_size]
        if not batch:
            continue
        width = len(batch[0])
        values_clause = ", ".join(["(" + ", ".join(["?"] * width) + ")"] * len(batch))
        parameters = [value for row in batch for value in row]
        connection.execute(f"{insert_prefix} VALUES {values_clause}", parameters)
    return len(materialised)
