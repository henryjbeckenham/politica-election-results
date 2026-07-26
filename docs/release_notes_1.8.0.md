# Release notes 1.8.0

Release 1.8.0 is Stage 14.6: complete 2010 federal election ingestion.

The release adds all 47 governed final AEC sources in the 2010 acquisition plan and publishes 2010 beside the unchanged 2025, 2022, 2019, 2016 and 2013 elections. The application, feeds, maps, analyses, downloads and static site follow the selected election. The 2010 House map uses the 150 official AEC divisions applicable to polling day.

The 2010 import reconciles 158 contests, 1,198 candidacies, 205,788 vote results, 1,896 participation results, 1,875 count rounds, 74,400 candidate count totals, 9,239 reported count movements and 190 outcomes. It preserves all 156 registered Senate group-voting tickets and their 9,048 candidate preference positions.

The AEC's eight published candidate-by-paper matrices contain 493,129 non-ticket below-the-line papers with 23,659,799 governed preferences. The official non-ticket aggregate is 493,142, leaving a source-availability gap of 13 paper records: 10 in New South Wales and one each in Victoria, Queensland and Tasmania. Politica records that exact gap and does not invent anonymous ballots. The 12,229,091 official ticket votes and 493,142 official non-ticket votes reconcile to 12,722,233 formal Senate votes; the paper-level representation covers 12,722,220 of those votes.

The 51 import warnings retain official 2010 party and group labels that do not have a canonical match in the reference snapshot. They are visible audit warnings, not failed totals, missing candidates or missing divisions. No substitute canonical party was invented.

- Application: 1.8.0
- Database schema: 0.2.0 plus the additive Stage 14.5 pre-reform Senate tables and Stage 14.6 validation contract
- Publication feed contract: 1.8.0
- Visualisation contract: 2.0.0
- Static-site format: 1.1.0

The prior 2025, 2022, 2019, 2016 and 2013 database releases and every earlier immutable release remain unchanged. Installation creates and activates a new combined six-election release.
