#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ROOT="${RUN_ROOT:-${RUNNER_TEMP:-/tmp}/stage5_v06}"
INPUT="$RUN_ROOT/input"
V05_WORK="$RUN_ROOT/v05"
EVIDENCE="$RUN_ROOT/evidence"
mkdir -p "$INPUT" "$V05_WORK" "$EVIDENCE"

V05_DRIVE_ID="${V05_DRIVE_ID:-1smCZOC03WqqQxsPAO4Sg4OYsX8F-FQq_}"
V05_SHA256="${V05_SHA256:-c0028534de83645ba102b9a211754203db1c333942458552fbc7032b7d4781e9}"
STAGE3_DDL_SHA256="${STAGE3_DDL_SHA256:-daab4b5f476d093fa975ebd93b72e941ab08e29b59519567ddddfc3caeb81fcc}"
V05_FIXTURE_SHA256="${V05_FIXTURE_SHA256:-d051ffaa3fb53686fe44a2d39d41df20a8fbe2281cd17cd5337fb03b7c3336ed}"
V05_ASSERTIONS_SHA256="${V05_ASSERTIONS_SHA256:-acc3abaf5247e3c2c59475f09d09a15baafec29626a2df32a1836b79eac822e7}"

python3 -m py_compile "$ROOT/reconcile.py" "$ROOT/reconcile_v2.py" "$ROOT/reconcile_v3.py"
python3 -m json.tool "$ROOT/reconciliation_config.json" >/dev/null
python3 "$ROOT/reconcile_v3.py" self-test \
  --config "$ROOT/reconciliation_config.json" \
  --output "$EVIDENCE/preflight_self_tests.json"

pg_isready -h "${PGHOST:-127.0.0.1}" -p "${PGPORT:-5432}" -U "${PGUSER:-postgres}"
version="$(psql -X -qAt -d postgres -c 'SHOW server_version_num')"
printf '%s\n' "$version" | tee "$EVIDENCE/postgresql_server_version_num.txt"
test "$version" = "180004"

V05_ZIP="$INPUT/politica_stage5_legislation_sync_v0_5.zip"
urls=(
  "https://drive.usercontent.google.com/download?id=${V05_DRIVE_ID}&export=download&confirm=t"
  "https://drive.google.com/uc?export=download&id=${V05_DRIVE_ID}"
)
success=0
for url in "${urls[@]}"; do
  if curl --fail --location --retry 3 --retry-all-errors --connect-timeout 30 --max-time 180 \
    "$url" -o "$V05_ZIP"; then
    if printf '%s  %s\n' "$V05_SHA256" "$V05_ZIP" | sha256sum --check --status; then
      success=1
      break
    fi
  fi
done
test "$success" = "1"
printf '%s  %s\n' "$V05_SHA256" "$V05_ZIP" | tee "$INPUT/v05_package.sha256"
unzip -t "$V05_ZIP" | tee "$INPUT/v05_unzip_test.log"
unzip -q "$V05_ZIP" -d "$V05_WORK"
V05_ROOT="$(find "$V05_WORK" -mindepth 1 -maxdepth 1 -type d -name 'politica_stage5_legislation_sync_v0_5*' | head -n 1)"
test -n "$V05_ROOT"
printf '%s\n' "$V05_ROOT" > "$INPUT/v05_root.txt"

mkdir -p "$EVIDENCE/v05_tests"
set +e
(
  cd "$V05_ROOT"
  PYTHONPATH="$V05_ROOT/src" python3 -m unittest discover -s tests -v
) >"$EVIDENCE/v05_tests/stdout.log" 2>"$EVIDENCE/v05_tests/stderr.log"
test_code=$?
set -e
printf '%s\n' "$test_code" > "$EVIDENCE/v05_tests/exit_code.txt"
test "$test_code" = "0"
grep -Eq 'Ran 70 tests' "$EVIDENCE/v05_tests/stderr.log"
grep -Eq '^OK$' "$EVIDENCE/v05_tests/stderr.log"

SQL_ROOT="$V05_ROOT/sql"
DDL="$SQL_ROOT/stage3_full_domain_v0_10b.sql"
FIXTURE="$SQL_ROOT/stage5_canonical_live_fixture.sql"
ASSERTIONS="$SQL_ROOT/stage5_canonical_live_assertions.sql"
test -f "$DDL" -a -f "$FIXTURE" -a -f "$ASSERTIONS"
printf '%s  %s\n' "$STAGE3_DDL_SHA256" "$DDL" | sha256sum --check
printf '%s  %s\n' "$V05_FIXTURE_SHA256" "$FIXTURE" | sha256sum --check
printf '%s  %s\n' "$V05_ASSERTIONS_SHA256" "$ASSERTIONS" | sha256sum --check
mkdir -p "$EVIDENCE/postgresql"

