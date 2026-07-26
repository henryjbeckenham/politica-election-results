-- Run from the project root against:
-- data/database/politica_election_results.duckdb

-- 1. Latest validation status.
SELECT validation_status, rules_executed, blocker_count, warning_count, completed_at
FROM audit.validation_run
ORDER BY completed_at DESC NULLS LAST
LIMIT 1;

-- 2. House TPP totals by division and party.
SELECT c.contest_name,
       p.short_name AS party,
       max(v.integer_value) FILTER (WHERE v.measure_type='votes') AS votes,
       max(v.decimal_value) FILTER (WHERE v.measure_type='vote_share') AS vote_share,
       max(v.decimal_value) FILTER (WHERE v.measure_type='swing') AS swing
FROM results.vote_result v
JOIN core.contest c USING (contest_id)
JOIN sync.party p USING (party_id)
WHERE v.result_type='tpp'
  AND v.vote_type='total'
  AND v.election_reporting_unit_id IS NULL
GROUP BY c.contest_name, p.short_name
ORDER BY c.contest_name, p.short_name;

-- 3. House TPP state and territory totals.
SELECT u.official_label AS state_or_territory,
       p.short_name AS party,
       max(v.integer_value) FILTER (WHERE v.measure_type='votes') AS votes,
       max(v.decimal_value) FILTER (WHERE v.measure_type='vote_share') AS vote_share,
       max(v.decimal_value) FILTER (WHERE v.measure_type='swing') AS swing
FROM results.vote_result v
JOIN geography.election_reporting_unit u USING (election_reporting_unit_id)
JOIN sync.party p USING (party_id)
WHERE v.result_type='tpp'
  AND v.contest_id IS NULL
  AND u.reporting_unit_type='state_total'
GROUP BY u.official_label, p.short_name
ORDER BY u.official_label, p.short_name;

-- 4. Declared winners.
SELECT c.contest_name,
       ca.ballot_name AS elected_candidate,
       coalesce(p.short_name, ca.official_party_name) AS party,
       o.elected_order
FROM results.contest_outcome o
JOIN core.contest c USING (contest_id)
JOIN core.candidacy ca USING (candidacy_id)
LEFT JOIN sync.party p USING (party_id)
WHERE o.outcome_type='elected'
ORDER BY c.contest_name, o.elected_order;

-- 5. Formal Senate ballots by state or territory.
SELECT c.contest_name,
       count(*) AS formal_ballots,
       sum(b.preference_count) AS counted_preference_positions,
       count(*) FILTER (WHERE b.above_the_line) AS above_the_line,
       count(*) FILTER (WHERE NOT b.above_the_line) AS below_the_line
FROM ballot.ballot b
JOIN core.contest c USING (contest_id)
GROUP BY c.contest_name
ORDER BY c.contest_name;

-- 6. Open canonical party-label mapping warnings.
SELECT observed_value, issue_message, expected_value, resolution_status
FROM audit.validation_issue
WHERE severity='warning' AND resolution_status='open'
ORDER BY observed_value;

-- 7. Approved publication snapshot and governed source cutoff.
SELECT s.publication_snapshot_id, s.snapshot_name, s.approval_status,
       s.approved_at, count(r.source_revision_id) AS source_revisions,
       s.snapshot_hash
FROM publish.publication_snapshot s
JOIN publish.publication_snapshot_source_revision r USING (publication_snapshot_id)
GROUP BY s.publication_snapshot_id, s.snapshot_name, s.approval_status,
         s.approved_at, s.snapshot_hash;

