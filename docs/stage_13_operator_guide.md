# Stage 13.0 operator guide

Stage 13.0 changes the public results interface, not the election database.

## After installation

1. Start Politica with `./start_politica.command`.
2. Open `http://127.0.0.1:8765/results/`.
3. Use **Overview**, **House**, **Senate** and **Sources** in the top navigation.
4. Use the State, Party and Electorate/Search controls. The selections appear in the page address, so a filtered view can be bookmarked or shared.
5. Select a House electorate to open its candidate result.
6. Select a Senate state or territory to compare group results and declared outcomes.
7. Use **Sources** to inspect the exact release checksums and download the fixed CSV or JSON feeds.
8. In the operator, open **Website publication** and build a new package. Stage 13 creates a new static-site release because the public interface and visualisation contract changed; it does not rebuild or change the election database.

## What Stage 13.0 is for

This is the common foundation for later visual work. It makes routing, filters, colours, formatting, tooltips, legends, metric wording, downloads, accessibility and release evidence reusable so each later chart does not invent its own rules.

## What it does not yet claim

- Senate totals on the page are people declared elected at the selected election, not all 76 continuing and newly elected senators.
- The House composition is a clear party grouping, not physical chamber seating.
- Historical swings are unavailable until an earlier election is registered under a comparison policy.
- Maps are unavailable until governed electorate geometry is registered.
- Full Senate transfer-flow diagrams are unavailable until origin-and-destination transfers are published as a governed contract.

These limits are recorded in the visualisation contract and cannot be silently presented as completed data.
