from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HOST = "api.prod.legislation.gov.au"
BASE = f"https://{HOST}/v1"
OUT = Path("validation/stage5_v06_full_reconciliation/paging_probe_evidence")
RAW = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 45
PAGE_SIZE = 500


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def fetch(label: str, collection: str, params: dict[str, str | int]) -> dict[str, Any]:
    url = BASE + f"/{collection}?" + urllib.parse.urlencode(params)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != HOST:
        raise SystemExit(f"unapproved request: {url}")
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "Politica-Stage5-v0.6-paging-probe/1.0"},
    )
    started = time.monotonic()
    body = b""
    headers: dict[str, str] = {}
    status: int | None = None
    error: str | None = None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise RuntimeError("response size ceiling exceeded")
            status = int(response.status)
            headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        headers = dict(exc.headers.items()) if exc.headers else {}
        body = exc.read(MAX_BYTES + 1)
        error = f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    digest = hashlib.sha256(body).hexdigest()
    stem = f"{label}_{digest[:12]}"
    body_path = RAW / f"{stem}.body"
    headers_path = RAW / f"{stem}.headers.json"
    body_path.write_bytes(body)
    write_json(headers_path, headers)
    payload: Any = None
    if body:
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = None
    return {
        "label": label,
        "collection": collection,
        "method": "GET",
        "requested_url": url,
        "request_body": None,
        "status": status,
        "error": error,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "response_sha256": digest,
        "byte_count": len(body),
        "body_file": str(body_path),
        "headers_file": str(headers_path),
        "payload": payload,
    }


def normalized(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def identity_for(collection: str, row: dict[str, Any]) -> str:
    if collection == "Titles":
        values = [row.get("id")]
    elif collection == "Versions":
        values = [
            row.get("titleId"),
            row.get("start"),
            row.get("retrospectiveStart"),
            row.get("registerId"),
            row.get("compilationNumber"),
        ]
    elif collection == "Documents":
        values = [
            row.get("titleId"),
            row.get("start"),
            row.get("retrospectiveStart"),
            row.get("rectificationVersionNumber"),
            row.get("type"),
            row.get("uniqueTypeNumber"),
            row.get("volumeNumber"),
            row.get("format"),
        ]
    elif collection == "Departments":
        values = [row.get("id")]
    elif collection == "TextApplies":
        values = [row.get("type"), row.get("titleId"), row.get("titleName"), row.get("provisions")]
    else:
        raise ValueError(collection)
    encoded = json.dumps([normalized(value) for value in values], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def summarize(row: dict[str, Any]) -> dict[str, Any]:
    payload = row["payload"]
    values = payload.get("value", []) if isinstance(payload, dict) else []
    identities = [identity_for(row["collection"], item) for item in values] if isinstance(values, list) else []
    register_nulls = None
    if row["collection"] in {"Versions", "Documents"} and isinstance(values, list):
        register_nulls = sum(1 for item in values if item.get("registerId") is None)
    return {
        "label": row["label"],
        "collection": row["collection"],
        "status": row["status"],
        "error": row["error"],
        "value_count": len(values) if isinstance(values, list) else None,
        "identity_count": len(set(identities)),
        "duplicate_identity_count": len(identities) - len(set(identities)),
        "register_id_null_count": register_nulls,
        "byte_count": row["byte_count"],
        "response_sha256": row["response_sha256"],
        "first_identity": identities[0] if identities else None,
        "last_identity": identities[-1] if identities else None,
        "sample_first": values[:2] if isinstance(values, list) else None,
        "sample_last": values[-2:] if isinstance(values, list) else None,
        "requested_url": row["requested_url"],
        "identities": identities,
    }


def main() -> None:
    specs = {
        "Titles": {
            "$select": "id",
            "$orderby": "id",
        },
        "Versions": {
            "$select": "titleId,start,retrospectiveStart,registerId,compilationNumber",
            "$orderby": "titleId,start,retrospectiveStart,registerId,compilationNumber",
        },
        "Documents": {
            "$select": "titleId,start,retrospectiveStart,registerId,rectificationVersionNumber,type,uniqueTypeNumber,volumeNumber,format",
            "$orderby": "titleId,start,type,uniqueTypeNumber,format",
        },
        "Departments": {
            "$select": "id",
            "$orderby": "id",
        },
        "TextApplies": {
            "$select": "type,titleId,titleName,provisions",
            "$orderby": "titleId,type,titleName,provisions",
        },
    }
    calls: list[dict[str, Any]] = []
    summaries: dict[str, list[dict[str, Any]]] = {}
    for collection, base_params in specs.items():
        summaries[collection] = []
        for page_index, skip in enumerate((0, PAGE_SIZE)):
            params = dict(base_params)
            params.update({"$top": PAGE_SIZE, "$skip": skip})
            row = fetch(f"{collection.lower()}_page_{page_index}", collection, params)
            calls.append(row)
            summaries[collection].append(summarize(row))

    overlaps: dict[str, Any] = {}
    for collection, pages in summaries.items():
        first = set(pages[0].pop("identities"))
        second = set(pages[1].pop("identities"))
        overlaps[collection] = {
            "page_identity_overlap_count": len(first & second),
            "union_identity_count": len(first | second),
            "both_pages_http_200": all(page["status"] == 200 for page in pages),
            "full_first_page": pages[0]["value_count"] == PAGE_SIZE or collection in {"Departments", "TextApplies"},
        }

    manifest = [{key: value for key, value in row.items() if key != "payload"} for row in calls]
    (OUT / "request_manifest.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in manifest), encoding="utf-8"
    )
    result = {
        "status": "completed",
        "page_size": PAGE_SIZE,
        "request_count": len(calls),
        "request_ceiling": 10,
        "methods": ["GET"],
        "request_bodies": [None],
        "response_size_ceiling_bytes": MAX_BYTES,
        "pages": summaries,
        "overlaps": overlaps,
    }
    write_json(OUT / "paging_probe_result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
