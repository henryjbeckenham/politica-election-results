# Release notes 1.1.2

Release 1.1.2 is the Stage 11.2 Senate group publication correction.

## Corrected

- The active 2025 immutable release stores Senate party aggregates as `party_total`, while the later Stage 8 individual-ingestion route stores richer authority groups as `group_total`.
- The fixed `senate_group_results` publication feed now accepts both governed representations.
- A state with current `group_total` rows uses those rows exclusively; otherwise the feed publishes that state's existing `party_total` rows under the stable `group_total` output contract.
- National-only rows and rows without a canonical state are excluded from the state comparison feed.
- The database, active pointer, raw sources, Grand Database and Google Sheets remain read-only and unchanged.

## Verification

- a regression fixture reproduces the exact header-only CSV failure from an active release containing only `party_total` rows;
- a precedence test proves that later `group_total` rows suppress the fallback within the same state;
- the complete Stage 4–11.2 suite remains mandatory during installation;
- the installer runs a read-only verification against the actual active immutable release and requires non-empty positive-vote results for ACT, NSW, NT, QLD, SA, TAS, VIC and WA; and
- the installer verifies that `data/app/releases/active.json` is byte-for-byte unchanged.

Application version is 1.1.2. Database schema remains 0.2.0. Publication feed contract remains 1.0.0. No election release or Grand Database row is modified.
