"""Controlled Google Sheets -> DuckDB reference-data synchronisation.

Only the Grand Database ``People``, ``Parties`` and ``Constituencies`` tabs are
read.  The Google client is created with the Sheets read-only OAuth scope and
this module deliberately exposes no Google write operation.  ``apply=True``
means "apply the reviewed snapshot to the local ``sync`` schema"; it never
means writing election results (or anything else) back to Google Sheets.

The public entry point is :class:`GoogleSheetsReferenceSynchronizer`.  A normal
application flow is:

1. call ``run(apply=False)`` and show its diff to the user;
2. retain the returned ``source_revision_sha256``;
3. after explicit user approval, call ``run(apply=True,
   expected_source_revision_sha256=<reviewed revision>)``.

Google dependencies are imported lazily so the database remains usable without
them.  Online sync requires ``google-api-python-client`` and ``google-auth``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import duckdb
import yaml

from ..db import bulk_insert
from ..grand_sync import _boolean, _date, _row_hash, _timestamp

SHEETS_READ_ONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
ALLOWED_SHEETS = ("People", "Parties", "Constituencies")
EXPECTED_TARGETS = {
    "People": "sync.person",
    "Parties": "sync.party",
    "Constituencies": "sync.constituency",
}


class SheetsSyncError(RuntimeError):
    """Base class for controlled reference-sync errors."""


class GoogleSheetsDependencyError(SheetsSyncError):
    """Raised when the optional official Google client libraries are absent."""


class CredentialsConfigurationError(SheetsSyncError):
    """Raised when service-account credentials are absent or ambiguous."""


class SyncContractError(SheetsSyncError):
    """Raised when the Grand Database contract would permit an unsafe sync."""


class SourceValidationError(SheetsSyncError):
    """Raised when a source tab does not conform to the declared contract."""


class ApplyConfirmationError(SheetsSyncError):
    """Raised when local application was not confirmed against a reviewed revision."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_value,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def _quoted_a1_tab(tab_name: str) -> str:
    return "'" + tab_name.replace("'", "''") + "'!A:AZ"


