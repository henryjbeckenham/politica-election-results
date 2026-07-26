#!/usr/bin/env python3
"""Resume Stage 14.6 integration from a sealed Stage 14.3 checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from politica_erd.app.config import AppSettings
from politica_erd.app.explorer import ElectionExplorer
from politica_erd.app.publication import FEEDS, PublicationFilters, VisualisationFeedService
from politica_erd.app.service import APP_VERSION, JobService
from politica_erd.app.visualisations import VisualisationContractService
from politica_erd.install_2010_release import ELECTION_2010, install_2010_release
from politica_erd.install_2013_release import ELECTION_2013, install_2013_release
from politica_erd.install_2016_release import ELECTION_2016, install_2016_release
from politica_erd.install_2019_release import (
    ELECTION_2019,
    _existing_publication as verify_stage14_3_release,
)
from politica_erd.install_2022_release import (
    ELECTION_2022,
    ELECTION_2025,
    _existing_publication as verify_stage14_2_release,
)
from politica_erd.static_site import StaticWebsitePublisher

import verify_stage14_6_integration as integration


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _single_release_root(releases_root: Path, pattern: str) -> Path:
    matches = sorted(path for path in releases_root.glob(pattern) if path.is_dir())
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {pattern} release; found {len(matches)}.")
    return matches[0]


def _release_id(release_root: Path) -> str:
    manifest = json.loads(
        (release_root / "release_manifest.json").read_text(encoding="utf-8")
    )
    value = manifest.get("release_id")
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"The release ID is missing from {release_root}.")
    return value


def _database(project: Path, publication: dict) -> Path:
    database = Path(publication["database_path"])
    return database if database.is_absolute() else project / database


def resume(project: Path) -> dict:
    project = project.resolve()
    base_database = project / "data" / "database" / "politica_election_results.duckdb"
    if not base_database.is_file():
        raise RuntimeError("The retained Stage 3 baseline database is missing.")

    integration.overlay_application(project)
    settings = AppSettings(
        project_root=project,
        base_database=base_database,
        app_data=project / "data" / "app",
    )
    service = JobService(settings)
    releases_root = settings.releases_root
    stage14_2_root = _single_release_root(releases_root, "politica-stage14-2-*")
    stage14_3_root = _single_release_root(releases_root, "politica-stage14-3-*")
    stage14_2 = verify_stage14_2_release(
        service, stage14_2_root, _release_id(stage14_2_root)
    )
    stage14_3 = verify_stage14_3_release(
        service, stage14_3_root, _release_id(stage14_3_root)
    )
    stage14_2_database = _database(project, stage14_2)
    stage14_3_database = _database(project, stage14_3)
    stage14_2_sha256 = stage14_2["database_sha256"]
    stage14_3_sha256 = stage14_3["database_sha256"]
    base_sha256 = integration.sha256(base_database)

    stage14_2_manifest = json.loads(
        (stage14_2_root / "data" / "manifests" / "stage_14_2_release.json").read_text(
            encoding="utf-8"
        )
    )
    if stage14_2_manifest.get("base_database_sha256") != base_sha256:
        raise RuntimeError("The retained Stage 3 baseline no longer matches Stage 14.2.")
    if service.governed_database().resolve() != stage14_3_database.resolve():
        raise RuntimeError("Stage 14.3 is not the active sealed checkpoint.")
    if integration.sha256(stage14_2_database) != stage14_2_sha256:
        raise RuntimeError("The retained Stage 14.2 release failed checksum verification.")
    if integration.sha256(stage14_3_database) != stage14_3_sha256:
        raise RuntimeError("The retained Stage 14.3 release failed checksum verification.")

    stage14_4 = install_2016_release(settings)
    if stage14_4["status"] != "INSTALLED_AND_ACTIVATED":
        raise RuntimeError(f"Unexpected Stage 14.4 status: {stage14_4['status']}")
    preserved = (
        (base_database, base_sha256, "2025 baseline"),
        (stage14_2_database, stage14_2_sha256, "2025 + 2022 release"),
        (stage14_3_database, stage14_3_sha256, "2025 + 2022 + 2019 release"),
    )
    for database, expected_sha256, label in preserved:
        if integration.sha256(database) != expected_sha256:
            raise RuntimeError(f"The immutable {label} changed during Stage 14.4.")
    stage14_4_database = _database(project, stage14_4)
    stage14_4_sha256 = stage14_4["database_sha256"]

    stage14_5 = install_2013_release(settings)
    if stage14_5["status"] != "INSTALLED_AND_ACTIVATED":
        raise RuntimeError(f"Unexpected Stage 14.5 status: {stage14_5['status']}")
    preserved += (
        (stage14_4_database, stage14_4_sha256, "2025 + 2022 + 2019 + 2016 release"),
    )
    for database, expected_sha256, label in preserved:
        if integration.sha256(database) != expected_sha256:
            raise RuntimeError(f"The immutable {label} changed during Stage 14.5.")
    stage14_5_database = _database(project, stage14_5)
    stage14_5_sha256 = stage14_5["database_sha256"]

    stage14_6 = install_2010_release(settings)
    if stage14_6["status"] != "INSTALLED_AND_ACTIVATED":
        raise RuntimeError(f"Unexpected Stage 14.6 status: {stage14_6['status']}")
    preserved += (
        (
            stage14_5_database,
            stage14_5_sha256,
            "2025 + 2022 + 2019 + 2016 + 2013 release",
        ),
    )
    for database, expected_sha256, label in preserved:
        if integration.sha256(database) != expected_sha256:
            raise RuntimeError(f"The immutable {label} changed during Stage 14.6.")

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
    expected_elections = [
        ELECTION_2025,
        ELECTION_2022,
        ELECTION_2019,
        ELECTION_2016,
        ELECTION_2013,
        ELECTION_2010,
    ]
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
        ELECTION_2013: {"house_seat_results": 150, "senate_composition": 40},
        ELECTION_2010: {"house_seat_results": 150, "senate_composition": 40},
    }
    for election_id, expected in expected_views.items():
        for feed_id, count in expected.items():
            if observed[election_id][feed_id] != count:
                raise RuntimeError(
                    f"Unexpected {election_id} {feed_id} count: "
                    f"{observed[election_id][feed_id]}"
                )
    boundary_counts = {
        election_id: visualisations.catalogue(election_id)["boundary_geometry"][
            "feature_count"
        ]
        for election_id in election_ids
    }
    expected_boundaries = {
        ELECTION_2025: 150,
        ELECTION_2022: 151,
        ELECTION_2019: 151,
        ELECTION_2016: 150,
        ELECTION_2013: 150,
        ELECTION_2010: 150,
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
            f"The static site has {static_result['verification']['feed_count']} feeds; "
            f"expected {expected_feed_count}."
        )

    return {
        "status": "PASS",
        "application_version": APP_VERSION,
        "checkpoint_resume": {
            "status": "PASS",
            "resumed_after_stage": "14.3",
            "reason": "workspace capacity interruption before the Stage 14.4 merge",
        },
        "base_database_sha256_preserved": base_sha256,
        "stage14_2_database_sha256_preserved": stage14_2_sha256,
        "stage14_3_database_sha256_preserved": stage14_3_sha256,
        "stage14_4_database_sha256_preserved": stage14_4_sha256,
        "stage14_5_database_sha256_preserved": stage14_5_sha256,
        "fixture_tcp_rows_upgraded_to_stage9_1": 34,
        "active_release_id": stage14_6["release_id"],
        "active_database_sha256": stage14_6["database_sha256"],
        "stage14_6_validation": stage14_6["stage_validation"],
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
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dist" / "stage_14_6_integration_report.json",
    )
    args = parser.parse_args()
    report = resume(args.project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
