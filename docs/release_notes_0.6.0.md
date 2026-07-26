# Release notes 0.6.0

## Outcome

Release 0.6.0 completes Stage 6: a reviewed bootstrap route for an entirely new Australian Electoral Commission federal election.

The operator supplies the official `HouseCandidatesDownload-<event>.csv` file, the official `SenateCandidatesDownload-<event>.csv` file, or both. Before any database copy is created, Politica reads every candidate row and previews the proposed election ID, event number, polling date, chambers, vacancies, contests, candidacies and exact reference matches.

After explicit confirmation, Politica:

1. pins the checksum-verified active release;
2. copies it into an isolated job workspace;
3. checksum-registers and stages the original candidate files;
4. creates the election, selected election chambers, electoral-system versions, contests, constituency snapshots and candidacies in one DuckDB transaction;
5. records precise source-row lineage;
6. verifies that People, Parties and Constituencies did not change;
7. validates the working database automatically; and
8. allows publication only when every blocking check passes.

## Safety boundaries

- The filename event number, AEC source-preamble event number and operator-entered event number must be identical.
- An existing AEC event or deterministic election ID cannot be registered again.
- Candidate IDs cannot be blank, duplicated or used in multiple contests.
- House division names and state codes must remain internally consistent.
- Senate state and territory codes and vacancy values are checked explicitly.
- Unknown People, Parties and Constituencies are retained as `unmatched` or `conflict`; they are never invented.
- `sync.person`, `sync.party` and `sync.constituency` are compared before and after the transaction and must be byte-logically unchanged.
- The active release is never edited in place. Publication creates and activates a new immutable release bundle.
- A failed transaction leaves no partial election structure in the working database.

## Scope

Stage 6 supports new federal AEC election registration from the official candidate CSV files. It does not infer a new election from result files, write to the Grand Database or Google Sheets, fetch candidate files from the internet, or claim support for state and territory electoral authorities.

Once a new House election is published, the existing Stage 4 and Stage 5 individual result routes can target its official DivisionID and CandidateID register. Additional House, Senate, count and ballot source formats still require their own registered transformers.

The database schema remains version 0.2.0; no migration is required.