@dataclass(frozen=True)
class GoogleServiceAccountConfig:
    """Service-account credential source, normally populated from environment.

    ``POLITICA_GOOGLE_SERVICE_ACCOUNT_JSON`` accepts the complete JSON document.
    ``POLITICA_GOOGLE_SERVICE_ACCOUNT_FILE`` accepts a file path and takes
    precedence over the conventional ``GOOGLE_APPLICATION_CREDENTIALS`` path.
    Supplying both JSON and a file is rejected so credential selection is never
    implicit.  ``POLITICA_GOOGLE_DELEGATED_SUBJECT`` is optional and only needed
    for domain-wide delegation.
    """

    credentials_file: Path | None = None
    credentials_json: str | None = None
    delegated_subject: str | None = None

    @classmethod
    def from_environment(cls) -> GoogleServiceAccountConfig:
        file_value = os.getenv("POLITICA_GOOGLE_SERVICE_ACCOUNT_FILE") or os.getenv(
            "GOOGLE_APPLICATION_CREDENTIALS"
        )
        return cls(
            credentials_file=Path(file_value).expanduser() if file_value else None,
            credentials_json=os.getenv("POLITICA_GOOGLE_SERVICE_ACCOUNT_JSON"),
            delegated_subject=os.getenv("POLITICA_GOOGLE_DELEGATED_SUBJECT") or None,
        )

    def load(self) -> tuple[Any, dict[str, Any]]:
        """Return official google-auth credentials and a non-secret descriptor."""

        if self.credentials_file is not None and self.credentials_json:
            raise CredentialsConfigurationError(
                "Set either POLITICA_GOOGLE_SERVICE_ACCOUNT_FILE (or "
                "GOOGLE_APPLICATION_CREDENTIALS) or POLITICA_GOOGLE_SERVICE_ACCOUNT_JSON, "
                "not both."
            )
        if self.credentials_file is None and not self.credentials_json:
            raise CredentialsConfigurationError(
                "Google Sheets sync needs service-account credentials. Set "
                "POLITICA_GOOGLE_SERVICE_ACCOUNT_FILE (or GOOGLE_APPLICATION_CREDENTIALS) "
                "to a service-account JSON file, or set "
                "POLITICA_GOOGLE_SERVICE_ACCOUNT_JSON to the complete JSON document."
            )

        try:
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover - depends on optional packages
            raise GoogleSheetsDependencyError(
                "Google Sheets sync requires the optional packages "
                "'google-auth' and 'google-api-python-client'."
            ) from exc

        if self.credentials_json:
            try:
                info = json.loads(self.credentials_json)
            except json.JSONDecodeError as exc:
                raise CredentialsConfigurationError(
                    "POLITICA_GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON."
                ) from exc
            if not isinstance(info, dict):
                raise CredentialsConfigurationError(
                    "POLITICA_GOOGLE_SERVICE_ACCOUNT_JSON must contain a JSON object."
                )
            source = "environment_json"
        else:
            assert self.credentials_file is not None
            path = self.credentials_file.resolve()
            if not path.is_file():
                raise CredentialsConfigurationError(
                    f"Google service-account credential file does not exist: {path}"
                )
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CredentialsConfigurationError(
                    f"Could not read a valid service-account JSON file: {path}"
                ) from exc
            source = "credential_file"

        if info.get("type") != "service_account":
            raise CredentialsConfigurationError(
                "The configured Google credential must be a service-account credential."
            )
        try:
            credentials = service_account.Credentials.from_service_account_info(
                info, scopes=[SHEETS_READ_ONLY_SCOPE]
            )
        except (TypeError, ValueError) as exc:
            raise CredentialsConfigurationError(
                "The configured service-account JSON is missing required Google credential fields."
            ) from exc
        if self.delegated_subject:
            credentials = credentials.with_subject(self.delegated_subject)

        descriptor = {
            "source": source,
            "client_email": info.get("client_email"),
            "project_id": info.get("project_id"),
            "delegated_subject": self.delegated_subject,
            "oauth_scopes": [SHEETS_READ_ONLY_SCOPE],
        }
        return credentials, descriptor


class GoogleSheetsReader:
    """Minimal, read-only wrapper around the official Google Sheets v4 API."""

    def __init__(
        self,
        credentials: GoogleServiceAccountConfig | None = None,
        *,
        service: Any | None = None,
        credential_descriptor: Mapping[str, Any] | None = None,
    ) -> None:
        if service is not None:
            self._service = service
            self.credential_descriptor = dict(
                credential_descriptor or {"source": "injected_service", "oauth_scopes": []}
            )
            return

        loaded, descriptor = (credentials or GoogleServiceAccountConfig.from_environment()).load()
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - depends on optional packages
            raise GoogleSheetsDependencyError(
                "Google Sheets sync requires the optional package "
                "'google-api-python-client' (and its 'google-auth' dependency)."
            ) from exc
        self._service = build(
            "sheets", "v4", credentials=loaded, cache_discovery=False
        )
        self.credential_descriptor = descriptor

    def fetch_reference_snapshot(
        self, spreadsheet_id: str, sheet_names: Sequence[str] = ALLOWED_SHEETS
    ) -> dict[str, Any]:
        """Fetch only the three permitted reference tabs as formatted rows."""

        requested = tuple(sheet_names)
        if requested != ALLOWED_SHEETS:
            raise SyncContractError(
                f"Reference sync may read only {ALLOWED_SHEETS!r}, in that order; "
                f"received {requested!r}."
            )
        ranges = [_quoted_a1_tab(name) for name in requested]
        try:
            metadata = (
                self._service.spreadsheets()
                .get(
                    spreadsheetId=spreadsheet_id,
                    includeGridData=False,
                    fields="spreadsheetId,properties(title,locale,timeZone)",
                )
                .execute()
            )
            response = (
                self._service.spreadsheets()
                .values()
                .batchGet(
                    spreadsheetId=spreadsheet_id,
                    ranges=ranges,
                    majorDimension="ROWS",
                    valueRenderOption="FORMATTED_VALUE",
                    dateTimeRenderOption="FORMATTED_STRING",
                )
                .execute()
            )
        except Exception as exc:  # googleapiclient errors are optional import types
            raise SheetsSyncError(
                "Google Sheets read failed. Confirm that the service-account email has "
                "viewer access to the Grand Database and that the workbook ID is correct."
            ) from exc

        value_ranges = response.get("valueRanges", [])
        if len(value_ranges) != len(requested):
            raise SourceValidationError(
                f"Google returned {len(value_ranges)} tab ranges for "
                f"{len(requested)} requested reference tabs."
            )

        captured_at = _utc_now()
        tables = {
            name: {"range": value_range.get("range"), "values": value_range.get("values", [])}
            for name, value_range in zip(requested, value_ranges, strict=True)
        }
        snapshot: dict[str, Any] = {
            "spreadsheet_id": metadata.get("spreadsheetId", spreadsheet_id),
            "spreadsheet_properties": metadata.get("properties", {}),
            "captured_at": captured_at.isoformat(),
            "credential": self.credential_descriptor,
            "tables": tables,
        }
        snapshot["source_revision_sha256"] = _source_revision(snapshot)
        return snapshot


