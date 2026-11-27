-- 005: record a one-time subscription fee in the first approved order of an active period.
-- Apply once to the database before deploying the matching application code.

BEGIN;

ALTER TABLE customers
    ADD COLUMN subscription_fee_recorded boolean NOT NULL DEFAULT false;

ALTER TABLE orders
    ADD COLUMN subscription_fee numeric(12, 2) NOT NULL DEFAULT 0,
    ADD CONSTRAINT ck_orders_subscription_fee CHECK (subscription_fee >= 0);

-- Existing active subscriptions predate this fee and are treated as already covered.
UPDATE customers c
SET subscription_fee_recorded = true
WHERE EXISTS (
    SELECT 1
    FROM subscriptions s
    WHERE s.customer_id = c.customer_id
      AND s.status = 'ACTIVE'
);

COMMIT;
