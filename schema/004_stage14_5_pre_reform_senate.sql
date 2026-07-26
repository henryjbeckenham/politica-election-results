CREATE TABLE IF NOT EXISTS ballot.group_voting_ticket (
    group_voting_ticket_id UUID PRIMARY KEY,
    contest_id VARCHAR NOT NULL,
    ballot_group_id UUID NOT NULL,
    ticket_number INTEGER NOT NULL,
    preference_count INTEGER NOT NULL,
    publication_status VARCHAR NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    record_status VARCHAR NOT NULL,
    UNIQUE (contest_id, ballot_group_id, ticket_number, source_revision_id),
    CHECK (ticket_number > 0),
    CHECK (preference_count > 0)
);

CREATE TABLE IF NOT EXISTS ballot.group_voting_ticket_preference (
    group_voting_ticket_preference_id UUID PRIMARY KEY,
    group_voting_ticket_id UUID NOT NULL,
    preference_rank INTEGER NOT NULL,
    candidacy_id UUID NOT NULL,
    source_marking VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    UNIQUE (group_voting_ticket_id, preference_rank),
    CHECK (preference_rank > 0)
);

INSERT OR IGNORE INTO audit.validation_rule VALUES
('rule_2013_source_count', '2013 AEC source count', '1.0.0', 'blocker', 'provenance', 'source_file_revision', NULL, 'The governed 2013 import contains all 47 official AEC source revisions.', TRUE),
('rule_2013_house_contests', '2013 House contest count', '1.0.0', 'blocker', 'core', 'contest', NULL, 'The 2013 House election contains 150 divisions with historical identity crosswalks.', TRUE),
('rule_2013_senate_contests', '2013 Senate contest count', '1.0.0', 'blocker', 'core', 'contest', NULL, 'The 2013 Senate election contains eight state and territory contests, including the void WA contest.', TRUE),
('rule_2013_house_candidates', '2013 House candidate count', '1.0.0', 'blocker', 'core', 'candidacy', NULL, 'The official 2013 House register contains 1,188 candidacies.', TRUE),
('rule_2013_senate_candidates', '2013 Senate candidate count', '1.0.0', 'blocker', 'core', 'candidacy', NULL, 'The official 2013 Senate register contains 529 candidacies.', TRUE),
('rule_2013_house_elected', '2013 House elected count', '1.0.0', 'blocker', 'results', 'contest_outcome', NULL, 'Exactly 150 House members are declared elected.', TRUE),
('rule_2013_senate_elected', '2013 Senate published outcome count', '1.0.0', 'blocker', 'results', 'contest_outcome', NULL, 'All 40 published Senate outcomes are retained, with the six WA outcomes marked as superseded after the contest was voided.', TRUE),
('rule_2013_senate_counts', '2013 Senate count totals', '1.0.0', 'blocker', 'count', 'count_candidate_total', NULL, 'All 122,305 uniquely mapped candidate totals from the official Senate distributions are loaded.', TRUE),
('rule_2013_house_formal_reconciliation', '2013 House formal reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'House first-preference totals reconcile to formal votes in every division.', TRUE),
('rule_2013_senate_formal_reconciliation', '2013 Senate formal reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'Senate candidate and group first preferences reconcile to formal votes in every state and territory.', TRUE),
('rule_2013_participation_reconciliation', '2013 participation reconciliation', '1.0.0', 'blocker', 'results', 'participation_result', NULL, 'Formal plus informal votes reconcile to total votes for every contest.', TRUE),
('rule_2013_source_lineage', '2013 fact source lineage', '1.0.0', 'blocker', 'provenance', 'row_lineage', NULL, 'Every imported authoritative result, count and outcome has exact source-row lineage.', TRUE),
('rule_2013_no_duplicate_facts', '2013 no duplicate fact grains', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'No imported fact natural grain appears twice within a source revision.', TRUE),
('rule_2013_house_tpp_sources', '2013 House TPP source coverage', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'All state, division and polling-place TPP facts are normalised from the official files.', TRUE),
('rule_2013_house_tpp_formal_reconciliation', '2013 House TPP division reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'The two party totals reconcile to formal votes in every House division.', TRUE),
('rule_2013_house_tpp_polling_reconciliation', '2013 House TPP polling reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'Polling-place TPP party totals reconcile to polling-place formal first preferences.', TRUE),
('rule_2013_house_tpp_state_reconciliation', '2013 House TPP state reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'State TPP party totals reconcile to the sum of their division totals.', TRUE),
('rule_2013_formal_preference_sources', '2013 BTL source coverage', '1.0.0', 'blocker', 'ballot', 'ballot_dataset', NULL, 'All eight official non-ticket below-the-line matrices are registered as ballot datasets.', TRUE),
('rule_2013_formal_ballot_count', '2013 non-ticket ballot count', '1.0.0', 'blocker', 'ballot', 'ballot', NULL, 'All 471,030 anonymous non-ticket formal Senate ballot papers are represented.', TRUE),
('rule_2013_formal_ballot_reconciliation', '2013 non-ticket ballot reconciliation', '1.0.0', 'blocker', 'ballot', 'ballot_dataset', NULL, 'Each BTL dataset row count reconciles to the official non-ticket total for its state or territory.', TRUE),
('rule_2013_formal_preference_path', '2013 BTL counted path validity', '1.0.0', 'blocker', 'ballot', 'ballot_preference', NULL, 'Every retained BTL ballot has a unique consecutive counted path beginning at preference one.', TRUE),
('rule_2013_group_voting_tickets', '2013 group voting ticket coverage', '1.0.0', 'blocker', 'ballot', 'group_voting_ticket', NULL, 'All 265 registered tickets and their 20,492 candidate preferences are normalised.', TRUE),
('rule_2013_group_voting_ticket_reconciliation', '2013 ticket-use reconciliation', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'Ticket votes plus non-ticket votes reconcile to formal Senate votes in every jurisdiction.', TRUE);
