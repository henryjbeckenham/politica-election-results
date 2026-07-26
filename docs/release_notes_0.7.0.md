# Release notes 0.7.0

Release 0.7.0 completes Stage 7: governed individual ingestion for ten complete AEC Senate summary formats.

It adds state and division first-preference facts, ballot-group structure, candidate ballot positions, Senate division reporting units, state enrolment, state/division participation summaries, declared Senator outcomes and current elected-member records. Every route verifies the filename event against the selected election, requires complete governed coverage, reconciles applicable arithmetic, records row-level lineage and supports source-revision supersession.

Official 2025 full-scale verification covers 11,824 staged rows, 67,812 vote facts, 1,746 participation facts, 40 elected outcomes, 125 ballot groups, 330 group memberships and 150 division reporting units. Stage 4–6 behaviour remains under regression test.

The update is non-destructive. It preserves `data/app`, active and historical releases, raw sources, Parquet artifacts, `.env` and Google credentials.
