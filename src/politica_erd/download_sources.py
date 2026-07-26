from __future__ import annotations

import argparse
import csv
import hashlib
import json
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .build import PROJECT_ROOT


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _line_count(member) -> int:
    count = 0
    last = b""
    while chunk := member.read(1024 * 1024):
        count += chunk.count(b"\n")
        last = chunk[-1:]
    return count + (1 if last and last != b"\n" else 0)


def source_signature(path: Path) -> tuple[int | None, str | None, list[str]]:
    if path.suffix.lower() == ".zip":
        row_count = 0
        schema_parts: list[str] = []
        first_signature: list[str] = []
        with zipfile.ZipFile(path) as archive:
            for member_name in sorted(
                name for name in archive.namelist() if name.lower().endswith(".csv")
            ):
                with archive.open(member_name) as raw:
                    wrapper = raw.read(1024 * 1024).decode("utf-8-sig").splitlines()
                first = wrapper[0] if wrapper else ""
                has_metadata = "Event:" in first
                header_line = wrapper[1] if has_metadata and len(wrapper) > 1 else first
                headers = next(csv.reader([header_line])) if header_line else []
                with archive.open(member_name) as raw:
                    lines = _line_count(raw)
                row_count += max(0, lines - (2 if has_metadata else 1))
                schema_parts.extend([member_name, *headers])
                if not first_signature:
                    first_signature = [first, *headers]
        signature = hashlib.sha256("\x1f".join(schema_parts).encode("utf-8")).hexdigest()
        return row_count, signature, first_signature
    if path.suffix.lower() != ".csv":
        return None, None, []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        first_line = handle.readline().rstrip("\r\n")
        reader = csv.reader(handle)
        headers = next(reader)
        row_count = sum(1 for _ in reader)
    signature = hashlib.sha256("\x1f".join(headers).encode("utf-8")).hexdigest()
    return row_count, signature, [first_line, *headers]


def download_sources(project_root: Path = PROJECT_ROOT, force: bool = False) -> dict:
    catalogue_path = project_root / "config" / "source_catalogue_2025.yml"
    catalogue = yaml.safe_load(catalogue_path.read_text(encoding="utf-8"))
    raw_dir = project_root / catalogue["raw_directory"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    base_url = catalogue["base_download_url"].rstrip("/")
    records = []
    for source in catalogue["sources"]:
        url = source.get("url", f"{base_url}/{source['file']}")
        destination = raw_dir / source["file"]
        if force or not destination.exists():
            temporary = destination.with_suffix(destination.suffix + ".part")
            request = urllib.request.Request(url, headers={"User-Agent": "Politica-ERD/0.2.0"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(destination)
        row_count, schema_signature, signature = source_signature(destination)
        archive_members = []
        if destination.suffix.lower() == ".zip":
            with zipfile.ZipFile(destination) as archive:
                archive_members = sorted(archive.namelist())
        records.append(
            {
                **source,
                "url": url,
                "path": str(destination.relative_to(project_root)),
                "size_bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
                "row_count": row_count,
                "schema_signature_sha256": schema_signature,
                "source_signature": signature,
                "archive_members": archive_members,
            }
        )
    manifest = {
        "event_id": catalogue["event_id"],
        "election_id": catalogue["election_id"],
        "authority_id": catalogue["authority_id"],
        "phase": catalogue["phase"],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "landing_pages": catalogue["landing_pages"],
        "source_count": len(records),
        "total_size_bytes": sum(record["size_bytes"] for record in records),
        "sources": records,
    }
    manifest_path = project_root / "data" / "manifests" / "aec_2025_sources.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = download_sources(force=args.force)
    print(json.dumps({"source_count": manifest["source_count"], "total_size_bytes": manifest["total_size_bytes"]}, indent=2))


if __name__ == "__main__":
    main()
