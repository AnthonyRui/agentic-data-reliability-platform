\set ON_ERROR_STOP on
BEGIN;
SET TIME ZONE 'UTC';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA raw;
CREATE SCHEMA platform;

CREATE TABLE raw.raw_customers (
    customer_id bigint PRIMARY KEY,
    region text NOT NULL,
    signup_ts date NOT NULL,
    segment text NOT NULL
);
CREATE TABLE raw.raw_products (
    product_id bigint PRIMARY KEY,
    category text NOT NULL,
    unit_cost numeric(12, 2) NOT NULL
);
-- Raw landing data intentionally allows duplicates and missing fields so future
-- quality checks can detect injected upstream faults instead of hiding them.
CREATE TABLE raw.raw_orders (
    order_id bigint,
    customer_id bigint,
    product_id bigint,
    order_ts timestamptz,
    amount numeric(12, 2),
    currency text,
    status text
);
COPY raw.raw_customers FROM '/seed/raw_customers.csv' WITH (FORMAT csv, HEADER MATCH);
COPY raw.raw_products FROM '/seed/raw_products.csv' WITH (FORMAT csv, HEADER MATCH);
COPY raw.raw_orders FROM '/seed/raw_orders.csv' WITH (FORMAT csv, HEADER MATCH);
CREATE INDEX ON raw.raw_orders(order_ts);

-- Permission group only; no login or credential is created for an agent yet.
CREATE ROLE reliability_readonly NOLOGIN;
GRANT USAGE ON SCHEMA raw TO reliability_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA raw TO reliability_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT SELECT ON TABLES TO reliability_readonly;

CREATE TABLE platform.bootstrap (ready boolean NOT NULL);
INSERT INTO platform.bootstrap VALUES (true);
COMMIT;
