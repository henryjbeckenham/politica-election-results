# Architecture

## Purpose

The Politica Election Results Database stores governed Australian election results, their official sources, transformation lineage, audit evidence and publication outputs.

## Ownership boundary

The Election Results Database owns:

- elections and chambers;
- contests and candidacies;
- reporting units;
- vote, participation and count results;
- ballot groups and formal-preference datasets;
- provenance, transformations and validation evidence;
- election-result publication feeds.

The Politica Grand Database remains authoritative for shared People, Parties and Constituencies reference data.

## Storage layers

The latest reproduced architecture evidence, Stage 14.4, defines:

1. `data/raw`: immutable official files.
2. `data/staging`: source-native records and mapping status.
3. `data/database`: governed DuckDB databases.
4. `data/parquet`: partitioned facts and formal-preference paths.
5. `data/snapshots`: read-only reference snapshots.
6. `data/manifests`: source, checksum, transformation and export manifests.
7. `config/adapters`: versioned source-format definitions.
8. `docs`: operating instructions and generated catalogues.

Only source code, small configuration, tests, schemas, documentation and small manifests belong in GitHub. Runtime data and immutable release artifacts are external and checksummed.

## Release identity

An accepted release is identified by all of:

- application version;
- schema version;
- release ID;
- source commit;
- database SHA-256;
- data-manifest version;
- election coverage;
- passing validation evidence.

No single filename or version string proves a release identity.