for db in stage5_v06_a stage5_v06_b; do
  dropdb --if-exists "$db"
  createdb "$db"
  mkdir -p "$EVIDENCE/postgresql/$db"
  for file in "$DDL" "$FIXTURE" "$ASSERTIONS"; do
    name="$(basename "$file")"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$EVIDENCE/postgresql/$db/${name}.started_at_utc.txt"
    set +e
    psql -X -d "$db" -v ON_ERROR_STOP=1 -f "$file" \
      >"$EVIDENCE/postgresql/$db/${name}.stdout.log" \
      2>"$EVIDENCE/postgresql/$db/${name}.stderr.log"
    code=$?
    set -e
    printf '%s\n' "$code" > "$EVIDENCE/postgresql/$db/${name}.exit_code.txt"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$EVIDENCE/postgresql/$db/${name}.completed_at_utc.txt"
    sha256sum "$file" > "$EVIDENCE/postgresql/$db/${name}.sha256"
    test "$code" = "0"
    test ! -s "$EVIDENCE/postgresql/$db/${name}.stderr.log"
  done
  grep -q 'STAGE5_CANONICAL_LIVE_POSTGRES_PASS' \
    "$EVIDENCE/postgresql/$db/stage5_canonical_live_assertions.sql.stdout.log"
done

psql -X -qAt -d stage5_v06_a -c \
  "SELECT external_identifier FROM bls_source.external_identifiers WHERE external_identifier IN ('F2016L01916','F2026C00596','O-000882') ORDER BY 1" \
  > "$EVIDENCE/postgresql/canonical_ids.txt"
test "$(wc -l < "$EVIDENCE/postgresql/canonical_ids.txt")" = "3"
psql -X -qAt -d stage5_v06_a -c \
  "SELECT count(*) FROM bls_source.external_identifier_evidence" \
  > "$EVIDENCE/postgresql/external_identifier_evidence_count.txt"
grep -qx '3' "$EVIDENCE/postgresql/external_identifier_evidence_count.txt"

if test -f "$SQL_ROOT/stage5_canonical_semantic_snapshot.sql"; then
  for db in stage5_v06_a stage5_v06_b; do
    psql -X -qAt -d "$db" -f "$SQL_ROOT/stage5_canonical_semantic_snapshot.sql" \
      > "$EVIDENCE/postgresql/$db/semantic_snapshot.json"
    sha256sum "$EVIDENCE/postgresql/$db/semantic_snapshot.json" \
      > "$EVIDENCE/postgresql/$db/semantic_snapshot.sha256"
  done
  cmp "$EVIDENCE/postgresql/stage5_v06_a/semantic_snapshot.json" \
      "$EVIDENCE/postgresql/stage5_v06_b/semantic_snapshot.json"
fi
echo STAGE5_V0_5_CANONICAL_INTEGRATION_COMPLETED | tee "$EVIDENCE/postgresql/completion_marker.txt"

cp "$V05_ZIP" "$EVIDENCE/"
cp "$INPUT/v05_package.sha256" "$EVIDENCE/"
cp "$INPUT/v05_unzip_test.log" "$EVIDENCE/"
python3 "$ROOT/reconcile_v3.py" live \
  --config "$ROOT/reconciliation_config.json" \
  --evidence "$EVIDENCE" \
  --package-root "$V05_ROOT" \
  --canonical-ids "$EVIDENCE/postgresql/canonical_ids.txt" \
  | tee "$EVIDENCE/full_reconciliation_stdout.log"

grep -q STAGE5_V0_6_FULL_RECONCILIATION_PASS "$EVIDENCE/full_reconciliation_stdout.log"
grep -q STAGE5_AC_019_PASS "$EVIDENCE/full_reconciliation_stdout.log"
grep -q STAGE5_AC_030_PASS "$EVIDENCE/full_reconciliation_stdout.log"
grep -q STAGE5_COMPLETED_STAGE6_AUTHORISED "$EVIDENCE/full_reconciliation_stdout.log"

python3 - "$EVIDENCE" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
result = json.loads((root / 'final_reconciliation_result.json').read_text())
summary = result['reconciliation']
assert result['status'] == 'passed'
assert result['s5_ac_019'] == 'passed'
assert summary['matched_identifier_count'] == 3
assert summary['canonical_only_identifier_count'] == 0
assert summary['missing_raw_evidence_count'] == 0
assert summary['invalid_specialised_identifier_evidence_count'] == 0
assert summary['conflicting_duplicate_source_identity_count'] == 0
assert summary['review_required_count'] == 0
acceptance = json.loads((root / 'stage5_final_acceptance_assessment_v0_6.json').read_text())
assert acceptance['criteria_passed'] == 30
assert acceptance['stage5_closed'] is True
PY

rm -rf "$EVIDENCE/working"
python3 "$ROOT/reconcile_v3.py" package \
  --evidence "$EVIDENCE" \
  --source-root "$ROOT" \
  --package-root "$V05_ROOT" \
  --baseline-zip "$V05_ZIP" \
  --final "$EVIDENCE/final" \
  | tee "$EVIDENCE/deterministic_packaging_stdout.log"
grep -q STAGE5_V0_6_DETERMINISTIC_PACKAGING_PASS "$EVIDENCE/deterministic_packaging_stdout.log"
unzip -t "$EVIDENCE/final/politica_stage5_legislation_sync_v0_6.zip" \
  | tee "$EVIDENCE/final/v06_package_unzip_test.log"
unzip -t "$EVIDENCE/final/politica_stage5_v0_6_full_reconciliation_evidence.zip" \
  | tee "$EVIDENCE/final/v06_evidence_unzip_test.log"

echo STAGE5_V0_6_WORKFLOW_PASS
