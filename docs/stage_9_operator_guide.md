# Stage 9 operator guide

## Purpose

Stage 9 lets a non-SQL operator inspect and export current election records from Politica's active immutable release. It does not ingest files, edit the Grand Database, change a publication, or expose anonymous individual ballot paths.

The sidebar shows two different versions:

- **Application 0.9.1** identifies the installed operator code after the Stage 9.1 corrective update.
- **Schema 0.2.0** identifies the governed database structure.

An application update does not silently rename or migrate the database schema.

## Open the explorer

1. Start Politica normally with `./start_politica.command`.
2. Open `http://127.0.0.1:8765` if Safari does not open automatically.
3. Select **Explore & export** in the left navigation.
4. Wait for the active-release badge and record totals to appear.

If the screen reports that the governed database is unavailable, stop and run `uv run politica-erd-validate`. Do not select an old `.duckdb` file manually.

## Available datasets

- **Election results** — current vote facts pivoted into votes, vote share and swing at their official reporting grain.
- **Elected candidates** — current declared contest outcomes.
- **Participation and turnout** — enrolment, formal, informal, turnout and related participation measures.
- **Senate count rounds** — round metadata plus candidate-total, transfer and exhausted-row counts.
- **Senate candidate count totals** — candidate positions at each count round.
- **Formal-ballot datasets** — one inventory row per active anonymous ballot partition; individual ballot paths are intentionally not exported.
- **Contests and candidacies** — governed contest status and candidacy counts.

Only current facts are shown. Earlier corrected observations and sources remain in the database as superseded audit history but do not appear in ordinary explorer results.

## Filter and inspect

1. Choose a dataset and election.
2. Narrow by chamber, state or territory, and contest as needed.
3. For election results, optionally choose result type, vote type and reporting level.
4. Use search for a candidate, party, contest, reporting unit or measure supported by that dataset.
5. Select **Apply filters**.
6. Review the row count, grain and source-revision column before using the result.

The table is paginated. Changing the page size changes only the screen; it does not change a CSV export.

## Export a CSV

1. Apply and review the filters first.
2. Select **Export filtered CSV**.
3. Safari downloads a UTF-8 CSV containing the full filtered result set, not merely the visible page.
4. Retain the canonical IDs and `source_revision_id` column when passing the file downstream.

Exports are deterministically ordered and contain stable field names. Requests above 1,000,000 rows are blocked by default; narrow the filters and try again. The downloaded CSV is a derivative analyst file. Record its filename, creation date and active release ID when it becomes an input to another system.

## Safety model

- DuckDB is opened with `read_only=True`.
- The active release pointer and artifact checksums are verified before use.
- The server accepts only registered datasets and fixed filters, never arbitrary SQL.
- Filter values are bound parameters rather than SQL text.
- Export uses the same fixed query as the on-screen review.
- Querying or exporting cannot write to Google Sheets, create an ingestion job, activate a release or alter a database checksum.
