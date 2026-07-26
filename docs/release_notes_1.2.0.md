# Release notes 1.2.0

Release 1.2.0 is Stage 12.0: governed static website publication.

The local operator can now build a complete, host-ready copy of the public
election-results website from the checksum-verified active database release.
The build contains the compiled Observable Framework interface, all seven
fixed publication feeds in JSON and CSV, individual feed manifests, a static
catalogue and a complete file-checksum manifest.

The public package never contains DuckDB, raw election sources, Google Sheets
credentials, the local operator interface or ingestion controls. It does not
upload or deploy anything. Deployment and domain connection remain a separate
Stage 12.1 operation.

Every package has a deterministic site release ID and ZIP checksum. Rebuilding
the same application and database release produces the same immutable website
release. If the active database changes, the operator marks the existing site
package as requiring an update.

Application version is 1.2.0. Database schema remains 0.2.0. Publication feed
contract remains 1.0.0. Static site format is 1.0.0.
