from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import duckdb


@dataclass(frozen=True)
class TransformContext:
    connection: duckdb.DuckDBPyConnection
    job: dict
    dataset: dict
    import_run_id: str
    source_revision_id: str
    transform_run_id: str
    source_container: Path
    work_root: Path
    base_artifact_root: Path


@dataclass(frozen=True)
class TransformResult:
    inserted_rows: int
    rejected_rows: int = 0
    notes: str | None = None
    source_rows: int | None = None


Transformer = Callable[[TransformContext], TransformResult]
_REGISTRY: dict[tuple[str, str], tuple[str, Transformer]] = {}


def register_transformer(
    adapter_id: str,
    dataset_key: str,
    version: str,
    transformer: Transformer,
) -> None:
    """Register an explicit canonical transformer for one governed dataset."""

    _REGISTRY[(adapter_id, dataset_key)] = (version, transformer)


def get_transformer(adapter_id: str, dataset_key: str) -> tuple[str, Transformer] | None:
    return _REGISTRY.get((adapter_id, dataset_key))


def transformer_catalogue() -> list[dict]:
    return [
        {"adapter_id": adapter_id, "dataset_key": dataset_key, "transform_version": value[0]}
        for (adapter_id, dataset_key), value in sorted(_REGISTRY.items())
    ]


# Registration is deliberately explicit. Importing this module installs only
# transformer implementations that ship with, and are tested by, this release.
from . import aec_individual as _aec_individual  # noqa: E402,F401
from . import aec_house_summaries as _aec_house_summaries  # noqa: E402,F401
from . import aec_senate_summaries as _aec_senate_summaries  # noqa: E402,F401
from . import aec_senate_remaining as _aec_senate_remaining  # noqa: E402,F401
from . import aec_formal_preferences as _aec_formal_preferences  # noqa: E402,F401
