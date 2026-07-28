from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Iterable


SEGMENT_REQUEST_CEILING = 3_000
SEGMENT_RULESET_VERSION = "stage5-v0.6-segmented-reconciliation-1"


def load_runtime(path: Path):
    spec = importlib.util.spec_from_file_location("stage5_v06_full_reconciliation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load governed runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) + "\n")


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_deterministic_gzip_text(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.open("wb")
    zipped = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
    text = __import__("io").TextIOWrapper(zipped, encoding="utf-8", newline="\n")
    return raw, zipped, text


def collection_spec(module: Any, name: str):
    for spec in module.COLLECTIONS:
        if spec.name == name:
            return spec
    raise KeyError(f"unknown collection: {name}")


def collection_counts(evidence: Path) -> dict[str, int]:
    values = read_json(evidence / "counts" / "collection_counts.json")
    return {str(key): int(value) for key, value in values.items()}


def partition_plans(evidence: Path) -> dict[str, list[dict[str, Any]]]:
    return read_json(evidence / "partition_plan.json")


def configuration(evidence: Path) -> dict[str, Any]:
    return read_json(evidence / "reconciliation_configuration.json")


def page_count_for(expected_count: int, page_size: int) -> int:
    return math.ceil(expected_count / page_size)


def validate_page_sequence(rows: list[dict[str, Any]], *, allow_partial: bool, planned_pages: int) -> int:
    pages = [int(row["page"]) for row in rows]
    if len(pages) != len(set(pages)):
        raise RuntimeError("duplicate page number in retained page manifest")
    if pages != sorted(pages):
        raise RuntimeError("retained page manifest is not ordered by page")
    expected = list(range(len(pages)))
    if pages != expected:
        raise RuntimeError(f"retained page manifest has a gap or non-zero start: {pages[:3]} ... {pages[-3:] if pages else []}")
    if len(pages) > planned_pages:
        raise RuntimeError("retained page manifest exceeds the governed page plan")
    if not allow_partial and len(pages) != planned_pages:
        raise RuntimeError(f"retained page manifest is incomplete: {len(pages)} of {planned_pages}")
    return len(pages)


def all_request_indices(evidence: Path) -> list[int]:
    indices: list[int] = []
    for path in [
        evidence / "request_manifest.jsonl",
        evidence / "post_enumeration" / "request_manifest.jsonl",
    ]:
        for row in read_jsonl(path):
            if row.get("request_index") is not None:
                indices.append(int(row["request_index"]))
    for path in sorted((evidence / "collections").glob("*/page_manifest.jsonl")):
        for row in read_jsonl(path):
            if row.get("request_index") is not None:
                indices.append(int(row["request_index"]))
    for path in sorted((evidence / "contract").glob("*.request.json")):
        try:
            row = read_json(path)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("request_index") is not None:
            indices.append(int(row["request_index"]))
    return indices


def next_request_index(evidence: Path) -> int:
    indices = all_request_indices(evidence)
    if not indices:
        raise RuntimeError("no retained request index exists in the governed seed evidence")
    if len(indices) != len(set(indices)):
        duplicates = sorted({value for value in indices if indices.count(value) > 1})
        raise RuntimeError(f"duplicate retained request indices: {duplicates[:10]}")
    return max(indices) + 1


def assert_seed_compatible(module: Any, evidence: Path) -> None:
    config = configuration(evidence)
    if int(config["page_size"]) != int(module.PAGE_SIZE):
        raise RuntimeError("seed page size does not match the governed runtime")
    if config["approved_host"] != module.APPROVED_HOST:
        raise RuntimeError("seed approved host does not match the governed runtime")
    if config["ruleset_version"] != module.RULESET_VERSION:
        raise RuntimeError("seed ruleset version does not match the governed runtime")
    if int(config["max_concurrency"]) != 1:
        raise RuntimeError("seed concurrency is not one")
    if (evidence / "final_reconciliation_watermark.json").exists():
        raise RuntimeError("seed already owns a final reconciliation watermark")
    for checkpoint in (evidence / "collections").glob("*/checkpoint.json"):
        row = read_json(checkpoint)
        if row.get("final_watermark_committed") is True:
            raise RuntimeError(f"partial checkpoint improperly owns a final watermark: {checkpoint}")
        if row.get("disappearance_threshold_advanced") is True:
            raise RuntimeError(f"partial checkpoint advanced disappearance state: {checkpoint}")


def retained_state(module: Any, evidence: Path, spec: Any, expected_count: int) -> dict[str, Any]:
    root = evidence / "collections" / spec.name
    rows = read_jsonl(root / "page_manifest.jsonl")
    planned_pages = page_count_for(expected_count, module.PAGE_SIZE)
    completed_pages = validate_page_sequence(rows, allow_partial=True, planned_pages=planned_pages)

    seen: set[str] = set()
    seen_external: set[str] = set()
    rows_seen = 0
    duplicate_observation_count = 0
    duplicate_external_identifier_count = 0
    duplicate_source_record_count = 0
    null_external_identifier_count = 0
    page_body_bytes = 0
    boundary_ties = 0
    previous_last_order = None
    previous_identity = None
    previous_external_identifier = None
    previous_locator = None

    for record in rows:
        body_file = record.get("body_file")
        if not body_file:
            raise RuntimeError(f"missing retained body for {spec.name} page {record.get('page')}")
        body_path = root / body_file
        body = module.gzip_read(body_path)
        if module.sha256_bytes(body) != record["response_sha256"]:
            raise RuntimeError(f"altered retained response: {body_path}")
        page_body_bytes += len(body)
        payload = json.loads(body.decode("utf-8"))
        values = payload.get("value")
        if not isinstance(values, list) or len(values) != int(record["row_count"]):
            raise RuntimeError(f"retained response shape changed: {body_path}")
        page_index = int(record["page"])
        expected_page_count = min(module.PAGE_SIZE, expected_count - page_index * module.PAGE_SIZE)
        if len(values) != expected_page_count:
            raise RuntimeError(
                f"{spec.name} retained page {page_index} has {len(values)} rows; expected {expected_page_count}"
            )
        current_orders = [module.order_key(spec, row) for row in values]
        if current_orders != sorted(current_orders):
            raise RuntimeError(f"{spec.name} retained page {page_index} is not ordered")
        if previous_last_order is not None and current_orders:
            if previous_last_order > current_orders[0]:
                raise RuntimeError(f"{spec.name} retained boundary is not monotonic at page {page_index}")
            if previous_last_order == current_orders[0]:
                boundary_ties += 1
        if current_orders:
            previous_last_order = current_orders[-1]

        page_duplicate_records = 0
        for row_index, row in enumerate(values):
            identity = module.observation_identity(spec, row)
            external_value = row.get(spec.external_identifier_field) if spec.external_identifier_field else None
            external_identifier = str(external_value) if external_value is not None else None
            identity_duplicate = identity in seen
            external_duplicate = external_identifier is not None and external_identifier in seen_external
            if identity_duplicate:
                duplicate_observation_count += 1
            if external_duplicate:
                duplicate_external_identifier_count += 1
            if identity_duplicate or external_duplicate:
                duplicate_source_record_count += 1
                page_duplicate_records += 1
            if not identity_duplicate:
                seen.add(identity)
                if external_identifier is None and spec.external_identifier_field:
                    null_external_identifier_count += 1
            if external_identifier is not None and not external_duplicate:
                seen_external.add(external_identifier)
            previous_identity = identity
            previous_external_identifier = external_identifier
            previous_locator = {"page": page_index, "row_index": row_index}
        if int(record.get("duplicate_source_record_count", 0)) != page_duplicate_records:
            raise RuntimeError(f"{spec.name} retained duplicate count changed at page {page_index}")
        rows_seen += len(values)

    return {
        "manifest_rows": rows,
        "completed_pages": completed_pages,
        "planned_pages": planned_pages,
        "seen": seen,
        "seen_external": seen_external,
        "rows_seen": rows_seen,
        "duplicate_observation_count": duplicate_observation_count,
        "duplicate_external_identifier_count": duplicate_external_identifier_count,
        "duplicate_source_record_count": duplicate_source_record_count,
        "null_external_identifier_count": null_external_identifier_count,
        "page_body_bytes": page_body_bytes,
        "boundary_ties": boundary_ties,
        "previous_last_order": previous_last_order,
        "previous_identity": previous_identity,
        "previous_external_identifier": previous_external_identifier,
        "previous_locator": previous_locator,
    }


def fetch_segment(args: argparse.Namespace) -> None:
    module = load_runtime(args.runtime)
    evidence = args.evidence.resolve()
    assert_seed_compatible(module, evidence)
    counts = collection_counts(evidence)
    plans = partition_plans(evidence)
    spec = collection_spec(module, args.collection)
    expected_count = counts[spec.name]
    state = retained_state(module, evidence, spec, expected_count)
    next_page = int(state["completed_pages"])
    if args.start_page != next_page:
        raise RuntimeError(
            f"{spec.name} segment must start at exact next page {next_page}, not {args.start_page}"
        )
    if args.end_page <= args.start_page or args.end_page > state["planned_pages"]:
        raise RuntimeError("segment end is outside the governed collection page plan")
    request_count = args.end_page - args.start_page
    if request_count > SEGMENT_REQUEST_CEILING:
        raise RuntimeError(
            f"segment contains {request_count} requests; ceiling is {SEGMENT_REQUEST_CEILING}"
        )

    collection_root = evidence / "collections" / spec.name
    manifest_path = collection_root / "page_manifest.jsonl"
    duplicate_path = collection_root / "duplicate_source_observations.jsonl"
    checkpoint_path = collection_root / "checkpoint.json"
    request_index = next_request_index(evidence)
    start_request_index = request_index
    seen = state["seen"]
    seen_external = state["seen_external"]
    rows_seen = int(state["rows_seen"])
    duplicate_observation_count = int(state["duplicate_observation_count"])
    duplicate_external_identifier_count = int(state["duplicate_external_identifier_count"])
    duplicate_source_record_count = int(state["duplicate_source_record_count"])
    previous_last_order = state["previous_last_order"]
    previous_identity = state["previous_identity"]
    previous_external_identifier = state["previous_external_identifier"]
    previous_locator = state["previous_locator"]
    boundary_ties = int(state["boundary_ties"])

    for page_index in range(args.start_page, args.end_page):
        skip = page_index * module.PAGE_SIZE
        expected_page_count = min(module.PAGE_SIZE, expected_count - skip)
        params = {
            "$select": ",".join(spec.select_fields),
            "$orderby": ",".join(spec.order_fields),
            "$top": module.PAGE_SIZE,
            "$skip": skip,
        }
        record = module.request_get(
            url=module.safe_url(f"/v1/{spec.name}", params),
            accept="application/json",
            request_index=request_index,
            evidence_root=collection_root,
            label=f"page_{page_index:06d}",
        )
        request_index += 1
        body = record.pop("body")
        payload = json.loads(body.decode("utf-8"))
        values = payload.get("value")
        if not isinstance(values, list):
            raise RuntimeError(f"{spec.name} page {page_index} has no OData value array")
        if len(values) != expected_page_count:
            raise RuntimeError(
                f"{spec.name} page {page_index} returned {len(values)} rows; expected {expected_page_count}"
            )
        current_orders = [module.order_key(spec, row) for row in values]
        if current_orders != sorted(current_orders):
            raise RuntimeError(f"{spec.name} page {page_index} is not ordered as requested")
        if previous_last_order is not None and current_orders:
            if previous_last_order > current_orders[0]:
                raise RuntimeError(f"{spec.name} page boundary is not monotonic at page {page_index}")
            if previous_last_order == current_orders[0]:
                boundary_ties += 1
        if current_orders:
            previous_last_order = current_orders[-1]

        partition = module.page_partition(plans[spec.name], page_index)
        page_identities: list[str] = []
        page_duplicate_records = 0
        for row_index, row in enumerate(values):
            identity = module.observation_identity(spec, row)
            page_identities.append(identity)
            external_value = row.get(spec.external_identifier_field) if spec.external_identifier_field else None
            external_identifier = str(external_value) if external_value is not None else None
            identity_duplicate = identity in seen
            external_duplicate = external_identifier is not None and external_identifier in seen_external
            if identity_duplicate:
                duplicate_observation_count += 1
            if external_duplicate:
                duplicate_external_identifier_count += 1
            if identity_duplicate or external_duplicate:
                duplicate_source_record_count += 1
                page_duplicate_records += 1
                classifications = ["duplicate_source_observation"]
                if identity_duplicate:
                    classifications.append("duplicate_observation_identity")
                if external_duplicate:
                    classifications.append("duplicate_authoritative_external_identifier")
                duplicate_row = {
                    "source_system": "Federal Register of Legislation public API",
                    "collection": spec.name,
                    "source_namespace": spec.identifier_type_code,
                    "external_identifier": external_identifier,
                    "observation_identity": identity,
                    "classification": classifications,
                    "page": page_index,
                    "row_index": row_index,
                    "partition": partition,
                    "selected_row_sha256": module.canonical_json_hash(row),
                    "adjacent_to_previous_observation": identity == previous_identity,
                    "adjacent_to_previous_external_identifier": (
                        external_identifier is not None
                        and external_identifier == previous_external_identifier
                    ),
                    "previous_locator": previous_locator
                    if identity == previous_identity
                    or (
                        external_identifier is not None
                        and external_identifier == previous_external_identifier
                    )
                    else None,
                    "recommended_action": "retain_source_anomaly_and_deduplicate_candidate_identity_without_altering_identifier",
                    "automatic_remediation_permitted": False,
                    "review_status": "advisory_review_open_non_blocking",
                    "ruleset_version": module.RULESET_VERSION,
                }
                append_jsonl(duplicate_path, duplicate_row)
            if not identity_duplicate:
                seen.add(identity)
            if external_identifier is not None and not external_duplicate:
                seen_external.add(external_identifier)
            previous_identity = identity
            previous_external_identifier = external_identifier
            previous_locator = {"page": page_index, "row_index": row_index}

        rows_seen += len(values)
        record.update(
            {
                "collection": spec.name,
                "page": page_index,
                "partition": partition,
                "skip": skip,
                "top": module.PAGE_SIZE,
                "row_count": len(values),
                "duplicate_source_record_count": page_duplicate_records,
                "first_observation_identity": page_identities[0] if page_identities else None,
                "last_observation_identity": page_identities[-1] if page_identities else None,
                "segmented_reconciliation_ruleset": SEGMENT_RULESET_VERSION,
            }
        )
        append_jsonl(manifest_path, record)
        write_json(
            checkpoint_path,
            {
                "status": "in_progress",
                "collection": spec.name,
                "completed_page": page_index,
                "completed_pages": page_index + 1,
                "planned_pages": state["planned_pages"],
                "rows_seen": rows_seen,
                "unique_observations": len(seen),
                "duplicate_source_records": duplicate_source_record_count,
                "partition": partition,
                "configuration_hash": configuration(evidence).get("configuration_sha256"),
                "contract_hash": module.EXPECTED_NORMALISED_OPENAPI_SHA256,
                "segment_ruleset_version": SEGMENT_RULESET_VERSION,
                "final_watermark_committed": False,
                "disappearance_threshold_advanced": False,
            },
        )
        time.sleep(module.REQUEST_DELAY_SECONDS)

    segment_result = {
        "status": "passed",
        "collection": spec.name,
        "start_page": args.start_page,
        "end_page_exclusive": args.end_page,
        "pages_fetched": request_count,
        "rows_seen_total": rows_seen,
        "completed_pages_total": args.end_page,
        "planned_pages": state["planned_pages"],
        "start_request_index": start_request_index,
        "next_request_index": request_index,
        "final_watermark_committed": False,
        "disappearance_threshold_advanced": False,
        "segment_ruleset_version": SEGMENT_RULESET_VERSION,
    }
    write_json(
        evidence / "segments" / f"{spec.name}_{args.start_page:06d}_{args.end_page:06d}.json",
        segment_result,
    )
    print(json.dumps(segment_result, indent=2, sort_keys=True))


def rebuild_collection(args: argparse.Namespace) -> None:
    module = load_runtime(args.runtime)
    evidence = args.evidence.resolve()
    assert_seed_compatible(module, evidence)
    counts = collection_counts(evidence)
    plans = partition_plans(evidence)
    spec = collection_spec(module, args.collection)
    expected_count = counts[spec.name]
    collection_root = evidence / "collections" / spec.name
    manifest_rows = read_jsonl(collection_root / "page_manifest.jsonl")
    planned_pages = page_count_for(expected_count, module.PAGE_SIZE)
    validate_page_sequence(manifest_rows, allow_partial=False, planned_pages=planned_pages)

    source_set_path = collection_root / "source_observation_set.txt.gz"
    source_external_path = collection_root / "source_external_identifier_set.txt.gz"
    source_non_external_path = collection_root / "source_non_external_observation_set.txt.gz"
    duplicate_report_path = collection_root / "duplicate_source_observations.jsonl"
    for path in (source_set_path, source_external_path, source_non_external_path, duplicate_report_path):
        if path.exists():
            path.unlink()

    seen: set[str] = set()
    seen_external: set[str] = set()
    source_digest = hashlib.sha256()
    external_digest = hashlib.sha256()
    non_external_digest = hashlib.sha256()
    duplicate_digest = hashlib.sha256()
    rows_seen = 0
    duplicate_observation_count = 0
    duplicate_external_identifier_count = 0
    duplicate_source_record_count = 0
    null_external_identifier_count = 0
    non_external_count = 0
    page_body_bytes = 0
    boundary_ties = 0
    previous_last_order = None
    previous_identity = None
    previous_external_identifier = None
    previous_locator = None

    source_raw, source_zip, source_text = open_deterministic_gzip_text(source_set_path)
    ext_raw, ext_zip, ext_text = open_deterministic_gzip_text(source_external_path)
    non_raw, non_zip, non_text = open_deterministic_gzip_text(source_non_external_path)
    duplicate_handle = duplicate_report_path.open("w", encoding="utf-8")
    try:
        for record in manifest_rows:
            page_index = int(record["page"])
            body_file = record.get("body_file")
            if not body_file:
                raise RuntimeError(f"missing retained body for {spec.name} page {page_index}")
            body_path = collection_root / body_file
            body = module.gzip_read(body_path)
            if module.sha256_bytes(body) != record["response_sha256"]:
                raise RuntimeError(f"altered retained response: {body_path}")
            page_body_bytes += len(body)
            payload = json.loads(body.decode("utf-8"))
            values = payload.get("value")
            expected_page_count = min(module.PAGE_SIZE, expected_count - page_index * module.PAGE_SIZE)
            if not isinstance(values, list) or len(values) != expected_page_count:
                raise RuntimeError(f"{spec.name} retained page {page_index} shape or count changed")
            current_orders = [module.order_key(spec, row) for row in values]
            if current_orders != sorted(current_orders):
                raise RuntimeError(f"{spec.name} retained page {page_index} is not ordered")
            if previous_last_order is not None and current_orders:
                if previous_last_order > current_orders[0]:
                    raise RuntimeError(f"{spec.name} retained boundary is not monotonic at page {page_index}")
                if previous_last_order == current_orders[0]:
                    boundary_ties += 1
            if current_orders:
                previous_last_order = current_orders[-1]

            partition = module.page_partition(plans[spec.name], page_index)
            page_duplicate_records = 0
            for row_index, row in enumerate(values):
                identity = module.observation_identity(spec, row)
                external_value = row.get(spec.external_identifier_field) if spec.external_identifier_field else None
                external_identifier = str(external_value) if external_value is not None else None
                identity_duplicate = identity in seen
                external_duplicate = external_identifier is not None and external_identifier in seen_external
                if identity_duplicate:
                    duplicate_observation_count += 1
                if external_duplicate:
                    duplicate_external_identifier_count += 1
                if identity_duplicate or external_duplicate:
                    duplicate_source_record_count += 1
                    page_duplicate_records += 1
                    classifications = ["duplicate_source_observation"]
                    if identity_duplicate:
                        classifications.append("duplicate_observation_identity")
                    if external_duplicate:
                        classifications.append("duplicate_authoritative_external_identifier")
                    duplicate_row = {
                        "source_system": "Federal Register of Legislation public API",
                        "collection": spec.name,
                        "source_namespace": spec.identifier_type_code,
                        "external_identifier": external_identifier,
                        "observation_identity": identity,
                        "classification": classifications,
                        "page": page_index,
                        "row_index": row_index,
                        "partition": partition,
                        "selected_row_sha256": module.canonical_json_hash(row),
                        "adjacent_to_previous_observation": identity == previous_identity,
                        "adjacent_to_previous_external_identifier": (
                            external_identifier is not None
                            and external_identifier == previous_external_identifier
                        ),
                        "previous_locator": previous_locator
                        if identity == previous_identity
                        or (
                            external_identifier is not None
                            and external_identifier == previous_external_identifier
                        )
                        else None,
                        "recommended_action": "retain_source_anomaly_and_deduplicate_candidate_identity_without_altering_identifier",
                        "automatic_remediation_permitted": False,
                        "review_status": "advisory_review_open_non_blocking",
                        "ruleset_version": module.RULESET_VERSION,
                    }
                    line = json.dumps(duplicate_row, sort_keys=True) + "\n"
                    duplicate_handle.write(line)
                    duplicate_digest.update(line.encode("utf-8"))
                if not identity_duplicate:
                    seen.add(identity)
                    line = identity + "\n"
                    source_text.write(line)
                    source_digest.update(line.encode("utf-8"))
                    if external_identifier is None:
                        if spec.external_identifier_field:
                            null_external_identifier_count += 1
                        non_external_count += 1
                        non_text.write(line)
                        non_external_digest.update(line.encode("utf-8"))
                if external_identifier is not None and not external_duplicate:
                    seen_external.add(external_identifier)
                    line = external_identifier + "\n"
                    ext_text.write(line)
                    external_digest.update(line.encode("utf-8"))
                previous_identity = identity
                previous_external_identifier = external_identifier
                previous_locator = {"page": page_index, "row_index": row_index}
            if int(record.get("duplicate_source_record_count", 0)) != page_duplicate_records:
                raise RuntimeError(f"{spec.name} duplicate accounting changed at page {page_index}")
            rows_seen += len(values)
    finally:
        for text, zipped, raw in [
            (source_text, source_zip, source_raw),
            (ext_text, ext_zip, ext_raw),
            (non_text, non_zip, non_raw),
        ]:
            text.flush()
            text.close()
            zipped.close()
            raw.close()
        duplicate_handle.flush()
        duplicate_handle.close()

    if rows_seen != expected_count:
        raise RuntimeError(f"{spec.name} row total {rows_seen} does not match count {expected_count}")
    if len(seen) + duplicate_observation_count != rows_seen:
        raise RuntimeError(f"{spec.name} duplicate accounting does not reconcile")

    result = {
        "status": "passed",
        "collection": spec.name,
        "reconciliation_mode": spec.reconciliation_mode,
        "expected_count": expected_count,
        "page_size": module.PAGE_SIZE,
        "page_count": planned_pages,
        "partition_count": len(plans[spec.name]),
        "rows_seen": rows_seen,
        "unique_observation_count": len(seen),
        "duplicate_observation_count": duplicate_observation_count,
        "duplicate_authoritative_external_identifier_count": duplicate_external_identifier_count,
        "duplicate_source_record_count": duplicate_source_record_count,
        "duplicate_source_observation_report_sha256": sha256_file(duplicate_report_path),
        "duplicate_source_observation_stream_sha256": duplicate_digest.hexdigest(),
        "authoritative_external_identifier_count": len(seen_external),
        "null_external_identifier_count": null_external_identifier_count,
        "boundary_tie_count": boundary_ties,
        "source_observation_stream_sha256": source_digest.hexdigest(),
        "source_external_identifier_stream_sha256": external_digest.hexdigest(),
        "source_non_external_observation_count": non_external_count,
        "source_non_external_observation_stream_sha256": non_external_digest.hexdigest(),
        "source_observation_set_file_sha256": sha256_file(source_set_path),
        "source_external_identifier_set_file_sha256": sha256_file(source_external_path),
        "source_non_external_observation_set_file_sha256": sha256_file(source_non_external_path),
        "retained_page_body_bytes": page_body_bytes,
        "all_pages_complete": True,
        "all_partitions_complete": True,
        "final_watermark_committed": False,
        "disappearance_threshold_advanced": False,
        "segmented_reconciliation_ruleset": SEGMENT_RULESET_VERSION,
    }
    write_json(collection_root / "source_enumeration_result.json", result)
    write_json(
        collection_root / "checkpoint.json",
        {
            **result,
            "status": "succeeded",
            "configuration_hash": configuration(evidence).get("configuration_sha256"),
            "contract_hash": module.EXPECTED_NORMALISED_OPENAPI_SHA256,
            "final_reconciliation_watermark_eligible": True,
            "final_watermark_committed": False,
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True))


def finalize(args: argparse.Namespace) -> None:
    module = load_runtime(args.runtime)
    evidence = args.evidence.resolve()
    assert_seed_compatible(module, evidence)
    counts = collection_counts(evidence)
    source_results = {}
    for spec in module.COLLECTIONS:
        result_path = evidence / "collections" / spec.name / "source_enumeration_result.json"
        if not result_path.exists():
            raise RuntimeError(f"collection has not been rebuilt: {spec.name}")
        source_results[spec.name] = read_json(result_path)
        if source_results[spec.name].get("status") != "passed":
            raise RuntimeError(f"collection result is not passed: {spec.name}")

    request_index = next_request_index(evidence)
    post_counts, post_requests, request_index = module.fetch_counts(
        evidence / "post_enumeration", request_index
    )
    post_root = evidence / "post_enumeration"
    (post_root / "request_manifest.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in post_requests),
        encoding="utf-8",
    )
    count_result = {
        "status": "passed" if post_counts == counts else "failed",
        "initial_counts": counts,
        "post_enumeration_counts": post_counts,
        "counts_unchanged": post_counts == counts,
    }
    write_json(post_root / "count_stability.json", count_result)
    if post_counts != counts:
        raise RuntimeError("Federal Register collection counts changed during segmented reconciliation")

    replay_results = {
        spec.name: module.replay_collection(spec, evidence, counts[spec.name])
        for spec in module.COLLECTIONS
    }
    differences = module.classify_differences(
        evidence_root=evidence,
        canonical_tsv=args.canonical_tsv,
        canonical_versions_tsv=args.canonical_versions_tsv,
        canonical_documents_tsv=args.canonical_documents_tsv,
        canonical_text_applies_tsv=args.canonical_text_applies_tsv,
    )
    complete_manifest = module.build_complete_request_manifest(evidence)
    if complete_manifest["request_count"] != request_index:
        raise RuntimeError(
            f"complete request manifest count {complete_manifest['request_count']} "
            f"does not match executed requests {request_index}"
        )
    config = configuration(evidence)
    if request_index != int(config["planned_requests"]):
        raise RuntimeError(
            f"actual request count {request_index} does not equal planned count {config['planned_requests']}"
        )
    contract = read_json(evidence / "contract" / "contract_comparison.json")
    final = module.semantic_result(
        contract=contract,
        counts=counts,
        source_results=source_results,
        replay_results=replay_results,
        differences=differences,
        request_count=request_index,
    )
    segmented_result = {
        "status": "passed",
        "ruleset_version": SEGMENT_RULESET_VERSION,
        "segments": sorted(
            (read_json(path) for path in (evidence / "segments").glob("*.json")),
            key=lambda row: (row["collection"], row["start_page"]),
        ),
        "partial_or_failed_segment_committed_final_watermark": False,
        "partial_or_failed_segment_advanced_disappearance_threshold": False,
        "semantic_reconciliation_result_sha256": final["semantic_result_sha256"],
    }
    write_json(evidence / "segmented_execution_result.json", segmented_result)
    write_json(evidence / "semantic_reconciliation_result.json", final)
    write_json(
        evidence / "final_reconciliation_watermark.json",
        {
            "status": "committed",
            "ruleset_version": module.RULESET_VERSION,
            "segmented_execution_ruleset_version": SEGMENT_RULESET_VERSION,
            "configuration_sha256": final["configuration_sha256"],
            "contract_sha256": final["contract"]["current_normalised_openapi_sha256"],
            "completed_pages": final["metrics"]["completed_pages"],
            "completed_partitions": final["metrics"]["completed_partitions"],
            "source_counts": counts,
            "failed": 0,
            "all_pages_complete": True,
            "all_partitions_complete": True,
            "disappearance_threshold_advanced": False,
        },
    )
    (evidence / "completion_marker.txt").write_text(
        "STAGE5_V0_6_FULL_RECONCILIATION_COMPLETED\n", encoding="utf-8"
    )
    print(json.dumps(final, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch-segment")
    fetch.add_argument("--evidence", type=Path, required=True)
    fetch.add_argument("--collection", required=True)
    fetch.add_argument("--start-page", type=int, required=True)
    fetch.add_argument("--end-page", type=int, required=True)
    fetch.set_defaults(func=fetch_segment)

    rebuild = sub.add_parser("rebuild-collection")
    rebuild.add_argument("--evidence", type=Path, required=True)
    rebuild.add_argument("--collection", required=True)
    rebuild.set_defaults(func=rebuild_collection)

    final = sub.add_parser("finalize")
    final.add_argument("--evidence", type=Path, required=True)
    final.add_argument("--canonical-tsv", type=Path, required=True)
    final.add_argument("--canonical-versions-tsv", type=Path, required=True)
    final.add_argument("--canonical-documents-tsv", type=Path, required=True)
    final.add_argument("--canonical-text-applies-tsv", type=Path, required=True)
    final.set_defaults(func=finalize)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
