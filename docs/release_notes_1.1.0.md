# Release notes 1.1.0

Release 1.1.0 is Stage 11, the first user-facing Politica Election Results site.

This release:

- serves a separate public results interface at `/results/` while retaining the operator at `/`;
- visualises the House composition, party totals, electorate winners and candidate results;
- provides Senate group and declared-member views by state and territory;
- compares turnout and informality across chambers and reporting units;
- exposes release-bound CSV, JSON and manifest downloads;
- displays the exact active release, database checksum, application version, schema version and feed contract;
- reads only the seven fixed Stage 10 public GET feeds;
- adds no write route, arbitrary SQL, database migration or new election fact;
- normalises historical lower-case state codes for filtering while preserving feed output values; and
- ships a prebuilt Observable Framework site so Node.js is not required after installation.

Application version is 1.1.0. Database schema remains 0.2.0. Publication feed contract remains 1.0.0. The active immutable election release and its checksum do not change during installation.
