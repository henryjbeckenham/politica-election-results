# Stage 13.1 operator guide

## Open the diagrams

1. Start Politica with `./start_politica.command`.
2. Open `http://127.0.0.1:8765/results/`.
3. The **Overview** route contains the House composition diagram.
4. The **Senate** route contains the full Senate composition diagram and the separate 2025 election-result panels.

## Use the diagrams

- Select any seat to open its member details.
- Use Tab and Enter to operate every seat without a mouse.
- Select a party in the legend to emphasise that party throughout the page.
- Use State and Search to emphasise matching members while preserving the full chamber shape.
- House details link to the selected electorate result.
- Senate details show state, party, grouping, term expiry and snapshot date.
- Copy or bookmark the page address to retain the current filters and selected member.

## Interpret them correctly

- House seats are individual declared 2025 electorate outcomes.
- Senate seats are the 76 memberships recorded by the Parliament of Australia snapshot dated 14 May 2026.
- The arcs group political affiliations; they do not reproduce physical desk positions.
- **Declared Senators** lower on the Senate route means people elected at the 2025 election, while **Senate composition** means the full chamber snapshot.

## Publish the updated static site

Open **Website publication** in the local operator and build a new package, or run `uv run python -m politica_erd.static_site`. The new site package contains all eight fixed feeds and both composition diagrams. Nothing is uploaded automatically.
