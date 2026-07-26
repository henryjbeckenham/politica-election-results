# Stage 6 operator guide

## What Stage 6 does

Stage 6 adds the empty governed structure for a new AEC federal election. It starts from the authority's candidate register because later result files identify contests and candidates by those official IDs.

Nothing is pasted into DuckDB or Google Sheets. The application performs a preview, creates an isolated database copy, validates it and publishes a new immutable release only after operator approval.

## Files to obtain

Download at least one of these official files for the same AEC event:

- `HouseCandidatesDownload-<event-number>.csv`
- `SenateCandidatesDownload-<event-number>.csv`

Use both for a normal federal election covering both chambers. Do not rename the files or edit their first AEC metadata line.

## Register the election

1. Start Politica and open **Ingest data**.
2. Open **Register an entirely new AEC election**.
3. Select the House and/or Senate candidate CSV files.
4. Enter the election name, numeric AEC event number and polling day.
5. Select the election type, source phase and current contest status.
6. If a Senate file is included, verify the vacancies per state and territory. Mark a whole-Senate election only when that is legally correct.
7. Select **Preview registration**.

The preview is read-only. Check the event ID, date, chambers, contest count and candidate count. The matched/unmatched totals describe exact matches against the active Grand Database snapshot. Unmatched references do not block election registration because the official candidate name and party label remain preserved and the canonical IDs remain null.

8. Tick the review confirmation and select **Start registration**.
9. Wait for the job to reach **Validated**.
10. Open **Validate & publish**, inspect the Stage 6 checks and create a publication snapshot.

Until step 10, the active database used by Politica is unchanged. Publication uses compare-and-swap activation; if the active release changed after the preview job began, publication stops and asks for a new job.

## After publication

The new election appears in the Election selector. You can then ingest supported House result files for that event. The event number in every result filename must match the newly governed election.

An unsupported result format remains staging-only. Politica will preserve it but will not publish it into canonical result tables until a tested transformer exists.

## What to do about unmatched references

Review the official labels against the authoritative Grand Database separately. Add or audit missing People, Parties or Constituencies there, then use **Google Sheets sync** to preview and activate the updated reference snapshot. Stage 6 itself never writes back to that Sheet.

## Failure messages

- **Already registered** — use the existing election; do not create a duplicate.
- **Event mismatch** — obtain the correct official files or correct the entered event number. Do not rename a mismatched file.
- **Duplicate CandidateID** — the candidate file is invalid or incomplete; obtain a fresh official copy.
- **Invalid division/state** — verify the file is the unedited AEC candidate download.
- **Source already registered** — those exact bytes already exist in the governed source history.

A rejected preview changes no database. A failed execution affects only its isolated working copy.
