from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


APPROVED_HOST = "api.prod.legislation.gov.au"
BASE = f"https://{APPROVED_HOST}"
PAGE_SIZE = 100
MAX_ROWS_PER_LEAF = 10_000
REQUEST_CEILING = 500
MAX_RESPONSE_BYTES = 4096
TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 4
REQUEST_DELAY_SECONDS = 0.5
ORDER_FIELDS = ["titleId", "start", "rectificationVersionNumber", "type", "format"]
SELECT_FIELDS = [
    "titleId",
    "start",
    "retrospectiveStart",
    "rectificationVersionNumber",
    "type",
    "uniqueTypeNumber",
    "volumeNumber",
    "format",
]
RULESET_VERSION = "stage5-v0.6-document-prefix-plan-1"


@dataclass(frozen=True)
class CountEvidence:
    label: str
    filter_expression: str | None
    count: int
    request_index: int
    requested_url: str
    response_sha256: str
    byte_count: int
    duration_ms: int
    attempts: int
    body_file: str
    headers_file: str


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def load_title_ids(path: Path) -> list[str]:
    import gzip

    values = [line.strip() for line in gzip.open(path, "rt", encoding="utf-8") if line.strip()]
    if not values:
        raise RuntimeError("authoritative Titles identifier set is empty")
    if values != sorted(values):
        raise RuntimeError("authoritative Titles identifier set is not ordered")
    if len(values) != len(set(values)):
        raise RuntimeError("authoritative Titles identifier set contains duplicates")
    return values


def safe_count_url(filter_expression: str | None) -> str:
    params = {} if filter_expression is None else {"$filter": filter_expression}
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    url = BASE + "/v1/Documents/$count" + query
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != APPROVED_HOST:
        raise RuntimeError(f"unapproved request: {url}")
    return url


def fetch_count(
    *,
    output: Path,
    label: str,
    filter_expression: str | None,
    request_index: int,
) -> CountEvidence:
    if request_index >= REQUEST_CEILING:
        raise RuntimeError("Document partition count request ceiling exceeded")
    url = safe_count_url(filter_expression)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "text/plain",
            "User-Agent": "Politica-Stage5-v0.6-document-prefix-plan/1.0",
        },
    )
    body = b""
    headers: dict[str, str] = {}
    status: int | None = None
    last_error: str | None = None
    started = time.monotonic()
    attempts = 0
    for attempt in range(MAX_ATTEMPTS):
        attempts = attempt + 1
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("count response size ceiling exceeded")
                status = int(response.status)
                headers = dict(response.headers.items())
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
    raw.mkdir(parents=True, exist_ok=True)
    body_path = raw / f"{request_index:04d}_{label}_{digest[:12]}.body"
    headers_path = raw / f"{request_index:04d}_{label}_{digest[:12]}.headers.json"
    body_path.write_bytes(body)
    write_json(headers_path, headers)
    if last_error is not None:
        raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")
    text = body.decode("utf-8").strip()
    if not text.isdigit():
        raise RuntimeError(f"{label} did not return a non-negative integer")
    return CountEvidence(
        label=label,
        filter_expression=filter_expression,
        count=int(text),
        request_index=request_index,
        requested_url=url,
        response_sha256=digest,
        byte_count=len(body),
        duration_ms=duration_ms,
        attempts=attempts,
        body_file=str(body_path.relative_to(output)),
        headers_file=str(headers_path.relative_to(output)),
    )


