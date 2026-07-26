# Release notes 1.7.0

Release 1.7.0 is Stage 14.5: complete 2013 federal election ingestion.

The release adds all 47 governed final AEC sources in the 2013 acquisition plan and publishes 2013 beside the unchanged 2025, 2022, 2019 and 2016 elections. The application, feeds, maps, analyses, downloads and static site follow the selected election. The 2013 map uses the 150 official AEC divisions applicable to that election.

The 2013 import reconciles 158 contests, 1,717 candidacies, 268,448 vote results, 1,896 participation results, 2,525 count rounds, 131,071 candidate count totals, 14,633 reported count movements and 190 outcomes. It preserves all 265 registered Senate group-voting tickets and their 20,492 candidate preference positions. The available paper-level source contains 471,030 non-ticket below-the-line ballots with 33,704,176 governed preferences; the other 12,941,989 formal Senate votes are represented by the official group-ticket aggregates and registered ticket schedules.

The originally published 2013 Western Australian Senate result is retained as official source evidence, with the contest marked `void` and its six outcomes marked `superseded`. Stage 14.5 does not substitute the later 2014 supplementary election.

The 100 import warnings retain official 2013 party and group labels that do not have a canonical match in the reference snapshot. They are visible audit warnings, not failed totals, missing candidates or missing divisions. No substitute canonical party was invented.

- Application: 1.7.0
- Database schema: 0.2.0 plus the additive Stage 14.5 pre-reform Senate tables
- Publication feed contract: 1.7.0
- Visualisation contract: 1.9.0
- Static-site format: 1.1.0

The prior 2025, 2022, 2019 and 2016 database releases and every earlier immutable release remain unchanged. Installation creates and activates a new combined five-election release.
