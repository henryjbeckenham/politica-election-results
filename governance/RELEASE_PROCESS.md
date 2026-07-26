# Release process

## Candidate creation

1. Start from the accepted default-branch commit recorded in `CURRENT_STATE.json`.
2. Create a stage branch.
3. Resolve only the external artifacts required by that stage.
4. Create a fresh candidate database. Do not write to the predecessor.
5. Preserve every official source file and record its provenance and checksum.

## Validation

1. Run schema, controlled-value, relationship and data-dictionary checks.
2. Run election-specific source and reconciliation checks.
3. Check deterministic identifiers and collision handling.
4. Check row lineage and source-revision coverage.
5. Run regression tests for every previously accepted election.
6. Close all database writers.
7. Perform final read-only verification against the candidate copy.

## Publication

1. Generate the release manifest and validation report.
2. Store the database, Parquet artifacts, official sources and release package outside ordinary Git history.
3. Record exact external locations, sizes and SHA-256 checksums in `manifests/DATA_MANIFEST.json`.
4. Commit source and manifest changes.
5. Review and merge the stage branch.
6. Tag the accepted merge commit.
7. Update the external active-release pointer.
8. Update `CURRENT_STATE.json` last.

If any blocking check fails, do not activate or tag the candidate.

