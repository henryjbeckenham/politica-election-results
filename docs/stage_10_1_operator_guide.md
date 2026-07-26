# Stage 10.1 operator guide

## What the update changes

Stage 10.1 repairs two canonical person links in the active 2025 release:

- Anne Maree Stanley — Werriwa
- Luke John Gosling — Solomon

It does not edit the Grand Database, source CSV files or the prior immutable release. The installer copies the active release, applies the guarded links, validates the copy, freezes it and then activates it.

## Verification after installation

Start Politica and open **Visualisation feeds**. Select the 2025 election and download **House seat results** again. The file must contain 150 unique contests, and the Werriwa and Solomon rows must now contain non-empty `person_id` and `person_name` values.

All other result numbers, TCP shares, margins and party totals remain unchanged. Because activation changes the governed database checksum, the new export has a new release ID, database SHA-256 and publication ID.
