# Release 0.1.0 — Stage 1 foundation

Released: 2026-07-16

## Outcome

The approved findings-report architecture has been translated into an executable, governed DuckDB foundation. The database contains current read-only Grand Database references and deliberately contains no election facts.

## Delivered

- 12 logical schemas and 56 physical tables.
- 572-field generated data dictionary.
- 115 controlled values.
- 113 declared logical cross-schema relationships.
- Immutable-source, staging, canonical, results, count, ballot, derived and publication layers.
- Deterministic identifiers and revision checksums.
- Read-only Grand Database sync: 171 people, 20 parties and 591 constituencies.
- Constituency official-code fields integrated in the canonical sync; 150 published codes and 441 explicitly unknown statuses.
- AEC 2025 adapter configuration and source-signature detection foundation.
- Validation report with PASS status and eight passing automated tests.

## Deliberately absent

- Election, contest, candidacy, result, count and ballot facts.
- Official AEC 2025 source files.
- Application/user interface code.

## Next milestone

Stage 2 will register immutable AEC 2025 source revisions, implement the House and Senate adapters, quarantine unresolved labels, load canonical facts, and reconcile all published totals before release.
