"""Short post-installation smoke verification for Stage 14.3."""

from __future__ import annotations

import json

from .app.config import AppSettings
from .app.explorer import ElectionExplorer
from .app.publication import PublicationFilters, VisualisationFeedService
from .app.service import APP_VERSION, JobService
from .app.visualisations import VisualisationContractService
from .install_2019_release import ELECTION_2019, ELECTION_2022, ELECTION_2025


def verify() -> dict:
    settings = AppSettings.from_environment()
    service = JobService(settings)
    database = service.governed_database()
    explorer = ElectionExplorer(
        service.governed_database,
        service._database_external_root,
        app_version=APP_VERSION,
    )
    feeds = VisualisationFeedService(
        explorer,
        service.governed_release_identity,
        composition_contract_path=settings.project_root
        / "config"
        / "parliament_composition_48th.yml",
    )
    visualisations = VisualisationContractService(
        settings.project_root / "config" / "visualisation_contract.yml",
        feeds,
        service.governed_release_identity,
        app_version=APP_VERSION,
    )
    catalogue = feeds.catalogue()
    election_ids = [item["election_id"] for item in catalogue["elections"]]
    checks = {
        "election_catalogue": (
            election_ids,
            [ELECTION_2025, ELECTION_2022, ELECTION_2019],
        ),
        "2025_house_seats": (
            feeds.build(
                "house_seat_results", PublicationFilters(election_id=ELECTION_2025)
            ).row_count,
            150,
        ),
        "2022_house_seats": (
            feeds.build(
                "house_seat_results", PublicationFilters(election_id=ELECTION_2022)
            ).row_count,
            151,
        ),
        "2019_house_seats": (
            feeds.build(
                "house_seat_results", PublicationFilters(election_id=ELECTION_2019)
            ).row_count,
            151,
        ),
        "2025_senate_view": (
            feeds.build(
                "senate_composition", PublicationFilters(election_id=ELECTION_2025)
            ).row_count,
            76,
        ),
        "2022_senate_view": (
            feeds.build(
                "senate_composition", PublicationFilters(election_id=ELECTION_2022)
            ).row_count,
            40,
        ),
        "2019_senate_view": (
            feeds.build(
                "senate_composition", PublicationFilters(election_id=ELECTION_2019)
            ).row_count,
            40,
        ),
        "2025_boundaries": (
            visualisations.catalogue(ELECTION_2025)["boundary_geometry"]["feature_count"],
            150,
        ),
        "2022_boundaries": (
            visualisations.catalogue(ELECTION_2022)["boundary_geometry"]["feature_count"],
            151,
        ),
        "2019_boundaries": (
            visualisations.catalogue(ELECTION_2019)["boundary_geometry"]["feature_count"],
            151,
        ),
    }
    failures = [
        {"check": name, "observed": observed, "expected": expected}
        for name, (observed, expected) in checks.items()
        if observed != expected
    ]
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False))
    return {
        "status": "PASS",
        "application_version": APP_VERSION,
        "database_path": str(database),
        "release": service.governed_release_identity(),
        "checks": len(checks),
        "failures": [],
    }


def main() -> None:
    print(json.dumps(verify(), indent=2))


if __name__ == "__main__":
    main()
