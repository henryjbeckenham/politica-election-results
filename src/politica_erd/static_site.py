from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import mimetypes
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from filelock import FileLock

from .app.config import AppSettings
from .app.explorer import ElectionExplorer
from .app.publication import (
    FEEDS,
    FEED_VERSION,
    PublicationFilters,
    VisualisationFeedService,
)
from .app.service import APP_VERSION, JobService
from .app.store import JobStore
from .app.visualisations import VisualisationContractService


SITE_FORMAT_VERSION = "1.1.0"
STATIC_MODE_CALL = (
    'createResultsApp(document.querySelector("#politica-results"), '
    '{staticBase: "./data"});'
)
LOCAL_MODE_CALL = 'createResultsApp(document.querySelector("#politica-results"));'
FORBIDDEN_SUFFIXES = {".duckdb", ".db", ".sqlite", ".pem", ".key", ".p12"}
FORBIDDEN_NAMES = {".env", "credentials.json", "service-account.json"}


class WebsitePublicationError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(_canonical_json(document) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _inventory(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "publication-manifest.json":
            continue
        relative = path.relative_to(root).as_posix()
        rows.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "media_type": mimetypes.guess_type(path.name)[0]
                or "application/octet-stream",
            }
        )
    return rows


def _deterministic_zip(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    os.replace(temporary, destination)


class StaticWebsitePublisher:
    """Create a host-ready, read-only site from one verified governed release."""

    def __init__(
        self,
        settings: AppSettings,
        feeds: VisualisationFeedService,
        visualisations: VisualisationContractService,
        database_resolver: Callable[[], Path],
        identity_resolver: Callable[[], dict[str, Any]],
        *,
        results_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.feeds = feeds
        self.visualisations = visualisations
        self._database_resolver = database_resolver
        self._identity_resolver = identity_resolver
        self.results_root = (
            results_root
            or Path(__file__).resolve().parent / "app" / "results"
        ).resolve()

    def _path_for_pointer(self, path: Path) -> tuple[str, str]:
        resolved = path.resolve()
        try:
            return "project_root", resolved.relative_to(
                self.settings.project_root.resolve()
            ).as_posix()
        except ValueError:
            return "absolute", str(resolved)

    def _resolve_pointer_path(self, value: str, base: str) -> Path:
        path = Path(value)
        if base == "absolute":
            return path.resolve()
        if base == "project_root":
            return (self.settings.project_root / path).resolve()
        raise WebsitePublicationError(f"Unsupported website pointer base: {base}")

    def _site_release_id(self, identity: dict[str, Any], election_id: str) -> str:
        basis = {
            "site_format_version": SITE_FORMAT_VERSION,
            "application_version": APP_VERSION,
            "feed_version": FEED_VERSION,
            "visualisation_contract_sha256": self.visualisations.contract_sha256,
            "composition_contract_sha256": self.feeds.composition_contract_sha256,
            "boundary_contract_sha256": self.visualisations.boundary_contract_sha256,
            "boundary_geojson_sha256": self.visualisations.boundary_geojson_sha256,
            "release_id": identity.get("release_id"),
            "database_sha256": identity.get("database_sha256"),
            "release_manifest_sha256": identity.get("release_manifest_sha256"),
            "election_id": election_id,
        }
        return "site_" + _sha256_bytes(_canonical_json(basis))[:32]

    def _copy_compiled_site(self, destination: Path) -> None:
        if not self.results_root.is_dir():
            raise WebsitePublicationError(
                "The compiled public results site is missing from this installation."
            )
        for source in self.results_root.rglob("*"):
            if source.is_symlink():
                raise WebsitePublicationError(
                    f"The compiled site contains an unsupported symbolic link: {source.name}"
                )
        shutil.copytree(self.results_root, destination, dirs_exist_ok=True)
        index = destination / "index.html"
        rendered = index.read_text(encoding="utf-8")
        if LOCAL_MODE_CALL not in rendered:
            raise WebsitePublicationError(
                "The compiled results entry point does not match the registered static-export transformer."
            )
        index.write_text(
            rendered.replace(LOCAL_MODE_CALL, STATIC_MODE_CALL, 1),
            encoding="utf-8",
        )

    @staticmethod
    def _static_catalogue(
        catalogue: dict[str, Any], site_release_id: str
    ) -> dict[str, Any]:
        document = json.loads(json.dumps(catalogue, default=str))
        document["static_publication"] = True
        document["site_format_version"] = SITE_FORMAT_VERSION
        document["site_release_id"] = site_release_id
        election_id = document.get("default_election_id") or ""
        for feed in document.get("feeds", []):
            feed_id = feed["feed_id"]
            feed["urls"] = {
                "json": f"data/feeds/{election_id}/{feed_id}.json",
                "csv": f"data/feeds/{election_id}/{feed_id}.csv",
                "manifest": f"data/feeds/{election_id}/{feed_id}.manifest.json",
            }
        return document

    def _write_feed_files(
        self,
        root: Path,
        election_ids: list[str],
        expected_identity: dict[str, Any],
    ) -> list[dict[str, Any]]:
        feed_root = root / "data" / "feeds"
        feed_root.mkdir(parents=True, exist_ok=True)
        summaries: list[dict[str, Any]] = []
        for election_id in election_ids:
            election_root = feed_root / election_id
            election_root.mkdir(parents=True, exist_ok=True)
            for feed_id in FEEDS:
                representation = self.feeds.build(
                    feed_id, PublicationFilters(election_id=election_id)
                )
                release = representation.manifest.get("release", {})
                if release.get("database_sha256") != expected_identity.get(
                    "database_sha256"
                ):
                    raise WebsitePublicationError(
                        "The governed database changed while the public website was being built. Run the build again."
                    )
                (election_root / f"{feed_id}.json").write_bytes(
                    representation.json_bytes
                )
                (election_root / f"{feed_id}.csv").write_bytes(representation.csv_bytes)
                (election_root / f"{feed_id}.manifest.json").write_bytes(
                    _canonical_json(representation.manifest) + b"\n"
                )
                summaries.append(
                    {
                        "election_id": election_id,
                        "feed_id": feed_id,
                        "publication_id": representation.publication_id,
                        "row_count": representation.row_count,
                        "data_sha256": representation.manifest["data_sha256"],
                    }
                )
        return summaries

    def verify_release(self, release_root: Path) -> dict[str, Any]:
        root = release_root.resolve()
        manifest_path = root / "publication-manifest.json"
        if not manifest_path.is_file():
            raise WebsitePublicationError(
                "The website publication manifest is missing."
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WebsitePublicationError(
                "The website publication manifest is unreadable."
            ) from exc
        core = dict(manifest)
        recorded_manifest_sha256 = core.pop("manifest_sha256", None)
        if recorded_manifest_sha256 != _sha256_bytes(_canonical_json(core)):
            raise WebsitePublicationError(
                "The website publication manifest checksum does not match."
            )
        if manifest.get("site_format_version") != SITE_FORMAT_VERSION:
            raise WebsitePublicationError(
                "The website publication format is not supported by this application."
            )
        expected = {item["path"]: item for item in manifest.get("files", [])}
        actual_rows = _inventory(root)
        actual = {item["path"]: item for item in actual_rows}
        if set(expected) != set(actual):
            raise WebsitePublicationError(
                "The website file inventory differs from its immutable manifest."
            )
        for path, recorded in expected.items():
            observed = actual[path]
            if (
                recorded.get("sha256") != observed["sha256"]
                or recorded.get("size_bytes") != observed["size_bytes"]
            ):
                raise WebsitePublicationError(
                    f"The published website file failed checksum verification: {path}"
                )
            name = Path(path).name.lower()
            if name in FORBIDDEN_NAMES or Path(name).suffix in FORBIDDEN_SUFFIXES:
                raise WebsitePublicationError(
                    f"A private database or credential file was blocked from the website package: {path}"
                )

        index = (root / "index.html").read_text(encoding="utf-8")
        if STATIC_MODE_CALL not in index or LOCAL_MODE_CALL in index:
            raise WebsitePublicationError(
                "The exported website is not pinned to its packaged static feeds."
            )
        catalogue = json.loads(
            (root / "data" / "catalogue.json").read_text(encoding="utf-8")
        )
        if not catalogue.get("static_publication"):
            raise WebsitePublicationError(
                "The exported feed catalogue is not marked as a static publication."
            )
        if catalogue.get("site_release_id") != manifest.get("site_release_id"):
            raise WebsitePublicationError(
                "The website and catalogue release identities do not match."
            )
        composition_contract = (
            catalogue.get("supplemental_contracts", {})
            .get("parliamentary_composition", {})
        )
        if composition_contract.get("contract_sha256") != manifest.get(
            "composition_contract_sha256"
        ):
            raise WebsitePublicationError(
                "The parliamentary composition checksum does not match the website manifest."
            )
        visualisations = json.loads(
            (root / "data" / "visualisations.json").read_text(encoding="utf-8")
        )
        if not visualisations.get("static_publication"):
            raise WebsitePublicationError(
                "The exported visualisation contract is not marked as a static publication."
            )
        if visualisations.get("site_release_id") != manifest.get("site_release_id"):
            raise WebsitePublicationError(
                "The website and visualisation contract release identities do not match."
            )
        if visualisations.get("contract_sha256") != manifest.get(
            "visualisation_contract_sha256"
        ):
            raise WebsitePublicationError(
                "The visualisation contract checksum does not match the website manifest."
            )
        if visualisations.get("release", {}).get("database_sha256") != manifest.get(
            "database", {}
        ).get("database_sha256"):
            raise WebsitePublicationError(
                "The visualisation contract is not bound to the website database release."
            )
        boundary = visualisations.get("boundary_geometry") or {}
        if boundary.get("contract_sha256") != manifest.get(
            "boundary_contract_sha256"
        ):
            raise WebsitePublicationError(
                "The electorate-boundary contract checksum does not match the website manifest."
            )
        derived = boundary.get("derived_geometry") or {}
        if derived.get("sha256") != manifest.get("boundary_geojson_sha256"):
            raise WebsitePublicationError(
                "The electorate-boundary geometry checksum does not match the website manifest."
            )
        asset_path = str(derived.get("public_asset_path") or "")
        geometry_path = (root / "data" / asset_path).resolve()
        try:
            geometry_path.relative_to((root / "data").resolve())
        except ValueError as exc:
            raise WebsitePublicationError(
                "The electorate-boundary asset path is invalid."
            ) from exc
        if not geometry_path.is_file() or _sha256_file(geometry_path) != derived.get(
            "sha256"
        ):
            raise WebsitePublicationError(
                "The electorate-boundary GeoJSON is missing or failed checksum verification."
            )

        election_ids = manifest.get("election_ids") or [manifest.get("election_id")]
        boundary_contracts = manifest.get("boundary_contracts") or {}
        for election_id in election_ids:
            visualisation_path = (
                root / "data" / "visualisations" / f"{election_id}.json"
            )
            election_visualisations = json.loads(
                visualisation_path.read_text(encoding="utf-8")
            )
            if (
                election_visualisations.get("default_election_id") != election_id
                or election_visualisations.get("site_release_id")
                != manifest.get("site_release_id")
            ):
                raise WebsitePublicationError(
                    f"The visualisation contract is not bound to {election_id}."
                )
            election_boundary = election_visualisations.get("boundary_geometry") or {}
            recorded_boundary = boundary_contracts.get(election_id) or {}
            election_derived = election_boundary.get("derived_geometry") or {}
            if (
                recorded_boundary.get("contract_sha256")
                != election_boundary.get("contract_sha256")
                or recorded_boundary.get("geojson_sha256")
                != election_derived.get("sha256")
            ):
                raise WebsitePublicationError(
                    f"The boundary contract does not match {election_id}."
                )
            election_geometry = (
                root / "data" / str(election_derived.get("public_asset_path") or "")
            ).resolve()
            if (
                not election_geometry.is_file()
                or _sha256_file(election_geometry) != election_derived.get("sha256")
            ):
                raise WebsitePublicationError(
                    f"The boundary geometry is missing for {election_id}."
                )

        verified_feeds: list[dict[str, Any]] = []
        database_sha256 = manifest.get("database", {}).get("database_sha256")
        for election_id in election_ids:
            for feed_id in FEEDS:
                feed_root = root / "data" / "feeds" / election_id
                json_path = feed_root / f"{feed_id}.json"
                csv_path = feed_root / f"{feed_id}.csv"
                feed_manifest_path = feed_root / f"{feed_id}.manifest.json"
                document = json.loads(json_path.read_text(encoding="utf-8"))
                feed_manifest = json.loads(feed_manifest_path.read_text(encoding="utf-8"))
                if document.get("manifest") != feed_manifest:
                    raise WebsitePublicationError(
                        f"The {election_id} {feed_id} JSON and standalone feed manifests differ."
                    )
                rows = document.get("data")
                if not isinstance(rows, list) or len(rows) != feed_manifest.get("row_count"):
                    raise WebsitePublicationError(
                        f"The {election_id} {feed_id} row count does not match its manifest."
                    )
                if _sha256_bytes(_canonical_json(rows)) != feed_manifest.get("data_sha256"):
                    raise WebsitePublicationError(
                        f"The {election_id} {feed_id} feed data checksum does not match."
                    )
                if feed_manifest.get("filters", {}).get("election_id") != election_id:
                    raise WebsitePublicationError(
                        f"The {feed_id} feed is not bound to {election_id}."
                    )
                if feed_manifest.get("release", {}).get("database_sha256") != database_sha256:
                    raise WebsitePublicationError(
                        f"The {feed_id} feed is not bound to the website database release."
                    )
                if feed_id == "senate_composition" and feed_manifest.get("supplemental_contract"):
                    supplemental = feed_manifest["supplemental_contract"]
                    if supplemental.get("contract_sha256") != manifest.get(
                        "composition_contract_sha256"
                    ):
                        raise WebsitePublicationError(
                            "The Senate composition feed is not bound to the website composition contract."
                        )
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.reader(handle)
                    header = next(reader, [])
                    csv_rows = sum(1 for _ in reader)
                if "_database_sha256" not in header or csv_rows != len(rows):
                    raise WebsitePublicationError(
                        f"The {election_id} {feed_id} CSV does not match its JSON publication."
                    )
                verified_feeds.append(
                    {"election_id": election_id, "feed_id": feed_id, "row_count": len(rows)}
                )

        return {
            "status": "PASS",
            "site_release_id": manifest.get("site_release_id"),
            "application_version": manifest.get("application_version"),
            "site_format_version": SITE_FORMAT_VERSION,
            "file_count": len(actual_rows) + 1,
            "feed_count": len(verified_feeds),
            "feeds": verified_feeds,
            "database_sha256": database_sha256,
            "release_root": str(root),
            "failures": [],
        }

    def _write_active_pointer(
        self,
        release_root: Path,
        export_zip: Path,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        release_base, release_value = self._path_for_pointer(release_root)
        export_base, export_value = self._path_for_pointer(export_zip)
        pointer = {
            "site_release_id": manifest["site_release_id"],
            "status": "READY_TO_DEPLOY",
            "release_path_base": release_base,
            "release_path": release_value,
            "export_path_base": export_base,
            "export_path": export_value,
            "export_sha256": _sha256_file(export_zip),
            "manifest_sha256": manifest["manifest_sha256"],
            "database_sha256": manifest["database"]["database_sha256"],
            "election_id": manifest["election_id"],
            "created_at": manifest["created_at"],
        }
        _atomic_json(self.settings.website_active_pointer, pointer)
        return pointer

    def build(self, election_id: str | None = None) -> dict[str, Any]:
        self.settings.ensure_directories()
        lock = FileLock(str(self.settings.website_root / ".build.lock"))
        with lock:
            database = self._database_resolver().resolve()
            identity = self._identity_resolver()
            observed_sha256 = _sha256_file(database)
            if observed_sha256 != identity.get("database_sha256"):
                raise WebsitePublicationError(
                    "The active database checksum changed before website publication began."
                )
            catalogue = self.feeds.catalogue()
            selected_election = election_id or catalogue.get("default_election_id")
            if not selected_election:
                raise WebsitePublicationError(
                    "No active election is available for website publication."
                )
            if selected_election not in {
                row.get("election_id") for row in catalogue.get("elections", [])
            }:
                raise WebsitePublicationError(
                    f"The selected election is not available: {selected_election}"
                )
            election_ids = [
                str(row["election_id"]) for row in catalogue.get("elections", [])
            ]
            site_release_id = self._site_release_id(identity, selected_election)
            release_root = self.settings.website_releases_root / site_release_id
            export_zip = self.settings.website_exports_root / f"{site_release_id}.zip"

            if release_root.exists():
                verification = self.verify_release(release_root)
                # Always reproduce the deterministic export. An existing ZIP
                # must not be trusted merely because its source release still
                # passes verification.
                _deterministic_zip(release_root, export_zip)
                manifest = json.loads(
                    (release_root / "publication-manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                pointer = self._write_active_pointer(
                    release_root, export_zip, manifest
                )
                return self._result(
                    manifest, release_root, export_zip, pointer, verification, reused=True
                )

            temporary_root = Path(
                tempfile.mkdtemp(
                    prefix=f".{site_release_id}.tmp-",
                    dir=self.settings.website_releases_root,
                )
            )
            try:
                self._copy_compiled_site(temporary_root)
                feed_summaries = self._write_feed_files(
                    temporary_root, election_ids, identity
                )
                static_catalogue = self._static_catalogue(
                    catalogue, site_release_id
                )
                (temporary_root / "data").mkdir(parents=True, exist_ok=True)
                visualisation_root = temporary_root / "data" / "visualisations"
                visualisation_root.mkdir(parents=True, exist_ok=True)
                visualisation_documents: dict[str, dict[str, Any]] = {}
                for current_election in election_ids:
                    document = self.visualisations.catalogue(current_election)
                    document["static_publication"] = True
                    document["site_format_version"] = SITE_FORMAT_VERSION
                    document["site_release_id"] = site_release_id
                    visualisation_documents[current_election] = document
                    (visualisation_root / f"{current_election}.json").write_bytes(
                        _canonical_json(document) + b"\n"
                    )
                static_visualisations = visualisation_documents[selected_election]
                (temporary_root / "data" / "catalogue.json").write_bytes(
                    _canonical_json(static_catalogue) + b"\n"
                )
                (temporary_root / "data" / "visualisations.json").write_bytes(
                    _canonical_json(static_visualisations) + b"\n"
                )
                (temporary_root / "README.txt").write_text(
                    "Politica verified static election-results publication.\n"
                    "Upload the contents of this folder to a static web host.\n"
                    "No DuckDB database, Google credential or ingestion control is included.\n",
                    encoding="utf-8",
                )
                files = _inventory(temporary_root)
                manifest_core = {
                    "site_release_id": site_release_id,
                    "site_format_version": SITE_FORMAT_VERSION,
                    "application_version": APP_VERSION,
                    "schema_version": identity.get("schema_version"),
                    "feed_version": FEED_VERSION,
                    "visualisation_contract_version": static_visualisations.get(
                        "contract_version"
                    ),
                    "visualisation_contract_sha256": static_visualisations.get(
                        "contract_sha256"
                    ),
                    "composition_contract_sha256": (
                        self.feeds.composition_contract_sha256
                    ),
                    "boundary_contract_sha256": (
                        static_visualisations["boundary_geometry"]["contract_sha256"]
                    ),
                    "boundary_geojson_sha256": (
                        static_visualisations["boundary_geometry"]["derived_geometry"]["sha256"]
                    ),
                    "publication_status": "READY_TO_DEPLOY",
                    "read_only": True,
                    "election_id": selected_election,
                    "election_ids": election_ids,
                    "boundary_contracts": {
                        current_election: {
                            "contract_sha256": document["boundary_geometry"]["contract_sha256"],
                            "geojson_sha256": document["boundary_geometry"]["derived_geometry"]["sha256"],
                            "public_asset_path": document["boundary_geometry"]["derived_geometry"]["public_asset_path"],
                        }
                        for current_election, document in visualisation_documents.items()
                    },
                    "created_at": identity.get("activated_at")
                    or identity.get("published_at")
                    or "unrecorded",
                    "database": identity,
                    "feeds": feed_summaries,
                    "files": files,
                    "file_set_sha256": _sha256_bytes(_canonical_json(files)),
                }
                manifest = {
                    **manifest_core,
                    "manifest_sha256": _sha256_bytes(
                        _canonical_json(manifest_core)
                    ),
                }
                (temporary_root / "publication-manifest.json").write_bytes(
                    _canonical_json(manifest) + b"\n"
                )
                verification = self.verify_release(temporary_root)
                final_identity = self._identity_resolver()
                if final_identity.get("database_sha256") != identity.get(
                    "database_sha256"
                ):
                    raise WebsitePublicationError(
                        "The active release changed while the website was being built. No website package was activated."
                    )
                os.replace(temporary_root, release_root)
                verification = self.verify_release(release_root)
            except Exception:
                shutil.rmtree(temporary_root, ignore_errors=True)
                raise

            _deterministic_zip(release_root, export_zip)
            pointer = self._write_active_pointer(release_root, export_zip, manifest)
            return self._result(
                manifest, release_root, export_zip, pointer, verification, reused=False
            )

    @staticmethod
    def _result(
        manifest: dict[str, Any],
        release_root: Path,
        export_zip: Path,
        pointer: dict[str, Any],
        verification: dict[str, Any],
        *,
        reused: bool,
    ) -> dict[str, Any]:
        return {
            "status": "READY_TO_DEPLOY",
            "site_release_id": manifest["site_release_id"],
            "application_version": manifest["application_version"],
            "schema_version": manifest.get("schema_version"),
            "election_id": manifest["election_id"],
            "database_release_id": manifest["database"].get("release_id"),
            "database_sha256": manifest["database"].get("database_sha256"),
            "release_root": str(release_root.resolve()),
            "export_zip": str(export_zip.resolve()),
            "export_size_bytes": export_zip.stat().st_size,
            "export_sha256": pointer["export_sha256"],
            "manifest_sha256": manifest["manifest_sha256"],
            "file_count": verification["file_count"],
            "feed_count": verification["feed_count"],
            "feeds": verification["feeds"],
            "verification": verification,
            "preview_url": "/site-preview/",
            "download_url": "/api/site-publication/download",
            "reused_existing_release": reused,
        }

    def status(self) -> dict[str, Any]:
        pointer_path = self.settings.website_active_pointer
        if not pointer_path.is_file():
            return {
                "status": "NOT_BUILT",
                "application_version": APP_VERSION,
                "site_format_version": SITE_FORMAT_VERSION,
                "message": "No static website package has been built from the active release.",
            }
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            release_root = self._resolve_pointer_path(
                pointer["release_path"], pointer["release_path_base"]
            )
            export_zip = self._resolve_pointer_path(
                pointer["export_path"], pointer["export_path_base"]
            )
            verification = self.verify_release(release_root)
            if not export_zip.is_file() or _sha256_file(export_zip) != pointer.get(
                "export_sha256"
            ):
                raise WebsitePublicationError(
                    "The active website ZIP is missing or failed checksum verification."
                )
            current_identity = self._identity_resolver()
            current = current_identity.get("database_sha256") == pointer.get(
                "database_sha256"
            )
            return {
                "status": "READY_TO_DEPLOY",
                "application_version": APP_VERSION,
                "site_format_version": SITE_FORMAT_VERSION,
                "site_release_id": pointer["site_release_id"],
                "election_id": pointer.get("election_id"),
                "database_sha256": pointer.get("database_sha256"),
                "current_database_sha256": current_identity.get("database_sha256"),
                "matches_active_database": current,
                "release_root": str(release_root),
                "export_zip": str(export_zip),
                "export_size_bytes": export_zip.stat().st_size,
                "export_sha256": pointer["export_sha256"],
                "manifest_sha256": pointer.get("manifest_sha256"),
                "created_at": pointer.get("created_at"),
                "file_count": verification["file_count"],
                "feed_count": verification["feed_count"],
                "feeds": verification["feeds"],
                "preview_url": "/site-preview/",
                "download_url": "/api/site-publication/download",
                "verification": verification,
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, WebsitePublicationError) as exc:
            return {
                "status": "INVALID",
                "application_version": APP_VERSION,
                "site_format_version": SITE_FORMAT_VERSION,
                "message": str(exc),
            }

    def active_export(self) -> Path:
        status = self.status()
        if status.get("status") != "READY_TO_DEPLOY":
            raise WebsitePublicationError(
                status.get("message") or "No verified website package is available."
            )
        return Path(status["export_zip"])

    def active_file(self, requested: str = "") -> Path:
        status = self.status()
        if status.get("status") != "READY_TO_DEPLOY":
            raise WebsitePublicationError(
                status.get("message") or "No verified website preview is available."
            )
        root = Path(status["release_root"]).resolve()
        relative = requested.strip("/") or "index.html"
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WebsitePublicationError("Invalid website preview path.") from exc
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            raise FileNotFoundError(relative)
        return candidate


def _services(settings: AppSettings) -> tuple[JobService, StaticWebsitePublisher]:
    settings.ensure_directories()
    service = JobService(settings, JobStore(settings.jobs_root))
    explorer = ElectionExplorer(
        service.governed_database,
        service._database_external_root,
        app_version=APP_VERSION,
        max_export_rows=settings.explorer_max_export_rows,
    )
    feeds = VisualisationFeedService(
        explorer,
        service.governed_release_identity,
        max_rows=settings.publication_max_rows,
        composition_contract_path=(
            settings.project_root / "config" / "parliament_composition_48th.yml"
        ),
    )
    visualisations = VisualisationContractService(
        settings.project_root / "config" / "visualisation_contract.yml",
        feeds,
        service.governed_release_identity,
        app_version=APP_VERSION,
    )
    return service, StaticWebsitePublisher(
        settings,
        feeds,
        visualisations,
        service.governed_database,
        service.governed_release_identity,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build or verify a governed static Politica results website."
    )
    parser.add_argument("--election-id")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    settings = AppSettings.from_environment()
    _service, publisher = _services(settings)
    if args.verify:
        result = publisher.verify_release(args.verify)
    elif args.status:
        result = publisher.status()
    else:
        result = publisher.build(args.election_id)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
