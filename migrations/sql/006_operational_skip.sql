-- 006: the admin panel can skip a delivery for operational reasons (the customer is told
-- "due to operational delays"). Adds one allowed skip_reason, OPERATIONAL.
-- Additive: no existing row changes. Apply once, in a transaction.

BEGIN;

ALTER TABLE delivery_occurrences DROP CONSTRAINT IF EXISTS delivery_occurrences_skip_reason_check;
ALTER TABLE delivery_occurrences ADD CONSTRAINT delivery_occurrences_skip_reason_check CHECK (
    skip_reason IS NULL OR skip_reason IN ('USER_SKIP', 'NO_RESPONSE', 'OUT_OF_STOCK', 'OPERATIONAL'));

COMMIT;
