# Implementation status

## Stage 1 — governed foundation

Status: complete

The versioned schema, controlled values, deterministic identifiers, logical relationships, audit framework and read-only Grand Database reference snapshot are implemented.

## Stage 2 — 2025 federal election database

Status: complete and validated

Delivered:

- 45 immutable AEC final-results source revisions with SHA-256 checksums.
- House and Senate contests, candidates, ballot structure, reporting units, participation and outcomes.
- House first preferences, TCP and all four official TPP outputs: state, division, division by vote type and polling place.
- House and Senate distributions of preferences.
- All 15,871,189 formal Senate ballot papers represented as anonymous ordered preference paths in Parquet.
- Source-level transformation checkpoints and a safe `--resume` path.
- Precise source-row lineage for every authoritative fact.
- 27 blocking checks, all passed.
- 52 visible party-label mapping warnings and a review CSV; zero unmatched House constituencies.
- Publication snapshot and governed Parquet exports.
- Independent schema validator and 12 passing automated tests.

## Stage 3 — ingestion application

Status: implemented as release 0.3.0

Delivered:

- local web dashboard and guided upload workflow;
- CSV, XLSX and ZIP format inspection;
- adapter selection, source preview and mapping review;
- durable, resumable jobs operating on database working copies;
- validation and publication gates with pinned-base compare-and-swap activation;
- self-contained immutable release bundles containing DuckDB, required Parquet and manifests;
- a governed AEC 2025 reproduction path;
- Google Sheets read-only exact-base preview and crash-recoverable local sync;
- single-instance, checkpoint and publication crash recovery; and
- operator documentation and 52 passing automated tests.

New source formats still require an explicit versioned adapter and transformer. Unknown formats are staged and quarantined rather than guessed.

## Stage 4 — governed individual-file ingestion

Status: first route implemented as release 0.4.0

Delivered:

- first publishable individual transformer for complete AEC House first preferences by candidate and vote type;
- official DivisionID and CandidateID resolution against an already governed election;
- component-to-total reconciliation, deterministic facts and source-row lineage;
- immutable revision history with prior observations marked `superseded`;
- exact duplicate-byte detection and a backend duplicate execution guard;
- immutable uploaded source bytes copied into each published release inventory;
- automatic validation after canonical transformation;
- corrected terminal progress rendering;
- preservation of the pinned active Grand Database reference snapshot during full 2025 reproduction;
- validator defaulting to the checksum-verified active release pointer; and
- a persistent, private `.env` option for the existing Google Sheets read-only credential path.

## Stage 5 — House summary transformer group

Status: implemented as release 0.5.0

Delivered:

- seven additional publishable individual AEC House file formats: TCP by candidate and vote type, TPP by division, members elected, enrolment by division, informal votes by division, turnout by division and votes counted by division;
- generic numeric event IDs checked against the selected governed election;
- complete-contest coverage requirements for every new route;
- candidate-key resolution for TCP and elected outcomes without silent identity creation;
- vote-component, TPP, enrolment, informality and turnout arithmetic reconciliation;
- category-specific revision replacement for vote results, participation results and elected outcomes;
- preservation of current elected-member counts while prior outcomes remain queryable as superseded history;
- seven official-source checksum fixtures and a combined 1,200-row, 4,950-output execution test;
- Stage 4 regression coverage; and
- a compatibility proof against the complete Stage 2 database showing all 213,328 active vote facts remain unchanged after seven simulated revised sources.

## Stage 6 — new AEC election registration

Status: implemented as release 0.6.0

Delivered:

- read-only preview of a new federal AEC event from official House and/or Senate candidate CSV files;
- strict filename, AEC preamble and configured event-number agreement;
- deterministic election, chamber, contest, snapshot and candidacy identifiers;
- configurable election type, publication phase, contest status and Senate vacancies;
- exact-only matching to the active People, Parties and Constituencies snapshot;
- explicit unmatched/conflict preservation without Grand Database writes;
- one-transaction canonical insertion on an isolated checksum-pinned working copy;
- complete row lineage for the election structure and every candidacy;
- automatic Stage 6 validation, reference-count immutability checks and immutable publication;
- crash recovery for a commit completed immediately before its JSON job checkpoint; and
- Stage 4 and Stage 5 regression coverage.

## Stage 7 — Senate summary ingestion

Status: implemented as release 0.7.0

Delivered:

