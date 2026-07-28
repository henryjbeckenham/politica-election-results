from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


APPROVED_HOST = "api.prod.legislation.gov.au"
BASE = f"https://{APPROVED_HOST}"
PAGE_SIZE = 100
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 4
REQUEST_DELAY_SECONDS = 0.5
MAX_PAGES_PER_PARTITION = 100
RULESET_VERSION = "stage5-v0.6-document-prefix-traversal-1"
IDENTITY_FIELDS = [
    "titleId",
    "start",
    "retrospectiveStart",
    "rectificationVersionNumber",
    "type",
    "uniqueTypeNumber",
    "volumeNumber",
    "format",
]


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def gzip_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(body)


def normalized_order_value(value: Any) -> tuple[int, Any]:
    return (0, "") if value is None else (1, value)


def order_key(row: dict[str, Any], fields: list[str]) -> tuple[Any, ...]:
    return tuple(normalized_order_value(row.get(field)) for field in fields)


def observation_identity(row: dict[str, Any]) -> str:
    selected = {field: row.get(field) for field in IDENTITY_FIELDS}
    return sha256_bytes(canonical_json(selected))


def safe_url(filter_expression: str, select_fields: list[str], order_fields: list[str], skip: int) -> str:
    if len(order_fields) > 5:
        raise RuntimeError("Document order field count exceeds the tested source maximum of five")
    params = {
        "$filter": filter_expression,
        "$select": ",".join(select_fields),
        "$orderby": ",".join(order_fields),
        "$top": PAGE_SIZE,
        "$skip": skip,
    }
    url = BASE + "/v1/Documents?" + urllib.parse.urlencode(params)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != APPROVED_HOST:
        raise RuntimeError(f"unapproved request: {url}")
    return url


def fetch_page(
    *,
    output: Path,
    partition_index: int,
    page_index: int,
    request_index: int,
    filter_expression: str,
    select_fields: list[str],
    order_fields: list[str],
) -> tuple[dict[str, Any], bytes]:
    url = safe_url(filter_expression, select_fields, order_fields, page_index * PAGE_SIZE)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "Politica-Stage5-v0.6-document-prefix-traversal/1.0",
        },
    )
    body = b""
    headers: dict[str, str] = {}
    status: int | None = None
    final_url = url
    last_error: str | None = None
    started = time.monotonic()
    attempts = 0
    for attempt in range(MAX_ATTEMPTS):
        attempts = attempt + 1
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("Document page response size ceiling exceeded")
                status = int(response.status)
                headers = dict(response.headers.items())
                final_url = response.geturl()
            if status != 200:
                raise RuntimeError(f"unexpected HTTP {status}")
            last_error = None
            break
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            headers = dict(exc.headers.items()) if exc.headers else {}
            body = exc.read(MAX_RESPONSE_BYTES + 1)
            last_error = f"HTTP {exc.code}: {exc.reason}"
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < MAX_ATTEMPTS:
            time.sleep(2**attempt)
    duration_ms = int((time.monotonic() - started) * 1000)
    digest = sha256_bytes(body)
    raw = output / "raw"
    body_path = raw / f"page_{page_index:04d}_{digest[:12]}.body.gz"
    headers_path = raw / f"page_{page_index:04d}_{digest[:12]}.headers.json"
    gzip_write(body_path, body)
    write_json(headers_path, headers)
    if last_error is not None:
        write_json(
            output / "failure.json",
            {
                "status": "failed",
                "partition_index": partition_index,
                "page": page_index,
                "request_index": request_index,
                "requested_url": url,
                "attempts": attempts,
                "error": last_error,
                "response_sha256": digest,
                "byte_count": len(body),
                "final_watermark_committed": False,
                "disappearance_threshold_advanced": False,
            },
        )
        raise RuntimeError(last_error)
    record = {
        "partition_index": partition_index,
        "page": page_index,
        "request_index": request_index,
        "method": "GET",
        "requested_url": url,
        "final_url": final_url,
        "request_body": None,
        "status": status,
        "attempts": attempts,
        "duration_ms": duration_ms,
        "response_sha256": digest,
        "byte_count": len(body),
        "body_file": str(body_path.relative_to(output)),
        "headers_file": str(headers_path.relative_to(output)),
    }
    return record, body


def validate_leaf(plan: dict[str, Any], partition_index: int) -> dict[str, Any]:
    if plan.get("status") != "passed":
        raise RuntimeError("Document partition plan is not passed")
    leaves = plan.get("leaves")
    if not isinstance(leaves, list) or partition_index < 0 or partition_index >= len(leaves):
        raise RuntimeError("Document partition index is outside the governed plan")
    leaf = leaves[partition_index]
    if int(leaf["partition_index"]) != partition_index:
        raise RuntimeError("Document partition index does not match plan position")
    if int(leaf["page_size"]) != PAGE_SIZE:
        raise RuntimeError("Document partition page size does not match governed runtime")
    if int(leaf["page_count"]) > MAX_PAGES_PER_PARTITION:
        raise RuntimeError("Document partition exceeds the governed page ceiling")
    if leaf["order_fields"] != plan["order_fields"] or len(leaf["order_fields"]) > 5:
        raise RuntimeError("Document partition order fields are not governed")
    if leaf["select_fields"] != plan["select_fields"]:
        raise RuntimeError("Document partition select fields are not governed")
    return leaf


