# Stage 11.2 operator guide

Application 1.1.3 completes the official 2025 Senate ballot-group totals and retains the Stage 11.2 compatibility fallback. All eight state and territory tabs resolve both full governed contest names and AEC abbreviations. The completed active release now supplies the richer `group_total` representation, which takes precedence without publishing duplicate totals.

## Open the site

Start Politica normally:

```bash
cd "$HOME/Downloads/Politica_Election_Results_Database"
./start_politica.command
```

Then select **Public results** in the left navigation or open:

`http://127.0.0.1:8765/results/`

The operator Terminal must remain open while either interface is in use.

## What the public interface can do

- show the verified active election and release identity;
- summarise House declared seats and national party performance;
- filter House seats by state and search electorates, members or parties;
- show candidate totals for a selected electorate;
- compare Senate groups and declared Senators by state or territory;
- compare turnout and informality percentages;
- download each fixed publication feed as CSV; and
- expose the Senate count-progression JSON and release evidence.

## What it cannot do

The public interface cannot ingest a file, publish a job, edit DuckDB, write to Google Sheets, modify the Grand Database or execute arbitrary SQL. It does not publish an electorate map because boundary geometry is not yet governed by a fixed versioned feed.

## Troubleshooting

If the page reports that feeds could not be loaded, confirm that the Terminal still shows `Uvicorn running on http://127.0.0.1:8765`, then refresh the browser once. If the operator application reports that another instance is running, use the already-open instance instead of deleting the application lock.
