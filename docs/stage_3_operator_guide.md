# Stage 3 operator guide

This document records the Stage 3 baseline. For the current individual-file route and the corrected release workflow, use `docs/stage_4_operator_guide.md`.

## What Stage 3 adds

Stage 3 is a local web application around the validated Election Results Database. It provides one guided place to:

1. upload a CSV, XLSX or ZIP source;
2. inspect its headers and sample rows;
3. select a registered authority adapter;
4. review unresolved People, Parties and Constituencies;
5. execute or resume safe staging on a working database copy;
6. run blocking validation when the selected route has a registered canonical transformer; and
7. create an immutable publication release only after that canonical route passes.

The application also previews and applies a read-only Google Sheets refresh of the Grand Database reference tabs. It cannot write to Google Sheets.

## Start the application

DuckDB does not need to be installed separately. Python 3.11 or newer and `uv` are the only machine-level prerequisites. If `uv` is not already installed, run `python -m pip install uv` once.

On Windows, double-click `start_politica.bat`. On macOS, double-click `start_politica.command` after granting it permission to run. The equivalent terminal commands are below.

From the extracted project folder, run the following. The first `uv sync` needs internet access to obtain the locked Python packages; later starts reuse the local environment.

```bash
uv sync --locked
uv run politica-erd-app
```

The application opens in the default browser at `http://127.0.0.1:8765`. It listens only on the local computer. To choose another port:

```bash
uv run politica-erd-app --port 9000
```

Keep the launcher or terminal window open while a job is running. Closing only the browser tab is safe; closing the terminal stops the local server and interrupts active work. On the next start, Politica marks an interrupted job for checkpointed resume.

## Ingest a file

1. Select **Ingest data**.
2. Drop in one `.csv`, `.xlsx` or `.zip` file.
3. Confirm the detected adapter, dataset, destination and sample rows.
4. Enter the authority and election identifiers shown by the source release.
5. Start the job. The detected format card states whether the selected dataset has a registered canonical transformer.
6. If mapping review is requested, match the source label only to an existing canonical record. The ingestion application never creates a Grand Database record silently.
7. Run validation only when a canonical transformer is available.
8. Publish only when the application reports zero blocking failures and zero unresolved required mappings.

Unknown formats are preserved and quarantined. A known source format without a registered canonical transformer is also retained source-native and cannot publish. A developer adds a versioned adapter and transformation for a new format, after which the same governed workflow can be run without guessing column meanings.

To exercise the complete canonical path included in release 0.3.0, open **Ingest data**, expand **Advanced actions**, and select **Reproduce 2025 release**. This creates an isolated job and runs the existing 45-source AEC pipeline; it is a substantial rebuild and can take a long time.

## Safe execution model

- The original upload is stored unchanged with a SHA-256 checksum.
- Job metadata and uploads live under `data/app/jobs`; they are separate from the governed database.
- Transformations run against a per-job working copy of the DuckDB database.
- An interruption leaves the validated base release untouched and the job resumable.
- Publication creates a self-contained immutable release under `data/app/releases`, including its DuckDB file, required Parquet, manifests and integrity inventory, only after validation and operator approval.
- The supplied AEC 2025 batch adapter can reproduce the governed 2025 release through this working-copy path.

## Google Sheets reference sync

The source workbook must grant Viewer access to a Google service-account email. The one-time setup is:

1. create or choose a Google Cloud project;
2. enable the Google Sheets API;
3. create a service account and download its JSON key;
4. share the Grand Database with the service account's `client_email` as **Viewer**; and
5. point Politica to the downloaded JSON file before starting it.

On macOS or Linux:

```bash
POLITICA_GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/service-account.json uv run politica-erd-app
```

On Windows PowerShell:

```powershell
$env:POLITICA_GOOGLE_SERVICE_ACCOUNT_FILE = "C:\path\to\service-account.json"
uv run politica-erd-app
```

`GOOGLE_APPLICATION_CREDENTIALS` may be used instead. The application requests only the `spreadsheets.readonly` OAuth scope and reads exactly these tabs:

- `People`
- `Parties`
- `Constituencies`

The workbook ID is pinned by `config/grand_sync_contract.yml` (and mirrored in `config/database.yml`), or by an explicit deployment-level `POLITICA_GRAND_DATABASE_ID`. The operator interface cannot substitute another accessible workbook.

The workflow is **Preview changes** followed by **Apply and activate local snapshot**. Each preview token is one-shot; a repeated successful request returns the existing audit result and cannot overwrite its immutable release. Applying the exact reviewed revision updates only the local `sync` schema on a working database copy, validates it, creates an immutable local reference release and switches the application's active local release. It does not write to Google Sheets. Rows missing from a later sheet are retained locally and shown as warnings, protecting historical identities. The Grand Database `Results` tab is excluded by contract.

## What to back up

The complete release directory remains the backup unit. Keep the DuckDB file, Parquet files, raw sources, manifests, snapshots, configuration, application code and published releases together. Do not move only the `.duckdb` file.
