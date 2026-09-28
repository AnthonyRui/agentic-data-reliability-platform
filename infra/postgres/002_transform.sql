\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'reliability_transform') THEN
        CREATE ROLE reliability_transform NOLOGIN;
    END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION reliability_transform;
GRANT USAGE ON SCHEMA raw TO reliability_transform;
GRANT SELECT ON ALL TABLES IN SCHEMA raw TO reliability_transform;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT SELECT ON TABLES TO reliability_transform;
GRANT USAGE ON SCHEMA analytics TO reliability_readonly;
COMMIT;
