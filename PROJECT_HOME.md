# Project home

## Persistent locations

- GitHub repository: https://github.com/henryjbeckenham/politica-election-results
- Large-artifact store: https://drive.google.com/drive/folders/14NBzsGjVryTYE4r7fOE1gGbraT_cg2L5
- ChatGPT Project: Election Results (Politica)

## Current governed position

Read `CURRENT_STATE.json`. Do not infer the current release from a filename, conversation or historical package.

The intended continuation point is Stage 14.6, application 1.8.0, with federal election coverage for 2025, 2022, 2019, 2016, 2013 and 2010. The repository must not claim that release as accepted until the admission checks in `docs/BASELINE_MIGRATION.md` pass.

## Startup procedure

1. Read `CURRENT_STATE.json`.
2. Read `PROJECT_HANDOVER.md`.
3. Inspect the current default-branch commit.
4. Resolve each required external artifact through `manifests/DATA_MANIFEST.json`.
5. Confirm that the source version, release identity, database checksum and election coverage agree.
6. Report any missing or contradictory evidence before making changes.
7. Retrieve only the data required for the current task.

## Safety rules

- Never edit an accepted predecessor database.
- Never overwrite an immutable release.
- Never activate a candidate before every blocking check passes.
- Never commit credentials or large runtime data.
- Never use a conversational claim as release evidence.
- Update the handover, manifests and current-state record with every accepted stage.
