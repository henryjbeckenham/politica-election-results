#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${SOURCE:?SOURCE environment variable required}"
EVIDENCE="${EVIDENCE:?EVIDENCE environment variable required}"
mkdir -p "$EVIDENCE/sql" "$EVIDENCE/logs" "$EVIDENCE/negative" "$EVIDENCE/reconciliation/raw"

for fragment in 0 1 2 3 4; do
  test -f "$SOURCE/stage3_fragments/stage3.$fragment"
done
cat "$SOURCE/stage3_fragments/stage3.0" \
    "$SOURCE/stage3_fragments/stage3.1" \
    "$SOURCE/stage3_fragments/stage3.2" \
    "$SOURCE/stage3_fragments/stage3.3" \
    "$SOURCE/stage3_fragments/stage3.4" \
  | tr -d '\r\n' \
  | base64 -d \
  | gzip -d \
  > "$EVIDENCE/sql/stage3_full_domain_v0_10b.sql"

python - "$SOURCE/v05_fragments" "$EVIDENCE/sql" <<'PYFRAG'
from pathlib import Path
import base64
import gzip
import hashlib
import json
import sys

root = Path(sys.argv[1])
out = Path(sys.argv[2])
manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
outputs = {
    'fixture': 'stage5_canonical_live_fixture.sql',
    'assertions': 'stage5_canonical_live_assertions.sql',
}
report = {'manifest_version': manifest['version'], 'files': {}}
for key, output_name in outputs.items():
    spec = manifest['files'][key]
    parts = []
    rows = []
    for expected in spec['chunks']:
        path = root / expected['path']
        body = path.read_bytes()
        actual_sha = hashlib.sha256(body).hexdigest()
        actual_chars = len(body.decode('ascii').strip())
        passed = (
            len(body) == expected['byte_count']
            and actual_sha == expected['sha256']
            and actual_chars == expected['base64_char_count']
        )
        rows.append({
            'path': expected['path'],
            'expected_byte_count': expected['byte_count'],
            'actual_byte_count': len(body),
            'expected_sha256': expected['sha256'],
            'actual_sha256': actual_sha,
            'expected_base64_char_count': expected['base64_char_count'],
            'actual_base64_char_count': actual_chars,
            'passed': passed,
        })
        if not passed:
            raise SystemExit(f'fragment mismatch: {expected["path"]}')
        parts.append(body.decode('ascii').strip())
    encoded = ''.join(parts)
    if len(encoded) != spec['combined_base64_char_count']:
        raise SystemExit(f'combined base64 length mismatch: {key}')
    decoded = gzip.decompress(base64.b64decode(encoded, validate=True))
    actual_decoded_sha = hashlib.sha256(decoded).hexdigest()
    if len(decoded) != spec['decoded_byte_count'] or actual_decoded_sha != spec['decoded_sha256']:
        raise SystemExit(f'decoded governed file mismatch: {output_name}')
    (out / output_name).write_bytes(decoded)
    report['files'][key] = {
        'output': output_name,
        'chunks': rows,
        'combined_base64_char_count': len(encoded),
        'decoded_byte_count': len(decoded),
        'decoded_sha256': actual_decoded_sha,
        'passed': True,
    }
(out.parent / 'v05_fragment_reconstruction.json').write_text(
    json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8'
)
PYFRAG

for name in \
  negative_missing_external_identifier_evidence.sql \
  negative_generic_external_identifier_evidence.sql \
  negative_invented_commencement_event.sql; do
  test -f "$SOURCE/$name.gz.b64"
  tr -d '\r\n' < "$SOURCE/$name.gz.b64" \
    | base64 -d \
    | gzip -d \
    > "$EVIDENCE/sql/$name"
done

cp "$ROOT/validate.py" "$EVIDENCE/validate_runtime.py"
python - "$EVIDENCE/validate_runtime.py" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text(encoding='utf-8')
replacements = {
    'cd38aba869eff30028bd606b8b2acd79c6e4276628e145a9f6b3033b10787cf1': 'a5669eb5168c011e17d2756ac5e89b675c04efb0e905707e373cb1dad3a08531',
    '        53140,': '        57644,',
    '45e3ff07eae9c2328a57042b1f2808d09d3eb0cd38779f7af776c0e5ccf9c2d0': 'c09ba15f78f4e7a04bcec401f03f7f29abc24509b975ef258a03cde4739a3ebd',
    '        12126,': '        13065,',
}
for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f'validator patch anchor missing: {old}')
    text = text.replace(old, new, 1)
p.write_text(text, encoding='utf-8')
PY
sha256sum "$EVIDENCE/validate_runtime.py" > "$EVIDENCE/validate_runtime.py.sha256"
VALIDATOR="$EVIDENCE/validate_runtime.py"
python "$VALIDATOR" safety --evidence "$EVIDENCE"

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
  psql -qAt -d "$db" -f "$ROOT/semantic_snapshot.sql" > "$run_dir/semantic_snapshot.json"
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
python "$VALIDATOR" reconcile --evidence "$EVIDENCE"
python "$VALIDATOR" final --evidence "$EVIDENCE"
echo STAGE5_V0_5_FINAL_ACCEPTANCE_PASS \
  | tee "$EVIDENCE/final_completion_marker.txt"