def execute_partition(args: argparse.Namespace) -> None:
    plan_bytes = args.plan.read_bytes()
    plan = json.loads(plan_bytes.decode("utf-8"))
    leaf = validate_leaf(plan, args.partition_index)
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("Document partition output directory is not empty")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "partition_plan_leaf.json", leaf)
    write_json(
        output / "execution_configuration.json",
        {
            "status": "passed",
            "ruleset_version": RULESET_VERSION,
            "partition_plan_sha256": sha256_bytes(plan_bytes),
            "partition_index": args.partition_index,
            "approved_host": APPROVED_HOST,
            "page_size": PAGE_SIZE,
            "max_concurrency": 1,
            "max_attempts": MAX_ATTEMPTS,
            "timeout_seconds": TIMEOUT_SECONDS,
            "response_size_ceiling_bytes": MAX_RESPONSE_BYTES,
            "final_watermark_committed": False,
            "disappearance_threshold_advanced": False,
        },
    )
    page_count = int(leaf["page_count"])
    document_count = int(leaf["document_count"])
    cumulative_pages_before = sum(int(row["page_count"]) for row in plan["leaves"][: args.partition_index])
    request_index_base = int(plan["request_count"]) + cumulative_pages_before
    seen: set[str] = set()
    duplicate_count = 0
    boundary_tie_count = 0
    previous_last_order = None
    rows_seen = 0
    identity_digest = hashlib.sha256()
    manifest_path = output / "page_manifest.jsonl"
    duplicates_path = output / "duplicate_observations.jsonl"

    for page_index in range(page_count):
        record, body = fetch_page(
            output=output,
            partition_index=args.partition_index,
            page_index=page_index,
            request_index=request_index_base + page_index,
            filter_expression=leaf["filter_expression"],
            select_fields=leaf["select_fields"],
            order_fields=leaf["order_fields"],
        )
        payload = json.loads(body.decode("utf-8"))
        values = payload.get("value")
        expected = min(PAGE_SIZE, document_count - page_index * PAGE_SIZE)
        if not isinstance(values, list) or len(values) != expected:
            raise RuntimeError(
                f"partition {args.partition_index} page {page_index} returned "
                f"{len(values) if isinstance(values, list) else 'invalid'} rows; expected {expected}"
            )
        order_values = [order_key(row, leaf["order_fields"]) for row in values]
        if order_values != sorted(order_values):
            raise RuntimeError(f"partition {args.partition_index} page {page_index} is not ordered")
        if previous_last_order is not None and order_values:
            if previous_last_order > order_values[0]:
                raise RuntimeError(f"partition {args.partition_index} page boundary is not monotonic")
            if previous_last_order == order_values[0]:
                boundary_tie_count += 1
        if order_values:
            previous_last_order = order_values[-1]
        page_duplicates = 0
        page_identities: list[str] = []
        for row_index, row in enumerate(values):
            identity = observation_identity(row)
            page_identities.append(identity)
            if identity in seen:
                duplicate_count += 1
                page_duplicates += 1
                append_jsonl(
                    duplicates_path,
                    {
                        "classification": "duplicate_document_source_observation",
                        "partition_index": args.partition_index,
                        "prefix": leaf["prefix"],
                        "page": page_index,
                        "row_index": row_index,
                        "observation_identity": identity,
                        "selected_row_sha256": sha256_bytes(canonical_json(row)),
                        "automatic_remediation_permitted": False,
                        "review_status": "review_required",
                        "ruleset_version": RULESET_VERSION,
                    },
                )
            else:
                seen.add(identity)
                line = identity + "\n"
                identity_digest.update(line.encode("utf-8"))
        rows_seen += len(values)
        record.update(
            {
                "prefix": leaf["prefix"],
                "filter_expression": leaf["filter_expression"],
                "row_count": len(values),
                "duplicate_observation_count": page_duplicates,
                "first_observation_identity": page_identities[0] if page_identities else None,
                "last_observation_identity": page_identities[-1] if page_identities else None,
            }
        )
        append_jsonl(manifest_path, record)
        write_json(
            output / "checkpoint.json",
            {
                "status": "in_progress",
                "partition_index": args.partition_index,
                "prefix": leaf["prefix"],
                "completed_page": page_index,
                "completed_pages": page_index + 1,
                "planned_pages": page_count,
                "rows_seen": rows_seen,
                "unique_observations": len(seen),
                "duplicate_observations": duplicate_count,
                "final_watermark_committed": False,
                "disappearance_threshold_advanced": False,
                "ruleset_version": RULESET_VERSION,
            },
        )
        time.sleep(REQUEST_DELAY_SECONDS)

    if rows_seen != document_count:
        raise RuntimeError("Document partition rows do not reconcile to the retained count")
    if len(seen) + duplicate_count != rows_seen:
        raise RuntimeError("Document partition duplicate accounting does not reconcile")
    result = {
        "status": "passed",
        "partition_index": args.partition_index,
        "prefix": leaf["prefix"],
        "filter_expression": leaf["filter_expression"],
        "document_count": document_count,
        "page_count": page_count,
        "rows_seen": rows_seen,
        "unique_observation_count": len(seen),
        "duplicate_observation_count": duplicate_count,
        "boundary_tie_count": boundary_tie_count,
        "observation_identity_stream_sha256": identity_digest.hexdigest(),
        "page_manifest_sha256": sha256_file(manifest_path),
        "request_index_start": request_index_base,
        "request_index_end_exclusive": request_index_base + page_count,
        "ruleset_version": RULESET_VERSION,
        "final_watermark_committed": False,
        "disappearance_threshold_advanced": False,
    }
    write_json(output / "partition_result.json", result)
    write_json(output / "checkpoint.json", {**result, "status": "succeeded"})
    print(json.dumps(result, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--partition-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    execute_partition(parser.parse_args())


if __name__ == "__main__":
    main()
