# Release notes 1.4.0

Release 1.4.0 is Stage 14.2: complete 2022 federal election ingestion.

The release adds every governed final AEC source in the 2022 acquisition plan, imports the complete available House and Senate result model, and publishes 2022 beside the unchanged 2025 election. The application, feeds and static site now include an election selector. Map geometry follows the selected election: 150 divisions for 2025 and 151 for 2022.

The 2022 import reconciles 159 contests, 1,624 candidacies, 230,488 vote results, 1,908 participation results, 2,670 count rounds, 115,892 candidate count totals, 19,904 reported count movements and 191 outcomes. Eight formal-preference datasets contain 15,040,658 anonymous ballots and 101,100,266 governed preference positions.

The 72 import warnings are retained official 2022 party and group labels that do not have a current canonical Grand Database party match. They are visible audit warnings, not failed totals, missing candidates or missing divisions. No substitute canonical party was invented.

- Application: 1.4.0
- Database schema: 0.2.0
- Publication feed contract: 1.4.0
- Visualisation contract: 1.6.0
- Static-site format: 1.1.0

The prior 2025 database and all prior immutable releases remain unchanged. Installation creates and activates a new combined release.