- ten generic numeric-event AEC Senate summary adapters and six canonical transformer families;
- complete state and division first-preference coverage with candidate and above-the-line group facts;
- governed ballot groups, memberships and ballot positions without Grand Database writes;
- election-specific Senate division reporting units reconciled to governed House divisions;
- state enrolment and state/division informal, turnout and votes-counted facts;
- declared Senator outcomes and current elected-member records reconciled to vacancies;
- arithmetic, scope, identity, event-number, duplicate-grain and source-revision safeguards;
- immutable row lineage and category-specific supersession; and
- full official 2025 execution plus Stage 4–6 regression coverage.

## Stage 8 — remaining Senate grains

Status: implemented as release 0.8.0

Delivered:

- national and state Senate group-by-vote-type aggregates with source-defined group codes preserved without invented party mappings;
- the complete eight-state Senate distribution-of-preferences ZIP, including governed count rounds, candidate totals, candidate transfers, exhausted transfers and round adjustments;
- state formal-preference ZIP archives transformed directly into anonymous, partitioned Parquet ballot paths;
- deterministic source revision, ballot-dataset and fact identity with exact event/state checking;
- count invariants covering formal papers, vacancies, contiguous rounds, candidate coverage and elected totals;
- ballot header resolution against governed groups/candidates and the official BTL-first validity rule;
- crash-safe external-artifact checkpoints and release inventory pinning for high-volume ballot data;
- corrected-file supersession while preserving prior count and ballot revisions; and
- complete official 2025 ACT ballot and eight-state DOP execution plus Stage 4–7 regression coverage.

## Stage 9 — read-only explorer and governed export

Status: implemented as release 0.9.0

Delivered:

- a local **Explore & export** workspace over the checksum-verified active immutable release;
- separate, visible application and database-schema versions;
- fixed curated views for current vote results, declared outcomes, participation, Senate count rounds, candidate count totals, formal-ballot dataset inventories, and contests;
- election, chamber, state, contest, result-type, vote-type, reporting-level and text filters where applicable;
- paginated tables that expose canonical IDs, data grain and source-revision provenance;
- UTF-8 CSV downloads using the exact applied filters, stable columns and deterministic ordering;
- a one-million-row default export safety ceiling requiring broad downloads to be narrowed deliberately;
- parameter-bound filters, no arbitrary SQL, and read-only DuckDB connections throughout;
- active-revision rules that exclude superseded facts and superseded count sources; and
- full-scale compatibility checks against the 2025 fact and anonymous ballot inventory plus Stage 4–8 regression coverage.

## Stage 9.1 — House TCP corrective update

Status: implemented as release 0.9.1

Delivered:

- pair-level interpretation of the AEC House TCP `Swing` column;
- 266 zero-sum values retained as genuine swings and 34 100-sum values classified as vote shares;
- vote-share reconciliation against each candidate's total TCP votes;
- rejection of ambiguous or incomplete pairs;
- immutable-copy correction of the existing 2025 release with 34 prior facts retained as superseded history;
- exact source-revision, source-locator and source-row-hash lineage on all corrected facts;
- compare-and-swap activation with the prior release preserved;
- a blocking database-validation rule covering future TCP semantics; and
- Stage 4–9.1 regression, correction, repeat-run, rollback and immutable-publication tests.

## Stage 10 — versioned visualisation feeds

Status: implemented as release 1.0.0

## Stage 10.1 — elected-person identity correction

Status: implemented as release 1.0.1

The Werriwa and Solomon elected candidacies are linked to the unique authoritative Anne Stanley and Luke Gosling People records in a new validated immutable release. Future AEC imports use the same conservative first-given-name plus family-name fallback only when it resolves to exactly one canonical person; ambiguous matches remain unresolved.

Delivered:

- seven fixed JSON and CSV feed presets plus a machine-readable feed catalogue;
- deterministic publication IDs bound to feed version, filters, release ID and database SHA-256;
- per-feed manifests containing field types, row counts, calculation version, data hash and source-revision set;
- stable election, contest, candidacy, person, party and ballot-group identifiers where applicable;
- calculated House TCP shares and margins derived from exact two-candidate vote totals;
- ETag and payload-checksum response headers with conditional `304 Not Modified` support;
- cross-origin access limited to dedicated read-only public GET routes;
- state, contest and election filters as bound values, with no arbitrary SQL;
- a governed publication ceiling and deterministic ordering, raised to 500,000 rows in Stage 14.4 for the complete 2016 double-dissolution Senate count;
- operator interface links for Observable JSON, Flourish-ready CSV and manifests; and
- official-scale verification across 158 contests, 190 outcomes, 1,896 participation facts and 64,965 Senate count-progression rows.

