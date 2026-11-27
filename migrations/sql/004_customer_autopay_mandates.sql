-- 004: one reusable Razorpay UPI Autopay mandate per customer.
-- Apply once to the shared database. The application stores gateway IDs only, never
-- UPI PINs, account credentials, or payment instrument details.

BEGIN;

CREATE TABLE IF NOT EXISTS customer_autopay_mandates (
    customer_id              varchar(20) PRIMARY KEY REFERENCES customers (customer_id) ON DELETE CASCADE,
    setup_id                 varchar(32) UNIQUE,
    razorpay_customer_id     varchar UNIQUE,
    razorpay_order_id        varchar UNIQUE,
    razorpay_payment_id      varchar UNIQUE,
    razorpay_token_id        varchar UNIQUE,
    status                   varchar NOT NULL DEFAULT 'PENDING'
                             CHECK (status IN ('PENDING', 'ACTIVE', 'FAILED', 'CANCELLED')),
    max_amount_paise         integer NOT NULL CHECK (max_amount_paise > 0),
    mandate_expires_at       timestamptz,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT customer_autopay_active_details_check CHECK (
        status <> 'ACTIVE' OR (
            razorpay_customer_id IS NOT NULL
            AND razorpay_payment_id IS NOT NULL
            AND razorpay_token_id IS NOT NULL
        )
    )
);

ALTER TABLE customer_autopay_mandates ENABLE ROW LEVEL SECURITY;

COMMIT;
