-- 004: hold deliveries that are short of stock instead of skipping them straight away.
-- At reminder time a delivery the stock can't cover becomes STOCK_HOLD (no message) until
-- hold_until (18:00 IST the day before delivery). A restock from the admin panel sends the
-- normal reminder; an unresolved hold becomes OUT_OF_STOCK and the customer is told, as before.
-- Additive: one new nullable column and one more allowed status. No existing row changes.
-- Apply once, in a transaction.

BEGIN;

ALTER TABLE delivery_occurrences ADD COLUMN IF NOT EXISTS hold_until timestamptz;

ALTER TABLE delivery_occurrences DROP CONSTRAINT IF EXISTS delivery_occurrences_status_check;
ALTER TABLE delivery_occurrences ADD CONSTRAINT delivery_occurrences_status_check CHECK (status IN (
    'SCHEDULED', 'NOTIFIED', 'APPROVED', 'ORDER_PLACED', 'SKIPPED', 'DELAYED', 'OUT_OF_STOCK', 'CANCELLED',
    'STOCK_HOLD'));

CREATE INDEX IF NOT EXISTS ix_occurrences_stock_hold
    ON delivery_occurrences (hold_until) WHERE status = 'STOCK_HOLD';

COMMIT;