def google_sheets_configuration_status(
    credentials: GoogleServiceAccountConfig | None = None,
) -> dict[str, Any]:
    """Return a non-throwing readiness result for a status/API endpoint."""

    configured = credentials or GoogleServiceAccountConfig.from_environment()
    has_source = bool(configured.credentials_file or configured.credentials_json)
    try:
        _, descriptor = configured.load()
        try:
            import googleapiclient.discovery  # noqa: F401
        except ImportError as exc:
            raise GoogleSheetsDependencyError(
                "Google Sheets sync requires the optional package "
                "'google-api-python-client' (and its 'google-auth' dependency)."
            ) from exc
    except GoogleSheetsDependencyError as exc:
        return {
            "available": False,
            "configured": has_source,
            "reason": "dependencies_missing",
            "message": str(exc),
            "read_only": True,
            "oauth_scope": SHEETS_READ_ONLY_SCOPE,
        }
    except CredentialsConfigurationError as exc:
        return {
            "available": False,
            "configured": has_source,
            "reason": "credentials_invalid" if has_source else "credentials_not_configured",
            "message": str(exc),
            "read_only": True,
            "oauth_scope": SHEETS_READ_ONLY_SCOPE,
        }
    return {
        "available": True,
        "configured": True,
        "reason": None,
        "message": "Google Sheets read-only synchronisation is configured.",
        "read_only": True,
        "oauth_scope": SHEETS_READ_ONLY_SCOPE,
        "credential": descriptor,
    }


@dataclass(frozen=True)
class _TableSpec:
    sheet: str
    target: str
    primary_key: str
    columns: tuple[str, ...]
    history_fields: tuple[str, ...]
    row_builder: Callable[[dict[str, str], datetime], tuple[Any, ...]]


def _person_row(row: dict[str, str], synced_at: datetime) -> tuple[Any, ...]:
    return (
        row["person_id"],
        row["full_name"],
        row["display_name"] or None,
        row["given_names"] or None,
        row["family_name"] or None,
        row["aliases"] or None,
        _date(row["date_of_birth"]),
        row["country"] or None,
        _boolean(row["active"]),
        row["record_status"] or None,
        row["audit_status"] or None,
        _row_hash(row),
        synced_at,
    )


