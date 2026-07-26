# Stage 2 summary

Stage 2 is complete. The database is no longer an empty foundation: it contains the final 2025 federal House and Senate result system, including candidates, reporting geography, vote facts, declared outcomes, count rounds, anonymous formal ballot paths, provenance and audit evidence.

The release passed all 27 blocking checks. These cover source completeness, contest and candidate counts, elected-member counts, House and Senate formal-vote reconciliation, participation arithmetic, source lineage, duplicate grains, constituency matches, reporting-unit matches, all TPP source grains, all TPP cross-source totals and all Senate formal-preference datasets.

The 52 remaining warnings are not failed election totals. They are official party/group labels that do not yet have a canonical Grand Database party ID. The source labels are retained, affected staging rows are marked for review and no replacement party is invented.

The database contains both relational tables and Parquet. Normal result facts are stored in DuckDB and exported to 30 governed Parquet partitions. The 15.9 million formal Senate ballots and 105 million preference positions remain in 43 Parquet files and are exposed through DuckDB views. This avoids duplicating a very large dataset while keeping it queryable as one logical database.

The next build is the ingestion application. It will not redesign this schema; it will call the implemented registry, staging, transformation, validation, checkpoint and publication components through a guided interface.

