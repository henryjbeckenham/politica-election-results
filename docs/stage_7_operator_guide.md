# Stage 7 operator guide

## Purpose

Stage 7 extends the governed individual-file workflow to complete Australian Electoral Commission Senate summaries. It does not write to Google Sheets or create People, Parties or Constituencies.

## Supported canonical files

- `SenateFirstPrefsByStateByVoteTypeDownload-<event>.csv`
- `SenateFirstPrefsByDivisionByVoteTypeDownload-<event>.csv`
- `SenateSenatorsElectedDownload-<event>.csv`
- `GeneralEnrolmentByStateDownload-<event>.csv`
- `SenateInformalByStateDownload-<event>.csv`
- `SenateTurnoutByStateDownload-<event>.csv`
- `SenateVotesCountedByStateDownload-<event>.csv`
- `SenateInformalByDivisionDownload-<event>.csv`
- `SenateTurnoutByDivisionDownload-<event>.csv`
- `SenateVotesCountedByDivisionDownload-<event>.csv`

The Senate DOP/count files, national/group-only aggregates and formal-preference ballot archives are not Stage 7 routes. Release 0.8.0 adds them through the separate governed Stage 8 workflow described in `stage_8_operator_guide.md`.

## Workflow for a future election

1. Register the election and its official House/Senate candidate files through **Register new AEC election**.
2. Publish that reviewed registration as an immutable release.
3. Open **Ingest data** and select the registered election.
4. Upload complete official Senate summary files. State first preferences are the recommended first result file because they establish ballot groups and candidate positions.
5. Confirm every file shows a canonical-capable registered format.
6. Execute the isolated job.
7. Review **Validate & publish**. Stage 7 requires non-empty canonical output, zero rejected rows, exact event-number agreement and complete governed scope.
8. Publish only after the validation report passes.

Corrected authority files are immutable new source revisions. They supersede only active facts from the same logical source; older observations remain queryable as history. Byte-identical reuploads do not create duplicate jobs.

## Safety rules

- The selected election must already contain governed Senate contests and candidacies.
- Candidate IDs, state contests and House DivisionIDs must resolve exactly.
- Every file must cover its full expected state or division scope.
- Vote-type components, turnout, informality and enrolment arithmetic must reconcile.
- Elected orders must exactly match each contest's vacancy count.
- The Grand Database remains read-only throughout.
