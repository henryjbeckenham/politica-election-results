# Architecture decision

## Final structure

Politica uses one unified logical Election Results Database with physical partitioning. Separate databases per election or jurisdiction are rejected because they duplicate schemas, fragment identifiers and make cross-election analysis unreliable.

## Ownership

| Domain | Authoritative system |
|---|---|
| Canonical People | Grand Database |
| Canonical Parties | Grand Database |
| Canonical Constituencies | Grand Database |
| Elections and contests | Election Results Database |
| Candidacies and ballot structures | Election Results Database |
| Reporting units and election geography | Election Results Database |
| Official election results and counting, including polling places | Election Results Database |
| Source files, revisions and row lineage | Election Results Database |
| Opinion-poll results | Grand Database |
| Publication feeds | Visualisation Database, generated downstream |

## Logical layers

- `control`: schema versions, releases, controlled values, jurisdictions, authorities and adapters.
- `sync`: read-only snapshots of canonical Grand Database entities.
- `core`: elections, chambers, contests, snapshots, candidacies and ballot structure.
- `geography`: reporting-unit identities, election snapshots, hierarchy and boundary metadata.
- `results`: participation, vote observations and declared outcomes.
- `count`: rounds, candidate totals and directed preference transfers.
- `ballot`: anonymous ballot datasets and ordered preferences.
- `provenance`: landing pages, immutable file revisions, imports, transforms and row lineage.
- `audit`: deterministic rules, runs and issues.
- `derived`: reproducible summaries, margins and flows.
- `publish`: approved publication snapshots and source cutoffs.
- `staging`: source-native rows awaiting validation and canonical reconciliation.

## Non-negotiable rules

- Missing data is never zero.
- Official reported values are distinguishable from Politica calculations.
- Every authoritative fact has an immutable source revision and precise locator.
- Corrected files create new revisions; they do not erase history.
- Unknown source schemas and labels are quarantined.
- Election ingestion never silently creates or edits Grand Database entities.
- The Grand Database polling `Results` table is never reused for election results.

## Aggregate result grains

Most vote facts belong to a contest. A small number of official outputs, such as House TPP by state, describe a chamber-wide jurisdiction aggregate rather than one contest. For these rows, `results.vote_result.contest_id` is null and an election reporting unit is mandatory. This keeps the official state total without inventing a synthetic contest.

House TPP is normalized with canonical Labor and Coalition party subjects at state, division, division/vote-type and polling-place grains. The official source values are marked `official_calculated` to distinguish an authority-derived TPP measure from a directly counted first preference.

## Storage decision

DuckDB is the relational control and query layer. Parquet stores governed fact exports and the high-volume formal-ballot preference paths. Google Sheets remains the canonical editing interface for reference entities, but it is not used as the election fact store because a single election contains tens of millions of ballot and preference observations.

## Read-only access decision

The local application exposes only curated, fixed query shapes to non-SQL users. Filters become bound DuckDB parameters; users cannot submit table names, column names, expressions or SQL fragments. Every explorer and publication-feed connection opens the checksum-verified active release with DuckDB's `read_only` flag. CSV exports use fixed queries and bound filters. Public feed responses additionally include a deterministic publication ID, release and database checksums, field contract, calculation version and source-revision set. Cross-origin access is enabled only for dedicated public GET responses; ingestion and synchronisation routes remain local same-origin operations.

The application release number is independent of the database schema number. Application 1.6.0 safely reads schema 0.2.0 and consumes feed contract 1.6.0 plus visualisation contract 1.8.0. The local public site is served beneath `/results/` and reads only fixed public GET feeds and election-specific release-bound visualisation catalogues. Static site format 1.1.0 freezes all nine feeds and the correct boundary contract for every installed election. The static package reads only its bundled relative files, contains no database or credentials, and carries a manifest tying every file to the governed database checksum.

## Visualisation foundation

Stage 13.0 separates reusable interface mechanics from individual charts. Design tokens, canonical party colours with documented fallbacks, formatting, URL state, route registration, legends and tooltips live in small browser modules. `config/visualisation_contract.yml` governs available and planned routes, metric definitions, feed dependencies, filters and known capability boundaries. The same validated contract is served through a public read-only endpoint and embedded in every static website release.

The contract deliberately blocks claims the current data cannot evidence. A complete 76-member Senate composition needs continuing-member data; physical chamber placement needs a seating source; swing analysis needs a governed comparison election; electorate maps need versioned boundary geometry; and full Senate transfer flows need origin-and-destination data beyond progressive totals.
