# Release notes 1.1.1

Release 1.1.1 is the Stage 11.1 corrective update.

## Corrected

- Senate contest names such as `New South Wales` and `Australian Capital Territory` are canonicalised to the fixed AEC state codes used by the public feeds.
- Senate group totals, declared Senators and participation rows now resolve consistently for ACT, NSW, NT, QLD, SA, TAS, VIC and WA.
- The browser has a defensive full-name fallback so older governed releases remain displayable even if a row has no state code.
- State-filtered feed queries continue to use bound parameters and remain read-only.

## Verification

- server tests exercise all three affected public feeds in every state and territory;
- browser tests reproduce the original null-state/full-contest-name failure;
- the existing Stage 4–11 regression suite remains mandatory during installation; and
- the installer verifies that `data/app/releases/active.json` is byte-for-byte unchanged.

Application version is 1.1.1. Database schema remains 0.2.0. Publication feed contract remains 1.0.0. No election release or Grand Database row is modified.
