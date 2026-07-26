# Stage 4 operator guide

## What changed

Stage 4 retains every Stage 3 screen and adds the first genuinely publishable individual-file route. You may now ingest a complete AEC House first-preference-by-vote-type CSV for an election already present in the database.

The route recognises filenames shaped like:

`HouseFirstPrefsByCandidateByVoteTypeDownload-31496.csv`

The number may differ. Recognition still requires the expected official headers; a filename alone is never trusted.

## Start the application on the configured Mac

Run `configure_google_sheets.command` once. It checks the already established credential path:

`~/Desktop/Business/Politica/Politica Credentials/politica-grand-database-reader.json`

It writes only that path and the pinned workbook ID into a private `.env` in the project folder. It never copies or changes the JSON credential. Then launch `start_politica.command`, or use:

```bash
cd ~/Downloads/Politica_Election_Results_Database
./start_politica.command
```

Keep the Terminal window open while the local application is running. `http://127.0.0.1:8765` remains local to the Mac.

## Ingest the supported individual file

1. Choose **Ingest data**.
2. Upload the complete AEC House first-preference-by-candidate-by-vote-type `.csv`.
3. Confirm that the detected dataset is **House first preferences by vote type** and that the card identifies a canonical transformer, not “staging only”.
4. Choose the existing AEC election to which the source belongs. For the current database this is the 2025 Australian federal election. Politica cross-checks the event number in the filename against that election and stops on a mismatch.
5. Start the job once. Exact duplicate bytes are blocked deliberately.
6. Wait for Register, Stage, Transform and Validate to show complete. Validation now follows transformation automatically.
7. Open the validation report. Do not publish if any blocking item failed.
8. If all blocking checks passed, approve publication. Politica creates and activates a new immutable local release; it does not alter Google Sheets or the earlier release.

The file must be complete. Before insertion, Politica proves that the file contains every active governed House candidacy for the selected election. A partial constituency or candidate extract therefore stops without becoming a publishable revision; the independent unchanged 2025 release counts provide a second publication check.

## Corrected authority revision

If the AEC later republishes the same logical filename with changed bytes, upload the corrected complete file. Politica assigns the next revision number. The old source and facts remain queryable as superseded history; the corrected facts become active. Uploading the exact same bytes again is rejected because it contains no new information.

## Full 2025 reproduction

**Reproduce 2025 release** remains available under the advanced ingestion actions. Release 0.4.0 restores the exact reference rows from the job's pinned active release after rebuilding the AEC facts. Therefore the current 238 People, 24 Parties and 599 Constituencies are retained instead of reverting to the older embedded snapshot.

The reproduction now validates automatically when it completes. A successful job should finish at 100% with all four phases complete.

## Validate the actual active release

Run:

```bash
cd ~/Downloads/Politica_Election_Results_Database
uv run politica-erd-validate
```

No long database path is required. The command reads `data/app/releases/active.json`, checks the active database SHA-256 and validates that immutable release. `--database` remains available for deliberate forensic checks of another file.

## Current limit

This first individual transformer assumes that the selected election, House contests and candidacies are already present. It cannot safely create a wholly new election by itself. The next development group is election registration, contest registration and candidate registration; once those are installed, additional result-file transformers can target new events without a monolithic reproduction.
