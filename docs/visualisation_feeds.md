# Visualisation feeds

Stage 10 exposes fixed publication contracts from the checksum-verified active immutable release. It never reads staging databases or accepts SQL.

## API catalogue

`GET /api/public/v1/feeds`

The catalogue lists every feed, its version, field contract, supported formats and current release identity. If `election_id` is omitted from a feed request, the most recent active election is selected deterministically.

## Feed endpoints

For each registered `{feed_id}`:

- `GET /api/public/v1/feeds/{feed_id}.json`
- `GET /api/public/v1/feeds/{feed_id}.csv`
- `GET /api/public/v1/feeds/{feed_id}/manifest.json`

Optional query parameters are `election_id`, `state` and `contest_id`. Values are bound query parameters. Unsupported feed names and inactive election IDs are rejected.

| Feed | Grain | Primary use |
|---|---|---|
| `house_candidate_results` | contest/result/candidate | result charts and candidate tables |
| `house_seat_results` | declared House seat | maps, winners and margins |
| `house_party_summary` | party/election | party votes, share and seats |
| `senate_group_results` | state/reporting unit/group | Senate group comparisons |
| `turnout_informality` | reporting unit/vote type/measure | turnout and informality charts |
| `declared_members` | elected candidacy | winner and member lists |
| `senate_count_progress` | count round/candidate | DOP progression and animation |
| `senate_count_movements` | count round/reported movement | candidate gains, losses and exhaustion |
| `senate_composition` | governed Senate membership | chamber and state delegation composition |

## Identity and verification

Every JSON response contains `manifest` and `data`. Every CSV row begins with `_feed_id`, `_feed_version`, `_publication_id`, `_release_id` and `_database_sha256`. The separate manifest records:

- API, feed and calculation versions;
- deterministic publication ID;
- exact filters;
- immutable release and schema identities;
- database, data, manifest and source-revision-set hashes;
- field names and types;
- row count; and
- contributing source revisions.

The HTTP response also supplies `ETag`, `X-Politica-Payload-SHA256`, `X-Politica-Release-ID`, `X-Politica-Publication-ID` and `X-Politica-Row-Count`. Repeating the same feed, filters and release produces the same bytes and ETag.

## TCP calculation

The AEC House TCP source reports signed swing for comparable contests but current vote share in the same source column for non-comparable contests. The Stage 10 seat feed avoids that mixed source meaning when calculating the winning margin: it derives both candidates' TCP shares and the percentage-point margin directly from their official TCP vote totals. The source-reported `tcp_swing` remains separately available where it genuinely exists.

## Security boundary

Only `/api/public/v1/...` GET responses carry permissive cross-origin headers. The ingestion, publication, Google Sheets and operator endpoints do not. Public feeds use read-only DuckDB connections, current facts/current source revisions, deterministic ordering and a default 500,000-row ceiling. This accommodates the complete 452,193-row 2016 double-dissolution Senate count feed without weakening election, state or contest filtering.

The application is still bound to `127.0.0.1` by default. That is suitable for local Observable development and CSV transfer. Public internet hosting is a separate deployment decision and should place only these read-only routes behind a governed proxy or static publication step.
