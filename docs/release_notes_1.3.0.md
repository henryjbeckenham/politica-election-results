# Release notes 1.3.0

Release 1.3.0 is Stage 13.0: governed visualisation foundation.

## Delivered

- a versioned, read-only visualisation contract covering routes, filters, metrics, visual components, feed dependencies and capability boundaries;
- a public `GET /api/public/v1/visualisations` catalogue with CORS, ETag and release-checksum evidence;
- the same contract embedded and verified in every downloadable static website package;
- a modular browser foundation for formatting, canonical party colours, DOM helpers, visual registration, URL state, legends and tooltips;
- shared design tokens for colour, typography, spacing, focus, elevation and responsive behaviour;
- four usable result routes: Overview, House, Senate and Sources;
- State, Party and Electorate/Search filters preserved in the page URL and restored by browser history;
- keyboard-operable party legends, seat markers and Senate selectors, non-colour labels, standard tooltips and reduced-motion support;
- explicit metric definitions for seats, party leadership, Senate outcomes, vote share, margin, turnout and informality;
- explicit blocks against presenting a full Senate composition, historical swing, physical seating, electorate maps or complete transfer flows before their required source contracts exist; and
- regression coverage for live and static operation, immutable release binding and all preceding publication capabilities.

## Unchanged

- Database schema remains 0.2.0.
- Feed contract remains 1.0.0.
- Static website format remains 1.0.0.
- The active election database, 2025 facts, source revisions, release history, Google Sheets configuration and Grand Database are not edited.
- No website is uploaded or externally deployed.
