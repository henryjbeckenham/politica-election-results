# Project handover

## Transition status

The GitHub continuity framework was created on 25 July 2026.

This repository is intentionally not marked as the verified Stage 14.6 source yet. The available migration packages prove the cumulative application through Stage 14.4, application 1.6.0. They do not contain the Stage 14.5 or Stage 14.6 cumulative update payloads needed to reproduce the governed 1.8.0 continuation point.

The persistent large-artifact structure was created in Google Drive at:

https://drive.google.com/drive/folders/14NBzsGjVryTYE4r7fOE1gGbraT_cg2L5

The folder structure is present and verified, but contains no admitted Stage 14.6 artifacts yet. The Stage 14.1 historical inventory archive has been transferred and read back from its archive folder. The supplied Stage 14.2, Stage 14.3 and Stage 14.4 packages are larger than the connected Drive transfer limit and remain to be placed in their prepared folders through Google Drive for Desktop or the Drive web interface.

## Proven migration evidence

The following outer packages passed ZIP integrity checks and SHA-256 calculation:

| Stage | Application | Outer package SHA-256 | Evidence |
|---|---:|---|---|
| 14.1 | Historical inventory | `02d5fbdcf481ec0fe68cb6d50046da158cda1d8d8501a4a6a7f9211882214837` | Source inventory, acquisition plan, test and integration reports |
| 14.2 | 1.4.0 | `7b37e3b89aa2090764edf78d5a23daf684972e31208c211877ca4b01cd7c71c4` | Cumulative update plus four 2022 data volumes |
| 14.3 | 1.5.0 | `1a1bb59b601a559d20d7d169de9c309daed9a1981100dc4ad7eb29f32e32366b` | Cumulative update plus four 2019 data volumes |
| 14.4 | 1.6.0 | `b79eb699fbed958857fe3f812c494a32f0e9ff1ba2623eecb0150ba448840e03` | Cumulative update plus four 2016 data volumes |

The Stage 14.4 cumulative update is 90,181,679 bytes and has SHA-256 `94258961e16e090fbef35001c832039bceed8c153d24ccdaf294ef6a78ec430d`.

Its packaged reports state:

- build status: `PASS`;
- application version: `1.6.0`;
- 170 of 170 Python tests passed after the documented fixture correction;
- 8 of 8 browser tests passed;
- clean integration status: `PASS`;
- active release ID: `release_1_6_0_2016_098dc5fbcafd7c4f`;
- active database SHA-256: `a4d7a18bbb38aa534951adf048ae7182ca2b9530eabf499de156a8dc221fc6c3`;
- election coverage: 2025, 2022, 2019 and 2016.

These are verified package claims. They do not prove the later 1.8.0 release.

## Required migration inputs

The next baseline-admission operation requires:

1. `Politica_Stage14_6_v1.8.0_Update.zip`, extracted from the large consolidated Stage 14.6 package if necessary.
2. The current `active.json`.
3. The Stage 14.6 build, test, integration and validation reports.
4. The exact current database and external Parquet artifacts, stored outside GitHub.

If the Stage 14.6 update is not cumulative, the Stage 14.5 update must also be supplied. This must be determined by inspecting the archive, not assumed.

## Next permitted operation

Run the baseline-admission procedure in `docs/BASELINE_MIGRATION.md`. Do not commence a 2007 addition or any other post-Stage 14.6 development until the admission status in `CURRENT_STATE.json` becomes `accepted`.
