CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS sync;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS geography;
CREATE SCHEMA IF NOT EXISTS results;
CREATE SCHEMA IF NOT EXISTS "count";
CREATE SCHEMA IF NOT EXISTS ballot;
CREATE SCHEMA IF NOT EXISTS provenance;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS derived;
CREATE SCHEMA IF NOT EXISTS publish;
CREATE SCHEMA IF NOT EXISTS staging;

CREATE TABLE IF NOT EXISTS control.schema_version (
    schema_version VARCHAR PRIMARY KEY,
    migration_id VARCHAR NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL,
    checksum_sha256 VARCHAR NOT NULL CHECK (length(checksum_sha256) = 64),
    backward_compatible BOOLEAN NOT NULL,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS control.database_release (
    release_id VARCHAR PRIMARY KEY,
    schema_version VARCHAR NOT NULL,
    release_status VARCHAR NOT NULL,
    release_started_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    created_by VARCHAR NOT NULL,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS control.data_dictionary (
    table_schema VARCHAR NOT NULL,
    table_name VARCHAR NOT NULL,
    field_name VARCHAR NOT NULL,
    field_order INTEGER NOT NULL,
    field_type VARCHAR NOT NULL,
    required BOOLEAN NOT NULL,
    primary_key BOOLEAN NOT NULL DEFAULT FALSE,
    foreign_key BOOLEAN NOT NULL DEFAULT FALSE,
    linked_schema VARCHAR,
    linked_table VARCHAR,
    linked_field VARCHAR,
    value_set_name VARCHAR,
    definition VARCHAR,
    null_semantics VARCHAR,
    validation_rule VARCHAR,
    introduced_version VARCHAR NOT NULL,
    deprecated_version VARCHAR,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (table_schema, table_name, field_name)
);

CREATE TABLE IF NOT EXISTS control.controlled_value (
    value_set_name VARCHAR NOT NULL,
    value_code VARCHAR NOT NULL,
    display_name VARCHAR NOT NULL,
    description VARCHAR,
    sort_order INTEGER NOT NULL,
    valid_from DATE,
    valid_to DATE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (value_set_name, value_code),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS control.relationship_contract (
    relationship_id VARCHAR PRIMARY KEY,
    child_schema VARCHAR NOT NULL,
    child_table VARCHAR NOT NULL,
    child_field VARCHAR NOT NULL,
    parent_schema VARCHAR NOT NULL,
    parent_table VARCHAR NOT NULL,
    parent_field VARCHAR NOT NULL,
    required BOOLEAN NOT NULL,
    relationship_kind VARCHAR NOT NULL DEFAULT 'logical_foreign_key',
    introduced_version VARCHAR NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (child_schema, child_table, child_field)
);

CREATE TABLE IF NOT EXISTS control.jurisdiction (
    jurisdiction_id VARCHAR PRIMARY KEY,
    jurisdiction_code VARCHAR NOT NULL UNIQUE,
    jurisdiction_name VARCHAR NOT NULL,
    jurisdiction_level VARCHAR NOT NULL,
    country_code VARCHAR NOT NULL,
    parent_jurisdiction_id VARCHAR,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS control.electoral_authority (
    authority_id VARCHAR PRIMARY KEY,
    authority_code VARCHAR NOT NULL UNIQUE,
    authority_name VARCHAR NOT NULL,
    jurisdiction_id VARCHAR NOT NULL,
    website_url VARCHAR,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS control.chamber (
    chamber_id VARCHAR PRIMARY KEY,
    chamber_code VARCHAR NOT NULL UNIQUE,
    chamber_name VARCHAR NOT NULL,
    chamber_type VARCHAR NOT NULL,
    jurisdiction_scope VARCHAR NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS control.election_type (
    election_type_id VARCHAR PRIMARY KEY,
    election_type_code VARCHAR NOT NULL UNIQUE,
    election_type_name VARCHAR NOT NULL,
    description VARCHAR,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS control.electoral_system_version (
    electoral_system_version_id VARCHAR PRIMARY KEY,
    system_code VARCHAR NOT NULL,
    system_name VARCHAR NOT NULL,
    version_label VARCHAR NOT NULL,
    jurisdiction_id VARCHAR,
    valid_from DATE,
    valid_to DATE,
    seats_per_contest INTEGER,
    preference_expression VARCHAR,
    quota_method VARCHAR,
    transfer_method VARCHAR,
    tie_rule VARCHAR,
    source_revision_id VARCHAR,
    notes VARCHAR,
    UNIQUE (system_code, version_label),
    CHECK (seats_per_contest IS NULL OR seats_per_contest > 0),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS control.adapter_registry (
    adapter_id VARCHAR PRIMARY KEY,
    authority_id VARCHAR NOT NULL,
    adapter_version VARCHAR NOT NULL,
    config_path VARCHAR NOT NULL,
    code_version VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    schema_signature_sha256 VARCHAR,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (adapter_id, adapter_version)
);

CREATE TABLE IF NOT EXISTS sync.person (
    person_id VARCHAR PRIMARY KEY,
    full_name VARCHAR NOT NULL,
    display_name VARCHAR,
    given_names VARCHAR,
    family_name VARCHAR,
    aliases VARCHAR,
    date_of_birth DATE,
    country VARCHAR,
    active BOOLEAN,
    record_status VARCHAR,
    audit_status VARCHAR,
    source_row_hash VARCHAR NOT NULL,
    grand_synced_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sync.party (
    party_id VARCHAR PRIMARY KEY,
    party_name VARCHAR NOT NULL,
    short_name VARCHAR,
    abbreviation VARCHAR,
    aliases VARCHAR,
    party_family VARCHAR,
    colour_hex VARCHAR,
    jurisdiction VARCHAR,
    country VARCHAR,
    active BOOLEAN,
    valid_from DATE,
    valid_to DATE,
    record_status VARCHAR,
    audit_status VARCHAR,
    source_row_hash VARCHAR NOT NULL,
    grand_synced_at TIMESTAMPTZ NOT NULL,
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS sync.constituency (
    constituency_id VARCHAR PRIMARY KEY,
    constituency_name VARCHAR NOT NULL,
    constituency_type VARCHAR NOT NULL,
    jurisdiction VARCHAR NOT NULL,
    chamber VARCHAR,
    state_territory VARCHAR,
    country VARCHAR,
    election_context VARCHAR,
    boundary_version VARCHAR,
    valid_from DATE,
    valid_to DATE,
    parent_constituency_id VARCHAR,
    aliases VARCHAR,
    legacy_group_id VARCHAR,
    source_id VARCHAR,
    source_locator VARCHAR,
    evidence_status VARCHAR,
    record_status VARCHAR,
    audit_status VARCHAR,
    audited_at TIMESTAMPTZ,
    audited_by VARCHAR,
    superseded_by_constituency_id VARCHAR,
    notes VARCHAR,
    official_constituency_code VARCHAR,
    official_code_status VARCHAR,
    source_row_hash VARCHAR NOT NULL,
    grand_synced_at TIMESTAMPTZ NOT NULL,
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS sync.external_identifier (
    external_identifier_id VARCHAR PRIMARY KEY,
    entity_type VARCHAR NOT NULL,
    canonical_id VARCHAR NOT NULL,
    authority_id VARCHAR NOT NULL,
    external_id_type VARCHAR NOT NULL,
    external_id_value VARCHAR NOT NULL,
    valid_from DATE,
    valid_to DATE,
    source_revision_id VARCHAR,
    source_locator VARCHAR,
    match_status VARCHAR NOT NULL,
    record_status VARCHAR NOT NULL,
    UNIQUE (entity_type, authority_id, external_id_type, external_id_value, valid_from),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS provenance.source_landing_page (
    source_landing_page_id VARCHAR PRIMARY KEY,
    authority_id VARCHAR NOT NULL,
    election_id VARCHAR,
    page_title VARCHAR,
    landing_url VARCHAR NOT NULL,
    archive_url VARCHAR,
    first_accessed_at TIMESTAMPTZ,
    last_verified_at TIMESTAMPTZ,
    availability_status VARCHAR NOT NULL,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS provenance.source_file (
    source_file_id VARCHAR PRIMARY KEY,
    authority_id VARCHAR NOT NULL,
    election_id VARCHAR,
    source_landing_page_id VARCHAR,
    dataset_title VARCHAR NOT NULL,
    dataset_family VARCHAR NOT NULL,
    chamber_code VARCHAR,
    geographic_scope VARCHAR,
    logical_status VARCHAR NOT NULL,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS provenance.source_file_revision (
    source_revision_id VARCHAR PRIMARY KEY,
    source_file_id VARCHAR NOT NULL,
    revision_number INTEGER NOT NULL,
    source_url VARCHAR,
    original_filename VARCHAR NOT NULL,
    archive_path VARCHAR NOT NULL,
    mime_type VARCHAR,
    encoding VARCHAR,
    delimiter VARCHAR,
    compression_type VARCHAR,
    file_size_bytes BIGINT NOT NULL CHECK (file_size_bytes >= 0),
    row_count BIGINT CHECK (row_count IS NULL OR row_count >= 0),
    sha256 VARCHAR NOT NULL CHECK (length(sha256) = 64),
    source_publication_at TIMESTAMPTZ,
    downloaded_at TIMESTAMPTZ NOT NULL,
    publication_status VARCHAR NOT NULL,
    schema_signature_sha256 VARCHAR,
    supersedes_source_revision_id VARCHAR,
    record_status VARCHAR NOT NULL,
    UNIQUE (source_file_id, revision_number),
    UNIQUE (source_file_id, sha256)
);

CREATE TABLE IF NOT EXISTS core.election (
    election_id VARCHAR PRIMARY KEY,
    official_event_id VARCHAR,
    election_name VARCHAR NOT NULL,
    election_date DATE NOT NULL,
    election_year INTEGER NOT NULL,
    jurisdiction_id VARCHAR NOT NULL,
    authority_id VARCHAR NOT NULL,
    election_type_id VARCHAR NOT NULL,
    publication_status VARCHAR NOT NULL,
    contest_status VARCHAR NOT NULL,
    parent_election_id VARCHAR,
    record_status VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (authority_id, official_event_id),
    CHECK (election_year = year(election_date))
);

CREATE TABLE IF NOT EXISTS core.election_relationship (
    election_relationship_id VARCHAR PRIMARY KEY,
    parent_election_id VARCHAR NOT NULL,
    child_election_id VARCHAR NOT NULL,
    relationship_type VARCHAR NOT NULL,
    notes VARCHAR,
    UNIQUE (parent_election_id, child_election_id, relationship_type),
    CHECK (parent_election_id <> child_election_id)
);

CREATE TABLE IF NOT EXISTS core.election_chamber (
    election_chamber_id VARCHAR PRIMARY KEY,
    election_id VARCHAR NOT NULL,
    chamber_id VARCHAR NOT NULL,
    electoral_system_version_id VARCHAR,
    vacancies INTEGER,
    whole_chamber BOOLEAN,
    publication_status VARCHAR NOT NULL,
    record_status VARCHAR NOT NULL,
    UNIQUE (election_id, chamber_id),
    CHECK (vacancies IS NULL OR vacancies >= 0)
);

CREATE TABLE IF NOT EXISTS core.election_key_date (
    election_key_date_id VARCHAR PRIMARY KEY,
    election_id VARCHAR NOT NULL,
    date_type VARCHAR NOT NULL,
    date_value DATE NOT NULL,
    date_status VARCHAR NOT NULL,
    source_revision_id VARCHAR,
    UNIQUE (election_id, date_type, date_value)
);

CREATE TABLE IF NOT EXISTS geography.boundary_version (
    boundary_version_id VARCHAR PRIMARY KEY,
    authority_id VARCHAR NOT NULL,
    boundary_name VARCHAR NOT NULL,
    effective_from DATE,
    effective_to DATE,
    crs VARCHAR,
    geometry_format VARCHAR,
    licence VARCHAR,
    source_revision_id VARCHAR,
    record_status VARCHAR NOT NULL,
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);

CREATE TABLE IF NOT EXISTS core.contest (
    contest_id VARCHAR PRIMARY KEY,
    election_chamber_id VARCHAR NOT NULL,
    canonical_constituency_id VARCHAR,
    official_contest_id VARCHAR,
    contest_name VARCHAR NOT NULL,
    vacancies INTEGER NOT NULL,
    electoral_system_version_id VARCHAR,
    contest_status VARCHAR NOT NULL,
    uncontested BOOLEAN NOT NULL DEFAULT FALSE,
    recount_status VARCHAR,
    publication_status VARCHAR NOT NULL,
    record_status VARCHAR NOT NULL,
    UNIQUE (election_chamber_id, official_contest_id),
    CHECK (vacancies >= 0)
);

CREATE TABLE IF NOT EXISTS core.contest_constituency_snapshot (
    contest_constituency_snapshot_id VARCHAR PRIMARY KEY,
    contest_id VARCHAR NOT NULL UNIQUE,
    canonical_constituency_id VARCHAR,
    official_constituency_code VARCHAR,
    official_constituency_name VARCHAR NOT NULL,
    constituency_type VARCHAR,
    boundary_version_id VARCHAR,
    enrolment BIGINT CHECK (enrolment IS NULL OR enrolment >= 0),
    source_revision_id VARCHAR,
    source_locator VARCHAR,
    match_status VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS core.candidacy (
    candidacy_id UUID PRIMARY KEY,
    contest_id VARCHAR NOT NULL,
    person_id VARCHAR,
    party_id VARCHAR,
    official_candidate_id VARCHAR,
    ballot_name VARCHAR NOT NULL,
    ballot_given_names VARCHAR,
    ballot_family_name VARCHAR,
    official_party_name VARCHAR,
    official_party_abbreviation VARCHAR,
    incumbent_status VARCHAR,
    nomination_status VARCHAR,
    match_status VARCHAR NOT NULL,
    publication_status VARCHAR NOT NULL,
    record_status VARCHAR NOT NULL,
    UNIQUE (contest_id, official_candidate_id)
);

CREATE TABLE IF NOT EXISTS core.ballot_group (
    ballot_group_id UUID PRIMARY KEY,
    contest_id VARCHAR NOT NULL,
    official_group_id VARCHAR,
    group_code VARCHAR,
    group_label VARCHAR,
    party_id VARCHAR,
    ticket_type VARCHAR,
    ungrouped BOOLEAN NOT NULL DEFAULT FALSE,
    publication_status VARCHAR NOT NULL,
    record_status VARCHAR NOT NULL,
    UNIQUE (contest_id, official_group_id)
);

CREATE TABLE IF NOT EXISTS core.ballot_group_membership (
    ballot_group_membership_id UUID PRIMARY KEY,
    ballot_group_id UUID NOT NULL,
    candidacy_id UUID NOT NULL,
    group_position INTEGER,
    membership_role VARCHAR,
    UNIQUE (ballot_group_id, candidacy_id),
    CHECK (group_position IS NULL OR group_position > 0)
);

CREATE TABLE IF NOT EXISTS core.ballot_position (
    ballot_position_id UUID PRIMARY KEY,
    contest_id VARCHAR NOT NULL,
    candidacy_id UUID,
    ballot_group_id UUID,
    column_number INTEGER,
    position_number INTEGER,
    rotation_context VARCHAR,
    source_revision_id VARCHAR,
    CHECK ((candidacy_id IS NOT NULL)::INTEGER + (ballot_group_id IS NOT NULL)::INTEGER = 1),
    CHECK (column_number IS NULL OR column_number > 0),
    CHECK (position_number IS NULL OR position_number > 0)
);

CREATE TABLE IF NOT EXISTS geography.reporting_unit (
    reporting_unit_id UUID PRIMARY KEY,
    authority_id VARCHAR NOT NULL,
    official_reporting_unit_code VARCHAR,
    canonical_name VARCHAR NOT NULL,
    reporting_unit_type VARCHAR NOT NULL,
    valid_from DATE,
    valid_to DATE,
    record_status VARCHAR NOT NULL,
    UNIQUE (authority_id, official_reporting_unit_code, valid_from),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS geography.election_reporting_unit (
    election_reporting_unit_id UUID PRIMARY KEY,
    election_id VARCHAR NOT NULL,
    contest_id VARCHAR,
    reporting_unit_id UUID,
    official_reporting_unit_code VARCHAR,
    official_label VARCHAR NOT NULL,
    source_reporting_unit_type VARCHAR,
    reporting_unit_type VARCHAR NOT NULL,
    address VARCHAR,
    suburb VARCHAR,
    postcode VARCHAR,
    latitude DECIMAL(10,7),
    longitude DECIMAL(10,7),
    match_status VARCHAR NOT NULL,
    source_revision_id VARCHAR,
    UNIQUE (election_id, contest_id, official_reporting_unit_code)
);

CREATE TABLE IF NOT EXISTS geography.reporting_unit_parent (
    reporting_unit_parent_id UUID PRIMARY KEY,
    child_election_reporting_unit_id UUID NOT NULL,
    parent_election_reporting_unit_id UUID NOT NULL,
    hierarchy_type VARCHAR NOT NULL,
    UNIQUE (child_election_reporting_unit_id, parent_election_reporting_unit_id, hierarchy_type),
    CHECK (child_election_reporting_unit_id <> parent_election_reporting_unit_id)
);

CREATE TABLE IF NOT EXISTS geography.boundary_geometry (
    boundary_geometry_id UUID PRIMARY KEY,
    boundary_version_id VARCHAR NOT NULL,
    canonical_constituency_id VARCHAR,
    official_feature_id VARCHAR,
    geometry_path VARCHAR NOT NULL,
    geometry_sha256 VARCHAR NOT NULL CHECK (length(geometry_sha256) = 64),
    geometry_type VARCHAR NOT NULL,
    bbox_min_x DOUBLE,
    bbox_min_y DOUBLE,
    bbox_max_x DOUBLE,
    bbox_max_y DOUBLE,
    UNIQUE (boundary_version_id, official_feature_id)
);

CREATE TABLE IF NOT EXISTS results.participation_result (
    participation_result_id UUID PRIMARY KEY,
    election_id VARCHAR NOT NULL,
    contest_id VARCHAR,
    election_reporting_unit_id UUID,
    vote_type VARCHAR NOT NULL,
    measure_type VARCHAR NOT NULL,
    integer_value BIGINT,
    decimal_value DECIMAL(38,12),
    value_status VARCHAR NOT NULL,
    value_basis VARCHAR NOT NULL,
    publication_status VARCHAR NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    import_run_id UUID,
    record_status VARCHAR NOT NULL,
    CHECK ((integer_value IS NOT NULL)::INTEGER + (decimal_value IS NOT NULL)::INTEGER <= 1),
    CHECK (integer_value IS NULL OR integer_value >= 0)
);

CREATE TABLE IF NOT EXISTS results.vote_result (
    vote_result_id UUID PRIMARY KEY,
    election_id VARCHAR NOT NULL,
    contest_id VARCHAR,
    election_reporting_unit_id UUID,
    subject_type VARCHAR NOT NULL,
    candidacy_id UUID,
    ballot_group_id UUID,
    party_id VARCHAR,
    question_option_code VARCHAR,
    result_type VARCHAR NOT NULL,
    vote_type VARCHAR NOT NULL,
    measure_type VARCHAR NOT NULL,
    integer_value BIGINT,
    decimal_value DECIMAL(38,12),
    value_status VARCHAR NOT NULL,
    value_basis VARCHAR NOT NULL,
    publication_status VARCHAR NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    import_run_id UUID,
    record_status VARCHAR NOT NULL,
    CHECK (contest_id IS NOT NULL OR election_reporting_unit_id IS NOT NULL),
    CHECK ((candidacy_id IS NOT NULL)::INTEGER + (ballot_group_id IS NOT NULL)::INTEGER + (party_id IS NOT NULL)::INTEGER + (question_option_code IS NOT NULL)::INTEGER + (subject_type = 'contest')::INTEGER = 1),
    CHECK ((integer_value IS NOT NULL)::INTEGER + (decimal_value IS NOT NULL)::INTEGER <= 1),
    CHECK (integer_value IS NULL OR integer_value >= 0)
);

CREATE TABLE IF NOT EXISTS "count".count_round (
    count_round_id UUID PRIMARY KEY,
    contest_id VARCHAR NOT NULL,
    round_number INTEGER NOT NULL,
    round_label VARCHAR,
    action_type VARCHAR NOT NULL,
    quota_value DECIMAL(38,12),
    transfer_value DECIMAL(38,12),
    source_calculation_type VARCHAR,
    source_calculation_value VARCHAR,
    remarks VARCHAR,
    publication_status VARCHAR NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    UNIQUE (contest_id, round_number, source_revision_id),
    CHECK (round_number >= 0)
);

CREATE TABLE IF NOT EXISTS "count".count_candidate_total (
    count_candidate_total_id UUID PRIMARY KEY,
    count_round_id UUID NOT NULL,
    candidacy_id UUID NOT NULL,
    papers_value BIGINT,
    votes_value DECIMAL(38,12),
    progressive_total DECIMAL(38,12),
    candidate_count_status VARCHAR,
    value_status VARCHAR NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    UNIQUE (count_round_id, candidacy_id),
    CHECK (papers_value IS NULL OR papers_value >= 0)
);

CREATE TABLE IF NOT EXISTS "count".preference_transfer (
    preference_transfer_id UUID PRIMARY KEY,
    count_round_id UUID NOT NULL,
    from_candidacy_id UUID,
    to_candidacy_id UUID,
    papers_value BIGINT,
    votes_value DECIMAL(38,12),
    exhausted BOOLEAN NOT NULL DEFAULT FALSE,
    value_status VARCHAR NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    CHECK (to_candidacy_id IS NOT NULL OR exhausted),
    CHECK (papers_value IS NULL OR papers_value >= 0)
);

CREATE TABLE IF NOT EXISTS results.contest_outcome (
    contest_outcome_id UUID PRIMARY KEY,
    contest_id VARCHAR NOT NULL,
    candidacy_id UUID,
    outcome_type VARCHAR NOT NULL,
    elected_order INTEGER,
    declared_at TIMESTAMPTZ,
    publication_status VARCHAR NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    record_status VARCHAR NOT NULL,
    CHECK (elected_order IS NULL OR elected_order > 0)
);

CREATE TABLE IF NOT EXISTS results.elected_member (
    elected_member_id UUID PRIMARY KEY,
    contest_outcome_id UUID NOT NULL UNIQUE,
    election_id VARCHAR NOT NULL,
    contest_id VARCHAR NOT NULL,
    candidacy_id UUID NOT NULL,
    person_id VARCHAR,
    vacancy_number INTEGER,
    grand_position_promotion_status VARCHAR NOT NULL,
    CHECK (vacancy_number IS NULL OR vacancy_number > 0)
);

CREATE TABLE IF NOT EXISTS ballot.ballot_dataset (
    ballot_dataset_id UUID PRIMARY KEY,
    election_chamber_id VARCHAR NOT NULL,
    contest_id VARCHAR,
    source_revision_id VARCHAR NOT NULL,
    dataset_scope VARCHAR NOT NULL,
    ballot_channel VARCHAR,
    anonymisation_method VARCHAR,
    privacy_notes VARCHAR,
    schema_version VARCHAR,
    row_count BIGINT CHECK (row_count IS NULL OR row_count >= 0),
    record_status VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS ballot.ballot (
    ballot_id UUID PRIMARY KEY,
    ballot_dataset_id UUID NOT NULL,
    anonymous_source_key VARCHAR,
    contest_id VARCHAR,
    ballot_channel VARCHAR,
    ballot_type VARCHAR,
    formality_status VARCHAR,
    above_the_line BOOLEAN,
    preference_count INTEGER,
    source_row_locator VARCHAR NOT NULL,
    CHECK (preference_count IS NULL OR preference_count >= 0)
);

CREATE TABLE IF NOT EXISTS ballot.ballot_preference (
    ballot_preference_id UUID PRIMARY KEY,
    ballot_id UUID NOT NULL,
    preference_rank INTEGER NOT NULL,
    candidacy_id UUID,
    ballot_group_id UUID,
    source_marking VARCHAR,
    CHECK ((candidacy_id IS NOT NULL)::INTEGER + (ballot_group_id IS NOT NULL)::INTEGER = 1),
    CHECK (preference_rank > 0),
    UNIQUE (ballot_id, preference_rank)
);

CREATE TABLE IF NOT EXISTS provenance.import_run (
    import_run_id UUID PRIMARY KEY,
    election_id VARCHAR,
    adapter_id VARCHAR NOT NULL,
    adapter_version VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    import_status VARCHAR NOT NULL,
    input_file_count INTEGER NOT NULL,
    source_row_count BIGINT,
    staged_row_count BIGINT,
    inserted_row_count BIGINT,
    rejected_row_count BIGINT,
    code_commit VARCHAR,
    notes VARCHAR,
    CHECK (input_file_count >= 0)
);

CREATE TABLE IF NOT EXISTS provenance.import_run_input (
    import_run_input_id UUID PRIMARY KEY,
    import_run_id UUID NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    input_role VARCHAR NOT NULL,
    UNIQUE (import_run_id, source_revision_id, input_role)
);

CREATE TABLE IF NOT EXISTS provenance.transform_run (
    transform_run_id UUID PRIMARY KEY,
    import_run_id UUID NOT NULL,
    transform_name VARCHAR NOT NULL,
    transform_version VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    input_row_count BIGINT,
    output_row_count BIGINT,
    output_hash VARCHAR,
    transform_status VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS provenance.row_lineage (
    row_lineage_id UUID PRIMARY KEY,
    target_schema VARCHAR NOT NULL,
    target_table VARCHAR NOT NULL,
    target_record_id VARCHAR NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    import_run_id UUID NOT NULL,
    transform_run_id UUID,
    source_row_hash VARCHAR,
    UNIQUE (target_schema, target_table, target_record_id, source_revision_id, source_locator)
);

CREATE TABLE IF NOT EXISTS audit.validation_rule (
    validation_rule_id VARCHAR PRIMARY KEY,
    rule_name VARCHAR NOT NULL UNIQUE,
    rule_version VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    scope_schema VARCHAR,
    scope_table VARCHAR,
    rule_sql VARCHAR,
    description VARCHAR NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS audit.validation_run (
    validation_run_id UUID PRIMARY KEY,
    import_run_id UUID,
    scope_type VARCHAR NOT NULL,
    scope_id VARCHAR,
    ruleset_version VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    rules_executed INTEGER NOT NULL,
    blocker_count INTEGER NOT NULL,
    warning_count INTEGER NOT NULL,
    validation_status VARCHAR NOT NULL,
    CHECK (rules_executed >= 0 AND blocker_count >= 0 AND warning_count >= 0)
);

CREATE TABLE IF NOT EXISTS audit.validation_issue (
    validation_issue_id UUID PRIMARY KEY,
    validation_run_id UUID NOT NULL,
    validation_rule_id VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    target_schema VARCHAR,
    target_table VARCHAR,
    target_record_id VARCHAR,
    source_revision_id VARCHAR,
    source_locator VARCHAR,
    issue_message VARCHAR NOT NULL,
    observed_value VARCHAR,
    expected_value VARCHAR,
    resolution_status VARCHAR NOT NULL,
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR,
    resolution_notes VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.source_record (
    staging_record_id UUID PRIMARY KEY,
    import_run_id UUID NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    dataset_key VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    source_row_number BIGINT,
    source_native_json JSON NOT NULL,
    mapped_json JSON,
    mapping_status VARCHAR NOT NULL,
    source_row_hash VARCHAR NOT NULL,
    UNIQUE (source_revision_id, dataset_key, source_locator)
);

CREATE TABLE IF NOT EXISTS derived.contest_summary (
    contest_summary_id UUID PRIMARY KEY,
    contest_id VARCHAR NOT NULL,
    publication_snapshot_id UUID,
    winner_candidacy_id UUID,
    margin_votes BIGINT,
    margin_percentage DECIMAL(18,8),
    turnout_percentage DECIMAL(18,8),
    informality_percentage DECIMAL(18,8),
    publication_status VARCHAR NOT NULL,
    calculation_version VARCHAR NOT NULL,
    input_hash VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS derived.party_summary (
    party_summary_id UUID PRIMARY KEY,
    election_chamber_id VARCHAR NOT NULL,
    party_id VARCHAR NOT NULL,
    publication_snapshot_id UUID,
    votes BIGINT,
    vote_share DECIMAL(18,8),
    swing DECIMAL(18,8),
    seats_won INTEGER,
    seat_change INTEGER,
    calculation_version VARCHAR NOT NULL,
    input_hash VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS derived.preference_flow (
    preference_flow_id UUID PRIMARY KEY,
    election_id VARCHAR NOT NULL,
    contest_id VARCHAR,
    source_subject_type VARCHAR NOT NULL,
    source_subject_id VARCHAR NOT NULL,
    target_subject_type VARCHAR NOT NULL,
    target_subject_id VARCHAR NOT NULL,
    flow_basis VARCHAR NOT NULL,
    papers BIGINT,
    votes_value DECIMAL(38,12),
    flow_percentage DECIMAL(18,8),
    calculation_version VARCHAR NOT NULL,
    input_hash VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS publish.publication_snapshot (
    publication_snapshot_id UUID PRIMARY KEY,
    snapshot_name VARCHAR NOT NULL,
    as_of_at TIMESTAMPTZ NOT NULL,
    schema_version VARCHAR NOT NULL,
    approval_status VARCHAR NOT NULL,
    approved_at TIMESTAMPTZ,
    approved_by VARCHAR,
    source_cutoff_description VARCHAR NOT NULL,
    snapshot_hash VARCHAR NOT NULL,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS publish.publication_snapshot_source_revision (
    publication_snapshot_source_revision_id UUID PRIMARY KEY,
    publication_snapshot_id UUID NOT NULL,
    source_revision_id VARCHAR NOT NULL,
    UNIQUE (publication_snapshot_id, source_revision_id)
);
