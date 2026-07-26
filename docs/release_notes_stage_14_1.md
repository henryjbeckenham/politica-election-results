# Stage 14.1 release notes

Stage 14.1 establishes the governed source layer for historical Australian federal general elections.

## Added

* A 47-election catalogue covering 1901 through 2022.
* Exact AEC event IDs and download paths for 2004 through 2022.
* Exact AEC legacy archive URLs, contents metadata and verified hashes for 1993 through 2001.
* Exact Parliamentary Handbook election IDs and API route templates for 1901 through 1990.
* National AEC boundary downloads where available for 2010 through 2022.
* A 47-row election register and a 988-record primary-source acquisition plan.
* Catalogue validation, one-election planning, complete planning and non-writing availability probes.
* Tests for year coverage, source counts, ballot-regime differences, archive reuse, early-election API routes and House-only elections.

## Data effect

None. This release does not download bulk sources, import election results, modify database schemas, change the active election pointer or rebuild the public website.

## Verified exceptions

* The 2013 Western Australian Senate result was voided. The 2014 Western Australian Senate election is not treated as part of the 2013 general election.
* The 1929 and 1954 general elections were House-only.
* The 1963, 1966, 1969 and 1972 election records contain only the relevant Senate casual-vacancy contests.
* South Australia and Tasmania used multi-member House arrangements at the first federal election.
* Complete national AEC vector boundaries were not located for 2007 or earlier.
