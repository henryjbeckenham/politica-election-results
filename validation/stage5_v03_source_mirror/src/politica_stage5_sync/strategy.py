from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from .models import Candidate, DiscoveryWindow, ReconciliationResult, RunCheckpoint


READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_read_only_request(method: str, url: str, approved_hosts: Iterable[str]) -> None:
    normalised = method.upper()
    if normalised not in READ_ONLY_METHODS:
        raise ValueError(f"method {normalised} is not allowed")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("only HTTPS source requests are allowed")
    allowed = {host.lower() for host in approved_hosts}
    if not parsed.hostname or parsed.hostname.lower() not in allowed:
        raise ValueError(f"host {parsed.hostname!r} is not approved")


def build_discovery_window(
    previous_successful_end: datetime | None,
    now: datetime,
    overlap_hours: int,
    source_field: str,
) -> DiscoveryWindow:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if previous_successful_end is not None and previous_successful_end.tzinfo is None:
        raise ValueError("previous_successful_end must be timezone-aware")
    if overlap_hours < 0:
        raise ValueError("overlap_hours cannot be negative")
    if previous_successful_end is None:
        start = now - timedelta(hours=overlap_hours)
    else:
        start = previous_successful_end - timedelta(hours=overlap_hours)
    if start > now:
        raise ValueError("window start cannot be after end")
    return DiscoveryWindow(start=start, end=now, overlap_hours=overlap_hours, source_field=source_field)


def candidate_identity(candidate: Candidate) -> str:
    if not candidate.external_identifier.strip():
        raise ValueError("candidate external identifier is required")
    payload = {
        "entity_type": candidate.entity_type,
        "external_identifier": candidate.external_identifier,
        "source_url": candidate.source_url,
    }
    return canonical_json_hash(payload)


def normalise_openapi_contract(value: Any) -> Any:
    """Remove only generated example values before structural comparison."""
    if isinstance(value, dict):
        return {
            key: normalise_openapi_contract(item)
            for key, item in sorted(value.items())
            if key != "example"
        }
    if isinstance(value, list):
        return [normalise_openapi_contract(item) for item in value]
    return value


def _count_example_differences(previous: Any, current: Any) -> int:
    if isinstance(previous, dict) and isinstance(current, dict):
        count = 0
        if previous.get("example") != current.get("example") and (
            "example" in previous or "example" in current
        ):
            count += 1
        for key in set(previous) | set(current):
            if key == "example":
                continue
            count += _count_example_differences(previous.get(key), current.get(key))
        return count
    if isinstance(previous, list) and isinstance(current, list):
        return sum(
            _count_example_differences(left, right)
            for left, right in zip(previous, current)
        )
    return 0


def _structural_differences(previous: Any, current: Any, path: str = "$") -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    if isinstance(previous, dict) and isinstance(current, dict):
        previous_keys = set(previous)
        current_keys = set(current)
        for key in sorted(previous_keys - current_keys):
            differences.append({"kind": "removed", "path": f"{path}.{key}"})
        for key in sorted(current_keys - previous_keys):
            differences.append({"kind": "added", "path": f"{path}.{key}"})
        for key in sorted(previous_keys & current_keys):
            differences.extend(
                _structural_differences(previous[key], current[key], f"{path}.{key}")
            )
        return differences
    if isinstance(previous, list) and isinstance(current, list):
        if previous != current:
            differences.append(
                {
                    "kind": "changed",
                    "path": path,
                    "previous": previous,
                    "current": current,
                }
            )
        return differences
    if previous != current:
        differences.append(
            {
                "kind": "changed",
                "path": path,
                "previous": previous,
                "current": current,
            }
        )
    return differences


