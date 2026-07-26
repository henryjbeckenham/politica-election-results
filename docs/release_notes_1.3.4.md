# Release notes 1.3.4

Release 1.3.4 is Stage 13.4: House analysis and improved electorate-map navigation.

## Added

- A dedicated **Analysis** page with an electoral pendulum, closest-contest ranking and AEC-reported winner TCP swing ranking.
- Incumbent-based party gains and losses, with open seats still identified separately as new members.
- First-preference vote share versus declared seat share.
- State and territory comparison cards for seats, average winning margin, average turnout and gains.
- First-preference, TCP and available TPP comparison for the selected electorate.
- Capital-city map close-ups for Sydney, Melbourne, Brisbane, Adelaide, Perth, Hobart, Canberra and Darwin; each close-up renders only boundaries that intersect its city window.
- General map zoom, drag-to-pan and reset controls, including keyboard +, − and 0 shortcuts.
- A bookmarkable `map_view` parameter that does not alter the independent election-result State filter.

## Interpretation boundary

The swing ranking uses `tcp_swing` exactly as supplied by the AEC source and attached to the declared winner. Politica does not present it as an independently recalculated cross-election swing. Such a calculation remains blocked until a compatible earlier election and redistribution policy are governed.

## Contracts

- Application: 1.3.4
- Database schema: 0.2.0
- Publication feed contract: 1.2.0
- Visualisation contract: 1.4.0
- Boundary contract: 1.0.0
- Static-site package format: 1.0.0

The installation does not edit the active election release, historical releases, raw election sources, Grand Database, Google Sheets configuration or credentials. It does not upload or externally deploy the website.
