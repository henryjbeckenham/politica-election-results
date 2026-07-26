# Validation rules

## Blocking classes

- archive integrity and safe extraction;
- source checksum and provenance completeness;
- schema and constraint equivalence;
- controlled-value conformance;
- primary-key uniqueness;
- foreign-key and relationship integrity;
- deterministic identifier stability;
- zero unexplained orphan records;
- election, contest and reporting-unit completeness;
- vote, participation, count and outcome reconciliation;
- row-lineage coverage;
- official-source coverage;
- regression preservation for earlier elections;
- immutable predecessor preservation;
- database and external-artifact checksum agreement;
- final read-only release verification.

## Engine representation differences

Database-engine serialization order is not semantic by itself. Comparison code may canonicalise unordered catalogue sets, such as complete constraint records, before comparison.

Canonicalisation must preserve the full content of every compared object. It must not drop constraints, columns, tables, views, macros or rows, and it must not turn a genuine semantic difference into a pass.

## Warnings

A warning is permissible only when:

- the affected record remains visible;
- no failed total or relationship is concealed;
- no replacement fact is invented;
- the warning has a stable identifier and documented resolution path;
- release policy explicitly classifies it as non-blocking.

