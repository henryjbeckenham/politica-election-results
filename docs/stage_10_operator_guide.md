# Stage 10 operator guide

## What Stage 10 does

Stage 10 lets a visualisation tool read a stable result feed without receiving the DuckDB file or SQL access. It is read-only and does not publish an election release, ingest a file or write to Google Sheets.

## Open the feed workspace

1. Start Politica with `./start_politica.command`.
2. Open `http://127.0.0.1:8765` if the browser does not open automatically.
3. Select **Visualisation feeds** in the left navigation.
4. Confirm that the release ID, schema and database SHA-256 are displayed.
5. Choose the election and, if required, one state or territory.

## Observable

Select **Copy JSON URL** for the required feed. A JSON response has this shape:

```json
{"manifest": {"feed_id": "house_seat_results"}, "data": []}
```

Load the URL with `fetch`, then use the `data` array. The local Politica application must remain running while Observable requests a `127.0.0.1` URL.

## Flourish

Select **Download CSV** and upload the downloaded file to Flourish. The first five columns identify the feed, version, publication, release and database checksum. Do not remove those columns from the retained source copy, even if a particular chart does not display them.

## Preserve evidence

Open **Manifest** for the same feed and filters. Save it beside any externally published chart or data extract. A later refresh can be distinguished by its publication ID, database checksum and data checksum.

## What a feed does not mean

A generated feed is not a new election-data release. It is a deterministic representation of the currently active immutable release. Changing the active release changes the feed publication identity; changing only chart styling does not.

## Troubleshooting

- **No rows:** check the selected election/state and whether the required source family has been ingested.
- **413 response:** narrow the state or contest filter below the 250,000-row safety ceiling.
- **422 response:** the feed name, election, state or contest is not registered/active.
- **Observable cannot connect:** keep Politica running and ensure the notebook is permitted to read a local `http://127.0.0.1:8765` resource.
- **A different ETag appears:** compare manifests; the active release, filters or feed version changed.
