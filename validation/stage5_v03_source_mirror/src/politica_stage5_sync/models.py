from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DiscoveryWindow:
    start: datetime
    end: datetime
    overlap_hours: int
    source_field: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "overlap_hours": self.overlap_hours,
            "source_field": self.source_field,
        }


@dataclass(frozen=True)
class Candidate:
    entity_type: str
    external_identifier: str
    source_url: str
    observed_fingerprint: str | None = None


@dataclass
class RunMetrics:
    discovered: int = 0
    fetched: int = 0
    unchanged: int = 0
    changed: int = 0
    created: int = 0
    superseded: int = 0
    failed: int = 0
    queued_for_review: int = 0

    def validate(self) -> None:
        for name, value in vars(self).items():
            if value < 0:
                raise ValueError(f"metric {name} cannot be negative")
        if self.fetched > self.discovered:
            raise ValueError("fetched cannot exceed discovered")

    def as_dict(self) -> dict[str, int]:
        self.validate()
        return dict(vars(self))


@dataclass(frozen=True)
class RunCheckpoint:
    run_id: str
    entity_set: str
    partition: int
    cursor: str | None
    completed_pages: int
    candidate_count: int
    updated_at: datetime
    configuration_hash: str
    contract_hash: str


@dataclass(frozen=True)
class ReconciliationResult:
    missing_from_source: tuple[str, ...] = field(default_factory=tuple)
    new_in_source: tuple[str, ...] = field(default_factory=tuple)
    common: tuple[str, ...] = field(default_factory=tuple)
