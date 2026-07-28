# Politica Stage 5 v0.6 full reconciliation

This diagnostic-only validation tranche completes the source-wide partitioned Federal Register identifier-set reconciliation required by Stage 5 Workstream 5.7 and S5-AC-019.

## Governed inputs

- Accepted Stage 5 v0.5 package Drive file: `1smCZOC03WqqQxsPAO4Sg4OYsX8F-FQq_`
- Required v0.5 package SHA-256: `c0028534de83645ba102b9a211754203db1c333942458552fbc7032b7d4781e9`
- Accepted Stage 3 database baseline: full-domain v0.10b on PostgreSQL 18.4, server version number `180004`

The workflow downloads the exact v0.5 package, verifies its checksum before extraction, reruns its deterministic tests and applies its accepted Stage 3 DDL, canonical fixture and assertions before reconciliation.

## Reconciliation scope

The source-wide identifier and observation sets cover:

- Titles
- Versions
- Documents
- Departments
- TextApplies

The API enforces a maximum `$top` of 500. The reconciler follows sequential OData continuation links, records page and partition completion, retains every exact response body and header set, and performs start-and-end count checks.

Versions with a null `registerId`, Documents and TextApplies use deterministic compound source-observation identities. Their source values are not converted into invented external identifiers.

## Safety boundaries

- Only GET and HEAD are permitted.
- Only `api.prod.legislation.gov.au` is approved.
- GET request bodies are prohibited.
- `$expand=Documents` on Versions is prohibited.
- Concurrency is fixed at one.
- Requests, response sizes, retries and total traversal are bounded.
- Production and ambiguous database targets are rejected.
- Apparent source disappearance never deletes or deactivates history.
- A single absence or any absence from a partial run cannot satisfy the disappearance threshold.
- Commencement-related signal fields do not create commencement events.

## Interpretation of source-only differences

The accepted Stage 5 v0.5 canonical database is deliberately bounded to representative records. Source observations outside that canonical baseline are therefore retained as governed future ingestion candidates. They are not force-created as incomplete canonical records merely to reduce the reconciliation difference count.

## Completion markers

A successful run produces:

```text
STAGE5_V0_6_FULL_RECONCILIATION_PASS
STAGE5_AC_019_PASS
STAGE5_AC_030_PASS
STAGE5_COMPLETED_STAGE6_AUTHORISED
STAGE5_V0_6_DETERMINISTIC_PACKAGING_PASS
```

Stage 6 is authorised only after the final audit passes. This workflow does not commence Stage 6, deploy a production database, edit the Politica Grand Database or create a public interface.
