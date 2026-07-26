# Ingestion process

Election files vary between authorities, elections and releases. They are therefore never inserted by assuming their columns already match the database.

## Implemented canonical pipeline

The following pipeline is implemented end to end for the governed AEC 2025 source set. The application runs it through the explicit **Reproduce 2025 release** action on an isolated job workspace. The current release restores the active Google Sheets reference snapshot after that rebuild, before validation, so a reproduction cannot silently revert People, Parties or Constituencies.

1. **Register the source.** The file, URL, election, authority, size, schema signature and SHA-256 checksum enter the source manifest.
2. **Detect the format.** The adapter registry compares the filename and required headers with an explicit versioned dataset definition.
3. **Preserve the original.** Non-ballot source rows enter `staging.source_record` as source-native JSON with row hashes and locators. Large formal-ballot archives remain immutable raw ZIPs and transform directly to governed Parquet.
4. **Resolve reference entities.** Constituency codes, party labels and candidate identities are matched against the read-only Grand Database snapshot. Unknown labels are not guessed.
5. **Transform to common grains.** Source columns are unpivoted and mapped into contests, candidacies, reporting units, vote facts, participation facts, outcomes, count rounds or ballots.
6. **Checkpoint.** Each large vote source records a completed transform and row count. A resumed run reuses any source whose checkpoint and output still agree.
7. **Quarantine unresolved mappings.** Source-native rows remain available, while unknown canonical labels become visible audit warnings and mapping-review entries.
8. **Reconcile.** Blocking rules compare candidate totals with formal votes, formal plus informal with turnout, TPP sources across grains, elected counts with vacancies, ballot archives with published totals, and facts with lineage.
9. **Publish.** Only a run with zero blocking failures receives an approved publication snapshot and governed Parquet exports.

## What happens with an uploaded CSV, XLSX or ZIP?

- The application registers the original file and checksum, inspects supported datasets, shows a preview and detects registered adapters.
- A matching adapter describes the source format; canonical insertion additionally requires a registered transformer for that exact adapter and dataset.
- If no canonical transformer is registered, the rows remain source-native and the publication gate fails visibly. A matching filename or header set alone never authorises insertion.
- If the headers do not match, ingestion stops in quarantine. A developer defines and tests a new adapter version and transformer; the system does not guess what a column means.
- The original file and its checksum remain unchanged throughout.
- A corrected authority file becomes a new source revision and a new publication snapshot. Prior facts remain as `superseded` history while only the replacement facts remain active.
- Byte-for-byte duplicate source files are rejected before execution; repeating the same bytes cannot manufacture another revision.
- Published individual-file releases include the immutable uploaded bytes, their checksum, their database provenance record and their precise row lineage in one release inventory.

Release 0.9.1 retains the Stage 8 CSV, XLSX and ZIP ingestion routes and the Stage 9 explorer. Each selected XLSX worksheet uses the same source-row interface as CSV data. The complete AEC 2025 batch transformer remains supplied. A dedicated grouped route registers a new federal AEC election from `HouseCandidatesDownload-<event-id>.csv`, `SenateCandidatesDownload-<event-id>.csv`, or both. Stages 7 and 8 then accept the complete supported Senate summaries, aggregates, DOP package and formal-ballot archives. None of these routes writes Grand Database references. The explorer is deliberately outside this write pipeline and cannot stage or publish data. Stage 9.1 adds pair-level classification and reconciliation for the mixed-semantics House TCP `Swing` column.

Individual canonical transformers accept complete revisions of:

- `HouseFirstPrefsByCandidateByVoteTypeDownload-<event-id>.csv`;
- `HouseTcpByCandidateByVoteTypeDownload-<event-id>.csv`;
- `HouseTppByDivisionDownload-<event-id>.csv`;
- `HouseMembersElectedDownload-<event-id>.csv`;
- `GeneralEnrolmentByDivisionDownload-<event-id>.csv`;
- `HouseInformalByDivisionDownload-<event-id>.csv`;
- `HouseTurnoutByDivisionDownload-<event-id>.csv`; and
- `HouseVotesCountedByDivisionDownload-<event-id>.csv`.

Each route targets an election whose House contests and candidacies are already governed. It checks the official filename event number, resolves official IDs, requires complete constituency coverage, reconciles applicable arithmetic, records precise lineage and automatically runs the publication-gate validation.

The Stage 6 candidate route creates the missing election, selected chambers, contests, snapshots and candidacies. It deliberately cannot create People, Parties or Constituencies: unknown references remain explicit and can later be resolved through a reviewed Grand Database sync.

Stage 7 additionally accepts complete revisions of `SenateFirstPrefsByStateByVoteTypeDownload`, `SenateFirstPrefsByDivisionByVoteTypeDownload`, `SenateSenatorsElectedDownload`, `GeneralEnrolmentByStateDownload`, and the Senate state/division informal, turnout and votes-counted files.

Stage 8 accepts complete revisions of `SenateFirstPrefsByGroupByVoteTypeDownload` and `SenateFirstPrefsByStateByGroupByVoteTypeDownload`. The eight `SenateStateDOPDownload` members must be supplied together inside `SenateDopDownload-<event>.zip`; an extracted single-state member cannot pass the complete-archive publication rule. Each `aec-senate-formalpreferences-<event>-<state>.zip` is a separate state archive. Those high-volume rows transform directly from the immutable ZIP to isolated partitioned Parquet, with only an anonymous ballot source key and the counted preference path retained.

## Operator application screens

The application exposes the governed workflow as:

1. election and authority selection;
2. file upload, or the controlled complete AEC 2025 reproduction action;
3. detected format and header preview;
4. canonical entity mapping review;
5. validation preview;
6. resumable import progress;
7. exception resolution; and
8. publication approval for a canonical-capable job.

The **Explore & export** screen is a ninth, separate read-only surface over the active release. It does not add a new insertion step. This application is an operator around the database engine, not a replacement for the database design.
