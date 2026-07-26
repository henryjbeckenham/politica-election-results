#!/usr/bin/env python3
"""Create the deterministic multipart Stage 14.4 Mac update."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "stage14_4_release"
CORE_NAME = "Politica_Stage14_4_v1.6.0_Update"
ASSET_ROOT = "stage14_4_assets"
DATA_PACKAGES = {
    "Politica_Stage14_4_v1.6.0_Data_1_NSW.zip": {"NSW"},
    "Politica_Stage14_4_v1.6.0_Data_2_VIC_SA.zip": {"VIC", "SA"},
    "Politica_Stage14_4_v1.6.0_Data_3_QLD_WA.zip": {"QLD", "WA"},
    "Politica_Stage14_4_v1.6.0_Data_4_ACT_NT_TAS_and_counts.zip": {
        "ACT",
        "NT",
        "TAS",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_zip(
    destination: Path,
    files: list[tuple[Path, str]],
    *,
    direct: bool = False,
) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    target = destination if direct else temporary
    if direct:
        destination.unlink(missing_ok=True)
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for source, relative in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(relative, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o100755 if source.stat().st_mode & stat.S_IXUSR else 0o100644
            info.external_attr = mode << 16
            info.create_system = 3
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    with target.open("rb") as handle:
        os.fsync(handle.fileno())
    if not direct:
        temporary.replace(destination)
    with zipfile.ZipFile(destination) as archive:
        damaged = archive.testzip()
        if damaged is not None:
            raise RuntimeError(
                f"The completed archive failed CRC validation at {damaged}: {destination}"
            )


def asset_files() -> dict[str, list[tuple[Path, str]]]:
    grouped = {name: [] for name in DATA_PACKAGES}
    state_to_package = {
        state: name for name, states in DATA_PACKAGES.items() for state in states
    }
    formal_root = ROOT / "data" / "parquet" / "aec_2016" / "formal_preferences"
    for state_directory in sorted(formal_root.glob("state=*")):
        state = state_directory.name.split("=", 1)[1]
        package = state_to_package[state]
        for path in sorted(state_directory.rglob("*.parquet")):
            relative = path.relative_to(ROOT).as_posix()
            grouped[package].append((path, f"{ASSET_ROOT}/{relative}"))

    # Fact Parquet belongs with the NSW package to keep all four archives below
    # the conservative portability ceiling used by the Work Mode filesystem.
    first_package = next(iter(DATA_PACKAGES))
    for path in sorted((ROOT / "data" / "parquet" / "aec_2016" / "facts").rglob("*.parquet")):
        grouped[first_package].append(
            (path, f"{ASSET_ROOT}/{path.relative_to(ROOT).as_posix()}")
        )

    source_manifest = json.loads(
        (ROOT / "data" / "manifests" / "aec_2016_sources.json").read_text(
            encoding="utf-8"
        )
    )
    for row in source_manifest["sources"]:
        path = ROOT / row["path"]
        state = None
        name = path.name
        if name.startswith("aec-senate-formalpreferences-20499-"):
            state = name.removeprefix(
                "aec-senate-formalpreferences-20499-"
            ).removesuffix(".zip")
        package = state_to_package[state] if state else list(DATA_PACKAGES)[-1]
        grouped[package].append(
            (path, f"{ASSET_ROOT}/{path.relative_to(ROOT).as_posix()}")
        )

    # The 54 compressed relational shards are distributed with the data
    # archives so the core installer remains safely below the transfer limit.
    # Largest-first placement keeps the four completed archives balanced.
    table_files = sorted(
        (ROOT / "data" / "stage14_4" / "tables").rglob("*.parquet"),
        key=lambda path: (-path.stat().st_size, path.as_posix()),
    )
    package_sizes = {
        name: sum(path.stat().st_size for path, _ in files)
        for name, files in grouped.items()
    }
    for path in table_files:
        package = min(
            grouped,
            key=lambda name: (package_sizes[name], name),
        )
        grouped[package].append(
            (path, f"{ASSET_ROOT}/{path.relative_to(ROOT).as_posix()}")
        )
        package_sizes[package] += path.stat().st_size
    return grouped


def copy_payload(destination: Path) -> None:
    payload = destination / "payload"
    payload.mkdir(parents=True)
    for directory in (
        "src",
        "config",
        "docs",
        "tests",
        "packaging",
        "visualisation",
        "schema",
        "scripts",
    ):
        source = ROOT / directory
        shutil.copytree(
            source,
            payload / directory,
            ignore=shutil.ignore_patterns("node_modules", ".cache", "*.pyc", "__pycache__"),
        )
    for filename in (
        ".gitignore",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "start_politica.command",
        "start_politica.bat",
        "configure_google_sheets.command",
    ):
        source = ROOT / filename
        if source.is_file():
            shutil.copy2(source, payload / filename)

    manifest_target = payload / "data" / "manifests"
    manifest_target.mkdir(parents=True)
    for path in sorted((ROOT / "data" / "manifests").glob("aec_2016*")):
        shutil.copy2(path, manifest_target / path.name)
    dist_target = payload / "dist"
    dist_target.mkdir(parents=True)
    for filename in (
        "stage_14_4_2016_build_manifest.json",
        "stage_14_4_2016_import_report.json",
        "stage_14_4_build_manifest.json",
        "stage_14_4_integration_report.json",
        "stage_14_4_test_report.json",
        "stage_14_4_unittest.log",
        "stage_14_4_stage13_1_rerun.log",
        "stage_14_4_browser.log",
    ):
        shutil.copy2(ROOT / "dist" / filename, dist_target / filename)


def build() -> dict:
    required = (
        ROOT / "dist" / "stage_14_4_integration_report.json",
        ROOT / "dist" / "stage_14_4_test_report.json",
        ROOT / "src" / "politica_erd" / "app" / "results" / "index.html",
    )
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"Stage 14.4 packaging prerequisite is missing: {path}")

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)

    grouped = asset_files()
    package_rows: list[dict] = []
    for name, files in grouped.items():
        destination = OUTPUT / name
        write_zip(destination, files)
        package_rows.append(
            {
                "filename": name,
                "size_bytes": destination.stat().st_size,
                "sha256": sha256(destination),
                "file_count": len(files),
                "uncompressed_size_bytes": sum(path.stat().st_size for path, _ in files),
            }
        )

    source_inventory = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for directory in ("src", "config", "docs", "visualisation", "schema")
        for path in sorted((ROOT / directory).rglob("*"))
        if path.is_file() and "node_modules" not in path.parts
    ]
    build_manifest = {
        "stage": "14.4",
        "application_version": "1.6.0",
        "status": "PASS",
        "source_file_count": len(source_inventory),
        "source_inventory_sha256": hashlib.sha256(
            json.dumps(source_inventory, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "data_packages": package_rows,
        "full_regression_report": "dist/stage_14_4_test_report.json",
        "clean_install_report": "dist/stage_14_4_integration_report.json",
    }
    (ROOT / "dist" / "stage_14_4_build_manifest.json").write_text(
        json.dumps(build_manifest, indent=2) + "\n", encoding="utf-8"
    )
    (ROOT / "data" / "manifests" / "stage14_4_asset_packages.json").write_text(
        json.dumps({"packages": package_rows}, indent=2) + "\n", encoding="utf-8"
    )

    with tempfile.TemporaryDirectory(prefix="politica-stage14-4-package-") as temporary:
        core = Path(temporary) / CORE_NAME
        core.mkdir()
        copy_payload(core)
        installer = (ROOT / "install_stage14_4.command").read_text(encoding="utf-8")
        replacements = {
            "__DATA_1_SHA256__": package_rows[0]["sha256"],
            "__DATA_2_SHA256__": package_rows[1]["sha256"],
            "__DATA_3_SHA256__": package_rows[2]["sha256"],
            "__DATA_4_SHA256__": package_rows[3]["sha256"],
        }
        for marker, value in replacements.items():
            installer = installer.replace(marker, value)
        installer_path = core / "install_stage14_4.command"
        installer_path.write_text(installer, encoding="utf-8")
        installer_path.chmod(0o755)
        shutil.copy2(ROOT / "packaging" / "READ_ME_STAGE14_4.md", core / "READ_ME_FIRST.md")
        core_files = [
            (path, f"{CORE_NAME}/{path.relative_to(core).as_posix()}")
            for path in sorted(core.rglob("*"))
            if path.is_file()
        ]
        core_zip = OUTPUT / f"{CORE_NAME}.zip"
        staged_core_zip = Path(temporary) / f"{CORE_NAME}.zip"
        # Build and validate the large core outside the mirrored workspace.
        # Only the already-complete archive is copied into the release folder.
        write_zip(staged_core_zip, core_files, direct=True)
        shutil.copy2(staged_core_zip, core_zip)
        with core_zip.open("rb") as handle:
            os.fsync(handle.fileno())
        with zipfile.ZipFile(core_zip) as archive:
            damaged = archive.testzip()
            if damaged is not None:
                raise RuntimeError(
                    f"The copied core archive failed CRC validation at {damaged}."
                )

    result = {
        "status": "PASS",
        "core": {
            "filename": core_zip.name,
            "size_bytes": core_zip.stat().st_size,
            "sha256": sha256(core_zip),
        },
        "data_packages": package_rows,
    }
    (OUTPUT / "release_inventory.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