def _party_row(row: dict[str, str], synced_at: datetime) -> tuple[Any, ...]:
    valid_from = _date(row["valid_from"])
    valid_to = _date(row["valid_to"])
    _validate_history("Parties", row["party_id"], valid_from, valid_to)
    return (
        row["party_id"],
        row["party_name"],
        row["short_name"] or None,
        row["abbreviation"] or None,
        row["aliases"] or None,
        row["party_family"] or None,
        row["colour_hex"] or None,
        row["jurisdiction"] or None,
        row["country"] or None,
        _boolean(row["active"]),
        valid_from,
        valid_to,
        row["record_status"] or None,
        row["audit_status"] or None,
        _row_hash(row),
        synced_at,
    )


def _constituency_row(row: dict[str, str], synced_at: datetime) -> tuple[Any, ...]:
    valid_from = _date(row["valid_from"])
    valid_to = _date(row["valid_to"])
    _validate_history("Constituencies", row["constituency_id"], valid_from, valid_to)
    return (
        row["constituency_id"],
        row["constituency_name"],
        row["constituency_type"],
        row["jurisdiction"],
        row["chamber"] or None,
        row["state_territory"] or None,
        row["country"] or None,
        row["election_context"] or None,
        row["boundary_version"] or None,
        valid_from,
        valid_to,
        row["parent_constituency_id"] or None,
        row["aliases"] or None,
        row["legacy_group_id"] or None,
        row["source_id"] or None,
        row["source_locator"] or None,
        row["evidence_status"] or None,
        row["record_status"] or None,
        row["audit_status"] or None,
        _timestamp(row["audited_at"]),
        row["audited_by"] or None,
        row["superseded_by_constituency_id"] or None,
        row["notes"] or None,
        row["official_constituency_code"] or None,
        row["official_code_status"] or None,
        _row_hash(row),
        synced_at,
    )


TABLE_SPECS: dict[str, _TableSpec] = {
    "People": _TableSpec(
        sheet="People",
        target="sync.person",
        primary_key="person_id",
        columns=(
            "person_id", "full_name", "display_name", "given_names", "family_name", "aliases",
            "date_of_birth", "country", "active", "record_status", "audit_status",
            "source_row_hash", "grand_synced_at",
        ),
        history_fields=(),
        row_builder=_person_row,
    ),
    "Parties": _TableSpec(
        sheet="Parties",
        target="sync.party",
        primary_key="party_id",
        columns=(
            "party_id", "party_name", "short_name", "abbreviation", "aliases", "party_family",
            "colour_hex", "jurisdiction", "country", "active", "valid_from", "valid_to",
            "record_status", "audit_status", "source_row_hash", "grand_synced_at",
        ),
        history_fields=("valid_from", "valid_to"),
        row_builder=_party_row,
    ),
    "Constituencies": _TableSpec(
        sheet="Constituencies",
        target="sync.constituency",
        primary_key="constituency_id",
        columns=(
            "constituency_id", "constituency_name", "constituency_type", "jurisdiction", "chamber",
            "state_territory", "country", "election_context", "boundary_version", "valid_from",
            "valid_to", "parent_constituency_id", "aliases", "legacy_group_id", "source_id",
            "source_locator", "evidence_status", "record_status", "audit_status", "audited_at",
            "audited_by", "superseded_by_constituency_id", "notes", "official_constituency_code",
            "official_code_status", "source_row_hash", "grand_synced_at",
        ),
        history_fields=("valid_from", "valid_to"),
        row_builder=_constituency_row,
    ),
}


def _validate_history(
    sheet: str, key: str, valid_from: date | None, valid_to: date | None
) -> None:
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        raise SourceValidationError(
            f"{sheet} row {key!r} has valid_to {valid_to} before valid_from {valid_from}."
        )


def _source_revision(snapshot: Mapping[str, Any]) -> str:
    payload = {
        "spreadsheet_id": snapshot.get("spreadsheet_id"),
        "tables": {
            name: snapshot.get("tables", {}).get(name, {}).get("values", [])
            for name in ALLOWED_SHEETS
        },
    }
    return _canonical_hash(payload)


