from __future__ import annotations

import ipaddress
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .strategy import canonical_json_hash


@dataclass(frozen=True)
class Stage5Config:
    raw: dict

    @property
    def hash(self) -> str:
        return canonical_json_hash(self.raw)


def load_config(path: str | Path) -> Stage5Config:
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    validate_config(raw)
    return Stage5Config(raw=raw)


def validate_config(raw: dict) -> None:
    required_sections = {"collector", "synchronisation", "database", "retention"}
    missing = required_sections - raw.keys()
    if missing:
        raise ValueError(f"missing sections: {', '.join(sorted(missing))}")
    collector = raw["collector"]
    parsed = urlparse(collector["base_url"])
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("collector.base_url must be an HTTPS URL")
    if parsed.hostname not in collector["approved_hosts"]:
        raise ValueError("collector base host must be approved")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ValueError("literal IP source hosts are not allowed")
    for key in ("request_timeout_seconds", "request_ceiling", "max_concurrency", "retry_ceiling", "page_size", "response_size_ceiling_bytes"):
        if int(collector[key]) < 1:
            raise ValueError(f"collector.{key} must be positive")
    if int(collector["max_concurrency"]) > 4:
        raise ValueError("collector.max_concurrency exceeds governed prototype ceiling")
    sync = raw["synchronisation"]
    if int(sync["overlap_hours"]) < 0:
        raise ValueError("synchronisation.overlap_hours cannot be negative")
    if int(sync["reconciliation_partition_count"]) < 1:
        raise ValueError("reconciliation_partition_count must be positive")
    if int(sync["disappearance_confirmation_runs"]) < 1:
        raise ValueError("disappearance_confirmation_runs must be positive")
    database = raw["database"]
    if int(database["required_postgresql_major"]) != 18:
        raise ValueError("Stage 5 requires PostgreSQL major version 18")
    if database["allow_production_targets"] is not False:
        raise ValueError("production database targets must remain disabled")
