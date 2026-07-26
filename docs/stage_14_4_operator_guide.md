# Stage 14.4 operator guide

## Installation

Stop Politica before installing. Keep every Stage 14.4 download part in the same Downloads folder, extract the core update and run `install_stage14_4.command`. The installer verifies every packaged asset, creates a new immutable combined release, runs a short four-election smoke check and rebuilds the local static website. It does not run the full development regression suite on the Mac.

## Using the election selector

Open `http://127.0.0.1:8765/results/`. The Federal election selector offers 2025, 2022, 2019 and 2016. It changes the House, Senate, analysis, map, source and download content together. The selected election is stored in the page address and can be bookmarked.

The 2025 Senate composition is the complete governed 76-seat 48th Parliament snapshot. The 2022 and 2019 views each contain the 40 senators declared elected at that election. The 2016 double-dissolution result contains all 76 senators declared elected. Historical views do not invent continuing membership beyond the selected result.

## Evidence and preservation

The 46 original AEC files remain in `data/raw/aec/2016_federal/20499/final`. Their checksums are governed by `config/source_checksums_2016.yml` and `data/manifests/aec_2016_sources.json`. Anonymous formal ballot paths remain under `data/parquet/aec_2016`.

The installer copies the current immutable 2025, 2022 and 2019 release before merging the prevalidated 2016 tables. It verifies that the predecessor database checksum, election result counts and source-revision identities do not change. The old release remains available beneath `data/app/releases`.
