"""Short post-installation smoke verification for Stage 14.5."""

from __future__ import annotations

import json

from .app.config import AppSettings
from .app.explorer import ElectionExplorer
from .app.publication import PublicationFilters, VisualisationFeedService
from .app.service import APP_VERSION, JobService
from .app.visualisations import VisualisationContractService
from .install_2013_release import (
    ELECTION_2013,
    ELECTION_2016,
    ELECTION_2019,
    ELECTION_2022,
    ELECTION_2025,
)


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
    elections = [
        (ELECTION_2025, 150, 76),
        (ELECTION_2022, 151, 40),
        (ELECTION_2019, 151, 40),
        (ELECTION_2016, 150, 76),
        (ELECTION_2013, 150, 40),
    ]
    catalogue = feeds.catalogue()
    election_ids = [item["election_id"] for item in catalogue["elections"]]
    checks: dict[str, tuple[object, object]] = {
        "election_catalogue": (election_ids, [item[0] for item in elections]),
    }
    for election_id, house_seats, senate_members in elections:
        year = election_id.split("_")[2]
        checks[f"{year}_house_seats"] = (
            feeds.build(
                "house_seat_results", PublicationFilters(election_id=election_id)
            ).row_count,
            house_seats,
        )
        checks[f"{year}_senate_view"] = (
            feeds.build(
                "senate_composition", PublicationFilters(election_id=election_id)
            ).row_count,
            senate_members,
        )
        checks[f"{year}_boundaries"] = (
            visualisations.catalogue(election_id)["boundary_geometry"]["feature_count"],
            house_seats,
        )
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
