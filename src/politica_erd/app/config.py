from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def discover_project_root(
    working_directory: Path | None = None,
    installed_package_root: Path | None = None,
) -> Path:
    """Resolve the operator project even when politica_erd is installed non-editably."""
    configured = os.environ.get("POLITICA_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    working = (working_directory or Path.cwd()).expanduser().resolve()
    for candidate in (working, *working.parents):
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "config").is_dir()
            and (candidate / "data").is_dir()
        ):
            return candidate
    return (
        installed_package_root or Path(__file__).resolve().parents[3]
    ).expanduser().resolve()


PROJECT_ROOT = discover_project_root()


def _environment_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


@dataclass(frozen=True)
class AppSettings:
    """Filesystem and upload limits for the local-only application."""

    project_root: Path = PROJECT_ROOT
    base_database: Path = PROJECT_ROOT / "data/database/politica_election_results.duckdb"
    app_data: Path = PROJECT_ROOT / "data/app"
    max_upload_bytes: int = 2 * 1024**3
    max_archive_bytes: int = 25 * 1024**3
    max_archive_members: int = 10_000
    max_xlsx_member_bytes: int = 512 * 1024**2
    preview_rows: int = 20
    stage_batch_size: int = 500
    explorer_max_export_rows: int = 1_000_000
    publication_max_rows: int = 500_000

    @classmethod
    def from_environment(cls) -> "AppSettings":
        project_root = _environment_path("POLITICA_PROJECT_ROOT", PROJECT_ROOT)
        return cls(
            project_root=project_root,
            base_database=_environment_path(
                "POLITICA_DATABASE_PATH",
                project_root / "data/database/politica_election_results.duckdb",
            ),
            app_data=_environment_path("POLITICA_APP_DATA", project_root / "data/app"),
            max_upload_bytes=int(os.environ.get("POLITICA_MAX_UPLOAD_BYTES", 2 * 1024**3)),
            max_archive_bytes=int(os.environ.get("POLITICA_MAX_ARCHIVE_BYTES", 25 * 1024**3)),
            max_archive_members=int(os.environ.get("POLITICA_MAX_ARCHIVE_MEMBERS", "10000")),
            max_xlsx_member_bytes=int(
                os.environ.get("POLITICA_MAX_XLSX_MEMBER_BYTES", 512 * 1024**2)
            ),
            preview_rows=int(os.environ.get("POLITICA_PREVIEW_ROWS", "20")),
            stage_batch_size=int(os.environ.get("POLITICA_STAGE_BATCH_SIZE", "500")),
            explorer_max_export_rows=int(
                os.environ.get("POLITICA_EXPLORER_MAX_EXPORT_ROWS", "1000000")
            ),
            publication_max_rows=int(
                os.environ.get("POLITICA_PUBLICATION_MAX_ROWS", "500000")
            ),
        )

    @property
    def jobs_root(self) -> Path:
        return self.app_data / "jobs"

    @property
    def releases_root(self) -> Path:
        return self.app_data / "releases"

    @property
    def website_root(self) -> Path:
        return self.app_data / "public_website"

    @property
    def website_releases_root(self) -> Path:
        return self.website_root / "releases"

    @property
    def website_exports_root(self) -> Path:
        return self.website_root / "exports"

    @property
    def website_active_pointer(self) -> Path:
        return self.website_root / "active.json"

    def ensure_directories(self) -> None:
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.releases_root.mkdir(parents=True, exist_ok=True)
        self.website_releases_root.mkdir(parents=True, exist_ok=True)
        self.website_exports_root.mkdir(parents=True, exist_ok=True)
