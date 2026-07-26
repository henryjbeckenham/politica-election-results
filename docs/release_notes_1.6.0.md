# Release notes 1.6.0

Release 1.6.0 is Stage 14.4: complete 2016 federal election ingestion.

The release adds every governed final AEC source in the 2016 acquisition plan, imports the complete available House and Senate result model, and publishes 2016 beside the unchanged 2025, 2022 and 2019 elections. The application, feeds, maps, analyses, downloads and static site follow the selected election. The 2016 map uses the 150 official AEC divisions applicable to that election.

The 2016 import reconciles 158 contests, 1,625 candidacies, 268,558 vote results, 1,896 participation results, 4,956 count rounds, 458,285 candidate count totals, 31,048 reported count movements and 226 outcomes. Eight formal-preference datasets contain 13,838,900 anonymous ballots and 93,842,251 governed preference positions. The Senate result contains all 76 senators declared elected at the double-dissolution election.

The 117 import warnings retain official 2016 party and group labels that do not have a canonical match in the reference snapshot. They are visible audit warnings, not failed totals, missing candidates or missing divisions. No substitute canonical party was invented.

- Application: 1.6.0
- Database schema: 0.2.0
- Publication feed contract: 1.6.0
- Visualisation contract: 1.8.0
- Static-site format: 1.1.0

The prior 2025, 2022 and 2019 database releases and every earlier immutable release remain unchanged. Installation creates and activates a new combined four-election release.