## Stage 11 — public election-results interface

Status: implemented and completed as application release 1.1.3

Delivered:

- a responsive public results site served locally at `/results/`;
- House composition, national party results and searchable electorate detail;
- Senate group comparisons and declared-member lists by state or territory;
- turnout and informality comparisons across both chambers;
- release, schema, application, feed-contract and checksum evidence;
- CSV downloads for every fixed publication feed and an on-demand Senate count-progress JSON route;
- a direct operator-navigation link that keeps operator and public functions separate;
- read-only browser access limited to the seven registered public GET feeds;
- canonical state and territory codes for lower-case constituency references, full Senate contest names and AEC abbreviations, without changing the feed contract;
- compatibility publication for both the existing 2025 `party_total` Senate aggregates and later `group_total` ingestions, with per-state preference for the richer representation;
- installation-time verification that the actual active immutable release publishes non-empty Senate group results for all eight jurisdictions;
- guarded reuse of the two already-registered official AEC group source revisions, producing 1,872 richer `group_total` facts in a newly validated release;
- a prebuilt Observable Framework site requiring no Node.js installation on the operator's Mac; and
- browser-runtime, static-route, API-boundary, compatibility and all-eight-state Senate feed tests.

## Stage 12.0 — governed static website publication

Status: implemented as application release 1.2.0

Delivered:

- deterministic, immutable website release IDs tied to the active database checksum;
- a host-ready ZIP containing the compiled Observable site and seven fixed feed contracts;
- packaged JSON, CSV, per-feed manifests and a static feed catalogue;
- a complete file inventory with size, media type and SHA-256 for every public artifact;
- verification that JSON, CSV, feed manifests and release identity agree;
- explicit blocking of database, credential and private-key file types;
- a dedicated **Website publication** operator view;
- local static-package preview and verified ZIP download;
- stale-package detection after the active database changes;
- a terminal build and status command; and
- no upload, hosting credential or external deployment operation.

## Stage 13.0 — governed visualisation foundation

Status: implemented as application release 1.3.0

Delivered:

- a release-bound, versioned contract for routes, visualisations, metrics, filters and dependencies;
- shared design tokens and modular browser services;
- URL-controlled State, Party, Electorate/Search, Chamber and Senate-state selections;
- reusable accessible legends, tooltips, chart containers and evidence/download panels;
- live and static contract delivery with checksum verification;
- explicit capability boundaries for Senate composition, seating, historical swing, maps and transfer flows; and
- Stage 9–13 focused regression tests plus installation-time full Stage 4–13 verification.

## Stage 13.1 — composition diagrams

Status: implemented as application release 1.3.1

Delivered interactive, accessible House and Senate chamber diagrams, including a governed 76-seat Senate snapshot.

## Stage 13.2 — electorate results

Status: implemented as application release 1.3.2

Delivered searchable result cards for all 150 House electorates, with declared member, seat status, primary votes, TCP, TPP, enrolment, turnout, informality and count metadata.

## Stage 13.3 — interactive electorate map

Status: implemented as application release 1.3.3

Delivered:

- the AEC national federal electoral-boundary dataset applicable to the 2025 election, preserved with its original archive and component checksums;
- a checksum-governed, browser-ready GeoJSON derivative containing exactly 150 unique divisions;
- an accessible SVG map linked to the House result cards;
- national and state views, party colours, result tooltips, keyboard selection and direct electorate-detail navigation;
- AEC source, attribution and licence evidence in the public Sources view; and
- boundary verification in both the local application and host-ready static website package.

The election database and all active and historical election releases remain unchanged.

## Stage 13.4 — House analysis and map navigation

Status: implemented as application release 1.3.4

Delivered:

- an electoral pendulum and closest-contest ranking using governed TCP winning margins;
- a clearly labelled ranking of AEC-reported winner TCP swing values;
- incumbent-based party gains and losses without guessing open-seat transfers;
- first-preference vote share versus declared seat share;
- state and territory comparison plus selected-electorate vote-type comparison;
- eight capital-city map close-ups and independent general zoom, pan and reset controls; and
- a bookmarkable map-view parameter that remains separate from result filtering.

Stage 13.4 consumes the existing fixed public feeds and does not change the database or election-release pointer.

## Stage 13.4.1 — practical electorate-map navigation

Status: implemented as application release 1.3.5

Delivered:

