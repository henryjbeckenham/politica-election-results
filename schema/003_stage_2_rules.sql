INSERT OR IGNORE INTO audit.validation_rule VALUES
('rule_2025_source_count', '2025 AEC source count', '1.2.0', 'blocker', 'provenance', 'source_file_revision', NULL, 'The governed 2025 AEC import contains all 45 registered source revisions.', TRUE),
('rule_2025_house_contests', '2025 House contest count', '1.0.0', 'blocker', 'core', 'contest', NULL, 'The 2025 federal House election contains 150 divisions.', TRUE),
('rule_2025_senate_contests', '2025 Senate contest count', '1.0.0', 'blocker', 'core', 'contest', NULL, 'The 2025 federal Senate election contains eight state or territory contests.', TRUE),
('rule_2025_house_candidates', '2025 House candidate count', '1.0.0', 'blocker', 'core', 'candidacy', NULL, 'The 2025 House candidate register contains 1,126 candidacies.', TRUE),
('rule_2025_senate_candidates', '2025 Senate candidate count', '1.0.0', 'blocker', 'core', 'candidacy', NULL, 'The 2025 Senate candidate register contains 330 candidacies.', TRUE),
('rule_2025_house_elected', '2025 House elected count', '1.0.0', 'blocker', 'results', 'contest_outcome', NULL, 'Exactly 150 House members are declared elected.', TRUE),
('rule_2025_senate_elected', '2025 Senate elected count', '1.0.0', 'blocker', 'results', 'contest_outcome', NULL, 'Exactly 40 senators are declared elected.', TRUE),
('rule_2025_senate_counts', '2025 Senate count candidate totals', '1.0.0', 'blocker', 'count', 'count_candidate_total', NULL, 'All 64,965 candidate observations in the official Senate distributions are loaded.', TRUE),
('rule_2025_house_formal_reconciliation', '2025 House formal reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'House candidate first-preference totals reconcile to formal votes in every division.', TRUE),
('rule_2025_senate_formal_reconciliation', '2025 Senate formal reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'Senate candidate and above-the-line first preferences reconcile to formal votes in every state and territory.', TRUE),
('rule_2025_participation_reconciliation', '2025 participation reconciliation', '1.0.0', 'blocker', 'results', 'participation_result', NULL, 'Formal plus informal votes reconcile to total votes for every contest.', TRUE),
('rule_2025_source_lineage', '2025 fact source lineage', '1.0.0', 'blocker', 'provenance', 'row_lineage', NULL, 'Every imported authoritative result, count and outcome has source-row lineage.', TRUE),
('rule_2025_no_duplicate_facts', '2025 no duplicate fact grains', '1.0.0', 'blocker', 'results', NULL, NULL, 'No imported fact natural grain appears more than once within a source revision.', TRUE),
('rule_2025_formal_preference_sources', '2025 Senate formal-preference source coverage', '1.0.0', 'blocker', 'ballot', 'ballot_dataset', NULL, 'All eight state and territory formal-preference archives are registered as ballot datasets.', TRUE),
('rule_2025_formal_ballot_count', '2025 Senate formal-ballot count', '1.0.0', 'blocker', 'ballot', 'ballot', NULL, 'All 15,871,189 formal Senate ballot papers are represented.', TRUE),
('rule_2025_formal_ballot_reconciliation', '2025 Senate formal-ballot state reconciliation', '1.0.0', 'blocker', 'ballot', 'ballot_dataset', NULL, 'Each formal-preference archive row count reconciles to the published formal vote total for its state or territory.', TRUE),
('rule_2025_formal_preference_path', '2025 Senate counted preference path validity', '1.0.0', 'blocker', 'ballot', 'ballot_preference', NULL, 'Each formal ballot has a reproducible counted preference path beginning at preference one.', TRUE),
('rule_2025_house_tpp_sources', '2025 House TPP source coverage', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'All state, division, division-by-vote-type and polling-place TPP facts are normalized from the four official files.', TRUE),
('rule_2025_house_tpp_formal_reconciliation', '2025 House TPP division reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'The two party totals reconcile to formal votes in every House division.', TRUE),
('rule_2025_house_tpp_vote_type_reconciliation', '2025 House TPP vote-type reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'The sum of official TPP vote types reconciles to each division and party total.', TRUE),
('rule_2025_house_tpp_polling_reconciliation', '2025 House TPP polling reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'Polling-place TPP party totals reconcile to polling-place formal first preferences.', TRUE),
('rule_2025_house_tpp_state_reconciliation', '2025 House TPP state reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'State TPP party totals reconcile to the sum of their division totals.', TRUE);

CREATE OR REPLACE MACRO control.uuid5_from_sha1(hash_value) AS (
    (
        substr(hash_value, 1, 12) || '5' || substr(hash_value, 14, 3) ||
        substr(
            '89ab',
            ((strpos('0123456789abcdef', substr(hash_value, 17, 1)) - 1) % 4) + 1,
            1
        ) ||
        substr(hash_value, 18, 15)
    )::UUID
);

CREATE OR REPLACE MACRO control.uuid5_name(name_value) AS (
    control.uuid5_from_sha1(
        sha1(
            unhex('908ec7be31c05ce7904222cb3e6c5d7e') ||
            encode(lower(trim(name_value)))
        )
    )
);
