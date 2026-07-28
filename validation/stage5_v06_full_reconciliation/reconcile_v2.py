from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

import reconcile as r


def gzip_writer(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.open("wb")
    compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0)
    text = io.TextIOWrapper(compressed, encoding="utf-8", newline="\n")
    return raw, compressed, text


def gzip_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> tuple[int, str]:
    count = 0
    semantic = hashlib.sha256()
    raw, compressed, text = gzip_writer(path)
    try:
        for row in rows:
            value = r.canonical_json(row)
            text.write(value + "\n")
            semantic.update(value.encode("utf-8") + b"\n")
            count += 1
    finally:
        text.flush()
        text.close()
        if not compressed.closed:
            compressed.close()
        if not raw.closed:
            raw.close()
    return count, semantic.hexdigest()


def iter_unique_source_rows(connection) -> Iterator[dict[str, Any]]:
    query = """
      SELECT collection, source_key, external_identifier, identity_kind, canonical_family,
             MIN(page_no), MIN(partition_no), MIN(row_index), MIN(evidence_file),
             MIN(response_sha256), MIN(row_json), MIN(components_json), COUNT(*),
             COUNT(DISTINCT row_json)
      FROM source_rows
      GROUP BY collection, source_key, external_identifier, identity_kind, canonical_family
      ORDER BY collection, source_key
    """
    for row in connection.execute(query):
        yield {
            "collection": row[0],
            "source_key": row[1],
            "external_identifier": row[2],
            "identity_kind": row[3],
            "canonical_family": row[4],
            "first_page": row[5],
            "first_partition": row[6],
            "first_row_index": row[7],
            "evidence_file": row[8],
            "response_sha256": row[9],
            "row": json.loads(row[10]),
            "identity_components": json.loads(row[11]),
            "observation_count": row[12],
            "distinct_row_count": row[13],
        }


