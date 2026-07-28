from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
from typing import Any


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def reconstruct(source: Path, output: Path, report_path: Path) -> None:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    archive_spec = manifest["archive"]
    encoded_parts: list[str] = []
    chunk_rows: list[dict[str, Any]] = []
    for sequence, relative in enumerate(archive_spec["chunk_paths"]):
        path = source / relative
        body = path.read_bytes()
        text = body.decode("ascii").strip()
        chunk_rows.append(
            {
                "sequence": sequence,
                "path": relative,
                "byte_count": len(body),
                "base64_char_count": len(text),
                "sha256": sha256(body),
            }
        )
        encoded_parts.append(text)
    encoded = "".join(encoded_parts)
    if len(encoded) != archive_spec["base64_char_count"]:
        raise SystemExit("combined base64 character count mismatch")
    gzip_bytes = base64.b64decode(encoded, validate=True)
    if len(gzip_bytes) != archive_spec["gzip_byte_count"]:
        raise SystemExit("gzip byte count mismatch")
    if sha256(gzip_bytes) != archive_spec["gzip_sha256"]:
        raise SystemExit("gzip SHA-256 mismatch")
    tar_bytes = gzip.decompress(gzip_bytes)
    if len(tar_bytes) != archive_spec["tar_byte_count"]:
        raise SystemExit("tar byte count mismatch")
    if sha256(tar_bytes) != archive_spec["tar_sha256"]:
        raise SystemExit("tar SHA-256 mismatch")

    output.mkdir(parents=True, exist_ok=True)
    expected_names = set(manifest["files"])
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
        members = archive.getmembers()
        actual_names = {member.name for member in members if member.isfile()}
        if actual_names != expected_names:
            raise SystemExit("tar member set mismatch")
        for member in members:
            destination = (output / member.name).resolve()
            if output.resolve() not in destination.parents:
                raise SystemExit(f"unsafe tar member: {member.name}")
        archive.extractall(output, filter="data")

    file_rows: list[dict[str, Any]] = []
    for name, expected in sorted(manifest["files"].items()):
        path = output / name
        body = path.read_bytes()
        actual_mode = format(path.stat().st_mode & 0o777, "04o")
        passed = (
            len(body) == expected["byte_count"]
            and sha256(body) == expected["sha256"]
            and actual_mode == expected["mode"]
        )
        file_rows.append(
            {
                "path": name,
                "expected_byte_count": expected["byte_count"],
                "actual_byte_count": len(body),
                "expected_sha256": expected["sha256"],
                "actual_sha256": sha256(body),
                "expected_mode": expected["mode"],
                "actual_mode": actual_mode,
                "passed": passed,
            }
        )
        if not passed:
            raise SystemExit(f"reconstructed file mismatch: {name}")
    os.chmod(output / "run_full_reconciliation.sh", 0o755)
    write_json(
        report_path,
        {
            "status": "passed",
            "transport": "independently retained base64 chunks with full archive and file gates",
            "chunks": chunk_rows,
            "archive": {
                "base64_char_count": len(encoded),
                "gzip_byte_count": len(gzip_bytes),
                "gzip_sha256": sha256(gzip_bytes),
                "tar_byte_count": len(tar_bytes),
                "tar_sha256": sha256(tar_bytes),
            },
            "files": file_rows,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    reconstruct(args.source, args.output, args.report)


if __name__ == "__main__":
    main()
