# Release notes 0.9.1

Release 0.9.1 is the Stage 9.1 corrective update. It corrects the interpretation of the `Swing` column in the AEC House two-candidate-preferred by-vote-type file.

For comparable contests, the two reported values are signed swings and sum to zero. For 17 non-comparable 2025 contests, the same source column instead contains the two candidates' current TCP vote shares, which sum to 100 and reconcile to their TCP votes. Version 0.9.0 stored all 300 values as swings. Version 0.9.1 stores 266 as swings and 34 as vote shares.

The corrective command copies the active checksum-pinned release, supersedes the 34 mislabelled facts, inserts 34 corrected source-lineaged facts, validates the complete copy, freezes a new artifact manifest and atomically activates it. It never edits or deletes the prior immutable release. The active vote-result count therefore remains 213,328, while the historical superseded count rises by 34.

The same pair-level classification and reconciliation rule now runs in both the complete 2025 reproduction transformer and every future individual House TCP ingestion. Ambiguous pairs are rejected rather than guessed. The main database validator also blocks any future release whose active TCP measure types disagree with the pair semantics.

There is no schema migration: the database schema remains 0.2.0. Application version 0.9.1 retains every Stage 9 explorer, export and Stage 3–8 ingestion capability.
