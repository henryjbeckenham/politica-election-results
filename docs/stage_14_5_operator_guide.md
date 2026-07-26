# Stage 14.5 operator guide

## Installation

Stop Politica before installing. Keep every Stage 14.5 download part in the same Downloads folder, extract the core update and run `install_stage14_5.command`. The installer verifies every packaged asset, creates a new immutable combined release, runs a short five-election smoke check and rebuilds the local static website. It does not run the full development regression suite on the Mac.

## Using the election selector

Open `http://127.0.0.1:8765/results/`. The Federal election selector offers 2025, 2022, 2019, 2016 and 2013. It changes the House, Senate, analysis, map, source and download content together. The selected election is stored in the page address and can be bookmarked.

The 2013 Senate view contains the 40 outcomes originally published for the election. Western Australia's six outcomes are labelled as later voided and superseded. The 2014 supplementary election is not part of Stage 14.5.

## Pre-reform Senate preferences

The 2013 Senate used group-voting tickets. Politica stores the 265 registered ticket schedules and the official ticket-vote totals separately from the 471,030 available paper-level non-ticket below-the-line ballots. Do not interpret the paper-level ballot count as the total formal vote. The two governed components reconcile to 13,413,019 formal Senate votes.

## Evidence and preservation

The 47 original AEC files remain in `data/raw/aec/2013_federal/17496/final`. Their checksums are governed by `config/source_checksums_2013.yml` and `data/manifests/aec_2013_sources.json`. Anonymous non-ticket ballot paths remain under `data/parquet/aec_2013`.

The installer copies the current immutable 2025, 2022, 2019 and 2016 release before merging the prevalidated 2013 tables. It verifies that predecessor result counts and source-revision identities do not change. The old release remains available beneath `data/app/releases`.
