#!/usr/bin/env python3
"""Run the full Stage 14.2 merge, feed and static-site integration in isolation."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import duckdb

from politica_erd.app.config import AppSettings
from politica_erd.app.explorer import ElectionExplorer
from politica_erd.app.publication import PublicationFilters, VisualisationFeedService
from politica_erd.app.service import APP_VERSION, JobService
from politica_erd.app.visualisations import VisualisationContractService
from politica_erd.install_2022_release import ELECTION_2022, ELECTION_2025, install_2022_release
from politica_erd.static_site import StaticWebsitePublisher
from politica_erd.tcp_measures import TcpReportedPercentage, classify_tcp_reported_percentages


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_assets(destination: Path) -> None:
    pairs = (
        (PROJECT_ROOT / "data" / "stage14_2" / "tables", destination / "data" / "stage14_2" / "tables"),
        (PROJECT_ROOT / "data" / "parquet" / "aec_2022", destination / "data" / "parquet" / "aec_2022"),
        (PROJECT_ROOT / "data" / "raw" / "aec" / "2022_federal", destination / "data" / "raw" / "aec" / "2022_federal"),
    )
    for source, target in pairs:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
    manifest_root = destination / "data" / "manifests"
    manifest_root.mkdir(parents=True, exist_ok=True)
    for source in (PROJECT_ROOT / "data" / "manifests").glob("aec_2022*"):
        shutil.copy2(source, manifest_root / source.name)
    (destination / "dist").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PROJECT_ROOT / "dist" / "stage_14_2_2022_import_report.json",
        destination / "dist" / "stage_14_2_2022_import_report.json",
    )


def upgrade_stage3_fixture(database: Path) -> int:
    """Bring the archived full baseline to the installed Stage 9.1 TCP contract.

    The only available self-contained baseline archive predates the corrective
    Stage 9.1 application release.  The user's installed Stage 13.5 database
    already contains this correction, so the isolated fixture must apply the
    same semantic classification before it can represent the supported base.
    """

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


def verify(base_zip: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="politica-stage14-2-integration-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(base_zip) as archive:
            archive.extractall(root)
        project = root / "Politica_Election_Results_Database"
        base_database = project / "data" / "database" / "politica_election_results.duckdb"
        corrected_tcp_rows = upgrade_stage3_fixture(base_database)
        if corrected_tcp_rows != 34:
            raise RuntimeError(
                f"The archived base fixture required {corrected_tcp_rows} TCP corrections; expected 34."
            )
        base_sha256 = sha256(base_database)
        copy_assets(project)
        settings = AppSettings(
            project_root=project,
            base_database=base_database,
            app_data=project / "data" / "app",
        )
        installation = install_2022_release(settings)
        if installation["status"] != "INSTALLED_AND_ACTIVATED":
            raise RuntimeError(f"Unexpected installation status: {installation['status']}")
        if sha256(base_database) != base_sha256:
            raise RuntimeError("The immutable 2025 base database changed during integration.")

        service = JobService(settings)
        explorer = ElectionExplorer(
            service.governed_database,
            service._database_external_root,
            app_version=APP_VERSION,
        )
        feeds = VisualisationFeedService(
            explorer,
            service.governed_release_identity,
            composition_contract_path=PROJECT_ROOT / "config" / "parliament_composition_48th.yml",
        )
        visualisations = VisualisationContractService(
            PROJECT_ROOT / "config" / "visualisation_contract.yml",
            feeds,
            service.governed_release_identity,
            app_version=APP_VERSION,
        )
        catalogue = feeds.catalogue()
        election_ids = [item["election_id"] for item in catalogue["elections"]]
        if election_ids != [ELECTION_2025, ELECTION_2022]:
            raise RuntimeError(f"Unexpected election catalogue: {election_ids}")
        observed = {}
        for election_id in election_ids:
            observed[election_id] = {
                feed_id: feeds.build(
                    feed_id, PublicationFilters(election_id=election_id)
                ).row_count
                for feed_id in (
                    "house_candidate_results",
                    "house_seat_results",
                    "house_party_summary",
                    "senate_group_results",
                    "turnout_informality",
                    "declared_members",
                    "senate_count_progress",
                    "senate_count_movements",
                    "senate_composition",
                )
            }
        if observed[ELECTION_2022]["house_seat_results"] != 151:
            raise RuntimeError("The 2022 House feed does not contain 151 declared seats.")
        if observed[ELECTION_2022]["senate_composition"] != 40:
            raise RuntimeError("The 2022 Senate view does not contain 40 elected senators.")
        if observed[ELECTION_2025]["senate_composition"] != 76:
            raise RuntimeError("The governed 2025 Senate snapshot no longer contains 76 seats.")
        boundary_counts = {
            election_id: visualisations.catalogue(election_id)["boundary_geometry"]["feature_count"]
            for election_id in election_ids
        }
        if boundary_counts != {ELECTION_2025: 150, ELECTION_2022: 151}:
            raise RuntimeError(f"Unexpected boundary coverage: {boundary_counts}")

        publisher = StaticWebsitePublisher(
            settings,
            feeds,
            visualisations,
            service.governed_database,
            service.governed_release_identity,
        )
        static_result = publisher.build()
        if static_result["verification"]["feed_count"] != 18:
            raise RuntimeError("The static website does not contain all 18 election-feed publications.")
        release_root = Path(static_result["release_root"])
        for election_id in election_ids:
            if not (release_root / "data" / "visualisations" / f"{election_id}.json").is_file():
                raise RuntimeError(f"The static visualisation contract is missing for {election_id}.")

        return {
            "status": "PASS",
            "application_version": APP_VERSION,
            "base_database_sha256_preserved": base_sha256,
            "fixture_tcp_rows_upgraded_to_stage9_1": corrected_tcp_rows,
            "active_release_id": installation["release_id"],
            "active_database_sha256": installation["database_sha256"],
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
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dist" / "stage_14_2_integration_report.json",
    )
    args = parser.parse_args()
    report = verify(args.base_zip)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
