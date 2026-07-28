from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

APPROVED_HOST = "api.prod.legislation.gov.au"
BASE = f"https://{APPROVED_HOST}"
OUT = Path("validation/stage5_v06_full_reconciliation/probe_evidence")
OUT.mkdir(parents=True, exist_ok=True)
RAW = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 30
USER_AGENT = "Politica-Stage5-v0.6-source-probe/1.0"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def allowed(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == APPROVED_HOST


def fetch(label: str, path: str, params: dict[str, str | int] | None = None, accept: str = "application/json") -> dict[str, Any]:
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    url = BASE + path + query
    if not allowed(url):
        raise SystemExit(f"unapproved request: {url}")
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": accept, "User-Agent": USER_AGENT},
    )
    started = time.monotonic()
    status: int | None = None
    headers: dict[str, str] = {}
    body = b""
    error: str | None = None
    final_url = url
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise RuntimeError("response size ceiling exceeded")
            status = int(response.status)
            headers = dict(response.headers.items())
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        headers = dict(exc.headers.items()) if exc.headers else {}
        body = exc.read(MAX_BYTES + 1)
        error = f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:  # retained diagnostic evidence
        error = f"{type(exc).__name__}: {exc}"

    digest = hashlib.sha256(body).hexdigest()
    stem = f"{label}_{digest[:12]}"
    body_path = RAW / f"{stem}.body"
    headers_path = RAW / f"{stem}.headers.json"
    body_path.write_bytes(body)
    write_json(headers_path, headers)
    parsed_json: Any = None
    if body:
        try:
            parsed_json = json.loads(body.decode("utf-8"))
        except Exception:
            parsed_json = None
    row = {
        "label": label,
        "method": "GET",
        "requested_url": url,
        "final_url": final_url,
        "request_body": None,
        "status": status,
        "error": error,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "response_sha256": digest,
        "byte_count": len(body),
        "body_file": str(body_path),
        "headers_file": str(headers_path),
    }
    if parsed_json is not None:
        row["json"] = parsed_json
    return row


def summarize_page(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("json")
    values = payload.get("value", []) if isinstance(payload, dict) else []
    return {
        "label": row["label"],
        "status": row["status"],
        "error": row["error"],
        "byte_count": row["byte_count"],
        "response_sha256": row["response_sha256"],
        "value_count": len(values) if isinstance(values, list) else None,
        "sample": values[:3] if isinstance(values, list) else None,
        "next_link": payload.get("@odata.nextLink") if isinstance(payload, dict) else None,
        "requested_url": row["requested_url"],
    }


def main() -> None:
    calls: list[dict[str, Any]] = []
    calls.append(fetch("openapi", "/swagger/v1/swagger.json"))
    calls.append(fetch("service_document", "/v1/"))

    collections = {
        "Titles": {
            "$select": "id",
            "$orderby": "id",
            "$top": 100,
            "$skip": 0,
        },
        "Versions": {
            "$select": "registerId",
            "$orderby": "registerId",
            "$top": 100,
            "$skip": 0,
        },
        "Documents": {
            "$select": "titleId,start,retrospectiveStart,rectificationVersionNumber,type,uniqueTypeNumber,volumeNumber,format",
            "$orderby": "titleId,start,retrospectiveStart,rectificationVersionNumber,type,uniqueTypeNumber,volumeNumber,format",
            "$top": 100,
            "$skip": 0,
        },
        "Departments": {
            "$select": "id",
            "$orderby": "id",
            "$top": 100,
            "$skip": 0,
        },
        "TextApplies": {
            "$select": "type,titleId,titleName,provisions",
            "$orderby": "titleId,type,titleName",
            "$top": 100,
            "$skip": 0,
        },
    }

    counts: dict[str, Any] = {}
    pages: dict[str, Any] = {}
    for name, params in collections.items():
        count_row = fetch(f"{name.lower()}_count", f"/v1/{name}/$count", accept="text/plain")
        calls.append(count_row)
        text = (RAW / Path(count_row["body_file"]).name).read_text(encoding="utf-8", errors="replace").strip()
        counts[name] = {
            "status": count_row["status"],
            "error": count_row["error"],
            "text": text,
            "parsed_integer": int(text) if text.isdigit() else None,
            "response_sha256": count_row["response_sha256"],
            "byte_count": count_row["byte_count"],
        }
        page_row = fetch(f"{name.lower()}_page_probe", f"/v1/{name}", params)
        calls.append(page_row)
        pages[name] = summarize_page(page_row)

    manifest = [{k: v for k, v in row.items() if k != "json"} for row in calls]
    (OUT / "request_manifest.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in manifest),
        encoding="utf-8",
    )
    result = {
        "status": "completed",
        "approved_host": APPROVED_HOST,
        "request_count": len(calls),
        "request_ceiling": 12,
        "response_size_ceiling_bytes": MAX_BYTES,
        "timeout_seconds": TIMEOUT_SECONDS,
        "methods": ["GET"],
        "request_bodies": [None],
        "counts": counts,
        "page_probes": pages,
        "contract": {
            "openapi_sha256": calls[0]["response_sha256"],
            "openapi_byte_count": calls[0]["byte_count"],
            "openapi_status": calls[0]["status"],
            "service_document_sha256": calls[1]["response_sha256"],
            "service_document_byte_count": calls[1]["byte_count"],
            "service_document_status": calls[1]["status"],
        },
    }
    write_json(OUT / "probe_result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
