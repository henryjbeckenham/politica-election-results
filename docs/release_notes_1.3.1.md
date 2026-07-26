# Release notes 1.3.1

Release 1.3.1 is Stage 13.1: composition diagrams.

## Delivered

- a governed, source-cited snapshot of all 76 senators in the 48th Parliament as at 14 May 2026;
- a fixed `senate_composition` feed in JSON, CSV and manifest formats;
- an interactive 150-seat House semicircle built from individual declared electorate outcomes;
- an interactive 76-seat Senate semicircle built from the full parliamentary snapshot;
- selectable House members and senators with non-colour party labels, keyboard operation, tooltips and detail panels;
- government, opposition and crossbench grouping without claiming unverified physical desk positions;
- state, party, search and member selection preserved in the page URL;
- composition checksums embedded in feed and static-site publication evidence; and
- static and live-site compatibility with the existing verified publication workflow.

## Installation correction

The final Stage 13.1 package explicitly binds a non-editable Python installation
back to the operator project directory. Both the application and the historical
workflow modules use that same governed root. This prevents configuration,
schema, adapter and raw-source files from being sought under
`.venv/lib/python3.12` when the installer or launcher runs. The behaviour is
covered by a dedicated non-editable-install subprocess regression test.

## Contract versions

- Application: 1.3.1
- Database schema: 0.2.0 (unchanged)
- Fixed feed contract: 1.1.0
- Visualisation contract: 1.1.0
- Design system: 1.1.0
- Static-site format: 1.0.0 (unchanged)

## Boundaries

The diagrams show political composition, not the physical desks occupied in either chamber. The Senate is a dated membership snapshot and must be refreshed through a new governed snapshot when membership changes. Election facts, raw AEC sources, historical releases, Google Sheets credentials and the Grand Database are not edited by this release.
