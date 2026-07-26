from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from .publication import FEEDS, FEED_VERSION, VisualisationFeedService


VISUALISATION_API_VERSION = "v1"


class VisualisationContractError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique(items: list[dict[str, Any]], key: str, label: str) -> set[str]:
    identifiers: list[str] = []
    for item in items:
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            raise VisualisationContractError(f"Every {label} requires a non-empty {key}.")
        identifiers.append(value)
    if len(identifiers) != len(set(identifiers)):
        raise VisualisationContractError(f"The visualisation contract contains duplicate {label} identifiers.")
    return set(identifiers)


class VisualisationContractService:
    """Validate and publish the release-bound visualisation foundation contract."""

    def __init__(
        self,
        contract_path: Path,
        feeds: VisualisationFeedService,
        identity_resolver: Callable[[], dict[str, Any]],
        *,
        app_version: str,
        boundary_contract_path: Path | None = None,
    ) -> None:
        self.contract_path = contract_path.resolve()
        self.feeds = feeds
        self._identity_resolver = identity_resolver
        self.app_version = app_version
        self._contract = self._load()
        self.contract_sha256 = hashlib.sha256(
            _canonical_json(self._contract)
        ).hexdigest()
        default_boundary_path = (
            boundary_contract_path
            or self.contract_path.with_name("electorate_boundaries_2025.yml")
        ).resolve()
        boundary_paths = [default_boundary_path]
        if boundary_contract_path is None:
            boundary_paths = sorted(
                self.contract_path.parent.glob("electorate_boundaries_*.yml")
            )
        contracts = [self._load_boundary_contract(path) for path in boundary_paths]
        self._boundary_contracts = {
            str(document["election_id"]): document for document in contracts
        }
        if len(self._boundary_contracts) != len(contracts):
            raise VisualisationContractError(
                "Every governed boundary contract must use a unique election_id."
            )
        self.boundary_contract_path = default_boundary_path
        self._boundary_contract = self._boundary_contracts.get(
            "election_fed_2025_05_03_general", contracts[-1]
        )
        self.boundary_contract_sha256 = hashlib.sha256(
            _canonical_json(self._boundary_contract)
        ).hexdigest()
        self.boundary_geojson_path = self._project_path(
            self._boundary_contract["derived_geometry"]["source_path"]
        )
        self.boundary_geojson_sha256 = self._boundary_contract[
            "derived_geometry"
        ]["sha256"]

    def _project_path(self, value: str) -> Path:
        project_root = self.contract_path.parent.parent
        return (project_root / value).resolve()

    def _load_boundary_contract(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise VisualisationContractError(
                f"The governed electorate-boundary contract is missing: {path}"
            )
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise VisualisationContractError(
                "The governed electorate-boundary contract could not be read."
            ) from exc
        if not isinstance(document, dict):
            raise VisualisationContractError(
                "The governed electorate-boundary contract must be a mapping."
            )
        required = {
            "contract_version",
            "boundary_id",
            "feature_count",
            "election_id",
            "source",
            "derived_geometry",
            "attribution",
            "governance",
        }
        missing = required - set(document)
        if missing:
            raise VisualisationContractError(
                f"The governed electorate-boundary contract is missing: {sorted(missing)}"
            )
        if not isinstance(document.get("feature_count"), int) or document.get(
            "feature_count"
        ) not in {150, 151}:
            raise VisualisationContractError(
                "A national electorate-boundary contract must contain 150 or 151 features."
            )
        if document.get("governance", {}).get("read_only") is not True:
            raise VisualisationContractError(
                "The electorate-boundary contract must be explicitly read-only."
            )
        source = document.get("source")
        derived = document.get("derived_geometry")
        attribution = document.get("attribution")
        if not all(isinstance(item, dict) for item in (source, derived, attribution)):
            raise VisualisationContractError(
                "Boundary source, derived geometry and attribution must be mappings."
            )
        source_url = str(source.get("download_url") or "")
        landing_url = str(source.get("landing_page_url") or "")
        if not source_url.startswith("https://www.aec.gov.au/") or not landing_url.startswith(
            "https://www.aec.gov.au/"
        ):
            raise VisualisationContractError(
                "A boundary contract must cite Australian Electoral Commission sources."
            )
        digest_pattern = re.compile(r"[0-9a-f]{64}")
        source_digest = str(source.get("source_archive_sha256") or "")
        geometry_digest = str(derived.get("sha256") or "")
        if not digest_pattern.fullmatch(source_digest) or not digest_pattern.fullmatch(
            geometry_digest
        ):
            raise VisualisationContractError(
                "Boundary source and geometry checksums must be lowercase SHA-256 values."
            )
        source_archive = self._project_path(str(source.get("source_archive_path") or ""))
        geometry_path = self._project_path(str(derived.get("source_path") or ""))
        if not source_archive.is_file() or _sha256_file(source_archive) != source_digest:
            raise VisualisationContractError(
                "The official AEC boundary archive is missing or failed checksum verification."
            )
        if source_archive.stat().st_size != source.get("source_archive_size_bytes"):
            raise VisualisationContractError(
                "The official AEC boundary archive size does not match its contract."
            )
        expected_components = source.get("source_components") or {}
        try:
            with zipfile.ZipFile(source_archive) as archive:
                names = set(archive.namelist())
                if names != set(expected_components):
                    raise VisualisationContractError(
                        "The AEC source-archive file inventory does not match its contract."
                    )
                for name, expected_digest in expected_components.items():
                    if hashlib.sha256(archive.read(name)).hexdigest() != expected_digest:
                        raise VisualisationContractError(
                            f"The AEC source component failed checksum verification: {name}"
                        )
        except (OSError, zipfile.BadZipFile) as exc:
            raise VisualisationContractError(
                "The official AEC boundary archive is unreadable."
            ) from exc
        if not geometry_path.is_file() or _sha256_file(geometry_path) != geometry_digest:
            raise VisualisationContractError(
                "The governed electorate GeoJSON is missing or failed checksum verification."
            )
        if geometry_path.stat().st_size != derived.get("size_bytes"):
            raise VisualisationContractError(
                "The governed electorate GeoJSON size does not match its contract."
            )
        try:
            geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VisualisationContractError(
                "The governed electorate GeoJSON could not be read."
            ) from exc
        features = geometry.get("features") if isinstance(geometry, dict) else None
        if not isinstance(features, list) or len(features) != document["feature_count"]:
            raise VisualisationContractError(
                "The governed electorate GeoJSON feature count does not match its contract."
            )
        names = [
            str((item.get("properties") or {}).get("electorate") or "").casefold()
            for item in features
        ]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise VisualisationContractError(
                "Every governed electorate geometry requires a unique electorate name."
            )
        allowed_types = {"Polygon", "MultiPolygon"}
        if any((item.get("geometry") or {}).get("type") not in allowed_types for item in features):
            raise VisualisationContractError(
                "The governed electorate GeoJSON contains an unsupported geometry type."
            )
        if "Australian Electoral Commission" not in str(
            attribution.get("notice") or ""
        ):
            raise VisualisationContractError(
                "The governed boundary contract must retain AEC attribution."
            )
        return document

    def _load(self) -> dict[str, Any]:
        if not self.contract_path.is_file():
            raise VisualisationContractError(
                f"The visualisation contract is missing: {self.contract_path}"
            )
        try:
            document = yaml.safe_load(self.contract_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise VisualisationContractError(
                "The visualisation contract could not be read."
            ) from exc
        if not isinstance(document, dict):
            raise VisualisationContractError("The visualisation contract must be a mapping.")
        for key in (
            "contract_version",
            "design_system_version",
            "default_route",
            "routes",
            "filters",
            "metrics",
            "visualisations",
        ):
            if key not in document:
                raise VisualisationContractError(
                    f"The visualisation contract is missing {key}."
                )
        if document.get("read_only") is not True:
            raise VisualisationContractError(
                "The public visualisation contract must be explicitly read-only."
            )

        routes = document["routes"]
        filters = document["filters"]
        metrics = document["metrics"]
        visualisations = document["visualisations"]
        if not all(isinstance(value, list) for value in (routes, filters, metrics, visualisations)):
            raise VisualisationContractError(
                "Routes, filters, metrics and visualisations must be lists."
            )
        route_ids = _unique(routes, "route_id", "route")
        filter_ids = _unique(filters, "filter_id", "filter")
        metric_ids = _unique(metrics, "metric_id", "metric")
        _unique(visualisations, "visualisation_id", "visualisation")
        if document["default_route"] not in route_ids:
            raise VisualisationContractError(
                "The default visualisation route is not registered."
            )
        route_statuses = {"available", "planned", "blocked"}
        for route in routes:
            if route.get("status") not in route_statuses:
                raise VisualisationContractError(
                    f"Route {route['route_id']} has an unsupported status."
                )
        parameters: list[str] = []
        for item in filters:
            parameter = item.get("parameter")
            if not isinstance(parameter, str) or not parameter:
                raise VisualisationContractError(
                    f"Filter {item['filter_id']} requires a URL parameter."
                )
            parameters.append(parameter)
        if len(parameters) != len(set(parameters)):
            raise VisualisationContractError(
                "Each visualisation filter must use a unique URL parameter."
            )
        available_feeds = set(FEEDS)
        for metric in metrics:
            if not metric.get("definition") or not metric.get("calculation"):
                raise VisualisationContractError(
                    f"Metric {metric['metric_id']} requires a definition and calculation."
                )
            unknown = set(metric.get("required_feeds") or []) - available_feeds
            if unknown:
                raise VisualisationContractError(
                    f"Metric {metric['metric_id']} uses unregistered feeds: {sorted(unknown)}"
                )
        for item in visualisations:
            if item.get("route_id") not in route_ids:
                raise VisualisationContractError(
                    f"Visualisation {item['visualisation_id']} uses an unknown route."
                )
            if item.get("status") not in route_statuses:
                raise VisualisationContractError(
                    f"Visualisation {item['visualisation_id']} has an unsupported status."
                )
            unknown_feeds = set(item.get("required_feeds") or []) - available_feeds
            unknown_metrics = set(item.get("metrics") or []) - metric_ids
            unknown_filters = set(item.get("filters") or []) - filter_ids
            if unknown_feeds or unknown_metrics or unknown_filters:
                raise VisualisationContractError(
                    f"Visualisation {item['visualisation_id']} contains an unregistered dependency."
                )
        return document

    def boundary_contract(self, election_id: str) -> dict[str, Any]:
        try:
            return self._boundary_contracts[election_id]
        except KeyError as exc:
            raise VisualisationContractError(
                f"No governed electorate-boundary contract is available for {election_id}."
            ) from exc

    def boundary_geojson_path_for(self, election_id: str) -> Path:
        contract = self.boundary_contract(election_id)
        return self._project_path(contract["derived_geometry"]["source_path"])

    def catalogue(self, election_id: str | None = None) -> dict[str, Any]:
        feed_catalogue = self.feeds.catalogue()
        selected_election = election_id or feed_catalogue.get("default_election_id")
        if selected_election and selected_election not in {
            row.get("election_id") for row in feed_catalogue.get("elections", [])
        }:
            raise VisualisationContractError(
                f"The selected election is not available: {selected_election}"
            )
        if not selected_election:
            raise VisualisationContractError("No active election is available.")
        boundary_contract = self.boundary_contract(selected_election)
        boundary_contract_sha256 = hashlib.sha256(
            _canonical_json(boundary_contract)
        ).hexdigest()
        identity = self._identity_resolver()
        return {
            "api_version": VISUALISATION_API_VERSION,
            "application_version": self.app_version,
            "feed_version": FEED_VERSION,
            "read_only": True,
            "default_election_id": selected_election,
            "release": identity,
            "elections": feed_catalogue.get("elections", []),
            "contract_sha256": self.contract_sha256,
            "boundary_geometry": {
                **json.loads(json.dumps(boundary_contract, default=str)),
                "contract_sha256": boundary_contract_sha256,
            },
            **json.loads(json.dumps(self._contract, default=str)),
        }
