# Politica Election Results Database

This repository is the durable source-control home for the Politica Election Results Database.

## Repository status

The repository transition began on 25 July 2026. The repository is not yet the verified Stage 14.6, application version 1.8.0 source baseline.

The supplied migration evidence has independently established:

- Stage 14.2, application 1.4.0, as the 2022 addition;
- Stage 14.3, application 1.5.0, as the 2019 addition;
- Stage 14.4, application 1.6.0, as the 2016 addition;
- a complete Stage 14.4 cumulative update payload;
- passing Stage 14.4 build, test and clean-integration reports.

The project-governed continuation target is Stage 14.6, application 1.8.0, covering the 2025, 2022, 2019, 2016, 2013 and 2010 Australian federal elections. That target remains `pending_verification` until the exact cumulative Stage 14.6 update and current release evidence are inspected and committed.

See [CURRENT_STATE.json](CURRENT_STATE.json) and [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md) before starting development.

## Authority order

1. The accepted commit on the default branch and `CURRENT_STATE.json`.
2. Checksummed current-release artifacts recorded in `manifests/DATA_MANIFEST.json`.
3. Immutable release evidence.
4. Historical development packages.
5. Conversational statements, which are not authoritative without reproducible files.

## Storage boundaries

GitHub stores application source, tests, schemas, configuration, small manifests and project documentation.

Large or generated artifacts must remain outside ordinary Git history, including:

- DuckDB databases;
- Parquet datasets;
- official AEC source archives;
- immutable release packages;
- formal-preference datasets;
- local environments and credentials;
- generated candidates and temporary run directories.

Their exact filenames, sizes, checksums and persistent locations belong in `manifests/DATA_MANIFEST.json`.

## Development rule

Never modify a verified predecessor database in place. Build a separate candidate, run all blocking validation, close every writer, complete final read-only verification, store the release artifacts, and update `CURRENT_STATE.json` last.

