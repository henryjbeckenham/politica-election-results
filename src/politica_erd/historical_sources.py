from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOGUE_PATH = PROJECT_ROOT / "config" / "source_catalogue_historical.yml"
STATES = ("NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT")
EXPECTED_YEARS = (
    1901, 1903, 1906, 1910, 1913, 1914, 1917, 1919, 1922, 1925, 1928,
    1929, 1931, 1934, 1937, 1940, 1943, 1946, 1949, 1951, 1954, 1955,
    1958, 1961, 1963, 1966, 1969, 1972, 1974, 1975, 1977, 1980, 1983,
    1984, 1987, 1990, 1993, 1996, 1998, 2001, 2004, 2007, 2010, 2013,
    2016, 2019, 2022,
)


@dataclass(frozen=True)
class SourceRecord:
    year: int
    source_key: str
    source_role: str
    authority: str
    chamber: str
    family: str
    acquisition_type: str
    url: str
    destination: str
    required: bool
    notes: str = ""


def load_catalogue(path: Path = CATALOGUE_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def election_for_year(catalogue: dict[str, Any], year: int) -> dict[str, Any]:
    matches = [item for item in catalogue["elections"] if int(item["year"]) == year]
    if len(matches) != 1:
        raise ValueError(f"Expected one election for {year}; found {len(matches)}")
    return matches[0]


def election_id(election: dict[str, Any]) -> str:
    return f"election_fed_{str(election['date']).replace('-', '_')}_general"


def _valid_https_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.netloc)


def _modern_download_pages(election: dict[str, Any]) -> dict[str, str]:
    event = election["aec_event_id"]
    root = election["web_root"]
    case = election["menu_case"]
    base = f"https://results.aec.gov.au/{event}/{root}"
    return {
        "house": f"{base}/HouseDownloadsMenu-{event}-{case}.htm",
        "senate": f"{base}/SenateDownloadsMenu-{event}-{case}.htm",
        "general": f"{base}/GeneralDownloadsMenu-{event}-{case}.htm",
    }


def _modern_sources(catalogue: dict[str, Any], election: dict[str, Any]) -> list[SourceRecord]:
    event = int(election["aec_event_id"])
    root = election["web_root"]
    profile = catalogue["modern_profiles"][election["modern_profile"]]
    records: list[SourceRecord] = []
    group_names = [*profile["source_groups"], *election.get("additional_source_groups", [])]
    for group_name in group_names:
        for source in catalogue["source_groups"][group_name]:
            states: Iterable[str | None] = source.get("states", [None])
            for state in states:
                values = {"event_id": event, "state": state or ""}
                filename = source["filename"].format(**values)
                key = source["key"] + (f"_{state.lower()}" if state else "")
                url = (
                    f"https://results.aec.gov.au/{event}/{root}/"
                    f"{source['location']}/{filename}"
                )
                records.append(
                    SourceRecord(
                        year=int(election["year"]),
                        source_key=key,
                        source_role="primary",
                        authority="Australian Electoral Commission",
                        chamber=source["chamber"],
                        family=source["family"],
                        acquisition_type="direct_file",
                        url=url,
                        destination=(
                            f"data/raw/aec/{election['year']}_federal/{event}/final/{filename}"
                        ),
                        required=True,
                    )
                )
    return records


def _legacy_archive_source(
    catalogue: dict[str, Any], election: dict[str, Any]
) -> SourceRecord:
    archive = catalogue["legacy_archives"][election["archive_id"]]
    return SourceRecord(
        year=int(election["year"]),
        source_key=election["archive_id"],
        source_role="primary",
        authority="Australian Electoral Commission",
        chamber="both",
        family="official_election_statistics_archive",
        acquisition_type="direct_archive",
        url=archive["url"],
        destination=f"data/raw/aec/legacy/{archive['filename']}",
        required=True,
        notes=(
            f"Shared archive covering {','.join(str(y) for y in archive['covers_years'])}; "
            f"verified SHA-256 {archive['verified_sha256']}"
        ),
    )


def _aph_sources(catalogue: dict[str, Any], election: dict[str, Any]) -> list[SourceRecord]:
    api_base = catalogue["authorities"]["aph_handbook"]["api_base"].rstrip("/")
    role = "primary" if election["tier"] == "handbook_api" else "corroboration"
    values = {
        "year": int(election["year"]),
        "aph_election_id": int(election["aph_election_id"]),
        "state": "{state}",
        "division": "{division}",
        "polling_place": "{polling_place}",
    }
    records: list[SourceRecord] = []
    groups = ("baseline", "state_cascade", "division_cascade", "polling_place_cascade")
    for group in groups:
        for endpoint in catalogue["aph_endpoint_templates"][group]:
            if endpoint["chamber"] == "senate" and not election["senate_contested"]:
                continue
            resolved = endpoint["endpoint"].format(**values)
            placeholders = any(token in resolved for token in ("{state}", "{division}", "{polling_place}"))
            records.append(
                SourceRecord(
                    year=int(election["year"]),
                    source_key=endpoint["key"],
                    source_role=role,
                    authority="Parliament of Australia, Parliamentary Handbook",
                    chamber=endpoint["chamber"],
                    family="historical_structured_results",
                    acquisition_type="api_template" if placeholders else "api_endpoint",
                    url=f"{api_base}/{resolved}",
                    destination=(
                        f"data/raw/aph_handbook/{election['year']}/"
                        f"{endpoint['key']}"
                        + ("__template.json" if placeholders else ".json")
                    ),
                    required=role == "primary",
                    notes=(
                        "Expand after enumerating states, divisions and polling places from the "
                        "baseline endpoints."
                        if placeholders
                        else ""
                    ),
                )
            )
    return records


