# ChatGPT continuity

## Standard new-chat instruction

Use the following instruction inside the Election Results (Politica) ChatGPT Project:

```text
Continue development of the Politica Election Results Database using its persistent Project sources.

Do not rely on earlier conversational claims.

1. Read PROJECT_HOME.md, CURRENT_STATE.json and PROJECT_HANDOVER.md from the GitHub repository.
2. Identify the current default-branch commit.
3. Resolve only the required large artifacts through manifests/DATA_MANIFEST.json.
4. Verify that source version, release ID, database checksum and election coverage agree.
5. Report the governed starting position and any missing or contradictory evidence before modifying anything.
6. Never reconstruct all historical stage ZIPs unless a verified inconsistency requires forensic recovery.
7. Never modify an accepted predecessor database in place.
8. Update the handover and manifests with every accepted stage.
```

## Fresh-chat transition test

The transition is proven only when a new chat can, without historical ZIP uploads:

- identify the accepted source commit;
- identify the accepted release ID and application version;
- state the exact election coverage;
- locate the current database and its external dependencies;
- reproduce or verify their checksums;
- identify the latest validation report;
- run an appropriate read-only source check;
- identify the next permitted development operation.