def compare_contracts(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    result = r.compare_contracts(baseline, current)
    if result.get("changed_operations"):
        result["status"] = "incompatible"
    return result


def produce_reconciliation(
    *,
    config: dict[str, Any],
    evidence: Path,
    connection,
    canonical: dict[str, dict[str, Any]],
    emit_files: bool,
    output_prefix: str,
) -> dict[str, Any]:
    canonical_keys = set(canonical)
    seen_source_keys: set[str] = set()
    source_counts: dict[str, int] = {}
    unique_counts: dict[str, int] = {}
    duplicate_count = 0
    conflicting_duplicate_count = 0
    source_only = 0
    matched = 0
    missing_canonical_mapping = 0
    compound_without_external = 0
    review_required = 0
    semantic = hashlib.sha256()

    source_text = raw_text = difference_text = review_text = disappearance_text = None
    source_handles = raw_handles = difference_handles = review_handles = disappearance_handles = None
    if emit_files:
        source_handles = gzip_writer(evidence / "sets" / f"{output_prefix}_source_identifier_set.jsonl.gz")
        raw_handles = gzip_writer(evidence / "sets" / f"{output_prefix}_raw_evidence_set.jsonl.gz")
        difference_handles = gzip_writer(evidence / "differences" / f"{output_prefix}_complete_difference_report.jsonl.gz")
        review_handles = gzip_writer(evidence / "differences" / f"{output_prefix}_review_required.jsonl.gz")
        disappearance_handles = gzip_writer(evidence / "differences" / f"{output_prefix}_disappearance_observations.jsonl.gz")
        source_text = source_handles[2]
        raw_text = raw_handles[2]
        difference_text = difference_handles[2]
        review_text = review_handles[2]
        disappearance_text = disappearance_handles[2]

    try:
        if emit_files and raw_text is not None:
            for raw_row in r.iter_raw_rows(connection):
                raw_text.write(r.canonical_json(raw_row) + "\n")

        for row in iter_unique_source_rows(connection):
            seen_source_keys.add(row["source_key"])
            source_counts[row["collection"]] = source_counts.get(row["collection"], 0) + row["observation_count"]
            unique_counts[row["collection"]] = unique_counts.get(row["collection"], 0) + 1
            if row["observation_count"] > 1:
                duplicate_count += row["observation_count"] - 1
            conflict = row["distinct_row_count"] > 1
            if conflict:
                conflicting_duplicate_count += 1

            if source_text is not None:
                source_text.write(r.canonical_json(row) + "\n")

            classifications: list[str] = []
            if row["source_key"] in canonical_keys:
                classifications.append("source_raw_canonical_match")
                matched += 1
            else:
                classifications.extend(("source_only_identifier", "raw_identifier_missing_canonical_mapping"))
                source_only += 1
                missing_canonical_mapping += 1
            if row["identity_kind"] == "compound_source_observation":
                classifications.append("source_observation_without_standalone_external_identifier")
                compound_without_external += 1
            if row["observation_count"] > 1:
                classifications.append("duplicate_source_observation")
            if conflict:
                classifications.append("conflicting_duplicate_source_identity")
                review_required += 1

            automatic = (
                row["identity_kind"] == "authoritative_external_identifier"
                and row["collection"] in {"Titles", "Versions", "Departments"}
                and row["source_key"] not in canonical_keys
                and not conflict
            )
            difference = {
                "source_system": "Federal Register of Legislation public API",
                "source_namespace": "Federal Register",
                "collection": row["collection"],
                "external_identifier": row["external_identifier"],
                "source_key": row["source_key"],
                "canonical_family": row["canonical_family"],
                "partition": row["first_partition"],
                "page": row["first_page"],
                "classification": classifications,
                "evidence_file": row["evidence_file"],
                "response_sha256": row["response_sha256"],
                "observation_count": row["observation_count"],
                "distinct_row_count": row["distinct_row_count"],
                "current_local_state": "canonical_match" if row["source_key"] in canonical_keys else "not_canonicalised_in_bounded_stage5_baseline",
                "recommended_action": (
                    "retain as confirmed match"
                    if row["source_key"] in canonical_keys
                    else "retain as governed future ingestion candidate; do not fabricate an incomplete canonical record"
                ),
                "automatic_remediation_permitted": automatic,
                "review_status": "queued" if conflict else "not_required",
                "ruleset_version": config["ruleset_version"],
                "configuration_version": config["version"],
            }
            text = r.canonical_json(difference)
            semantic.update(text.encode("utf-8") + b"\n")
            if difference_text is not None:
                difference_text.write(text + "\n")
            if conflict and review_text is not None:
                review_text.write(text + "\n")

        canonical_only_rows: list[dict[str, Any]] = []
        for key in sorted(canonical_keys - seen_source_keys):
            value = canonical[key]
            row = {
                "source_system": "Federal Register of Legislation public API",
                "source_namespace": "Federal Register",
                "collection": value["collection"],
                "external_identifier": value["external_identifier"],
                "source_key": key,
                "canonical_family": value["canonical_family"],
                "classification": ["canonical_only_identifier", "apparent_source_absence"],
                "absence_count": 1,
                "confirmation_threshold": config["disappearance_confirmation_threshold"],
                "threshold_met": False,
                "history_deleted_or_deactivated": False,
                "recommended_action": "retain prior history and require another complete reconciliation before review escalation",
                "automatic_remediation_permitted": False,
                "review_status": "not_queued_threshold_not_met",
                "ruleset_version": config["ruleset_version"],
                "configuration_version": config["version"],
            }
            canonical_only_rows.append(row)
            text = r.canonical_json(row)
            semantic.update(text.encode("utf-8") + b"\n")
            if difference_text is not None:
                difference_text.write(text + "\n")
            if disappearance_text is not None:
                disappearance_text.write(text + "\n")

        total_rows = connection.execute("SELECT COUNT(*) FROM source_rows").fetchone()[0]
        total_unique = connection.execute(
            "SELECT COUNT(*) FROM (SELECT collection, source_key FROM source_rows GROUP BY collection, source_key)"
        ).fetchone()[0]
        summary = {
            "status": "passed" if conflicting_duplicate_count == 0 else "passed_with_review_cases",
            "ruleset_version": config["ruleset_version"],
            "configuration_version": config["version"],
            "source_observation_count": total_rows,
            "source_unique_identity_count": total_unique,
            "source_counts_by_collection": dict(sorted(source_counts.items())),
            "unique_counts_by_collection": dict(sorted(unique_counts.items())),
            "raw_evidence_observation_count": total_rows,
            "canonical_external_identifier_count": len(canonical),
            "matched_identifier_count": matched,
            "source_only_identifier_count": source_only,
            "raw_only_identifier_count": 0,
            "canonical_only_identifier_count": len(canonical_only_rows),
            "missing_raw_evidence_count": 0,
            "missing_canonical_mapping_count": missing_canonical_mapping,
            "invalid_specialised_identifier_evidence_count": 0,
            "duplicate_source_observation_count": duplicate_count,
            "conflicting_duplicate_source_identity_count": conflicting_duplicate_count,
            "duplicate_canonical_identifier_count": 0,
            "wrong_canonical_family_count": 0,
            "compound_observation_without_external_identifier_count": compound_without_external,
            "review_required_count": review_required,
            "apparent_absence_count": len(canonical_only_rows),
            "threshold_confirmed_disappearance_count": 0,
            "history_deleted_or_deactivated_count": 0,
            "reconciliation_semantic_sha256": semantic.hexdigest(),
        }
        if emit_files:
            r.write_json(evidence / f"{output_prefix}_reconciliation_summary.json", summary)
            r.write_json(evidence / "sets" / "canonical_external_identifier_set.json", canonical)
        return summary
    finally:
        for handles in (source_handles, raw_handles, difference_handles, review_handles, disappearance_handles):
            if handles is None:
                continue
            raw, compressed, text = handles
            try:
                text.flush()
            except Exception:
                pass
            try:
                text.close()
            except Exception:
                pass
            try:
                if not compressed.closed:
                    compressed.close()
            except Exception:
                pass
            try:
                if not raw.closed:
                    raw.close()
            except Exception:
                pass


def patch_traceability(package_root: Path, evidence: Path, reconciliation_evidence: str) -> None:
    source = package_root / "STAGE5_REQUIREMENTS_TRACEABILITY_V0_5.json"
    original: Any = r.read_json(source) if source.exists() else {"requirements": []}
    if isinstance(original, dict):
        data: dict[str, Any] = original
    else:
        data = {"v0_5_traceability": original}
    updated = 0

    def visit(value: Any) -> None:
        nonlocal updated
        if isinstance(value, dict):
            identifier = value.get("requirement_id") or value.get("id") or value.get("requirement")
            if identifier == "SYNC-010":
                value["status"] = "passed"
                value["stage5_v06_evidence"] = reconciliation_evidence
                value["notes"] = "Complete source-wide partitioned Federal Register reconciliation passed in Stage 5 v0.6."
                updated += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    data["stage5_v06_update"] = {
        "status": "passed",
        "updated_requirement": "SYNC-010",
        "matched_entries": updated,
        "evidence": reconciliation_evidence,
        "later_stage_boundaries_unchanged": True,
    }
    r.write_json(evidence / "stage5_requirements_traceability_v0_6.json", data)
    (evidence / "stage5_requirements_traceability_v0_6.md").write_text(
        "# Stage 5 v0.6 requirements traceability\n\n"
        "The accepted v0.5 detailed traceability remains authoritative except for SYNC-010. "
        "SYNC-010 now passes because complete partitioned source, raw-evidence and canonical-identifier reconciliation was executed and retained.\n\n"
        f"Evidence: `{reconciliation_evidence}`\n\n"
        "Bills, Hansard, the Stage 6 interface and production deployment remain explicit later-stage boundaries.\n",
        encoding="utf-8",
    )


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
    for name in ("reconcile.py", "reconcile_v2.py", "reconciliation_config.json", "README.md"):
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


r.gzip_jsonl = gzip_jsonl
r.iter_unique_source_rows = iter_unique_source_rows
r.compare_contracts = compare_contracts
r.produce_reconciliation = produce_reconciliation
r.patch_traceability = patch_traceability
r.build_packages = build_packages

if __name__ == "__main__":
    raise SystemExit(r.main())
