# Release notes 0.8.0

Release 0.8.0 completes Stage 8: governed individual ingestion for the remaining AEC Senate group, count and formal-ballot grains.

It adds national and state group-by-vote-type facts, complete eight-state distribution-of-preferences rounds/totals/transfers, and direct high-volume transformation of state formal-preference ZIPs into anonymous partitioned Parquet. Source group codes are retained explicitly when a canonical party inference would be unsafe. National reporting units and both new codes are registered as governed controlled values.

The DOP route checks all eight states, exact event/state filenames, candidates, formal-paper invariants, count continuity, per-round coverage, governed state first-preference totals and the elected total. DOP actions and transfer values are derived from the correct count context; exhausted transfers become canonical transfer facts, and exhausted/gain-loss adjustments remain attached to their round. The two group files cross-reconcile national totals to the sum of state and territory totals. The ballot route resolves every official group/candidate preference column, applies the published BTL-first sequence rule, creates no elector identity, checkpoints external artifacts and pins every Parquet shard into the immutable release inventory.

Official full-scale verification covers both group files, the complete eight-state 2025 DOP package and the 293,474-ballot ACT formal-preference archive with 2,166,773 counted preference positions. Corrected DOP and formal ZIP simulations prove that prior revisions remain preserved while active totals are not duplicated. Stage 4–7 behaviour remains under regression test.

The update is non-destructive. It preserves `data/app`, active and historical releases, raw sources, existing Parquet artifacts, `.env` and Google credentials.