def _selected_sheets(sheet_names: Sequence[str]) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys(sheet_names))
    if not requested:
        raise SyncContractError("Select at least one Grand Database reference tab.")
    unsupported = [name for name in requested if name not in ALLOWED_SHEETS]
    if unsupported:
        raise SyncContractError(
            f"Unsupported Grand Database tab selection: {unsupported!r}. "
            f"Only {ALLOWED_SHEETS!r} may be synchronised."
        )
    return tuple(name for name in ALLOWED_SHEETS if name in requested)


def _load_contract(project_root: Path) -> dict[str, Any]:
    path = project_root / "config" / "grand_sync_contract.yml"
    try:
        contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SyncContractError(f"Could not load the Grand sync contract: {path}") from exc
    if not isinstance(contract, dict):
        raise SyncContractError("Grand sync contract must be a YAML object.")

    tables = contract.get("tables")
    if not isinstance(tables, dict) or tuple(tables) != ALLOWED_SHEETS:
        raise SyncContractError(
            f"Grand sync contract must contain only {ALLOWED_SHEETS!r}, in that order."
        )
    for sheet, expected_target in EXPECTED_TARGETS.items():
        definition = tables[sheet]
        spec = TABLE_SPECS[sheet]
        if definition.get("target") != expected_target:
            raise SyncContractError(
                f"{sheet} may target only {expected_target}; found {definition.get('target')!r}."
            )
        if definition.get("primary_key") != spec.primary_key:
            raise SyncContractError(
                f"{sheet} primary key must be {spec.primary_key!r}; "
                f"found {definition.get('primary_key')!r}."
            )
        headers = definition.get("headers")
        if not isinstance(headers, list) or not headers:
            raise SyncContractError(f"{sheet} must declare a non-empty header list.")
    return contract


def _records(
    table_payload: Mapping[str, Any], expected_headers: Sequence[str], primary_key: str, sheet: str
) -> list[dict[str, str]]:
    values = table_payload.get("values", [])
    if not isinstance(values, list) or not values:
        raise SourceValidationError(f"Grand Database {sheet} tab contains no rows.")
    headers = [_cell_text(value) for value in values[0]]
    if headers != list(expected_headers):
        raise SourceValidationError(
            f"Grand Database {sheet} header mismatch: expected {list(expected_headers)!r}; "
            f"found {headers!r}."
        )

    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for sheet_row, raw_row in enumerate(values[1:], start=2):
        if not isinstance(raw_row, list):
            raise SourceValidationError(f"{sheet} row {sheet_row} is not a cell array.")
        cells = [_cell_text(value) for value in raw_row]
        if len(cells) > len(headers) and any(value.strip() for value in cells[len(headers) :]):
            raise SourceValidationError(
                f"{sheet} row {sheet_row} contains values beyond the declared headers."
            )
        cells = (cells + [""] * len(headers))[: len(headers)]
        if not any(value.strip() for value in cells):
            continue
        record = dict(zip(headers, cells, strict=True))
        key = record[primary_key].strip()
        if not key:
            raise SourceValidationError(
                f"{sheet} row {sheet_row} is populated but has no {primary_key}."
            )
        if key in seen:
            raise SourceValidationError(
                f"{sheet} contains duplicate {primary_key} {key!r} (row {sheet_row})."
            )
        seen.add(key)
        record[primary_key] = key
        records.append(record)
    return records


@dataclass(frozen=True)
class RowChange:
    key: str
    action: str
    changed_fields: tuple[str, ...] = ()
    source_row_hash: str | None = None
    local_row_hash: str | None = None
    history: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "action": self.action,
            "changed_fields": list(self.changed_fields),
            "source_row_hash": self.source_row_hash,
            "local_row_hash": self.local_row_hash,
            "history": {key: _json_value(value) for key, value in self.history.items()},
        }


@dataclass(frozen=True)
class TableDiff:
    sheet: str
    target: str
    source_rows: int
    local_rows: int
    added: int
    updated: int
    unchanged: int
    retained_local: int
    changes: tuple[RowChange, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "target": self.target,
            "source_rows": self.source_rows,
            "local_rows": self.local_rows,
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "retained_local": self.retained_local,
            "changes": [change.as_dict() for change in self.changes],
        }


