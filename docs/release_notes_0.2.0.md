# Release 0.2.0 — Stage 2 database

Released: 2026-07-17

## Outcome

The 2025 Australian federal election is fully loaded into the governed Election Results Database. The release is validated, reproducible from its official sources and ready to serve as the data layer for the Stage 3 ingestion application.

## Release totals

| Item | Count |
|---|---:|
| Official source revisions | 45 |
| Contests | 158 |
| Candidacies | 1,456 |
| Reporting units | 9,098 |
| Vote-result facts | 213,328 |
| Participation facts | 1,896 |
| Count rounds | 2,235 |
| Count candidate totals | 72,687 |
| Preference transfers | 15,752 |
| Declared outcomes | 190 |
| Row-lineage records | 319,177 |
| Formal Senate ballots | 15,871,189 |
| Formal ballot preference positions | 104,961,382 |

## House TPP addition

The report-required two-party-preferred layer is included at all four published grains:

| Source grain | Normalized facts |
|---|---:|
| State/territory | 40 |
| Division | 750 |
| Division and vote type | 3,000 |
| Polling place | 44,700 |

TPP totals reconcile at division, vote-type, polling-place and state levels. State aggregates use `state_total` reporting units and nullable contest IDs because they describe a House-wide jurisdiction aggregate rather than one contest.

## Validation

- 27 blocking checks passed; 0 failed.
- All 45 raw source hashes passed.
- All 30 fact Parquet hashes and all 43 formal-preference Parquet hashes passed.
- Export row totals exactly match their database tables.
- All 150 House constituencies matched canonical Grand Database constituency IDs.
- 52 unresolved official party/group labels remain as visible warnings.
- 35,243 source-native rows carry a quarantined mapping status because they contain one of those labels. Candidate-level official facts are retained; no fallback canonical party was silently assigned.

## Operational improvements

- Source-level vote transformations checkpoint after completion.
- Interrupted imports can resume without repeating completed sources.
- Formal-preference checkpoints now verify expected counts and every file checksum before reuse.
- Unknown schemas are rejected instead of guessed.
- Unknown party labels remain source-native and enter a review queue.

## Not included

- A graphical ingestion application.
- Live Google Sheets synchronization.
- Automatic adapter generation for unseen future file layouts.

Those are Stage 3 application responsibilities; the database and 2025 ingestion engine they will call are now complete.