def next_char_groups(title_ids: list[str], prefix: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    exact: list[str] = []
    for value in title_ids:
        if not value.startswith(prefix):
            continue
        if len(value) == len(prefix):
            exact.append(value)
        else:
            groups.setdefault(value[len(prefix)], []).append(value)
    if exact:
        groups[""] = exact
    return groups


def build_prefix_tree(
    *,
    title_ids: list[str],
    root_prefixes: list[str],
    count_provider: Callable[[str], int],
    max_rows: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    leaves: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    def recurse(prefix: str, ids_for_prefix: list[str], expected_count: int | None = None) -> None:
        count = count_provider(prefix) if expected_count is None else expected_count
        node = {
            "prefix": prefix,
            "title_id_count": len(ids_for_prefix),
            "document_count": count,
            "page_count": math.ceil(count / PAGE_SIZE),
            "is_leaf": count <= max_rows,
        }
        nodes.append(node)
        if count <= max_rows:
            leaves.append(node)
            return
        groups = next_char_groups(ids_for_prefix, prefix)
        if not groups:
            raise RuntimeError(f"cannot split non-empty Document prefix {prefix}")
        child_rows: list[tuple[str, list[str], int]] = []
        for char, child_ids in sorted(groups.items()):
            if char == "":
                child_prefix = prefix
                child_filter = f"titleId eq '{prefix}'"
                child_count = count_provider("=" + prefix)
            else:
                child_prefix = prefix + char
                child_filter = f"startswith(titleId,'{child_prefix}')"
                child_count = count_provider(child_prefix)
            child_rows.append((child_prefix, child_ids, child_count))
        if sum(row[2] for row in child_rows) != count:
            raise RuntimeError(
                f"Document prefix coverage mismatch for {prefix}: children "
                f"{sum(row[2] for row in child_rows)} != parent {count}"
            )
        if len(child_rows) == 1 and child_rows[0][0] == prefix:
            raise RuntimeError(f"Document prefix {prefix} cannot be subdivided")
        for child_prefix, child_ids, child_count in child_rows:
            if child_prefix == prefix:
                leaf = {
                    "prefix": child_prefix,
                    "filter_expression": f"titleId eq '{prefix}'",
                    "title_id_count": len(child_ids),
                    "document_count": child_count,
                    "page_count": math.ceil(child_count / PAGE_SIZE),
                    "is_leaf": True,
                    "exact_title": True,
                }
                nodes.append(leaf)
                leaves.append(leaf)
            else:
                recurse(child_prefix, child_ids, child_count)

    for prefix in root_prefixes:
        values = [value for value in title_ids if value.startswith(prefix)]
        if not values:
            raise RuntimeError(f"root prefix has no authoritative Title identifiers: {prefix}")
        recurse(prefix, values)
    return leaves, nodes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--titles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-rows", type=int, default=MAX_ROWS_PER_LEAF)
    args = parser.parse_args()
    if args.max_rows <= 0 or args.max_rows > 20_000:
        raise SystemExit("max rows per leaf must be between 1 and 20000")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    title_ids = load_title_ids(args.titles)
    roots = sorted({value[:5] for value in title_ids})
    evidence_rows: list[CountEvidence] = []
    cache: dict[str, int] = {}

    def retained_count(key: str) -> int:
        if key in cache:
            return cache[key]
        if key.startswith("="):
            value = key[1:]
            filter_expression = f"titleId eq '{value}'"
            label = "exact_" + value
        else:
            filter_expression = f"startswith(titleId,'{key}')"
            label = "prefix_" + key
        row = fetch_count(
            output=output,
            label=label,
            filter_expression=filter_expression,
            request_index=len(evidence_rows),
        )
        evidence_rows.append(row)
        cache[key] = row.count
        time.sleep(REQUEST_DELAY_SECONDS)
        return row.count

    total_row = fetch_count(
        output=output,
        label="documents_total",
        filter_expression=None,
        request_index=len(evidence_rows),
    )
    evidence_rows.append(total_row)
    null_row = fetch_count(
        output=output,
        label="documents_null_title",
        filter_expression="titleId eq null",
        request_index=len(evidence_rows),
    )
    evidence_rows.append(null_row)
    if null_row.count != 0:
        raise RuntimeError("Documents with null titleId cannot be covered by the prefix plan")

    leaves, nodes = build_prefix_tree(
        title_ids=title_ids,
        root_prefixes=roots,
        count_provider=retained_count,
        max_rows=args.max_rows,
    )
    root_count = sum(cache[prefix] for prefix in roots)
    if root_count != total_row.count:
        raise RuntimeError(
            f"observed Title prefixes cover {root_count} Documents, not total {total_row.count}"
        )
    leaf_count = sum(int(row["document_count"]) for row in leaves)
    if leaf_count != total_row.count:
        raise RuntimeError(
            f"leaf partitions cover {leaf_count} Documents, not total {total_row.count}"
        )

    for index, leaf in enumerate(sorted(leaves, key=lambda row: (row["prefix"], row.get("exact_title", False)))):
        leaf["partition_index"] = index
        leaf.setdefault("filter_expression", f"startswith(titleId,'{leaf['prefix']}')")
        leaf["select_fields"] = SELECT_FIELDS
        leaf["order_fields"] = ORDER_FIELDS
        leaf["page_size"] = PAGE_SIZE

    request_manifest = [row.__dict__ for row in evidence_rows]
    (output / "request_manifest.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in request_manifest),
        encoding="utf-8",
    )
    plan = {
        "status": "passed",
        "ruleset_version": RULESET_VERSION,
        "approved_host": APPROVED_HOST,
        "page_size": PAGE_SIZE,
        "max_rows_per_leaf": args.max_rows,
        "max_concurrency": 1,
        "request_count": len(evidence_rows),
        "request_ceiling": REQUEST_CEILING,
        "title_identifier_count": len(title_ids),
        "root_prefix_count": len(roots),
        "node_count": len(nodes),
        "leaf_partition_count": len(leaves),
        "document_count": total_row.count,
        "null_title_id_count": null_row.count,
        "root_document_count_sum": root_count,
        "leaf_document_count_sum": leaf_count,
        "planned_page_count": sum(int(row["page_count"]) for row in leaves),
        "select_fields": SELECT_FIELDS,
        "order_fields": ORDER_FIELDS,
        "root_prefixes": roots,
        "leaves": sorted(leaves, key=lambda row: row["partition_index"]),
        "nodes": sorted(nodes, key=lambda row: (row["prefix"], row["document_count"])),
    }
    plan_bytes = json.dumps(plan, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    (output / "document_partition_plan.json").write_bytes(plan_bytes)
    write_json(
        output / "document_partition_plan_result.json",
        {
            "status": "passed",
            "plan_sha256": sha256_bytes(plan_bytes),
            "plan_byte_count": len(plan_bytes),
            "document_count": total_row.count,
            "leaf_partition_count": len(leaves),
            "planned_page_count": plan["planned_page_count"],
            "request_count": len(evidence_rows),
        },
    )
    print(json.dumps(plan, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
