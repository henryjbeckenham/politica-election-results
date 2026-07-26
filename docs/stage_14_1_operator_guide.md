# Stage 14.1 operator guide

## Purpose

Stage 14.1 installs a governed historical source catalogue and planning utility. It does not download bulk result files, import historical rows or change the active 2025 database release.

## Validate the catalogue

From the Politica project folder:

```bash
uv run python -m politica_erd.historical_sources validate
```

A valid inventory reports `PASS`, 47 elections, years 1901 through 2022, seven modern tally events and two legacy AEC archives.

## Inspect one election

Show only the primary sources required for the first insertion target:

```bash
uv run python -m politica_erd.historical_sources plan --year 2022 --primary-only
```

Export the same plan as CSV:

```bash
uv run python -m politica_erd.historical_sources plan --year 2022 --primary-only --format csv
```

For 2004 to 2022, each record is a directly downloadable AEC file. For 1993 to 2001, the primary record is an official AEC archive. For 1901 to 1990, baseline records are exact API endpoints and cascade records contain named placeholders to be expanded from the baseline response.

## Check current availability

```bash
uv run python -m politica_erd.historical_sources probe --year 2022 --primary-only
```

The probe is diagnostic only. It does not save or import the source files. A temporary upstream outage must not cause a governed source URL to be silently replaced.

## Export the full register

```bash
uv run python -m politica_erd.historical_sources inventory --format csv
uv run python -m politica_erd.historical_sources plan-all --primary-only --format csv
```

Stage 14.1 already installs the outputs of those commands in `docs/`.

## Future download destinations

The acquisition plan pre-assigns paths under:

* `data/raw/aec/{year}_federal/{event_id}/final/` for modern AEC event files;
* `data/raw/aec/legacy/` for official legacy archives;
* `data/raw/aph_handbook/{year}/` for Parliamentary Handbook JSON.

Future insertion installers must record retrieved checksums, HTTP metadata, retrieval time, catalogue version and the exact source record. They must create a new election release rather than editing the active 2025 release.

