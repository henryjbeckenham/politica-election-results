# Project handover

## Transition status

The cumulative Stage 14.6 application source, version 1.8.0, has been admitted for publication in this repository. The source archive, packaged build evidence and packaged clean-integration evidence were reproduced from the supplied Stage 14.6 package.

The persistent large-artifact structure was created in Google Drive at:

https://drive.google.com/drive/folders/14NBzsGjVryTYE4r7fOE1gGbraT_cg2L5

The current-release folder contains the installed `active.json` and a 1,975,108,950-byte installed-release ZIP. The connector can list the ZIP but cannot download any file above 100 MiB, so its internal database and Parquet artifacts have not yet been independently read back.

## Proven Stage 14.6 package evidence

The following outer packages passed ZIP integrity checks and SHA-256 calculation:

- outer package SHA-256: `8f377aae8fb384787e59706be86d95b8de0db072f4697053f0f3319384c08f7e`;
- cumulative update SHA-256: `ba387c5840e57d20b7de4556258e055b33a217c040f5c8cb5c4777b64b7d90f7`;
- application version: `1.8.0`;
- Python regression report: 187 of 187 passed;
- browser report: 8 of 8 passed;
- clean integration: `PASS`;
- packaged release ID: `release_1_8_0_2010_6396e7b7fa807851`;
- packaged database SHA-256: `69779c47c1480eedbfe0a9ae844ff8037283e0e314377d11058706a3b4e7b752`;
- election coverage: 2025, 2022, 2019, 2016, 2013 and 2010.

Independent repository-safe checks also passed: Python compilation, credential scanning and 15 unit tests.

## Installed-release evidence

The Drive `active.json` identifies a separate installed build:

- release ID: `release_1_8_0_2010_33214168bb0d50da`;
- database SHA-256: `1b0524a99c6a0ad39ada424ff45dbc460bdbda65da62b1889855a8042502ac70`;
- installed release ZIP: `politica-stage14-6-33214168bb0d50da.zip`;
- Drive file size: 1,975,108,950 bytes.

This may be a valid later installation of the same version, but it cannot be equated with the packaged clean-integration build without reading its release manifest and database.

## Next permitted operation

Complete the installed-release readback in `governance/BASELINE_MIGRATION.md`. Do not commence a 2007 addition or any other post-Stage 14.6 database development until `CURRENT_STATE.json` records an accepted release ID and database checksum.
