# Release notes 1.3.6

Release 1.3.6 is Stage 13.5: governed Senate visualisations.

The public Senate page now provides selectable state delegations, official quota context, a complete distribution-of-preferences count player, candidate progressive totals, election and exclusion milestones, signed reported count movements, exhausted-vote movements and final declared election order.

Publication feed contract 1.3.0 adds `senate_count_movements`. It exposes the AEC-reported gain or loss attached to each candidate and round, plus exhausted movements. The source does not identify a unique origin candidate for every destination, so the interface deliberately does not manufacture candidate-to-candidate paths.

- Application: 1.3.6
- Database schema: 0.2.0
- Publication feed contract: 1.3.0
- Visualisation contract: 1.5.0
- Static-site format: 1.0.0

The immutable election database, active release pointer, historical releases, raw AEC files, Google Sheets configuration, credentials and Grand Database are not changed.
