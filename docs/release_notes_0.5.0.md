# Release notes 0.5.0

## Outcome

Release 0.5.0 expands governed individual-file ingestion from one AEC House result format to eight. Seven new formats cover the principal contest-level House summaries for an election whose contests and candidacies already exist.

## New governed routes

The following `adapter_aec_2025_v1` dataset transformers are added at transformer version `1.0.0`:

- `house_tcp_by_vote_type`;
- `house_tpp_division`;
- `house_elected`;
- `enrolment_division`; and
- `house_participation`, which recognises the separate informal, turnout and votes-counted filenames.

Together with Stage 4's `house_first_preferences_by_vote_type`, the application canonically supports these complete official filenames:

1. `HouseFirstPrefsByCandidateByVoteTypeDownload-<event-id>.csv`
2. `HouseTcpByCandidateByVoteTypeDownload-<event-id>.csv`
3. `HouseTppByDivisionDownload-<event-id>.csv`
4. `HouseMembersElectedDownload-<event-id>.csv`
5. `GeneralEnrolmentByDivisionDownload-<event-id>.csv`
6. `HouseInformalByDivisionDownload-<event-id>.csv`
7. `HouseTurnoutByDivisionDownload-<event-id>.csv`
8. `HouseVotesCountedByDivisionDownload-<event-id>.csv`

## Validation and revision rules

- The numeric event ID in every filename must equal the selected election's official event ID.
- Every source must cover every active governed House contest; TCP must contain exactly two resolvable candidates per contest.
- Candidate, contest and party keys must already exist where the destination grain requires them.
- TCP vote types, TPP totals and shares, enrolment movements, formal plus informal totals, turnout percentages, and votes-counted components are reconciled before insertion.
- A correction with changed bytes receives the next source revision number.
- Only facts from earlier revisions of the same logical filename become `superseded`; an occupied grain from another logical source stops the job.
- Elected outcomes retain superseded history while the current elected-member projection remains singular.
- Exact duplicate bytes remain blocked before canonical execution.

## Verification

The official 2025 fixtures match the source-inventory SHA-256 values. A combined test stages 1,200 official rows and produces 4,950 governed outputs: 2,850 vote results, 1,800 participation observations, 150 outcomes and 150 current elected-member records.

A separate compatibility run used a copy of the complete Stage 2 database. Seven changed-byte revisions created 2,850 superseded vote observations, 1,800 superseded participation observations and 150 superseded outcomes while all active counts remained unchanged. The independent governed validator returned `PASS` with zero failures.

## Current boundary

Release 0.5.0 updates an established election. It does not yet bootstrap a wholly new election, create contests or candidacies, or write People, Parties or Constituencies back to Google Sheets. That registration workflow is the next development group.
