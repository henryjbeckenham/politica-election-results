# Baseline migration

## Objective

Admit the exact Stage 14.6, application 1.8.0 source and release evidence into the persistent GitHub and large-artifact storage system without reconstructing every historical stage during each later chat.

## Required inputs

- cumulative Stage 14.6 update archive;
- current active-release pointer;
- current release manifest;
- build, test, integration and validation reports;
- current DuckDB database;
- every Parquet artifact required by database views;
- exact checksums for all external artifacts.

## Admission procedure

1. Verify ZIP integrity and calculate SHA-256 before extraction.
2. Inventory every archive member and reject unsafe or unexpected paths.
3. Determine whether the Stage 14.6 payload is cumulative.
4. Extract the application source into an isolated candidate directory.
5. Remove only generated runtime data that is governed by the external data manifest.
6. Scan for credentials and machine-specific secrets.
7. Verify that `pyproject.toml`, application metadata and packaged release reports agree on version 1.8.0.
8. Reproduce the complete source-tree inventory and calculate deterministic checksums.
9. Run source-level tests that do not require unavailable large data.
10. Resolve the external current database and Parquet artifacts.
11. Reproduce the database checksum and release identity in read-only mode.
12. Confirm the exact election coverage.
13. Add application source, tests, schemas, configuration and documentation to a migration branch.
14. Update `manifests/DATA_MANIFEST.json` with external artifact locations and checksums.
15. Update `PROJECT_HANDOVER.md`.
16. Set `baseline_admission.status` to `accepted` only after all blocking checks pass.
17. Merge the reviewed migration commit to the default branch.
18. Tag the accepted source only after the merge commit and manifest agree.

## Rejection conditions

Reject the baseline if any of the following remains unexplained:

- source or report version mismatch;
- missing required release artifact;
- database checksum mismatch;
- missing Parquet dependency;
- failed blocking test;
- unsafe archive path;
- committed credential;
- election coverage inconsistent with the release manifest;
- an active pointer that does not resolve to the verified database.
