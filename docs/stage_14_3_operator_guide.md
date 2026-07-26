# Stage 14.3 operator guide

## Installation

Stop Politica before installing. Keep every Stage 14.3 download part in the same Downloads folder, extract the core update, and run `install_stage14_3.command`. The installer verifies every packaged asset, creates a new immutable combined release, runs a short three-election smoke check, and rebuilds the local static website. It does not run the full development regression suite on the Mac.

## Using the election selector

Open `http://127.0.0.1:8765/results/`. The Federal election selector offers 2025, 2022, and 2019. It changes the House, Senate, analysis, map, source, and download content together. The selected election is stored in the page address and can be bookmarked.

The 2025 Senate composition is the complete governed 76-seat 48th Parliament snapshot. The 2022 and 2019 Senate composition views each contain the 40 senators declared elected at that election. The interface labels these as election results rather than complete historical chambers.

## Evidence and preservation

The 45 original AEC files remain in `data/raw/aec/2019_federal/24310/final`. Their checksums are governed by `config/source_checksums_2019.yml` and `data/manifests/aec_2019_sources.json`. Anonymous formal ballot paths remain under `data/parquet/aec_2019`.

The installer copies the current immutable 2025 and 2022 release before merging the prevalidated 2019 tables. It verifies that the predecessor database checksum, election result counts, and source-revision identities do not change. The old release remains available beneath `data/app/releases`.
