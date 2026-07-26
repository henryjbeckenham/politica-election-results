#!/usr/bin/env python3
"""Verify a clean Stage 3 -> 14.2 -> 14.3 -> 14.4 upgrade in isolation."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from collections import defaultdict
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path

import duckdb

from politica_erd.app.config import AppSettings
from politica_erd.app.explorer import ElectionExplorer
from politica_erd.app.publication import FEEDS, PublicationFilters, VisualisationFeedService
from politica_erd.app.service import APP_VERSION, JobService
from politica_erd.app.visualisations import VisualisationContractService
from politica_erd.install_2016_release import ELECTION_2016, install_2016_release
from politica_erd.install_2019_release import ELECTION_2019, install_2019_release
from politica_erd.install_2022_release import (
    ELECTION_2022,
    ELECTION_2025,
    install_2022_release,
)
from politica_erd.static_site import StaticWebsitePublisher
from politica_erd.tcp_measures import TcpReportedPercentage, classify_tcp_reported_percentages


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def overlay_application(project: Path) -> None:
    """Install the Stage 14.4 application payload without touching project data."""

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
        shutil.copytree(
            PROJECT_ROOT / directory,
            project / directory,
            dirs_exist_ok=True,
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
        source = PROJECT_ROOT / filename
        if source.is_file():
            shutil.copy2(source, project / filename)


def copy_stage14_2_assets(core_zip: Path, data_zips: list[Path], project: Path) -> None:
    """Reconstruct the exact multipart Stage 14.2 installer payload."""

    with tempfile.TemporaryDirectory(prefix="politica-stage14-2-assets-") as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(core_zip) as archive:
            archive.extractall(staging / "core")
        payloads = list((staging / "core").glob("*/payload"))
        if len(payloads) != 1:
            raise RuntimeError("The Stage 14.2 core archive has an unexpected layout.")
        payload = payloads[0]
        shutil.copytree(
            payload / "data" / "stage14_2",
            project / "data" / "stage14_2",
            dirs_exist_ok=True,
        )
        manifest_root = project / "data" / "manifests"
        manifest_root.mkdir(parents=True, exist_ok=True)
        for source in (payload / "data" / "manifests").glob("aec_2022*"):
            shutil.copy2(source, manifest_root / source.name)
        (project / "dist").mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            payload / "dist" / "stage_14_2_2022_import_report.json",
            project / "dist" / "stage_14_2_2022_import_report.json",
        )
        for index, data_zip in enumerate(data_zips):
            with zipfile.ZipFile(data_zip) as archive:
                archive.extractall(staging / f"data-{index}")
            asset_root = staging / f"data-{index}" / "stage14_2_assets" / "data"
            if not asset_root.is_dir():
                raise RuntimeError(f"The Stage 14.2 data archive is malformed: {data_zip.name}")
            shutil.copytree(asset_root, project / "data", dirs_exist_ok=True)


def copy_stage14_3_assets(project: Path) -> None:
    pairs = (
        (PROJECT_ROOT / "data" / "stage14_3", project / "data" / "stage14_3"),
        (PROJECT_ROOT / "data" / "parquet" / "aec_2019", project / "data" / "parquet" / "aec_2019"),
        (PROJECT_ROOT / "data" / "raw" / "aec" / "2019_federal", project / "data" / "raw" / "aec" / "2019_federal"),
    )
    for source, target in pairs:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
    manifest_root = project / "data" / "manifests"
    manifest_root.mkdir(parents=True, exist_ok=True)
    for source in (PROJECT_ROOT / "data" / "manifests").glob("aec_2019*"):
        shutil.copy2(source, manifest_root / source.name)
    (project / "dist").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PROJECT_ROOT / "dist" / "stage_14_3_2019_import_report.json",
        project / "dist" / "stage_14_3_2019_import_report.json",
    )


def copy_stage14_4_assets(project: Path) -> None:
    """Copy only manifest-governed 2016 assets into the isolated project."""

    shutil.copytree(
        PROJECT_ROOT / "data" / "stage14_4" / "tables",
        project / "data" / "stage14_4" / "tables",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        PROJECT_ROOT / "data" / "parquet" / "aec_2016",
        project / "data" / "parquet" / "aec_2016",
        dirs_exist_ok=True,
    )
    source_manifest = json.loads(
        (PROJECT_ROOT / "data" / "manifests" / "aec_2016_sources.json").read_text(
            encoding="utf-8"
        )
    )
    for row in source_manifest["sources"]:
        source = PROJECT_ROOT / row["path"]
        target = project / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest_root = project / "data" / "manifests"
    manifest_root.mkdir(parents=True, exist_ok=True)
    for source in (PROJECT_ROOT / "data" / "manifests").glob("aec_2016*"):
        shutil.copy2(source, manifest_root / source.name)
    (project / "dist").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PROJECT_ROOT / "dist" / "stage_14_4_2016_import_report.json",
        project / "dist" / "stage_14_4_2016_import_report.json",
    )


def upgrade_stage3_fixture(database: Path) -> int:
    """Bring the archived baseline to the installed Stage 9.1 TCP contract."""

    connection = duckdb.connect(str(database))
    try:
        rows = connection.execute(
            """SELECT percentage.vote_result_id, percentage.contest_id,
                      percentage.decimal_value, votes.integer_value
               FROM results.vote_result percentage
               JOIN results.vote_result votes
                 ON votes.election_id=percentage.election_id
                AND votes.contest_id=percentage.contest_id
                AND votes.candidacy_id=percentage.candidacy_id
                AND votes.result_type=percentage.result_type
                AND votes.vote_type='total'
                AND votes.measure_type='votes'
                AND votes.source_revision_id=percentage.source_revision_id
               WHERE percentage.election_id=?
                 AND percentage.result_type='tcp'
                 AND percentage.vote_type='total'
                 AND percentage.measure_type IN ('swing','vote_share')
                 AND percentage.record_status='active'""",
            [ELECTION_2025],
        ).fetchall()
        grouped: dict[str, list[tuple]] = defaultdict(list)
        for row in rows:
            grouped[row[1]].append(row)
        corrected = 0
        for contest_id, pair in grouped.items():
            expected = classify_tcp_reported_percentages(
                (
                    TcpReportedPercentage(Decimal(str(row[2])), int(row[3]))
                    for row in pair
                ),
                context=f"integration fixture {contest_id}",
            )
            for vote_result_id, _contest_id, _reported, _votes in pair:
                corrected += connection.execute(
                    """UPDATE results.vote_result SET measure_type=?
                       WHERE vote_result_id=? AND measure_type<>?""",
                    [expected, vote_result_id, expected],
                ).fetchone()[0]
        connection.execute("CHECKPOINT")
        return corrected
    finally:
        connection.close()


def verify(
    base_zip: Path,
    stage14_2_core: Path,
    stage14_2_data: list[Path],
    work_root: Path | None = None,
) -> dict:
    workspace = (
        nullcontext(str(work_root))
        if work_root is not None
        else tempfile.TemporaryDirectory(prefix="politica-stage14-4-integration-")
    )
    with workspace as temporary:
        root = Path(temporary)
        root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(base_zip) as archive:
            archive.extractall(root)
        project = root / "Politica_Election_Results_Database"
        base_database = project / "data" / "database" / "politica_election_results.duckdb"
        corrected_tcp_rows = upgrade_stage3_fixture(base_database)
        if corrected_tcp_rows != 34:
            raise RuntimeError(
                f"The archived base required {corrected_tcp_rows} TCP corrections; expected 34."
            )
        base_sha256 = sha256(base_database)
        overlay_application(project)
        copy_stage14_2_assets(stage14_2_core, stage14_2_data, project)
        copy_stage14_3_assets(project)
        copy_stage14_4_assets(project)

        settings = AppSettings(
            project_root=project,
            base_database=base_database,
            app_data=project / "data" / "app",
        )
        stage14_2 = install_2022_release(settings)
        if stage14_2["status"] != "INSTALLED_AND_ACTIVATED":
            raise RuntimeError(f"Unexpected Stage 14.2 status: {stage14_2['status']}")
        stage14_2_database = Path(stage14_2["database_path"])
        if not stage14_2_database.is_absolute():
            stage14_2_database = project / stage14_2_database
        stage14_2_sha256 = stage14_2["database_sha256"]

        stage14_3 = install_2019_release(settings)
        if stage14_3["status"] != "INSTALLED_AND_ACTIVATED":
            raise RuntimeError(f"Unexpected Stage 14.3 status: {stage14_3['status']}")
        if sha256(base_database) != base_sha256:
            raise RuntimeError("The immutable 2025 baseline changed during integration.")
        if sha256(stage14_2_database) != stage14_2_sha256:
            raise RuntimeError("The immutable 2025 + 2022 release changed during Stage 14.3.")
        stage14_3_database = Path(stage14_3["database_path"])
        if not stage14_3_database.is_absolute():
            stage14_3_database = project / stage14_3_database
        stage14_3_sha256 = stage14_3["database_sha256"]

        stage14_4 = install_2016_release(settings)
        if stage14_4["status"] != "INSTALLED_AND_ACTIVATED":
            raise RuntimeError(f"Unexpected Stage 14.4 status: {stage14_4['status']}")
        if sha256(base_database) != base_sha256:
            raise RuntimeError("The immutable 2025 baseline changed during Stage 14.4.")
        if sha256(stage14_2_database) != stage14_2_sha256:
            raise RuntimeError("The immutable 2025 + 2022 release changed during Stage 14.4.")
        if sha256(stage14_3_database) != stage14_3_sha256:
            raise RuntimeError(
                "The immutable 2025 + 2022 + 2019 release changed during Stage 14.4."
            )

        service = JobService(settings)
        explorer = ElectionExplorer(
            service.governed_database,
            service._database_external_root,
            app_version=APP_VERSION,
        )
        feeds = VisualisationFeedService(
            explorer,
            service.governed_release_identity,
            composition_contract_path=project / "config" / "parliament_composition_48th.yml",
        )
        visualisations = VisualisationContractService(
            project / "config" / "visualisation_contract.yml",
            feeds,
            service.governed_release_identity,
            app_version=APP_VERSION,
        )
        election_ids = [row["election_id"] for row in feeds.catalogue()["elections"]]
        expected_elections = [ELECTION_2025, ELECTION_2022, ELECTION_2019, ELECTION_2016]
        if election_ids != expected_elections:
            raise RuntimeError(f"Unexpected election catalogue: {election_ids}")
        observed = {
            election_id: {
                feed_id: feeds.build(
                    feed_id, PublicationFilters(election_id=election_id)
                ).row_count
                for feed_id in FEEDS
            }
            for election_id in election_ids
        }
        expected_views = {
            ELECTION_2025: {"house_seat_results": 150, "senate_composition": 76},
            ELECTION_2022: {"house_seat_results": 151, "senate_composition": 40},
            ELECTION_2019: {"house_seat_results": 151, "senate_composition": 40},
            ELECTION_2016: {"house_seat_results": 150, "senate_composition": 76},
        }
        for election_id, expected in expected_views.items():
            for feed_id, count in expected.items():
                if observed[election_id][feed_id] != count:
                    raise RuntimeError(
                        f"Unexpected {election_id} {feed_id} count: {observed[election_id][feed_id]}"
                    )
        boundary_counts = {
            election_id: visualisations.catalogue(election_id)["boundary_geometry"]["feature_count"]
            for election_id in election_ids
        }
        expected_boundaries = {
            ELECTION_2025: 150,
            ELECTION_2022: 151,
            ELECTION_2019: 151,
            ELECTION_2016: 150,
        }
        if boundary_counts != expected_boundaries:
            raise RuntimeError(f"Unexpected boundary coverage: {boundary_counts}")

        publisher = StaticWebsitePublisher(
            settings,
            feeds,
            visualisations,
            service.governed_database,
            service.governed_release_identity,
            results_root=project / "src" / "politica_erd" / "app" / "results",
        )
        static_result = publisher.build()
        expected_feed_count = len(expected_elections) * len(FEEDS)
        if static_result["verification"]["feed_count"] != expected_feed_count:
            raise RuntimeError(
                f"The static site has {static_result['verification']['feed_count']} feeds; expected {expected_feed_count}."
            )

        return {
            "status": "PASS",
            "application_version": APP_VERSION,
            "base_database_sha256_preserved": base_sha256,
            "stage14_2_database_sha256_preserved": stage14_2_sha256,
            "stage14_3_database_sha256_preserved": stage14_3_sha256,
            "fixture_tcp_rows_upgraded_to_stage9_1": corrected_tcp_rows,
            "active_release_id": stage14_4["release_id"],
            "active_database_sha256": stage14_4["database_sha256"],
            "elections": election_ids,
            "feed_row_counts": observed,
            "boundary_feature_counts": boundary_counts,
            "static_site": {
                "site_release_id": static_result["site_release_id"],
                "feed_count": static_result["verification"]["feed_count"],
                "file_count": static_result["verification"]["file_count"],
                "export_size_bytes": static_result["export_size_bytes"],
                "export_sha256": static_result["export_sha256"],
            },
            "failures": [],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-zip", type=Path, required=True)
    parser.add_argument("--stage14-2-core", type=Path, required=True)
    parser.add_argument("--stage14-2-data", type=Path, nargs=4, required=True)
    parser.add_argument(
        "--work-root",
        type=Path,
        help="Retain the isolated project at this path for diagnostic reruns.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dist" / "stage_14_4_integration_report.json",
    )
    args = parser.parse_args()
    report = verify(
        args.base_zip,
        args.stage14_2_core,
        list(args.stage14_2_data),
        args.work_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
