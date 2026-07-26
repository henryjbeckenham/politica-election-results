# Stage 9.1 corrective update

## Scope

This update addresses one field-semantics defect discovered through the Farrer operational export. Farrer TCP values of 56.19 and 43.81 are current vote shares, not swings. The official 2025 file contains 17 such non-comparable contests (34 candidate observations). The other 133 contests contain genuine signed swing pairs (266 observations).

## Guarded classification

Politica classifies the two candidates together:

- a pair summing to zero is stored as `swing`;
- a pair summing to 100 is stored as `vote_share` only if both values reconcile to the candidates' total TCP votes;
- every other pair is rejected.

No row is interpreted in isolation and no contest is silently guessed.

## Existing-release correction

The updater resolves and verifies the active pointer, pins its database and external-artifact checksums, and works only on a new copy. The 34 former active facts become `superseded`; 34 deterministic `vote_share` facts are inserted with the same official source revision, locator and source-row hash. A correction import run, transform run, validation run and database-release record are written to the copy.

The copy must retain the active fact count, add exactly 34 historical facts, contain complete new lineage, pass the TCP semantic check and pass the entire database validator. It is then bound to an immutable file manifest and activated with a compare-and-swap pointer update. If any step fails, the old pointer remains active.

## Operator result

After installation, `politica-erd-validate` must report `PASS`, `vote_result_count` 213,328 and `superseded_vote_result_count` 34 more than before this correction. A Farrer results export will contain 56.19 and 43.81 under `vote_share`, with `swing` blank for those two TCP total rows.
