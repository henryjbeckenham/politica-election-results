from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

import reconcile_v2 as v2

r = v2.r


def scan_collection(
    *,
    config: dict[str, Any],
    http,
    evidence: Path,
    plan: dict[str, Any],
    connection,
) -> dict[str, Any]:
    name = plan["collection"]
    page_size = int(plan["page_size"])
    partition_span = int(config["partition_page_span"])
    checkpoint_path = evidence / "checkpoints" / f"{name}.json"
    page_manifest_path = evidence / "page_manifest.jsonl"
    started_at = r.utc_now()
    rows_total = 0
    page_fingerprints: set[tuple[str, int]] = set()

    for page_no in range(int(plan["planned_pages"])):
        partition = page_no // partition_span
        skip = page_no * page_size
        params = {
            "$top": page_size,
            "$skip": skip,
            "$select": ",".join(plan["select_fields"]),
            "$orderby": ",".join(plan["order_fields"]),
        }
        url = r.build_url(config["api_base"], plan["endpoint"], params)
        response = http.fetch(
            label=f"{name}_p{page_no:06d}",
            url=url,
            kind="collection_page",
            collection=name,
            partition=partition,
            page=page_no,
        )
        rows, returned_next = r.parse_collection(response["body"])
        expected_rows = min(page_size, int(plan["start_count"]) - skip)
        if len(rows) != expected_rows:
            raise RuntimeError(
                f"{name} page {page_no} returned {len(rows)} rows; expected {expected_rows}"
            )
        fingerprint = (response["row"]["response_sha256"], len(rows))
        if fingerprint in page_fingerprints and rows:
            raise RuntimeError(f"duplicate page body detected for {name} page {page_no}")
        page_fingerprints.add(fingerprint)

        body_file = response["row"]["body_file"]
        response_sha = response["row"]["response_sha256"]
        batch = []
        for row_index, row in enumerate(rows):
            identity = r.source_identity(name, plan, row)
            batch.append(
                (
                    name,
                    page_no,
                    partition,
                    row_index,
                    identity["source_key"],
                    identity["base_key"],
                    identity["external_identifier"],
                    identity["identity_kind"],
                    plan["canonical_family"],
                    body_file,
                    response_sha,
                    r.canonical_json(row),
                    r.canonical_json(identity["identity_components"]),
                )
            )
        connection.executemany(
            """
            INSERT INTO source_rows (
              collection,page_no,partition_no,row_index,source_key,base_key,
              external_identifier,identity_kind,canonical_family,evidence_file,
              response_sha256,row_json,components_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            batch,
        )
        connection.commit()
        rows_total += len(rows)
        page_row = {
            "collection": name,
            "partition": partition,
            "page": page_no,
            "skip": skip,
            "requested_url": url,
            "response_sha256": response_sha,
            "response_byte_count": response["row"]["response_byte_count"],
            "body_file": body_file,
            "row_count": len(rows),
            "expected_row_count": expected_rows,
            "source_next_url": returned_next,
        }
        with page_manifest_path.open("a", encoding="utf-8") as stream:
            stream.write(r.canonical_json(page_row) + "\n")
        r.write_json(
            checkpoint_path,
            {
                "status": "in_progress",
                "collection": name,
                "completed_pages": page_no + 1,
                "completed_rows": rows_total,
                "next_page": page_no + 1 if page_no + 1 < plan["planned_pages"] else None,
                "next_skip": skip + page_size if page_no + 1 < plan["planned_pages"] else None,
                "configuration_hash": r.config_hash(config),
                "updated_at_utc": r.utc_now(),
                "final_watermark_committed": False,
                "disappearance_threshold_advanced": False,
            },
        )

    end_count = r.parse_count(
        http.fetch(
            label=f"{name}_count_end",
            url=r.build_url(config["api_base"], plan["count_endpoint"]),
            kind="discovery",
            collection=name,
            accept="text/plain",
        )["body"]
    )
    completed_pages = int(plan["planned_pages"])
    completed = rows_total == plan["start_count"] == end_count
    result = {
        "collection": name,
        "status": "completed" if completed else "partial",
        "started_at_utc": started_at,
        "completed_at_utc": r.utc_now(),
        "start_count": plan["start_count"],
        "end_count": end_count,
        "rows_observed": rows_total,
        "planned_pages": plan["planned_pages"],
        "completed_pages": completed_pages,
        "completed_partitions": math.ceil(completed_pages / partition_span) if completed_pages else 0,
        "planned_partitions": len(plan["partitions"]),
        "pagination_method": "explicit $skip and $top over a source-supported ordered projection",
        "final_watermark_committed": False,
        "disappearance_threshold_advanced": False,
    }
    r.write_json(evidence / "collection_results" / f"{name}.json", result)
    r.write_json(
        checkpoint_path,
        {
            **result,
            "configuration_hash": r.config_hash(config),
            "next_page": None,
            "next_skip": None,
        },
    )
    if not completed:
        raise RuntimeError(f"{name} did not complete a stable count traversal: {result}")
    return result


def populate_from_retained(*, evidence: Path, plan: dict[str, Any], db_path: Path):
    connection = r.initialize_db(db_path)
    plan_by_collection = {row["collection"]: row for row in plan["collections"]}
    page_rows = [
        __import__("json").loads(line)
        for line in (evidence / "page_manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    page_rows.sort(key=lambda row: (row["collection"], int(row["page"])))
    for page in page_rows:
        body_path = evidence / page["body_file"]
        body = body_path.read_bytes()
        if r.sha256_bytes(body) != page["response_sha256"]:
            raise RuntimeError(f"retained page hash mismatch: {body_path}")
        rows, _ = r.parse_collection(body)
        if len(rows) != int(page["expected_row_count"]):
            raise RuntimeError(f"retained page row-count mismatch: {body_path}")
        collection = page["collection"]
        collection_plan = plan_by_collection[collection]
        batch = []
        for row_index, row in enumerate(rows):
            identity = r.source_identity(collection, collection_plan, row)
            batch.append(
                (
                    collection,
                    int(page["page"]),
                    int(page["partition"]),
                    row_index,
                    identity["source_key"],
                    identity["base_key"],
                    identity["external_identifier"],
                    identity["identity_kind"],
                    collection_plan["canonical_family"],
                    page["body_file"],
                    page["response_sha256"],
                    r.canonical_json(row),
                    r.canonical_json(identity["identity_components"]),
                )
            )
        connection.executemany("INSERT INTO source_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    connection.commit()
    return connection


def build_packages(args) -> None:
    evidence = Path(args.evidence)
    source_root = Path(args.source_root)
    baseline_zip = Path(args.baseline_zip)
    final = Path(args.final)
    final.mkdir(parents=True, exist_ok=True)

    temporary = Path(tempfile.mkdtemp(prefix="stage5_v06_source_"))
    source_stage = temporary / "politica_stage5_legislation_sync_v0_6"
    (source_stage / "evidence").mkdir(parents=True)
    (source_stage / "baseline").mkdir(parents=True)
    shutil.copy2(baseline_zip, source_stage / "baseline" / baseline_zip.name)
    for name in ("reconcile.py", "reconcile_v2.py", "reconcile_v3.py", "reconciliation_config.json", "README.md"):
        shutil.copy2(source_root / name, source_stage / name)
    for name in (
        "stage5_requirements_traceability_v0_6.json",
        "stage5_requirements_traceability_v0_6.md",
        "stage5_final_acceptance_assessment_v0_6.json",
        "stage5_final_acceptance_assessment_v0_6.md",
        "stage5_v0_6_full_reconciliation_findings.md",
        "final_reconciliation_result.json",
        "deterministic_self_tests.json",
        "retained_response_replay_result.json",
    ):
        shutil.copy2(evidence / name, source_stage / "evidence" / name)

    manifest_rows = []
    for path in sorted(source_stage.rglob("*")):
        if path.is_file() and path.name != "package_manifest.json":
            manifest_rows.append(
                {
                    "path": path.relative_to(source_stage).as_posix(),
                    "byte_count": path.stat().st_size,
                    "sha256": r.sha256_file(path),
                }
            )
    r.write_json(
        source_stage / "package_manifest.json",
        {
            "package": "politica_stage5_legislation_sync_v0_6",
            "baseline": {
                "package": baseline_zip.name,
                "sha256": r.sha256_file(baseline_zip),
                "byte_count": baseline_zip.stat().st_size,
            },
            "files": manifest_rows,
        },
    )

    package_a = final / "politica_stage5_legislation_sync_v0_6.build_a.zip"
    package_b = final / "politica_stage5_legislation_sync_v0_6.build_b.zip"
    info_a = r.deterministic_zip(source_stage, package_a)
    info_b = r.deterministic_zip(source_stage, package_b)
    if package_a.read_bytes() != package_b.read_bytes():
        raise RuntimeError("v0.6 source package builds are not byte-identical")
    package_final = final / "politica_stage5_legislation_sync_v0_6.zip"
    shutil.copy2(package_a, package_final)
    (final / "politica_stage5_legislation_sync_v0_6.zip.sha256").write_text(
        f"{info_a['sha256']}  {package_final.name}\n", encoding="utf-8"
    )

    evidence_a = final / "politica_stage5_v0_6_full_reconciliation_evidence.build_a.zip"
    evidence_b = final / "politica_stage5_v0_6_full_reconciliation_evidence.build_b.zip"
    excluded = {
        package_a.name,
        package_b.name,
        package_final.name,
        evidence_a.name,
        evidence_b.name,
        "politica_stage5_legislation_sync_v0_6.zip.sha256",
        "politica_stage5_v0_6_full_reconciliation_evidence.zip.sha256",
        "package_result.json",
    }
    evidence_info_a = r.deterministic_zip(evidence, evidence_a, excluded)
    evidence_info_b = r.deterministic_zip(evidence, evidence_b, excluded)
    if evidence_a.read_bytes() != evidence_b.read_bytes():
        raise RuntimeError("v0.6 evidence archive builds are not byte-identical")
    evidence_final = final / "politica_stage5_v0_6_full_reconciliation_evidence.zip"
    shutil.copy2(evidence_a, evidence_final)
    (final / "politica_stage5_v0_6_full_reconciliation_evidence.zip.sha256").write_text(
        f"{evidence_info_a['sha256']}  {evidence_final.name}\n", encoding="utf-8"
    )
    r.write_json(
        final / "package_result.json",
        {
            "status": "passed",
            "source_package": {**info_a, "file": package_final.name, "repeat_build_identical": True},
            "evidence_archive": {**evidence_info_a, "file": evidence_final.name, "repeat_build_identical": True},
        },
    )
    print("STAGE5_V0_6_DETERMINISTIC_PACKAGING_PASS")


r.scan_collection = scan_collection
r.populate_from_retained = populate_from_retained
r.build_packages = build_packages

if __name__ == "__main__":
    raise SystemExit(r.main())