def acquisition_plan(
    catalogue: dict[str, Any], year: int, include_corroboration: bool = True
) -> list[SourceRecord]:
    election = election_for_year(catalogue, year)
    records: list[SourceRecord] = []
    if election["tier"] == "full_tally":
        records.extend(_modern_sources(catalogue, election))
    elif election["tier"] == "legacy_archive":
        records.append(_legacy_archive_source(catalogue, election))
    records.extend(_aph_sources(catalogue, election))
    if not include_corroboration:
        records = [record for record in records if record.source_role == "primary"]
    return records


def validate_catalogue(catalogue: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    elections = catalogue.get("elections", [])
    years = tuple(sorted(int(item["year"]) for item in elections))
    if years != EXPECTED_YEARS:
        failures.append("Election year set does not exactly match the 47 general elections from 1901 to 2022.")
    if len(set(years)) != len(years):
        failures.append("Duplicate election years exist.")
    aph_ids = [int(item["aph_election_id"]) for item in elections]
    if len(set(aph_ids)) != len(aph_ids):
        failures.append("Duplicate Parliamentary Handbook election identifiers exist.")
    if catalogue.get("scope", {}).get("election_count") != len(elections):
        failures.append("Declared election_count does not match the election inventory.")
    if 2025 in years:
        failures.append("The already-ingested 2025 election must remain outside this historical inventory.")
    if {int(item["year"]) for item in elections if not item["senate_contested"]} != {1929, 1954}:
        failures.append("House-only general-election years must be exactly 1929 and 1954.")

    modern_event_ids: set[int] = set()
    primary_counts: dict[int, int] = {}
    for election in elections:
        year = int(election["year"])
        if election["tier"] == "full_tally":
            event = int(election["aec_event_id"])
            if event in modern_event_ids:
                failures.append(f"Duplicate AEC event id {event}.")
            modern_event_ids.add(event)
            pages = _modern_download_pages(election)
            for label, url in pages.items():
                if not _valid_https_url(url):
                    failures.append(f"Invalid {label} download page for {year}: {url}")
            sources = _modern_sources(catalogue, election)
            primary_counts[year] = len(sources)
            expected = int(
                election.get(
                    "expected_primary_source_count",
                    catalogue["modern_profiles"][election["modern_profile"]][
                        "expected_primary_source_count"
                    ],
                )
            )
            if len(sources) != expected:
                failures.append(
                    f"{year} expands to {len(sources)} primary files; expected {expected}."
                )
            keys = [record.source_key for record in sources]
            if len(keys) != len(set(keys)):
                failures.append(f"{year} contains duplicate expanded source keys.")
            for record in sources:
                if not _valid_https_url(record.url):
                    failures.append(f"Invalid source URL for {year}/{record.source_key}: {record.url}")
        elif election["tier"] == "legacy_archive":
            archive_id = election.get("archive_id")
            if archive_id not in catalogue["legacy_archives"]:
                failures.append(f"{year} refers to unknown legacy archive {archive_id}.")
            elif year not in catalogue["legacy_archives"][archive_id]["covers_years"]:
                failures.append(f"{year} is not declared in archive {archive_id} coverage.")
        elif election["tier"] != "handbook_api":
            failures.append(f"Unknown source tier for {year}: {election['tier']}")

        for record in _aph_sources(catalogue, election):
            probe_url = record.url.replace("{state}", "NSW").replace("{division}", "Test").replace(
                "{polling_place}", "Test"
            )
            if not _valid_https_url(probe_url):
                failures.append(f"Invalid Parliamentary Handbook URL template: {record.url}")

    for archive_id, archive in catalogue.get("legacy_archives", {}).items():
        if not _valid_https_url(archive["url"]):
            failures.append(f"Invalid legacy archive URL: {archive_id}")
        if len(str(archive.get("verified_sha256", ""))) != 64:
            failures.append(f"Legacy archive lacks a valid SHA-256: {archive_id}")

    return {
        "status": "PASS" if not failures else "FAIL",
        "catalogue_id": catalogue.get("catalogue_id"),
        "catalogue_version": str(catalogue.get("catalogue_version")),
        "election_count": len(elections),
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "modern_primary_source_counts": primary_counts,
        "legacy_archive_count": len(catalogue.get("legacy_archives", {})),
        "failures": failures,
    }


def probe_records(records: list[SourceRecord], timeout: int = 30) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for record in records:
        if record.acquisition_type == "api_template":
            results.append({"source_key": record.source_key, "url": record.url, "status": "TEMPLATE"})
            continue
        is_api = urlsplit(record.url).netloc == "handbookapi.aph.gov.au"
        request = urllib.request.Request(
            record.url,
            method="GET" if is_api else "HEAD",
            headers={
                "User-Agent": "Politica-ERD/14.1",
                "Accept": "application/json" if is_api else "*/*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if is_api:
                    response.read(1)
                results.append(
                    {
                        "source_key": record.source_key,
                        "url": record.url,
                        "status": "AVAILABLE",
                        "http_status": response.status,
                        "content_length": response.headers.get("Content-Length"),
                    }
                )
        except urllib.error.HTTPError as error:
            results.append(
                {
                    "source_key": record.source_key,
                    "url": record.url,
                    "status": "UNAVAILABLE",
                    "http_status": error.code,
                }
            )
        except Exception as error:  # pragma: no cover - external network diagnostic
            results.append(
                {"source_key": record.source_key, "url": record.url, "status": "ERROR", "error": str(error)}
            )
    return results


def _records_as_csv(records: list[SourceRecord]) -> str:
    output = io.StringIO()
    fieldnames = list(SourceRecord.__dataclass_fields__)
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(asdict(record) for record in records)
    return output.getvalue()


def inventory_rows(catalogue: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for election in sorted(catalogue["elections"], key=lambda item: int(item["year"]), reverse=True):
        year = int(election["year"])
        plan = acquisition_plan(catalogue, year, include_corroboration=False)
        pages = _modern_download_pages(election) if election["tier"] == "full_tally" else {}
        archive = (
            catalogue["legacy_archives"][election["archive_id"]]
            if election["tier"] == "legacy_archive"
            else {}
        )
        rows.append(
            {
                "year": year,
                "election_date": str(election["date"]),
                "election_id": election_id(election),
                "source_tier": election["tier"],
                "aec_event_id": election.get("aec_event_id", ""),
                "aph_election_id": election["aph_election_id"],
                "senate_contested": election["senate_contested"],
                "senate_scope": election.get("senate_scope", "general_election" if election["senate_contested"] else "none"),
                "required_primary_records": len(plan),
                "aec_house_download_page": pages.get("house", ""),
                "aec_senate_download_page": pages.get("senate", ""),
                "aec_general_download_page": pages.get("general", ""),
                "aec_archive_url": archive.get("url", ""),
                "aph_election_api": (
                    f"{catalogue['authorities']['aph_handbook']['api_base'].rstrip('/')}"
                    f"/Election?electionId={election['aph_election_id']}"
                ),
                "boundary_url": election.get("boundary_url", ""),
                "notes": election.get("note", ""),
            }
        )
    return rows


def _dicts_as_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the Stage 14.1 historical source catalogue.")
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--year", type=int, required=True)
    plan_parser.add_argument("--primary-only", action="store_true")
    plan_parser.add_argument("--format", choices=("json", "csv"), default="json")
    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("--year", type=int, required=True)
    probe_parser.add_argument("--primary-only", action="store_true")
    probe_parser.add_argument("--timeout", type=int, default=30)
    all_parser = subparsers.add_parser("plan-all")
    all_parser.add_argument("--primary-only", action="store_true")
    all_parser.add_argument("--format", choices=("json", "csv"), default="csv")
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--format", choices=("json", "csv"), default="csv")
    args = parser.parse_args()

    catalogue = load_catalogue(args.catalogue)
    if args.command == "validate":
        report = validate_catalogue(catalogue)
        print(json.dumps(report, indent=2, default=str))
        raise SystemExit(0 if report["status"] == "PASS" else 2)
    if args.command == "inventory":
        rows = inventory_rows(catalogue)
        if args.format == "csv":
            print(_dicts_as_csv(rows), end="")
        else:
            print(json.dumps(rows, indent=2, default=str))
        return
    if args.command == "plan-all":
        records = [
            record
            for year in EXPECTED_YEARS
            for record in acquisition_plan(
                catalogue, year, include_corroboration=not args.primary_only
            )
        ]
        if args.format == "csv":
            print(_records_as_csv(records), end="")
        else:
            print(json.dumps([asdict(record) for record in records], indent=2))
        return
    records = acquisition_plan(catalogue, args.year, include_corroboration=not args.primary_only)
    if args.command == "plan":
        if args.format == "csv":
            print(_records_as_csv(records), end="")
        else:
            print(json.dumps([asdict(record) for record in records], indent=2))
        return
    results = probe_records(records, timeout=args.timeout)
    print(json.dumps(results, indent=2))
    raise SystemExit(0 if all(item["status"] in {"AVAILABLE", "TEMPLATE"} for item in results) else 2)


if __name__ == "__main__":
    main()
