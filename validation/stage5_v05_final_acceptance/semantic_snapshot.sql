\pset tuples_only on
\pset format unaligned
SELECT jsonb_build_object(
  'title', (
    SELECT jsonb_build_object(
      'external_identifier', ei.external_identifier,
      'official_name', t.official_name,
      'collection', t.legislation_collection_source_value,
      'status', t.current_source_status_value,
      'is_in_force', t.is_in_force,
      'has_unincorporated_amendments', t.has_unincorporated_amendments
    )
    FROM bls_legislation.legislation_titles t
    JOIN bls_source.external_identifiers ei ON ei.target_record_id=t.record_id
    WHERE ei.external_identifier='F2016L01916'
  ),
  'title_names', (
    SELECT jsonb_agg(jsonb_build_object(
      'name_type', name_type_code,
      'name', exact_name,
      'period', valid_period::text,
      'preferred', is_preferred
    ) ORDER BY lower(valid_period))
    FROM bls_legislation.legislation_title_names
  ),
  'statuses', (
    SELECT jsonb_agg(jsonb_build_object(
      'status', source_status_value,
      'period', status_period::text
    ) ORDER BY lower(status_period))
    FROM bls_legislation.legislation_status_history
  ),
  'version', (
    SELECT jsonb_build_object(
      'external_identifier', ei.external_identifier,
      'period', v.version_period::text,
      'status', v.source_version_status_value,
      'compilation', v.compilation_number,
      'current', v.is_current,
      'latest', v.is_latest_registered,
      'has_unincorporated_amendments', v.has_unincorporated_amendments
    )
    FROM bls_legislation.legislation_versions v
    JOIN bls_source.external_identifiers ei ON ei.target_record_id=v.record_id
    WHERE ei.external_identifier='F2026C00596'
  ),
  'reasons', (
    SELECT jsonb_agg(jsonb_build_object(
      'type', reason_type_code,
      'value', exact_reason_value,
      'order', reason_order
    ) ORDER BY reason_type_code, reason_order)
    FROM bls_legislation.legislation_version_reasons
  ),
  'documents', (
    SELECT jsonb_agg(jsonb_build_object(
      'format', document_format_source_value,
      'pages', page_count,
      'bytes', source_size_in_bytes,
      'authorised', is_authorised,
      'content_hash_present', content_sha256 IS NOT NULL
    ) ORDER BY document_format_source_value)
    FROM bls_legislation.legislation_documents
  ),
  'department', (
    SELECT jsonb_build_object(
      'external_identifier', ei.external_identifier,
      'name', d.source_department_name,
      'politica_id', d.politica_organisation_id
    )
    FROM bls_legislation.departments d
    JOIN bls_source.external_identifiers ei ON ei.target_record_id=d.record_id
    WHERE ei.external_identifier='O-000882'
  ),
  'administering_department_count', (SELECT count(*) FROM bls_legislation.legislation_administering_departments),
  'scrutiny', (
    SELECT jsonb_agg(jsonb_build_object(
      'type', event_type_source_value,
      'at', event_at,
      'chamber', source_chamber_value,
      'expiry', motion_expiry_at
    ) ORDER BY event_at)
    FROM bls_legislation.scrutiny_events
  ),
  'external_identifiers', (
    SELECT jsonb_agg(external_identifier ORDER BY external_identifier)
    FROM bls_source.external_identifiers
    WHERE external_identifier IN ('F2016L01916','F2026C00596','O-000882')
  ),
  'external_identifier_evidence_count', (
    SELECT count(*)
    FROM bls_source.external_identifier_evidence e
    JOIN bls_source.external_identifiers i ON i.record_id=e.external_identifier_record_id
    WHERE i.external_identifier IN ('F2016L01916','F2026C00596','O-000882')
  ),
  'field_provenance_count', (
    SELECT count(*) FROM bls_audit.field_provenance
    WHERE ruleset_version='stage5-v0.5-canonical-1'
  ),
  'domain_record_evidence_missing', (
    SELECT count(*)
    FROM bls_core.records r
    JOIN bls_core.record_types rt ON rt.record_type_code=r.record_type_code
    WHERE rt.record_namespace='bls_legislation'
      AND NOT EXISTS (
        SELECT 1 FROM bls_source.record_evidence e
        WHERE e.target_record_id=r.record_id AND e.evidence_status_code='accepted'
      )
  ),
  'commencement_event_count', (SELECT count(*) FROM bls_legislation.commencement_events),
  'legislative_relationship_count', (SELECT count(*) FROM bls_legislation.legislative_relationships),
  'review_case', (
    SELECT jsonb_build_object(
      'reason', r.reason_code,
      'status', r.review_status_code,
      'candidate', c.candidate_value
    )
    FROM bls_audit.review_cases r
    JOIN bls_audit.review_case_candidates c ON c.review_case_record_id=r.record_id
    WHERE r.reason_code='unresolved_authorising_title'
  )
);
