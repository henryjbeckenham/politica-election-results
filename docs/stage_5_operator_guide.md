# Stage 5 operator guide

## What changed

Stage 5 retains every existing screen and adds seven individual AEC House transformers. Eight complete House CSV formats can now create a validated immutable local release for an election already present in the database.

## Start the application

From the installed project folder:

```bash
cd ~/Downloads/Politica_Election_Results_Database
./start_politica.command
```

Keep the Terminal window open. The application remains local at `http://127.0.0.1:8765`.

## Ingest a supported file

1. Choose **Ingest data** and upload one complete supported CSV.
2. Confirm that the detected format shows a canonical destination and does not say **Staging-only format**.
3. Continue to configuration. Confirm **Australian Electoral Commission**, the correct existing election and **Final official results**.
4. Confirm the source and create the job once.
5. Wait for Register, Stage, Transform and Validate to finish.
6. Review the validation report. Never publish a job with a blocking failure.
7. If all blocking checks pass, approve publication. A new immutable local release is created and activated; the Grand Database is not modified.

The accepted names are listed in `docs/release_notes_0.5.0.md`. The event number may differ, but it must match the selected election and the required official headers must also match.

## Expected duplicate message for the current 2025 files

All eight original 2025 source files are already registered by the complete Stage 2 build. Uploading the unchanged original file therefore reports that the exact source revision already exists. This is correct and proves duplicate protection is working. Do not edit an official file merely to bypass that protection.

Use an individual route when the authority publishes genuinely corrected bytes for the same logical file, or after a later election has first been registered in the database. A corrected revision preserves the earlier source and marks only its earlier canonical observations as superseded.

## Completeness rules

- Candidate result files must contain every governed House contest.
- TCP must contain exactly two official candidates in each contest.
- Elected-member, TPP and participation files must contain exactly one source row for every governed division.
- Unknown DivisionID or CandidateID values stop the job.
- Arithmetic mismatches stop the job before a canonical release can be published.

## Full 2025 reproduction and Google Sheets

**Reproduce 2025 release** remains the governed full-batch route. It retains the active People, Parties and Constituencies snapshot. **Google Sheets sync** remains read-only from the Grand Database into a reviewed local release; Stage 5 does not write back to Google Sheets.

## Current limit

Stage 5 cannot yet create a wholly new election and its contest/candidacy register. That must be completed before these result transformers can target a new event safely.
