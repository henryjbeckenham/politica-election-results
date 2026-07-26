# Stage 14.2 operator guide

## Installation

Stop Politica before installing. Keep every Stage 14.2 download part in the same Downloads folder, extract the core update, and run `install_stage14_2.command`. The installer verifies every packaged asset, creates a new immutable combined release, runs a short two-election smoke check and rebuilds the local static website. It does not run the full development regression suite on the Mac.

## Using the election selector

Open `http://127.0.0.1:8765/results/`. The Federal election selector changes the House, Senate, analysis, map, source and download content together. The selected election is stored in the page address and can be bookmarked.

The 2025 Senate composition is the complete governed 76-seat 48th Parliament snapshot. The 2022 Senate composition contains the 40 senators declared elected at the 2022 election. The interface labels these views differently so a 40-member election result is not presented as a complete historical chamber.

## Evidence and preservation

The 45 original AEC files remain in `data/raw/aec/2022_federal/27966/final`. Their checksums are governed by `config/source_checksums_2022.yml` and `data/manifests/aec_2022_sources.json`. Anonymous formal ballot paths remain under `data/parquet/aec_2022`.

The installer copies the current immutable 2025 release before merging the prevalidated 2022 tables. It verifies that the prior database checksum, 2025 result counts and 2025 source-revision identity did not change. The old release remains available beneath `data/app/releases`.