def classify_contract_change(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    previous_paths = set(previous.get("paths", {}))
    current_paths = set(current.get("paths", {}))
    previous_schemas = set(previous.get("components", {}).get("schemas", {}))
    current_schemas = set(current.get("components", {}).get("schemas", {}))
    removed_paths = sorted(previous_paths - current_paths)
    removed_schemas = sorted(previous_schemas - current_schemas)
    added_paths = sorted(current_paths - previous_paths)
    added_schemas = sorted(current_schemas - previous_schemas)

    previous_normalised = normalise_openapi_contract(previous)
    current_normalised = normalise_openapi_contract(current)
    structural_differences = _structural_differences(
        previous_normalised, current_normalised
    )
    incompatible = bool(
        removed_paths
        or removed_schemas
        or any(item["kind"] in {"removed", "changed"} for item in structural_differences)
    )
    if not structural_differences:
        classification = "unchanged"
    elif incompatible:
        classification = "incompatible"
    else:
        classification = "compatible_change"
    return {
        "previous_hash": canonical_json_hash(previous),
        "current_hash": canonical_json_hash(current),
        "previous_normalised_hash": canonical_json_hash(previous_normalised),
        "current_normalised_hash": canonical_json_hash(current_normalised),
        "example_difference_count": _count_example_differences(previous, current),
        "structural_difference_count": len(structural_differences),
        "structural_differences": structural_differences[:200],
        "added_paths": added_paths,
        "removed_paths": removed_paths,
        "added_schemas": added_schemas,
        "removed_schemas": removed_schemas,
        "classification": classification,
        "change_scope": (
            "none"
            if previous == current
            else "generated_examples_only"
            if not structural_differences
            else "structural"
        ),
        "block_affected_normalisation": incompatible,
    }


def should_retry(status_code: int | None, attempt: int, retry_ceiling: int) -> bool:
    if attempt >= retry_ceiling:
        return False
    return status_code is None or status_code in TRANSIENT_HTTP_STATUSES


def safe_output_watermark(
    *,
    run_status: str,
    all_partitions_complete: bool,
    all_pages_complete: bool,
    unresolved_fatal_failures: int,
    proposed_watermark: dict[str, Any],
) -> dict[str, Any]:
    if run_status != "succeeded":
        raise ValueError("watermark cannot be committed for a non-successful run")
    if not all_partitions_complete or not all_pages_complete:
        raise ValueError("watermark cannot be committed before all pages and partitions complete")
    if unresolved_fatal_failures:
        raise ValueError("watermark cannot be committed while fatal failures remain")
    if not proposed_watermark:
        raise ValueError("watermark cannot be empty")
    return proposed_watermark


def validate_checkpoint(
    checkpoint: RunCheckpoint,
    *,
    expected_configuration_hash: str,
    expected_contract_hash: str,
    partition_count: int,
) -> None:
    if checkpoint.configuration_hash != expected_configuration_hash:
        raise ValueError("checkpoint configuration hash does not match")
    if checkpoint.contract_hash != expected_contract_hash:
        raise ValueError("checkpoint contract hash does not match")
    if checkpoint.partition < 0 or checkpoint.partition >= partition_count:
        raise ValueError("checkpoint partition is outside configured range")
    if checkpoint.completed_pages < 0 or checkpoint.candidate_count < 0:
        raise ValueError("checkpoint counters cannot be negative")


def reconcile_identifier_sets(source_ids: Iterable[str], canonical_ids: Iterable[str]) -> ReconciliationResult:
    source = {value for value in source_ids if value}
    canonical = {value for value in canonical_ids if value}
    return ReconciliationResult(
        missing_from_source=tuple(sorted(canonical - source)),
        new_in_source=tuple(sorted(source - canonical)),
        common=tuple(sorted(source & canonical)),
    )


def disappearance_action(consecutive_missing_runs: int, confirmation_runs: int) -> str:
    if confirmation_runs < 1:
        raise ValueError("confirmation_runs must be at least one")
    if consecutive_missing_runs < 1:
        return "no_action"
    if consecutive_missing_runs < confirmation_runs:
        return "record_observation"
    return "create_review_case_and_change_event"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
