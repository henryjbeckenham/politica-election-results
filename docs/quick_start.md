# Quick start

## If you only want to keep the completed database

Keep the extracted release folder intact. You do not need to install DuckDB separately to preserve the database or run the Stage 3 application.

Do not separate `data/database/politica_election_results.duckdb` from `data/parquet`. The database's formal-ballot views use relative paths to those Parquet files.

## If you want to verify it

From the release folder:

```bash
uv sync --locked
uv run politica-erd-validate
```

Expected result: `"status": "PASS"` and an empty `failures` list. `uv` installs the project-specific DuckDB package, so DuckDB does not need to be installed separately.

## If you want to inspect the data directly

Developers and analysts can connect any DuckDB-compatible client to:

`data/database/politica_election_results.duckdb`

Open the client from the release folder so the relative Parquet views resolve. Ready-to-use SQL is in `docs/sample_queries.sql`.

## What not to do

- Do not open the `.duckdb` file in Excel or Google Sheets.
- Do not edit raw AEC files.
- Do not paste future election files into canonical tables.
- Do not replace an official source revision in place; corrections are registered as new revisions.

## Use the Stage 11 application

Start the guided upload, mapping, validation and publication interface with:

```bash
uv run politica-erd-app
```

Release 1.6.0 retains the complete ingestion workflow, explorer, publication feeds and static website publication. The public results page begins with an election selector for the complete 2025, 2022, 2019 and 2016 federal elections. House and Senate feeds, maps, analyses, downloads and static-site assets follow the selected election. Open **Website publication** to generate, preview and download a verified four-election static website package. Nothing is uploaded automatically.

To use the public-facing results site, select **Public results** in the operator navigation or open `http://127.0.0.1:8765/results/`. It provides House, Senate and participation views plus release-bound downloads. It is read-only and cannot modify any database. See `docs/stage_11_operator_guide.md`.

To inspect data without DuckDB or SQL, open **Explore & export**. Choose a dataset, apply the election and record filters, page through the current canonical rows, and select **Export filtered CSV** to download exactly the applied result set. The explorer cannot modify the database. See `docs/stage_9_operator_guide.md`.

To connect Observable, Flourish or another visualisation client, open **Visualisation feeds**. Choose the election and optional state, then copy a JSON URL, download CSV or retain the manifest. See `docs/stage_10_operator_guide.md`.

The application also recognises eight individual AEC House result formats for an already governed election. Exact copies of sources already registered in the active database are deliberately rejected as duplicates.

The supported 2025 import can still be reproduced from the command line with:

```bash
uv run politica-erd-download-2025
uv run politica-erd-import-2025
```

If an import is interrupted after source checkpoints exist:

```bash
uv run politica-erd-import-2025 --resume
```
