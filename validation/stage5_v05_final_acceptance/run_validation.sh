#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${SOURCE:?SOURCE environment variable required}"
EVIDENCE="${EVIDENCE:?EVIDENCE environment variable required}"
mkdir -p "$EVIDENCE/sql" "$EVIDENCE/logs" "$EVIDENCE/negative" "$EVIDENCE/reconciliation/raw"

for name in \
  stage3_full_domain_v0_10b.sql \
  stage5_canonical_live_fixture.sql \
  stage5_canonical_live_assertions.sql \
  negative_missing_external_identifier_evidence.sql \
  negative_generic_external_identifier_evidence.sql \
  negative_invented_commencement_event.sql; do
  test -f "$SOURCE/$name.gz.b64"
  base64 -d "$SOURCE/$name.gz.b64" | gzip -d > "$EVIDENCE/sql/$name"
done

python "$ROOT/validate.py" safety --evidence "$EVIDENCE"

version="$(psql -Atc 'SHOW server_version_num')"
printf '%s\n' "$version" | tee "$EVIDENCE/server_version_num.txt"
test "$version" = "180004"

positive_sql=(
  stage3_full_domain_v0_10b.sql
  stage5_canonical_live_fixture.sql
  stage5_canonical_live_assertions.sql
)

for db in stage5_v05_a stage5_v05_b; do
  dropdb --if-exists "$db"
  createdb "$db"
  run_dir="$EVIDENCE/$db"
  mkdir -p "$run_dir/logs"
  for sql_name in "${positive_sql[@]}"; do
    date -u +%Y-%m-%dT%H:%M:%SZ > "$run_dir/logs/$sql_name.started_at_utc.txt"
    set +e
    (cd "$EVIDENCE/sql" && psql -d "$db" -v ON_ERROR_STOP=1 -f "$sql_name") \
      > "$run_dir/logs/$sql_name.stdout.log" \
      2> "$run_dir/logs/$sql_name.stderr.log"
    code=$?
    set -e
    printf '%s\n' "$code" > "$run_dir/logs/$sql_name.exit_code.txt"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$run_dir/logs/$sql_name.completed_at_utc.txt"
    sha256sum "$EVIDENCE/sql/$sql_name" > "$run_dir/logs/$sql_name.sha256"
    if test "$code" -ne 0; then
      cat "$run_dir/logs/$sql_name.stderr.log" >&2
      exit "$code"
    fi
  done
  grep -q STAGE5_CANONICAL_LIVE_POSTGRES_PASS \
    "$run_dir/logs/stage5_canonical_live_assertions.sql.stdout.log"
  psql -d "$db" -f "$ROOT/semantic_snapshot.sql" > "$run_dir/semantic_snapshot.json"
  sha256sum "$run_dir/semantic_snapshot.json" > "$run_dir/semantic_snapshot.sha256"
done

cmp "$EVIDENCE/stage5_v05_a/semantic_snapshot.json" \
    "$EVIDENCE/stage5_v05_b/semantic_snapshot.json"
cp "$EVIDENCE/stage5_v05_a/semantic_snapshot.json" \
   "$EVIDENCE/canonical_semantic_snapshot.json"
sha256sum "$EVIDENCE/canonical_semantic_snapshot.json" \
  > "$EVIDENCE/canonical_semantic_snapshot.sha256"
echo STAGE5_V0_5_CANONICAL_INTEGRATION_COMPLETED \
  | tee "$EVIDENCE/completion_marker.txt"

for sql_name in \
  negative_missing_external_identifier_evidence.sql \
  negative_generic_external_identifier_evidence.sql \
  negative_invented_commencement_event.sql; do
  set +e
  (cd "$EVIDENCE/sql" && psql -d stage5_v05_a -v ON_ERROR_STOP=1 -f "$sql_name") \
    > "$EVIDENCE/negative/$sql_name.stdout.log" \
    2> "$EVIDENCE/negative/$sql_name.stderr.log"
  code=$?
  set -e
  printf '%s\n' "$code" > "$EVIDENCE/negative/$sql_name.exit_code.txt"
  test "$code" -ne 0
done

python - "$EVIDENCE/negative" <<'PY'
from pathlib import Path
import json, sys
root = Path(sys.argv[1])
rows = {
    path.name.removesuffix('.exit_code.txt'): int(path.read_text().strip())
    for path in sorted(root.glob('*.exit_code.txt'))
}
result = {
    'status': 'passed' if rows and all(code != 0 for code in rows.values()) else 'failed',
    'exit_codes': rows,
}
(root / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
if result['status'] != 'passed':
    raise SystemExit(1)
PY

psql -d stage5_v05_a -Atc \
  "SELECT external_identifier FROM bls_source.external_identifiers WHERE external_identifier IN ('F2016L01916','F2026C00596','O-000882') ORDER BY 1" \
  > "$EVIDENCE/reconciliation/canonical_ids.txt"
python "$ROOT/validate.py" reconcile --evidence "$EVIDENCE"
python "$ROOT/validate.py" final --evidence "$EVIDENCE"
echo STAGE5_V0_5_FINAL_ACCEPTANCE_PASS \
  | tee "$EVIDENCE/final_completion_marker.txt"
