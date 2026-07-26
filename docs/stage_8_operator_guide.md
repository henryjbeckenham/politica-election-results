# Stage 8 operator guide

## Purpose

Stage 8 completes the remaining AEC Senate ingestion grains for future governed elections. It adds aggregate group summaries, distribution-of-preferences counts and anonymous formal-preference ballot paths. It does not write to Google Sheets or create People, Parties or Constituencies.

## Supported canonical files

- `SenateFirstPrefsByGroupByVoteTypeDownload-<event>.csv`
- `SenateFirstPrefsByStateByGroupByVoteTypeDownload-<event>.csv`
- `SenateDopDownload-<event>.zip`, containing all eight official `SenateStateDOPDownload-<event>-<state>.csv` members
- `aec-senate-formalpreferences-<event>-<state>.zip`, one archive for each available state or territory

Do not extract and upload one DOP member by itself. The DOP publication check requires the official all-state archive. Formal-preference archives are intentionally state-by-state and may be published separately.

## Prerequisites for a future election

1. Register the election and its official Senate candidate file through **Register new AEC election** and publish that registration.
2. Ingest the complete Stage 7 state first-preference file. This establishes governed ballot groups, group memberships and ballot positions used to resolve a formal-preference archive.
3. Keep every downloaded authority file unchanged. A corrected authority publication must be uploaded as a new file revision, never pasted over an earlier source.

## Recommended ingestion order

1. Upload the national and state group aggregate CSV files, select the registered election and inspect the detected routes.
2. Upload the complete Senate DOP ZIP. Wait for all eight member datasets to appear in the preview.
3. Execute the isolated job and publish only after the Stage 8 validation report passes.
4. Upload formal-preference ZIP files one state at a time. These are large and can take materially longer than summary files. Before NSW or Victoria, keep at least 20 GB of free disk space as a working-space buffer.
5. Keep the Politica Terminal window open while execution is running. If the local application stops, restart it and resume the durable job; committed checkpoints and completed external artifacts are reused.

## What the application validates

- The event number in every outer and inner filename matches the selected governed election.
- Group vote-type components equal their published total and percentages reconcile within their scope.
- When both group files are supplied, every national source-group vote total equals the sum of the eight state and territory totals.
- A DOP package contains ACT, NSW, NT, QLD, SA, TAS, VIC and WA exactly once.
- Every DOP candidate resolves to a governed candidacy; rounds are contiguous and each round has the exact candidate-plus-adjustment coverage expected by the authority format.
- Published vacancies, formal papers, the governed state first-preference total and the final number elected reconcile.
- Every formal-ballot header resolves to a governed ballot group or candidate in the stated contest.
- A ballot uses its valid below-the-line sequence when preferences 1–6 exist; otherwise it uses the valid above-the-line sequence beginning at 1.
- Canonical output is non-empty, rejected rows are zero, the pinned base release has not changed, and all new source and Parquet artifacts are checksum-inventoried before publication.

## Privacy and revision behaviour

Formal-ballot storage is anonymous. Politica retains a deterministic source-row key, state/division collection context and counted preference path; it does not create an elector identity. Ballots and preferences are written to state/source-revision Parquet partitions rather than millions of JSON staging rows.

A byte-identical reupload is blocked. A genuinely corrected DOP or ballot archive becomes a new immutable source revision. Current views use only the active revision, while the prior source, count rows or ballot partition remain available as superseded history. Publishing never modifies the Grand Database.
