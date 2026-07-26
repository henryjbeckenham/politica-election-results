# Release notes 0.4.0

## Outcome

Release 0.4.0 begins Stage 4 by making one official AEC result format canonically ingestible as an individual file. It also corrects the operational defects observed during the completed Stage 3 walkthrough.

## New governed route

The registered route is:

`adapter_aec_2025_v1 / house_first_preferences_by_vote_type / transformer 1.0.0`

It accepts a complete `HouseFirstPrefsByCandidateByVoteTypeDownload-<event-id>.csv` for a selected AEC election whose House contests and candidacies already exist. It:

- resolves source DivisionID and CandidateID values to governed records;
- requires the numeric event ID in the official filename to match the selected election;
- excludes the AEC's CandidateID 999 informal summary rows;
- proves Ordinary + Absent + Provisional + PrePoll + Postal = Total for every candidate;
- proves the file covers every active governed House candidacy for the selected election;
- emits active ordinary, absent, provisional, early, postal and total vote facts plus swing where supplied;
- assigns deterministic fact and lineage identifiers;
- records the source as a numbered immutable revision;
- marks facts from the earlier revision as superseded; and
- stops on unknown official IDs, duplicate rows, conflicting active grains or invalid values.

## Corrected Stage 3 behaviour

- Canonical execution now runs validation automatically.
- Successful terminal jobs show every progress phase as complete.
- Validation failures have an explicit failed state.
- A complete 2025 reproduction restores the exact People, Parties and Constituencies rows from the job's pinned governed release before validation.
- `politica-erd-validate` follows `data/app/releases/active.json` by default and verifies the selected database checksum.
- Dashboard fact totals count active observations, not superseded history.
- Exact source-byte duplicates are shown during inspection and rejected again at execution.
- Published individual-file releases include the original upload under `data/raw/operator_uploads` and update its governed archive path before release hashing.

## Safety boundary

This route updates results for an established election. It does not invent or silently create missing election, contest, candidate, person, party or constituency records. A wholly new election therefore remains staging-only until its registration transformer group is added and tested.

## Verification

The Stage 4 tests cover generic event filenames, active-pointer checksum enforcement, reference preservation, first revision insertion, second revision supersession, release source copying and the complete official 2025 House first-preference file. The official source test stages 1,276 rows, excludes 150 informal summaries and produces 7,882 governed observations.