- practical zoom from 100% to 4,000%, centred on the pointer or touch location;
- mouse-wheel, trackpad, double-click, pinch, button, slider and keyboard zoom controls;
- persistent drag and keyboard panning while filters and selections redraw the map;
- eight real miniature capital-city inset maps rather than text-only shortcuts;
- a 150-electorate finder that opens and enlarges the selected division;
- adaptive electorate labels at useful zoom levels; and
- explicit **Back to Australia**, **Reset view** and **Enlarge on map** actions.

This is a corrective application and visualisation-contract update. It does not change the election database, its active release pointer or any source data.

## Stage 13.5 — Senate visualisations

Status: implemented as application release 1.3.6

Delivered:

- eight selectable state and territory delegation summaries over the complete governed 76-seat Senate snapshot;
- Senate group results with official quota multiples when the detailed count is loaded;
- an accessible count-round player with previous, next, direct-range, play, pause and speed controls;
- candidate progressive totals, round changes, count status and official quota markers;
- election and exclusion milestones plus the final declared elected-senator order;
- a new fixed `senate_count_movements` publication feed for reported gains, losses and exhausted values; and
- explicit protection against presenting unreported candidate-to-candidate transfer paths as facts.

The two detailed DOP feeds load only when the Senate route is opened. The database, active election-release pointer and registered source files are unchanged.

## Stage 14.1: historical source inventory

Status: implemented

Delivered a governed source catalogue for all 47 federal general elections from 1901 through 2022, including exact modern AEC tally-room files, legacy AEC archives, Parliamentary Handbook API routes and available national boundary downloads. This stage changed no election facts.

## Stage 14.2: complete 2022 federal election

Status: implemented as application release 1.4.0

Delivered:

- all 45 checksum-pinned final AEC 2022 source files and their immutable source manifest;
- 151 House contests and eight Senate contests with candidates, polling places, first preferences, TCP, TPP, participation, outcomes, count rounds and reported movements;
- 15,040,658 anonymous formal Senate ballots and 101,100,266 governed preference positions in partitioned Parquet;
- a separate immutable combined release that preserves the prior 2025 database checksum and source identity;
- an election selector across the explorer, public feeds, visualisations and static website;
- the official 151-division AEC boundary geometry applicable to the 2022 election; and
- election-specific Senate semantics that distinguish the 40 senators elected in 2022 from the complete governed 76-seat 2025 snapshot.

The installer uses prevalidated table and ballot shards. It performs only compatibility, checksum, installation and smoke checks on the operator's Mac; the full regression and clean-install suites are completed before release packaging.

## Stage 14.3: complete 2019 federal election

Status: implemented as application release 1.5.0

Delivered:

- all 45 checksum-pinned final AEC 2019 source files and their immutable source manifest;
- 151 House contests and eight Senate contests with candidates, polling places, first preferences, TCP, TPP, participation, outcomes, count rounds, and reported movements;
- 14,604,925 anonymous formal Senate ballots and 98,547,026 governed preference positions in partitioned Parquet;
- a separate immutable combined release that preserves the prior 2025 and 2022 database checksums and source identities;
- election selection across the explorer, public feeds, visualisations, and static website;
- the official 151-division AEC boundary geometry applicable to the 2019 election; and
- election-specific Senate semantics that distinguish the 40 senators elected in 2019 from the complete governed 76-seat 2025 snapshot.

The installer uses prevalidated table and ballot shards. It performs only compatibility, checksum, installation, and smoke checks on the operator's Mac. The full regression and clean-install suites are completed before release packaging.

## Stage 14.4: complete 2016 federal election

Status: implemented as application release 1.6.0

Delivered:

- all 46 checksum-pinned final AEC 2016 source files and their immutable source manifest;
- 150 House contests and eight Senate contests with candidates, polling places, first preferences, TCP, TPP, participation, outcomes, count rounds and reported movements;
- 13,838,900 anonymous formal Senate ballots and 93,842,251 governed preference positions transformed from the official compact vector format;
- explicit candidate-information mapping and preservation of valid `/` and `*` first-preference marks;
- all 76 senators declared elected at the 2016 double-dissolution election;
- a separate immutable combined release that preserves the prior 2025, 2022 and 2019 database checksums and source identities;
- election selection across the explorer, all 36 public feed combinations, visualisations and static website;
- the official 150-division AEC boundary geometry applicable to the 2016 election; and
- a 500,000-row governed publication ceiling that retains the complete 452,193-row 2016 Senate count feed.

The installer uses prevalidated table and ballot shards. It performs compatibility, checksum, installation and smoke checks on the operator's Mac. The full regression and clean-install suites are completed before release packaging.
