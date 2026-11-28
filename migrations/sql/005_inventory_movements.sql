-- 005: inventory movement history for the admin panel. Every stock change is logged:
-- RESTOCK and ADJUST from the admin panel, ORDER when an approved subscription delivery takes
-- stock (approve_delivery, same transaction as the decrement).
-- Adds one table; no existing table changes. Apply once, in a transaction.

BEGIN;

CREATE TABLE IF NOT EXISTS inventory_movements (
    id             bigserial   PRIMARY KEY,
    product_id     varchar     NOT NULL REFERENCES products (product_id),
    delta          integer     NOT NULL CHECK (delta <> 0),
    reason         varchar     NOT NULL CHECK (reason IN ('RESTOCK', 'ADJUST', 'ORDER')),
    occurrence_id  bigint      REFERENCES delivery_occurrences (occurrence_id),
    note           text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_inventory_movements_product
    ON inventory_movements (product_id, created_at DESC);

-- Same posture as the other tables: Flask connects as the owner; the Supabase public API is blocked.
ALTER TABLE inventory_movements ENABLE ROW LEVEL SECURITY;

COMMIT;
