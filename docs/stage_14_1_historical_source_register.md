# Stage 14.1 historical federal election source register

## Scope

This register covers every Australian federal general election before the existing 2025 release, from 1901 through 2022. It contains 47 elections. By-elections, referendums, Senate-only periodic elections, the 2014 Western Australian Senate election and state or territory elections are outside this catalogue and must receive separate governed event records later.

The accompanying `historical_election_source_inventory_1901_2022.csv` identifies the source tier and principal official download locations for every election. The accompanying `historical_source_acquisition_plan_1901_2022.csv` expands the catalogue to 988 required files, API endpoints and API templates.

## Official source locations

| Coverage | Primary authority | Exact location | Acquisition method |
|---|---|---|---|
| 2004 to 2022 | Australian Electoral Commission | `https://results.aec.gov.au/` | Open the election event, then its House, Senate or General CSV download menu. The governed catalogue expands every required CSV or ZIP to its direct file URL. |
| 2001 | Australian Electoral Commission | `https://www.aec.gov.au/About_AEC/Publications/statistics/files/aec-2001-election-statistics.zip` | Download the single official ZIP. Its governed SHA-256 is `73069fc7c4958388bbc559e15430c6d69268b117fdb1cee8ffd37ba90ae4b616`. |
| 1993, 1996 and 1998 | Australian Electoral Commission | `https://www.aec.gov.au/About_AEC/Publications/statistics/files/aec-1993-1996-1998-election-statistics.zip` | Download the shared official ZIP. Its governed SHA-256 is `ee3b46be593dfd2b6e8f29ca1acf78a1ae4a86f0dfd0aba71f33248bdbb025e8`. |
| 1901 to 1990 | Parliament of Australia | `https://handbookapi.aph.gov.au/api/Elections` | Retrieve the official Parliamentary Handbook JSON endpoints listed in the acquisition plan. Begin with the election, candidate, division and elected-member endpoints, then expand the state, division and polling-place templates from returned identifiers. |
| 1901 to 2016 corroboration | Parliamentary Library | `https://www.aph.gov.au/About_Parliament/Parliamentary_Departments/Parliamentary_Library/pubs/rp/rp1617/FederalElectionResults` | Use as an official cross-check for national election totals and historical interpretation. |
| Secondary corroboration | Australian Election Database | `https://doi.org/10.26193/HZYUXD` | Restricted secondary dataset. Do not make a Politica release depend on access to it. |

The AEC source catalogue is at `https://www.aec.gov.au/elections/federal_elections/Stats_CDRom.htm`. The AEC's complete federal election date list is at `https://www.aec.gov.au/elections/federal_elections/election-dates.htm`.

## Modern tally-room events

| Election | AEC event | Required direct files | Download-menu root | Senate ballot source |
|---|---:|---:|---|---|
| 2022 | 27966 | 45 | `https://results.aec.gov.au/27966/Website/` | Eight state and territory formal-preference ZIPs |
| 2019 | 24310 | 45 | `https://results.aec.gov.au/24310/Website/` | Eight state and territory formal-preference ZIPs |
| 2016 | 20499 | 46 | `https://results.aec.gov.au/20499/Website/` | Eight state and territory formal-preference ZIPs plus the separate candidate-information ZIP |
| 2013 | 17496 | 47 | `https://results.aec.gov.au/17496/Website/` | Group voting-ticket files and eight below-the-line ZIPs |
| 2010 | 15508 | 47 | `https://results.aec.gov.au/15508/Website/` | Group voting-ticket files and eight below-the-line ZIPs |
| 2007 | 13745 | 47 | `https://results.aec.gov.au/13745/Website/` | Group voting-ticket files and eight below-the-line ZIPs |
| 2004 | 12246 | 39 | `https://results.aec.gov.au/12246/results/` | Group voting-ticket files; no ballot-level BTL download is published in the event menu |

The required file families are candidates, elected members and senators, House first preferences, House TCP, House TPP, House preference distributions, enrolment, polling places, turnout, informality, votes counted, Senate first preferences, Senate group totals, Senate distributions and the ballot-regime-specific preference files. Every concrete URL is already expanded in `historical_source_acquisition_plan_1901_2022.csv`.

## Legacy AEC archives

The 2001 archive contains 1,754 members. Its import directory includes `divs.txt`, `hcands.txt`, `hppdop.txt`, `htppbypp.txt`, `scands.txt` and `sppvote.txt`, plus spreadsheet and publication tables.

The shared 1993, 1996 and 1998 archive contains 2,519 members. Each election has six semicolon-delimited import files corresponding to divisions, House candidates, House preference distributions, House TCP by polling place, Senate candidates and Senate polling-place votes. Senate distribution details and some summary values must be taken from the spreadsheets and publications in the same official archive.

## Parliamentary Handbook API acquisition

For each election from 1901 to 1990, the catalogue records the exact Handbook election ID. Required baseline routes are:

* `Election?electionId={aph_election_id}`
* `Candidates?electionId={aph_election_id}`
* `Divisions?state=&year={year}`
* `MembersElected?year={year}`
* `SenateCandidates?year={year}` when a Senate contest occurred
* `SenatorsElected?year={year}` when a Senate contest occurred

After baseline acquisition, state, division and polling-place routes are expanded using the returned names. The catalogue includes templates for Senate state statistics, Senate candidate and group results, quotas, House division statistics, first preferences, vote types, polling places, TCP, Senate division results and polling-place results.

The 1929 and 1954 general elections were House-only and therefore have no Senate source requirement in this inventory. The 1963, 1966, 1969 and 1972 election records identify the limited Senate casual-vacancy scope. The 1901 record preserves the multi-member House arrangements used in South Australia and Tasmania.

## Boundary geometry

The AEC boundary catalogue is `https://www.aec.gov.au/electorates/gis/gis_datadownload.htm`.

| Election | Official national boundary download |
|---|---|
| 2022 | `https://www.aec.gov.au/Electorates/gis/files/2021-Cwlth_electoral_boundaries_ESRI.zip` |
| 2019 | `https://www.aec.gov.au/electorates/gis/files/national-esri-fe2019.zip` |
| 2016 | `https://www.aec.gov.au/electorates/gis/files/national-midmif-09052016.zip` |
| 2013 | `https://www.aec.gov.au/electorates/gis/files/national-esri-16122011.zip` |
| 2010 | `https://www.aec.gov.au/electorates/gis/files/national-esri-2010.zip` |

No complete national AEC vector download was identified for 2007 or earlier. Historical result ingestion can proceed without geometry. A later historical-map stage must govern reconstructed or archival boundaries separately and must not reuse a later election's boundaries as if they were contemporaneous.

## Coverage limitations

The source depth is not uniform. The 2004 to 2022 tier supports the richest result model and polling-place detail. The 1993 to 2001 archives support detailed legacy ingestion with format-specific adapters. The 1901 to 1990 API supplies structured official historical results, but individual fields such as polling places, TCP, Senate quotas or vote-type breakdowns may be absent for particular elections. Missing historical values must remain null and source-described rather than inferred.
