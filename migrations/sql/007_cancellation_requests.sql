-- 007: the admin panel can ask a customer to confirm cancelling a subscription (operational
-- reasons). The customer answers on Telegram or the dashboard; only a confirmation cancels.
-- One row per request; at most one PENDING per subscription.
-- Adds one table; no existing table changes. Apply once, in a transaction.

BEGIN;

CREATE TABLE IF NOT EXISTS cancellation_requests (
    id               bigserial   PRIMARY KEY,
    subscription_id  bigint      NOT NULL REFERENCES subscriptions (subscription_id),
    status           varchar     NOT NULL DEFAULT 'PENDING'
                                 CHECK (status IN ('PENDING', 'CONFIRMED', 'DECLINED', 'WITHDRAWN')),
    requested_at     timestamptz NOT NULL DEFAULT now(),
    responded_at     timestamptz,
    channel          varchar     CHECK (channel IS NULL OR channel IN ('telegram', 'dashboard', 'admin'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_cancellation_requests_one_pending
    ON cancellation_requests (subscription_id) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS ix_cancellation_requests_subscription
    ON cancellation_requests (subscription_id, requested_at DESC);

-- Same posture as the other tables: Flask connects as the owner; the Supabase public API is blocked.
ALTER TABLE cancellation_requests ENABLE ROW LEVEL SECURITY;

COMMIT;
