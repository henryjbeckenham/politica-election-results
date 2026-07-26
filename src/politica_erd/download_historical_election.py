from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .build import PROJECT_ROOT
from .download_sources import sha256_file, source_signature
from .historical_sources import (
    acquisition_plan,
    election_for_year,
    election_id,
    load_catalogue,
)


USER_AGENT = "Politica-ERD/1.5.0 (historical-election-acquisition)"


class HistoricalDownloadError(RuntimeError):
    """Raised when an official historical source cannot be acquired safely."""


def _download_one(
    *,
    url: str,
    destination: Path,
    force: bool,
    retries: int,
    timeout: int,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and not force:
        return {
            "status": "reused",
            "size_bytes": destination.stat().st_size,
            "sha256": sha256_file(destination),
        }

    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                expected_length = response.headers.get("Content-Length")
                digest = hashlib.sha256()
                size = 0
                with partial.open("wb") as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
                        digest.update(block)
                        size += len(block)
                if expected_length is not None and size != int(expected_length):
                    raise HistoricalDownloadError(
                        f"Incomplete response for {destination.name}: "
                        f"received {size} bytes; expected {expected_length}."
                    )
                if size == 0:
                    raise HistoricalDownloadError(
                        f"The official source returned no bytes: {url}"
                    )
                os.replace(partial, destination)
                return {
                    "status": "downloaded",
                    "size_bytes": size,
                    "sha256": digest.hexdigest(),
                }
        except (OSError, urllib.error.URLError, HistoricalDownloadError) as error:
            last_error = error
            partial.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise HistoricalDownloadError(
        f"Unable to acquire {destination.name} after {retries} attempts: {last_error}"
    )


def _signature_record(project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    destination = project_root / record["path"]
    row_count, schema_signature, source_signature_rows = source_signature(destination)
    archive_members: list[str] = []
    if destination.suffix.casefold() == ".zip":
        import zipfile

        with zipfile.ZipFile(destination) as archive:
            archive_members = sorted(archive.namelist())
    return {
        **record,
        "row_count": row_count,
        "schema_signature_sha256": schema_signature,
        "source_signature": source_signature_rows,
        "archive_members": archive_members,
    }


def _checksum_contract(project_root: Path, year: int) -> dict[str, str] | None:
    path = project_root / "config" / f"source_checksums_{year}.yml"
    if not path.is_file():
        return None
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        str(key): str(value).lower()
        for key, value in (document.get("sha256_by_source_key") or {}).items()
    }


def download_election(
    year: int,
    *,
    project_root: Path = PROJECT_ROOT,
    force: bool = False,
    workers: int = 8,
    retries: int = 4,
    timeout: int = 300,
    require_checksum_contract: bool = False,
) -> dict[str, Any]:
    catalogue = load_catalogue(project_root / "config" / "source_catalogue_historical.yml")
    election = election_for_year(catalogue, year)
    if election.get("tier") != "full_tally":
        raise HistoricalDownloadError(
            f"{year} is not governed by the modern AEC full-tally acquisition route."
        )
    plan = acquisition_plan(catalogue, year, include_corroboration=False)
    if any(item.acquisition_type != "direct_file" for item in plan):
        raise HistoricalDownloadError(
            f"The {year} primary plan contains a non-file source and cannot use this route."
        )
    profile = catalogue["modern_profiles"][election["modern_profile"]]
    expected_count = int(
        election.get(
            "expected_primary_source_count",
            profile["expected_primary_source_count"],
        )
    )
    if len(plan) != expected_count:
        raise HistoricalDownloadError(
            f"The governed {year} plan contains {len(plan)} files; expected {expected_count}."
        )

    checksum_contract = _checksum_contract(project_root, year)
    if require_checksum_contract and checksum_contract is None:
        raise HistoricalDownloadError(
            f"The checksum contract for {year} is not installed."
        )

    downloaded: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                _download_one,
                url=item.url,
                destination=project_root / item.destination,
                force=force,
                retries=retries,
                timeout=timeout,
            ): item
            for item in plan
        }
        for future in as_completed(futures):
            item = futures[future]
            result = future.result()
            expected_sha = (checksum_contract or {}).get(item.source_key)
            if expected_sha and result["sha256"].lower() != expected_sha:
                raise HistoricalDownloadError(
                    f"Checksum mismatch for {item.source_key}: "
                    f"{result['sha256']} != {expected_sha}"
                )
            downloaded[item.source_key] = result

    records: list[dict[str, Any]] = []
    for item in plan:
        path = Path(item.destination)
        records.append(
            {
                "key": item.source_key,
                "file": path.name,
                "family": item.family,
                "chamber": item.chamber,
                "url": item.url,
                "path": path.as_posix(),
                "required": item.required,
                "size_bytes": downloaded[item.source_key]["size_bytes"],
                "sha256": downloaded[item.source_key]["sha256"],
                "acquisition_status": downloaded[item.source_key]["status"],
            }
        )

    # Signature work is deliberately bounded separately from network concurrency.
    signed: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, workers))) as executor:
        futures = {
            executor.submit(_signature_record, project_root, record): record["key"]
            for record in records
        }
        for future in as_completed(futures):
            signed[futures[future]] = future.result()
    records = [signed[item.source_key] for item in plan]

    event = str(election["aec_event_id"])
    web_root = election["web_root"]
    case = election["menu_case"]
    base = f"https://results.aec.gov.au/{event}/{web_root}"
    manifest = {
        "manifest_version": "1.0.0",
        "event_id": event,
        "election_id": election_id(election),
        "election_year": year,
        "election_date": str(election["date"]),
        "authority_id": "authority_aec",
        "phase": "final",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "landing_pages": {
            "house": f"{base}/HouseDownloadsMenu-{event}-{case}.htm",
            "senate": f"{base}/SenateDownloadsMenu-{event}-{case}.htm",
            "general": f"{base}/GeneralDownloadsMenu-{event}-{case}.htm",
        },
        "source_count": len(records),
        "total_size_bytes": sum(item["size_bytes"] for item in records),
        "checksum_contract_present": checksum_contract is not None,
        "sources": records,
    }
    output = project_root / "data" / "manifests" / f"aec_{year}_sources.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Acquire a governed modern historical AEC election source set."
    )
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--require-checksums", action="store_true")
    args = parser.parse_args()
    manifest = download_election(
        args.year,
        force=args.force,
        workers=args.workers,
        require_checksum_contract=args.require_checksums,
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "election_id": manifest["election_id"],
                "source_count": manifest["source_count"],
                "total_size_bytes": manifest["total_size_bytes"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