@dataclass(frozen=True)
class SyncRunResult:
    audit_id: str
    mode: str
    applied: bool
    spreadsheet_id: str
    spreadsheet_title: str | None
    source_revision_sha256: str
    fetched_at: datetime
    applied_at: datetime | None
    credential: Mapping[str, Any]
    selected_sheets: tuple[str, ...]
    tables: Mapping[str, TableDiff]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "operation": "grand_database_reference_sync",
            "mode": self.mode,
            "applied": self.applied,
            "direction": "Grand Database -> Election Results Database sync schema",
            "spreadsheet_id": self.spreadsheet_id,
            "spreadsheet_title": self.spreadsheet_title,
            "source_revision_sha256": self.source_revision_sha256,
            "fetched_at": self.fetched_at.isoformat(),
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "credential": dict(self.credential),
            "guardrails": {
                "google_access": "read_only",
                "google_oauth_scope": SHEETS_READ_ONLY_SCOPE,
                "source_sheets_read": list(ALLOWED_SHEETS),
                "selected_reference_sheets": list(self.selected_sheets),
                "local_targets": [EXPECTED_TARGETS[name] for name in self.selected_sheets],
                "election_results_read_from_grand_database": False,
                "election_results_written_to_grand_database": False,
                "source_missing_rows_deleted_locally": False,
            },
            "tables": {name: diff.as_dict() for name, diff in self.tables.items()},
            "warnings": list(self.warnings),
        }


