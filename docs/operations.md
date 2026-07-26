# Operations, synchronization and recovery

## Working directory

Operate from the project root. Ballot and ballot-preference views refer to `data/parquet` with project-relative paths.

## Grand Database synchronization

The `sync` schema is a local read-only replica. Its packaged baseline snapshot is `data/snapshots/grand_database_2026-07-16.json`, governed by `config/grand_sync_contract.yml`.

Stage 3 performs a governed refresh through the Google Sheets API:

1. read People, Parties and Constituencies using the pinned contract and read-only OAuth scope;
2. verify the exact tab headers and calculate a source revision;
3. show additions, changes, unchanged rows and missing historical rows before any local change;
4. require the exact reviewed revision when the operator applies the preview;
5. write a new immutable local snapshot and update only a per-job working database copy; and
6. rerun validation before activating the new local reference release.

Rows absent from a later workbook are retained locally for historical integrity. The application never writes to Google Sheets, and the Grand Database `Results` tab is excluded by contract.

## Source update procedure

1. Add the new official file to the source catalogue.
2. Run the downloader to create a fresh manifest and checksums.
3. Confirm exactly one adapter detects each supported file.
4. Run the import. Use `--resume` only when the current manifest matches the existing run.
5. Require zero blocking validation failures.
6. Preserve the new database, manifests, raw sources and Parquet together.

## Backup unit

The recoverable release unit is the entire project data package, including:

- `data/database`;
- `data/parquet`;
- `data/raw`;
- `data/manifests`;
- `data/snapshots`;
- `data/app/releases` and its active-release pointer, when the operator has published a Stage 3 release;
- `config`, `schema`, `src`, `docs`, `tests`; and
- the Stage 2 and Stage 3 build manifests and validation/test reports under `dist`.

The raw layer is required for a full audit/rebuild. The Parquet layer is required for formal-ballot views. A copy of the `.duckdb` file alone is not a complete backup.

## Restore test

1. Extract the package into a new directory.
2. Change into that directory.
3. Run `uv sync --locked`.
4. Run `uv run politica-erd-validate`.
5. Run the automated tests.
6. Compare the database SHA-256 with `dist/build_manifest.json`.

## Migration rule

Schema changes are new numbered files in `schema`; do not edit a released database manually. A migration must preserve source revisions and lineage, update the schema version when required, rebuild generated catalogues and pass both empty-schema and loaded-database validation.

## Explorer and CSV export

The Stage 9 explorer resolves the active release pointer through the same checksum verification used by validation and ingestion. It then opens DuckDB read-only and applies only registered parameterised queries. A query or export therefore cannot become a database migration or a publication action.

The default CSV ceiling is 1,000,000 rows. Narrow a larger request by election, chamber, state, contest, result type, vote type or reporting level. Do not increase the ceiling merely to move the anonymous formal-ballot corpus: Stage 9 exports the ballot-dataset inventory, not individual ballots or preference paths.

## Failure recovery

- A download ending in `.part` is incomplete and must not enter a manifest.
- A failed transformation can be rerun with `--resume`; completed source checkpoints are reused only when their observed row counts match.
- Formal-preference checkpoints are reused only after their expected counts and all file hashes pass.
- A failed blocking check prevents publication.
- Never delete the prior validated release until the replacement independently passes.
