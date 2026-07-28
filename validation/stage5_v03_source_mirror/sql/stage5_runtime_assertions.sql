\set ON_ERROR_STOP on

DO $$
BEGIN
    IF current_setting('server_version_num')::integer <> 180004 THEN
        RAISE EXCEPTION 'Stage 5 v0.3 validation requires exact PostgreSQL 18.4 / 180004';
    END IF;
END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM stage5_runtime.raw_captures
        WHERE encode(digest(body, 'sha256'), 'hex') <> body_sha256
           OR octet_length(body) <> byte_count
    ) THEN
        RAISE EXCEPTION 'database raw-capture bytes failed hash or byte-count validation';
    END IF;
END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM stage5_runtime.watermarks w
        JOIN stage5_runtime.runs r ON r.run_id = w.run_id
        WHERE r.status <> 'succeeded'
    ) THEN
        RAISE EXCEPTION 'non-successful run owns a committed watermark';
    END IF;
END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT run_id FROM stage5_runtime.runs WHERE status = 'succeeded'
        EXCEPT
        SELECT run_id FROM stage5_runtime.watermarks GROUP BY run_id HAVING count(*) = 2
    ) THEN
        RAISE EXCEPTION 'successful run lacks the atomic pair of Titles and Versions watermarks';
    END IF;
END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM stage5_runtime.candidates
        WHERE btrim(external_identifier) = ''
    ) THEN
        RAISE EXCEPTION 'candidate with empty external identifier';
    END IF;
END;
$$;

SELECT 'STAGE5_RUNTIME_POSTGRES_PASS' AS result;
