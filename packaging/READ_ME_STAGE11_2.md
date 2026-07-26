# Stage 11.2 v1.1.2 corrective update

This release corrects the Senate group publication compatibility gap confirmed by the header-only operational CSV export.

The active 2025 release retains canonical `party_total` Senate aggregates. Later Stage 8 ingestions can carry richer `group_total` rows. The fixed feed now prefers `group_total` per state and otherwise publishes the existing `party_total` values through the same stable output contract.

The update changes application code, tests, documentation and prebuilt public assets only. It does not edit the database, active pointer, release history, raw sources, Google Sheets configuration, credentials or Grand Database.
