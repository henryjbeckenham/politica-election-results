# Release notes 1.5.0

Release 1.5.0 is Stage 14.3: complete 2019 federal election ingestion.

The release adds every governed final AEC source in the 2019 acquisition plan, imports the complete available House and Senate result model, and publishes 2019 beside the unchanged 2025 and 2022 elections. The application, feeds, maps, analyses, downloads, and static site follow the selected election. The 2019 map uses the 151 official AEC divisions applicable to that election.

The 2019 import reconciles 159 contests, 1,514 candidacies, 242,038 vote results, 1,908 participation results, 2,562 count rounds, 135,171 candidate count totals, 19,698 reported count movements, and 191 outcomes. Eight formal-preference datasets contain 14,604,925 anonymous ballots and 98,547,026 governed preference positions.

The 99 import warnings retain official 2019 party and group labels that do not have a canonical match in the reference snapshot. They are visible audit warnings, not failed totals, missing candidates, or missing divisions. No substitute canonical party was invented.

- Application: 1.5.0
- Database schema: 0.2.0
- Publication feed contract: 1.5.0
- Visualisation contract: 1.7.0
- Static-site format: 1.1.0

The prior 2025 and 2022 database release and every earlier immutable release remain unchanged. Installation creates and activates a new combined three-election release.
