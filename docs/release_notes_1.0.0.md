# Release notes 1.0.0

Release 1.0.0 is Stage 10: versioned visualisation feeds and publication APIs.

It adds seven fixed read-only feed contracts with JSON, CSV and manifest representations. Every representation is tied to the exact active immutable release and includes deterministic publication identity, stable entity identifiers, source-revision provenance and cryptographic checksums.

The release adds no schema migration and makes no change to the user's active database. Application 1.0.0 continues to read schema 0.2.0 and retains all Stage 3–9.1 ingestion, synchronisation, correction, exploration and export behaviour.

The official-scale verification generated all seven contracts over the 2025 fact inventory, including 1,726 House candidate result rows, 150 House seat rows, 28 House party summary rows, 1,896 participation rows, 190 declared-member rows and 64,965 Senate count-progression rows. The disposable reference-less build correctly omitted the 420 Senate group facts that require the user's synced Parties references; the installed release retains those references.
