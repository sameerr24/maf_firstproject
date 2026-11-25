-- 002: Telegram as the demo messaging channel (stand-in for WhatsApp).
-- Adds one table; no existing table changes. Apply once, in a transaction.
--
-- A bot can only message chats that started it, by chat id (never by phone number).
-- The customer shares their phone number with the bot; it is matched to
-- customers.phone_e164 and the chat id is stored here.

CREATE TABLE IF NOT EXISTS telegram_links (
    customer_id       varchar     PRIMARY KEY REFERENCES customers (customer_id),
    chat_id           bigint      NOT NULL UNIQUE,   -- where the bot sends messages
    telegram_user_id  bigint      NOT NULL,          -- who shared the number (must own the chat)
    linked_at         timestamptz NOT NULL DEFAULT now()
);

-- Same posture as the other tables: Flask connects as the owner; the Supabase public API is blocked.
ALTER TABLE telegram_links ENABLE ROW LEVEL SECURITY;
