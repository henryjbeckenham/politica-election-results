# Release notes 0.3.0

## Stage 3 ingestion application

This release adds the first local operator application without changing the validated Stage 2 election facts.

Delivered:

- responsive local web interface for dashboard, upload, mapping review, validation, publication and reference sync;
- CSV, XLSX and ZIP inspection with archive traversal and compression-bomb protections;
- versioned adapter detection by filename and required headers;
- immutable upload storage, durable job state and resumable execution;
- exact-only canonical matching against read-only People, Parties and Constituencies;
- quarantine of unknown schemas and unresolved labels;
- per-job DuckDB working copies and validation-gated immutable releases;
- self-contained DuckDB, Parquet and manifest release bundles with artifact verification;
- pinned-base compare-and-swap activation, single-instance enforcement and crash recovery;
- controlled reproduction path for the complete governed AEC 2025 import;
- Google Sheets read-only exact-base preview and crash-recoverable local apply/activation with source-revision confirmation;
- local-only launcher on `127.0.0.1` and an operator guide.

The complete AEC 2025 batch route is the canonical insertion path included in this release. A standalone uploaded dataset is inspected and staged, but it cannot publish unless a canonical transformer is explicitly registered for that adapter and dataset. This is a deliberate guard against treating similar-looking columns as equivalent.

The canonical Stage 2 database remains release 0.2.0 and retains its original checksum. Version 0.3.0 identifies the application package wrapped around that validated database.