class GoogleSheetsReferenceSynchronizer:
    """Preview and explicitly apply the three canonical reference-table diffs.

    The contract (or ``POLITICA_GRAND_DATABASE_ID`` deployment setting) pins the
    authoritative spreadsheet. A caller-supplied ID must match that pin unless
    an internal caller deliberately opts into ``allow_workbook_override``.
    """

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        project_root: Path,
        *,
        reader: GoogleSheetsReader | None = None,
        workbook_id: str | None = None,
        credentials: GoogleServiceAccountConfig | None = None,
        allow_workbook_override: bool = False,
    ) -> None:
        self.connection = connection
        self.project_root = Path(project_root)
        self.contract = _load_contract(self.project_root)
        configured_id = self.contract.get("source", {}).get("workbook_id")
        pinned_id = os.getenv("POLITICA_GRAND_DATABASE_ID") or configured_id
        if not pinned_id:
            raise SyncContractError(
                "No Grand Database workbook ID was supplied or declared in the contract."
            )
        if workbook_id and workbook_id != pinned_id and not allow_workbook_override:
            raise SyncContractError(
                f"Requested spreadsheet {workbook_id!r} does not match the pinned Grand "
                f"Database spreadsheet {pinned_id!r}. Arbitrary workbook overrides are disabled."
            )
        self.pinned_workbook_id = str(pinned_id)
        self.workbook_id = str(workbook_id if allow_workbook_override and workbook_id else pinned_id)
        self.reader = reader or GoogleSheetsReader(credentials)

    def fetch_snapshot(self) -> dict[str, Any]:
        return self.reader.fetch_reference_snapshot(self.workbook_id)

    def run(
        self,
        *,
        apply: bool = False,
        expected_source_revision_sha256: str | None = None,
        snapshot: Mapping[str, Any] | None = None,
        snapshot_path: Path | None = None,
        selected_sheets: Sequence[str] = ALLOWED_SHEETS,
    ) -> SyncRunResult:
        """Fetch/preview reference diffs and optionally apply them locally.

        Applying requires both ``apply=True`` and the exact source revision from
        a previous preview.  Source rows missing from the new sheet are reported
        and retained locally, protecting historical identities from accidental
        deletion.  ``valid_from`` and ``valid_to`` are parsed, validated and
        stored without inference.
        """

        selected = _selected_sheets(selected_sheets)
        current = dict(snapshot) if snapshot is not None else self.fetch_snapshot()
        spreadsheet_id = str(current.get("spreadsheet_id") or self.workbook_id)
        if spreadsheet_id != self.workbook_id:
            raise SourceValidationError(
                f"Snapshot workbook {spreadsheet_id!r} does not match configured Grand "
                f"Database {self.workbook_id!r}."
            )
        source_revision = _source_revision(current)
        declared_revision = current.get("source_revision_sha256")
        if declared_revision and declared_revision != source_revision:
            raise SourceValidationError(
                "Snapshot source_revision_sha256 does not match its reference-tab contents."
            )
        if apply and expected_source_revision_sha256 != source_revision:
            if expected_source_revision_sha256 is None:
                message = (
                    "Applying a Sheets sync requires expected_source_revision_sha256 from "
                    "the reviewed preview."
                )
            else:
                message = (
                    "The Grand Database reference revision changed after preview; review a "
                    "new diff before applying."
                )
            raise ApplyConfirmationError(message)

        fetched_at = _parse_captured_at(current.get("captured_at"))
        typed_rows = self._typed_rows(current, fetched_at)
        diffs = {
            sheet: self._diff_table(TABLE_SPECS[sheet], rows)
            for sheet, rows in typed_rows.items()
            if sheet in selected
        }
        warnings = tuple(
            f"{sheet}: {diff.retained_local} local row(s) absent from the source were retained."
            for sheet, diff in diffs.items()
            if diff.retained_local
        )

        applied_at = None
        if apply:
            self._apply_rows({sheet: typed_rows[sheet] for sheet in selected})
            applied_at = _utc_now()

        if snapshot_path is not None:
            _write_snapshot(snapshot_path, current, source_revision)

        audit_id = "grand-sync-" + _canonical_hash(
            {
                "source_revision": source_revision,
                "fetched_at": fetched_at,
                "mode": "apply" if apply else "preview",
                "selected_sheets": selected,
            }
        )[:20]
        properties = current.get("spreadsheet_properties", {})
        return SyncRunResult(
            audit_id=audit_id,
            mode="apply" if apply else "preview",
            applied=apply,
            spreadsheet_id=spreadsheet_id,
            spreadsheet_title=properties.get("title") if isinstance(properties, dict) else None,
            source_revision_sha256=source_revision,
            fetched_at=fetched_at,
            applied_at=applied_at,
            credential=current.get("credential", self.reader.credential_descriptor),
            selected_sheets=selected,
            tables=diffs,
            warnings=warnings,
        )

    def _typed_rows(
        self, snapshot: Mapping[str, Any], synced_at: datetime
    ) -> dict[str, list[tuple[Any, ...]]]:
        tables = snapshot.get("tables")
        if not isinstance(tables, Mapping) or set(tables) != set(ALLOWED_SHEETS):
            raise SourceValidationError(
                f"Snapshot must contain exactly the reference tabs {ALLOWED_SHEETS!r}."
            )
        typed: dict[str, list[tuple[Any, ...]]] = {}
        for sheet in ALLOWED_SHEETS:
            spec = TABLE_SPECS[sheet]
            definition = self.contract["tables"][sheet]
            records = _records(
                tables[sheet], definition["headers"], spec.primary_key, sheet
            )
            try:
                typed[sheet] = [spec.row_builder(record, synced_at) for record in records]
            except (TypeError, ValueError) as exc:
                raise SourceValidationError(
                    f"Could not parse a typed value in the Grand Database {sheet} tab: {exc}"
                ) from exc
        return typed

    def _diff_table(
        self, spec: _TableSpec, source_rows: Sequence[tuple[Any, ...]]
    ) -> TableDiff:
        selected = ", ".join(spec.columns)
        try:
            local_rows = self.connection.execute(
                f"SELECT {selected} FROM {spec.target}"
            ).fetchall()
        except duckdb.Error as exc:
            raise SheetsSyncError(
                f"Local target {spec.target} is unavailable; build/migrate the database first."
            ) from exc

        key_index = spec.columns.index(spec.primary_key)
        hash_index = spec.columns.index("source_row_hash")
        local_by_key = {str(row[key_index]): row for row in local_rows}
        source_by_key = {str(row[key_index]): row for row in source_rows}
        changes: list[RowChange] = []
        added = updated = unchanged = 0

        for key in sorted(source_by_key):
            source = source_by_key[key]
            local = local_by_key.get(key)
            history = {
                field: source[spec.columns.index(field)] for field in spec.history_fields
            }
            if local is None:
                added += 1
                changes.append(
                    RowChange(
                        key=key,
                        action="add",
                        changed_fields=tuple(
                            column for column in spec.columns if column != "grand_synced_at"
                        ),
                        source_row_hash=source[hash_index],
                        history=history,
                    )
                )
            elif source[hash_index] == local[hash_index]:
                unchanged += 1
            else:
                updated += 1
                changed_fields = tuple(
                    column
                    for index, column in enumerate(spec.columns)
                    if column != "grand_synced_at"
                    and _json_value(source[index]) != _json_value(local[index])
                )
                changes.append(
                    RowChange(
                        key=key,
                        action="update",
                        changed_fields=changed_fields,
                        source_row_hash=source[hash_index],
                        local_row_hash=local[hash_index],
                        history=history,
                    )
                )

        retained = sorted(set(local_by_key) - set(source_by_key))
        for key in retained:
            local = local_by_key[key]
            changes.append(
                RowChange(
                    key=key,
                    action="retain_local",
                    local_row_hash=local[hash_index],
                    history={
                        field: local[spec.columns.index(field)] for field in spec.history_fields
                    },
                )
            )
        return TableDiff(
            sheet=spec.sheet,
            target=spec.target,
            source_rows=len(source_rows),
            local_rows=len(local_rows),
            added=added,
            updated=updated,
            unchanged=unchanged,
            retained_local=len(retained),
            changes=tuple(changes),
        )

    def _apply_rows(self, typed_rows: Mapping[str, Sequence[tuple[Any, ...]]]) -> None:
        try:
            self.connection.execute("BEGIN TRANSACTION")
            for sheet in ALLOWED_SHEETS:
                if sheet not in typed_rows:
                    continue
                spec = TABLE_SPECS[sheet]
                rows = list(typed_rows[sheet])
                key_index = spec.columns.index(spec.primary_key)
                for start in range(0, len(rows), 500):
                    keys = [row[key_index] for row in rows[start : start + 500]]
                    if not keys:
                        continue
                    placeholders = ", ".join("?" for _ in keys)
                    self.connection.execute(
                        f"DELETE FROM {spec.target} WHERE {spec.primary_key} IN ({placeholders})",
                        keys,
                    )
                columns = ", ".join(spec.columns)
                bulk_insert(
                    self.connection,
                    f"INSERT INTO {spec.target} ({columns})",
                    rows,
                )
            self.connection.execute("COMMIT")
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except duckdb.Error:
                pass
            raise


def _parse_captured_at(value: Any) -> datetime:
    if not value:
        return _utc_now()
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise SourceValidationError(f"Invalid snapshot captured_at value: {value!r}") from exc
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _write_snapshot(path: Path, snapshot: Mapping[str, Any], source_revision: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(snapshot)
    payload["source_revision_sha256"] = source_revision
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_value) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


__all__ = [
    "ALLOWED_SHEETS",
    "SHEETS_READ_ONLY_SCOPE",
    "ApplyConfirmationError",
    "CredentialsConfigurationError",
    "GoogleServiceAccountConfig",
    "GoogleSheetsDependencyError",
    "GoogleSheetsReader",
    "GoogleSheetsReferenceSynchronizer",
    "SheetsSyncError",
    "SourceValidationError",
    "SyncContractError",
    "SyncRunResult",
    "TableDiff",
    "google_sheets_configuration_status",
]
