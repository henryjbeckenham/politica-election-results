# Stage 14.6 operator guide

## Installation

Stop Politica before installing. Keep every Stage 14.6 download part in the same Downloads folder, extract the core update and run `install_stage14_6.command`. The installer verifies every packaged asset, creates a new immutable combined release, runs a short six-election smoke check and rebuilds the local static website. It does not run the full development regression suite on the Mac.

## Using the election selector

Open `http://127.0.0.1:8765/results/`. The Federal election selector offers 2025, 2022, 2019, 2016, 2013 and 2010. It changes the House, Senate, analysis, map, source and download content together. The selected election is stored in the page address and can be bookmarked.

The 2010 Senate view presents the 40 senators declared elected at that election. It does not imply that the other continuing senators were elected in 2010 or recreate a complete historical chamber snapshot.

## Pre-reform Senate preferences

The 2010 Senate used group-voting tickets. Politica stores the 156 registered ticket schedules and the official ticket-vote totals separately from the available paper-level non-ticket below-the-line matrices.

The paper matrices contain 493,129 records, while the official non-ticket aggregate contains 493,142 votes. The 13 unavailable paper records are distributed as 10 in New South Wales and one each in Victoria, Queensland and Tasmania. They are explicitly recorded as unavailable. Do not interpret the paper-level count as the complete non-ticket aggregate, and do not add synthetic ballots to close the gap.

The 12,229,091 official ticket votes and 493,142 official non-ticket votes reconcile to 12,722,233 formal Senate votes. The governed paper paths contain 23,659,799 valid candidate preferences.

## Evidence and preservation

The 47 original AEC files remain in `data/raw/aec/2010_federal/15508/final`. Their checksums are governed by `config/source_checksums_2010.yml` and `data/manifests/aec_2010_sources.json`. Anonymous non-ticket ballot paths remain under `data/parquet/aec_2010`.

The installer copies the current immutable 2025, 2022, 2019, 2016 and 2013 release before merging the prevalidated 2010 tables. It verifies that predecessor result counts and source-revision identities do not change. The old release remains available beneath `data/app/releases`.
