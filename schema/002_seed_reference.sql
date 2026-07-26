INSERT OR IGNORE INTO control.jurisdiction VALUES
('jurisdiction_aus_federal', 'fed', 'Commonwealth of Australia', 'federal', 'AUS', NULL, TRUE),
('jurisdiction_aus_nsw', 'nsw', 'New South Wales', 'state', 'AUS', 'jurisdiction_aus_federal', TRUE),
('jurisdiction_aus_vic', 'vic', 'Victoria', 'state', 'AUS', 'jurisdiction_aus_federal', TRUE),
('jurisdiction_aus_qld', 'qld', 'Queensland', 'state', 'AUS', 'jurisdiction_aus_federal', TRUE),
('jurisdiction_aus_wa', 'wa', 'Western Australia', 'state', 'AUS', 'jurisdiction_aus_federal', TRUE),
('jurisdiction_aus_sa', 'sa', 'South Australia', 'state', 'AUS', 'jurisdiction_aus_federal', TRUE),
('jurisdiction_aus_tas', 'tas', 'Tasmania', 'state', 'AUS', 'jurisdiction_aus_federal', TRUE),
('jurisdiction_aus_act', 'act', 'Australian Capital Territory', 'territory', 'AUS', 'jurisdiction_aus_federal', TRUE),
('jurisdiction_aus_nt', 'nt', 'Northern Territory', 'territory', 'AUS', 'jurisdiction_aus_federal', TRUE);

INSERT OR IGNORE INTO control.electoral_authority VALUES
('authority_aec', 'aec', 'Australian Electoral Commission', 'jurisdiction_aus_federal', 'https://www.aec.gov.au', TRUE),
('authority_nswec', 'nswec', 'New South Wales Electoral Commission', 'jurisdiction_aus_nsw', 'https://elections.nsw.gov.au', TRUE),
('authority_vec', 'vec', 'Victorian Electoral Commission', 'jurisdiction_aus_vic', 'https://www.vec.vic.gov.au', TRUE),
('authority_ecq', 'ecq', 'Electoral Commission of Queensland', 'jurisdiction_aus_qld', 'https://www.ecq.qld.gov.au', TRUE),
('authority_waec', 'waec', 'Western Australian Electoral Commission', 'jurisdiction_aus_wa', 'https://www.elections.wa.gov.au', TRUE),
('authority_ecsa', 'ecsa', 'Electoral Commission of South Australia', 'jurisdiction_aus_sa', 'https://www.ecsa.sa.gov.au', TRUE),
('authority_tec', 'tec', 'Tasmanian Electoral Commission', 'jurisdiction_aus_tas', 'https://www.tec.tas.gov.au', TRUE),
('authority_elections_act', 'elections_act', 'Elections ACT', 'jurisdiction_aus_act', 'https://www.elections.act.gov.au', TRUE),
('authority_ntec', 'ntec', 'Northern Territory Electoral Commission', 'jurisdiction_aus_nt', 'https://ntec.nt.gov.au', TRUE);

INSERT OR IGNORE INTO control.chamber VALUES
('chamber_house', 'house', 'House of Representatives or lower house', 'lower_house', 'parliamentary', TRUE),
('chamber_senate', 'senate', 'Senate or upper house', 'upper_house', 'parliamentary', TRUE),
('chamber_legislative_assembly', 'legislative_assembly', 'Legislative Assembly', 'lower_house', 'parliamentary', TRUE),
('chamber_legislative_council', 'legislative_council', 'Legislative Council', 'upper_house', 'parliamentary', TRUE),
('chamber_councillor', 'councillor', 'Local councillor', 'local_office', 'local_government', TRUE),
('chamber_mayor', 'mayor', 'Mayor or lord mayor', 'local_office', 'local_government', TRUE),
('chamber_referendum', 'referendum', 'Referendum or plebiscite question', 'ballot_question', 'referendum', TRUE);

INSERT OR IGNORE INTO control.election_type VALUES
('election_type_general', 'general', 'General election', 'Scheduled election for a chamber or government', TRUE),
('election_type_periodic', 'periodic', 'Periodic election', 'Scheduled partial election', TRUE),
('election_type_by_election', 'by_election', 'By-election', 'Election caused by a casual vacancy', TRUE),
('election_type_supplementary', 'supplementary', 'Supplementary election', 'Supplementary poll following a failed or void contest', TRUE),
('election_type_fresh', 'fresh', 'Fresh election', 'Fresh election ordered for a contest', TRUE),
('election_type_recount', 'recount', 'Recount', 'Official recount process', TRUE),
('election_type_countback', 'countback', 'Countback', 'Casual vacancy countback using existing ballots', TRUE),
('election_type_referendum', 'referendum', 'Referendum', 'Binding constitutional or statutory question', TRUE),
('election_type_plebiscite', 'plebiscite', 'Plebiscite', 'Official non-binding electoral question', TRUE);

INSERT OR IGNORE INTO audit.validation_rule VALUES
('rule_schema_no_orphan_foreign_keys', 'No orphan foreign keys', '1.0.0', 'blocker', NULL, NULL, NULL, 'All declared foreign keys must resolve.', TRUE),
('rule_source_revision_hash', 'Source revision hash format', '1.0.0', 'blocker', 'provenance', 'source_file_revision', NULL, 'Every source revision has a 64-character SHA-256.', TRUE),
('rule_missing_not_zero', 'Missing is not zero', '1.0.0', 'blocker', 'results', NULL, NULL, 'Missing values require value_status and cannot be silently converted to zero.', TRUE),
('rule_result_subject_exactly_one', 'Exactly one result subject', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'Every vote result resolves to exactly one typed subject.', TRUE),
('rule_formal_informal_turnout', 'Formal plus informal reconciles to turnout', '1.0.0', 'blocker', 'results', 'participation_result', NULL, 'Formal and informal totals reconcile to admitted turnout at the same grain.', TRUE),
('rule_candidate_totals_formal', 'Candidate totals reconcile to formal votes', '1.0.0', 'blocker', 'results', 'vote_result', NULL, 'Candidate first preferences reconcile to formal votes at contest and reporting-unit grain.', TRUE),
('rule_elected_count_vacancies', 'Elected count equals vacancies', '1.0.0', 'blocker', 'results', 'contest_outcome', NULL, 'Declared elected candidates equal contest vacancies unless explicitly excepted.', TRUE),
('rule_source_lineage_complete', 'Source lineage complete', '1.0.0', 'blocker', 'provenance', 'row_lineage', NULL, 'Every authoritative fact has an immutable source revision and precise locator.', TRUE),
('rule_current_revision_unique', 'One current source revision', '1.0.0', 'blocker', 'provenance', 'source_file_revision', NULL, 'Each logical source file has at most one active current revision.', TRUE),
('rule_unknown_source_labels_quarantined', 'Unknown source labels quarantined', '1.0.0', 'blocker', 'staging', 'source_record', NULL, 'Unknown authority labels cannot silently map to total or other.', TRUE);

