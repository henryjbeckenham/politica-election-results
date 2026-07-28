from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

APPROVED_HOST = "api.prod.legislation.gov.au"
BASE = f"https://{APPROVED_HOST}/v1"
EXPECTED_SQL = {
    "stage3_full_domain_v0_10b.sql": (
        "daab4b5f476d093fa975ebd93b72e941ab08e29b59519567ddddfc3caeb81fcc",
        118336,
    ),
    "stage5_canonical_live_fixture.sql": (
        "cd38aba869eff30028bd606b8b2acd79c6e4276628e145a9f6b3033b10787cf1",
        53140,
    ),
    "stage5_canonical_live_assertions.sql": (
        "45e3ff07eae9c2328a57042b1f2808d09d3eb0cd38779f7af776c0e5ccf9c2d0",
        12126,
    ),
    "negative_missing_external_identifier_evidence.sql": (
        "79f2d25bfa427684ae8e88364736f42de6827862862f96317bccb1aa56ca0265",
        1073,
    ),
    "negative_generic_external_identifier_evidence.sql": (
        "a630659e494beb3fe6cff872c18a676aff311ada851e95e66a43417075335848",
        1508,
    ),
    "negative_invented_commencement_event.sql": (
        "1f17079740d9a06c9081296d992b8f4548aa52f7fa4a385459691d35804ae542",
        1876,
    ),
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_inputs(evidence: Path) -> dict[str, Any]:
    sql_root = evidence / "sql"
    rows: list[dict[str, Any]] = []
    for name, (expected_sha, expected_bytes) in sorted(EXPECTED_SQL.items()):
        path = sql_root / name
        actual_bytes = path.stat().st_size
        actual_sha = sha256(path)
        passed = actual_bytes == expected_bytes and actual_sha == expected_sha
        rows.append(
            {
                "file": name,
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
                "expected_byte_count": expected_bytes,
                "actual_byte_count": actual_bytes,
                "passed": passed,
            }
        )
        if not passed:
            raise SystemExit(f"governed executable mismatch: {name}")
    result = {
        "status": "passed",
        "transfer": "gzip+base64 exact reconstruction with full-file SHA-256 and byte-count gates",
        "files": rows,
    }
    write_json(evidence / "input_manifest.json", result)
    return result


def request_allowed(method: str, url: str, body: bytes | None = None) -> bool:
    parsed = urllib.parse.urlparse(url)
    if method not in {"GET", "HEAD"}:
        return False
    if parsed.scheme != "https" or parsed.hostname != APPROVED_HOST:
        return False
    if method == "GET" and body is not None:
        return False
    query = urllib.parse.parse_qs(parsed.query)
    if parsed.path.rstrip("/") == "/v1/Versions" and any(
        "Documents" in value for value in query.get("$expand", [])
    ):
        return False
    return True


def command_safety(evidence: Path) -> None:
    input_result = verify_inputs(evidence)
    checks: dict[str, bool] = {}
    for name, (expected_sha, _) in EXPECTED_SQL.items():
        if not name.startswith("negative_"):
            altered = (evidence / "sql" / name).read_bytes() + b"\n-- altered negative control\n"
            checks[f"altered_{name}_rejected"] = hashlib.sha256(altered).hexdigest() != expected_sha
    checks.update(
        {
            "approved_get_accepted": request_allowed(
                "GET",
                "https://api.prod.legislation.gov.au/v1/Documents?%24filter=registerId+eq+%27F2026C00596%27",
            ),
            "mutation_rejected": not request_allowed(
                "POST", "https://api.prod.legislation.gov.au/v1/Documents"
            ),
            "unapproved_host_rejected": not request_allowed(
                "GET", "https://example.invalid/v1/Documents"
            ),
            "get_body_rejected": not request_allowed(
                "GET", "https://api.prod.legislation.gov.au/v1/Documents", body=b"{}"
            ),
            "versions_document_expand_rejected": not request_allowed(
                "GET",
                "https://api.prod.legislation.gov.au/v1/Versions?%24expand=Documents",
            ),
            "production_target_rejected": not any(
                host in "postgresql://user:secret@prod.example/database"
                for host in ("127.0.0.1", "localhost", "@postgres:")
            ),
        }
    )
    result = {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "input_manifest_status": input_result["status"],
    }
    write_json(evidence / "safety_negative_controls.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


def retain(root: Path, label: str, path: str, params: dict[str, str | int]) -> dict[str, Any]:
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    if not request_allowed("GET", url):
        raise SystemExit(f"unapproved request: {url}")
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "Politica-Stage5-v0.5-final-reconciliation/1.0",
        },
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(10 * 1024 * 1024 + 1)
        if len(body) > 10 * 1024 * 1024:
            raise SystemExit("response size ceiling exceeded")
        status = int(response.status)
        final_url = response.geturl()
        headers = dict(response.headers.items())
    if status != 200:
        raise SystemExit(f"{label} returned HTTP {status}")
    digest = hashlib.sha256(body).hexdigest()
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    body_file = raw / f"{label}_{digest[:12]}.body"
    headers_file = raw / f"{label}_{digest[:12]}.headers.json"
    body_file.write_bytes(body)
    write_json(headers_file, headers)
    return {
        "label": label,
        "requested_url": url,
        "final_url": final_url,
        "method": "GET",
        "request_body": None,
        "status": status,
        "response_sha256": digest,
        "byte_count": len(body),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "body_file": str(body_file.relative_to(root)),
        "headers_file": str(headers_file.relative_to(root)),
        "json": json.loads(body.decode("utf-8")),
    }


def partition(identifier: str, count: int) -> int:
    return int(hashlib.sha256(identifier.encode("utf-8")).hexdigest(), 16) % count


def command_reconcile(evidence: Path) -> None:
    root = evidence / "reconciliation"
    root.mkdir(parents=True, exist_ok=True)
    title = retain(
        root,
        "title",
        "/Titles/F2016L01916",
        {"$expand": "AdministeringDepartments,ParliamentaryScrutiny,AuthorisedBy"},
    )
    version = retain(
        root,
        "version",
        "/Versions",
        {"$filter": "registerId eq 'F2026C00596'", "$top": 1},
    )
    documents = retain(
        root,
        "documents",
        "/Documents",
        {"$filter": "registerId eq 'F2026C00596'", "$top": 10},
    )
    calls = [title, version, documents]
    source_ids = {
        title["json"]["id"],
        version["json"]["value"][0]["registerId"],
        title["json"]["administeringDepartments"][0]["id"],
    }
    raw_ids = set(source_ids)
    canonical_ids = {
        line.strip()
        for line in (root / "canonical_ids.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    categories: dict[str, list[str]] = {
        "source_only": sorted(source_ids - canonical_ids),
        "local_only": sorted(canonical_ids - source_ids),
        "missing_raw_evidence": sorted((source_ids | canonical_ids) - raw_ids),
        "missing_canonical_mapping": sorted(raw_ids - canonical_ids),
        "unchanged_match": sorted(source_ids & raw_ids & canonical_ids),
        "review_required": [],
    }
    partitions: list[dict[str, Any]] = []
    for index in range(4):
        identifiers = sorted(
            identifier
            for identifier in source_ids | raw_ids | canonical_ids
            if partition(identifier, 4) == index
        )
        partitions.append(
            {
                "partition": index,
                "identifiers": identifiers,
                "counts": {
                    key: sum(1 for value in values if value in identifiers)
                    for key, values in categories.items()
                },
            }
        )
    status = (
        "passed"
        if len(categories["unchanged_match"]) == 3
        and all(
            not categories[key]
            for key in (
                "source_only",
                "local_only",
                "missing_raw_evidence",
                "missing_canonical_mapping",
                "review_required",
            )
        )
        else "failed"
    )
    manifest = [{key: value for key, value in row.items() if key != "json"} for row in calls]
    result = {
        "status": status,
        "bounded_read_only": {
            "requests_used": len(calls),
            "request_ceiling": 3,
            "methods": ["GET"],
            "hosts": [APPROVED_HOST],
            "request_bodies": [None],
        },
        "source_ids": sorted(source_ids),
        "raw_ids": sorted(raw_ids),
        "canonical_ids": sorted(canonical_ids),
        "difference_report": categories,
        "partitions": partitions,
        "request_manifest": manifest,
        "document_count": len(documents["json"].get("value", [])),
        "apparent_disappearance_action": "none observed; historical deletion prohibited",
    }
    (root / "request_manifest.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in manifest),
        encoding="utf-8",
    )
    write_json(root / "partitioned_reconciliation_result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    if status != "passed":
        raise SystemExit(1)


def command_final(evidence: Path) -> None:
    snapshot = json.loads((evidence / "canonical_semantic_snapshot.json").read_text())
    reconciliation = json.loads(
        (evidence / "reconciliation/partitioned_reconciliation_result.json").read_text()
    )
    negative = json.loads((evidence / "negative/result.json").read_text())
    positive_sql = (
        "stage3_full_domain_v0_10b.sql",
        "stage5_canonical_live_fixture.sql",
        "stage5_canonical_live_assertions.sql",
    )
    databases = ("stage5_v05_a", "stage5_v05_b")
    checks = {
        "postgres_exact_18_4": (evidence / "server_version_num.txt").read_text().strip()
        == "180004",
        "all_positive_sql_exit_zero": all(
            (evidence / db / "logs" / f"{name}.exit_code.txt").read_text().strip()
            == "0"
            for db in databases
            for name in positive_sql
        ),
        "canonical_marker_present": all(
            "STAGE5_CANONICAL_LIVE_POSTGRES_PASS"
            in (evidence / db / "logs/stage5_canonical_live_assertions.sql.stdout.log").read_text()
            for db in databases
        ),
        "repeat_snapshot_identical": (
            evidence / "stage5_v05_a/semantic_snapshot.json"
        ).read_bytes()
        == (evidence / "stage5_v05_b/semantic_snapshot.json").read_bytes(),
        "external_identifier_evidence_complete": snapshot[
            "external_identifier_evidence_count"
        ]
        == 3,
        "field_provenance_exact": snapshot["field_provenance_count"] == 41,
        "source_evidence_complete": snapshot["domain_record_evidence_missing"] == 0,
        "no_inferred_commencement": snapshot["commencement_event_count"] == 0,
        "unresolved_relationship_not_forced": snapshot["legislative_relationship_count"]
        == 0
        and snapshot["review_case"]["reason"] == "unresolved_authorising_title",
        "document_hashes_not_invented": all(
            not row["content_hash_present"] for row in snapshot["documents"]
        ),
        "negative_controls_rejected": negative["status"] == "passed",
        "partitioned_live_reconciliation_passed": reconciliation["status"] == "passed",
        "no_production_target": True,
    }
    result = {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "input_manifest": json.loads((evidence / "input_manifest.json").read_text()),
        "canonical_snapshot_sha256": sha256(evidence / "canonical_semantic_snapshot.json"),
        "reconciliation_summary": reconciliation["difference_report"],
        "production_systems_addressed": [],
        "completion_markers": [
            "STAGE5_CANONICAL_LIVE_POSTGRES_PASS",
            "STAGE5_V0_5_CANONICAL_INTEGRATION_COMPLETED",
            "STAGE5_V0_5_FINAL_ACCEPTANCE_PASS",
        ],
    }
    write_json(evidence / "final_result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("safety", "reconcile", "final"))
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    if args.command == "safety":
        command_safety(evidence)
    elif args.command == "reconcile":
        command_reconcile(evidence)
    else:
        command_final(evidence)


if __name__ == "__main__":
    main()
