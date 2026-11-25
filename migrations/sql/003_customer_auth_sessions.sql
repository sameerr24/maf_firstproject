-- 003: persistent refresh-token rotation and cross-worker auth rate limits.
-- Apply once to the shared database. Existing customers and customer IDs are unchanged.

BEGIN;

CREATE TABLE IF NOT EXISTS customer_auth_refresh_tokens (
    token_hash       varchar(64) PRIMARY KEY,
    customer_id      varchar(20) NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
    family_id        varchar(36) NOT NULL,
    issued_at        timestamptz NOT NULL,
    expires_at       timestamptz NOT NULL,
    revoked_at       timestamptz,
    replaced_by_hash varchar(64)
);

CREATE INDEX IF NOT EXISTS ix_customer_auth_refresh_family
    ON customer_auth_refresh_tokens (family_id);
CREATE INDEX IF NOT EXISTS ix_customer_auth_refresh_customer
    ON customer_auth_refresh_tokens (customer_id);

CREATE TABLE IF NOT EXISTS customer_auth_rate_limits (
    bucket_key       varchar(64) PRIMARY KEY,
    window_started_at timestamptz NOT NULL,
    hits             integer NOT NULL CHECK (hits >= 0)
);

ALTER TABLE customer_auth_refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_auth_rate_limits ENABLE ROW LEVEL SECURITY;

COMMIT;
