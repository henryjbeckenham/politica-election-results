# Release notes 0.9.0

Release 0.9.0 delivers Stage 9: a read-only election-data explorer and provenance-bearing CSV export surface.

The local application now exposes seven curated datasets covering vote results, declared outcomes, participation, Senate count rounds, Senate candidate count totals, formal-ballot dataset inventories, and contests. Operators can filter by election, chamber, state, contest and applicable result dimensions; search supported descriptive fields; review paginated canonical rows; and download the complete applied result set as UTF-8 CSV.

The explorer resolves the checksum-verified active immutable release, opens it through DuckDB read-only connections, and uses fixed parameterised queries. It cannot accept arbitrary SQL. Current-record predicates and active source-revision joins prevent superseded facts from appearing as duplicates. CSVs retain stable entity IDs, reporting grain and source-revision provenance, and a default one-million-row ceiling prevents an accidental unbounded download.

Application version 0.9.0 is shown separately from database schema version 0.2.0. Stage 9 introduces no database migration and does not modify `data/app`, the active release, release history, raw sources, Parquet artifacts, `.env`, Google credentials or Grand Database content.

Verification covers each curated query, filter binding, pagination, revision visibility, export provenance, export-size enforcement, interface routing and checksum immutability. A disposable source-only compatibility build, intentionally separated from the user's private Google Sheets reference snapshot, opened all seven views across 158 contests, 212,908 directly mapped vote facts, 72,687 Senate candidate count totals and 15,871,189 anonymous formal ballots; the primary results query returned in approximately 1.1 seconds in the build environment. The installed active release, including its 213,328 vote facts and synced references, remains independently validated during installation.
