# Subscriptions + WhatsApp Module: Project Context

Read this before working on the subscription, notification, or order-history parts of this repo.

## Project
Induction POC: "Smart Reorder & Subscription Shopping". Monolithic repo, Flask + React + PostgreSQL (Supabase).
Two developers share the repo:
- **Sameer (`sameerr24`)**: recommendation logic (who to recommend which product to, and at what quantity and frequency), ordering of the recommendations grid, and auth.
- **Shubham (branch `shubham-mafproject-primary`)**: PDP, subscription dashboard, scheduler, WhatsApp notifications, inventory handling, order history.

Keep the two areas in separate folders/blueprints so we rarely edit the same files. Sameer may already have SQLAlchemy models for the original tables, so **reuse them; don't duplicate**.

## Target structure (to confirm with Sameer)
```
backend/
  app/
    __init__.py          # app factory, registers blueprints, starts scheduler
    config.py            # reads .env
    db.py                # SQLAlchemy instance
    auth.py              # Sameer's auth: decorator to get current customer_id
    models/              # shared models (one file per table or domain)
    recommendations/     # Sameer
    subscriptions/       # Shubham: routes, service, scheduler jobs
    notifications/       # Shubham: whatsapp_client.py, webhook routes, flow handlers
    orders/              # Shubham: order history (read-only)
  requirements.txt
  .env.example
frontend/                # React (Vite)
  src/pages/{Pdp,SubscriptionDashboard,OrderHistory,Recommendations}/
```
Never commit `.env`. `.env.example` lists: DATABASE_URL, WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_WABA_ID, WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN, plus whatever auth secret Sameer uses.

## Database (Supabase Postgres; already migrated)
Sameer's tables: products, customers, orders, order_items, customer_product_profiles. IDs are varchar.
Added by Shubham's migration:
- customers: customer_name, phone_e164 (E.164, unique), whatsapp_opt_in
- orders: source ('manual' | 'subscription'), delivery_slot
- inventory(product_id PK, available_qty CHECK >= 0, updated_at)
- subscriptions: customer_id, product_id, quantity, frequency_days, unit_price_at_subscription, status ACTIVE|PAUSED|CANCELLED, next_delivery_date, preferred_slot, paused_until, recommended_quantity, recommended_frequency_days, timestamps. Partial unique index `uq_live_subscription` allows one non-cancelled subscription per customer + product.
- delivery_occurrences: one row per delivery cycle. Status SCHEDULED, NOTIFIED, APPROVED, ORDER_PLACED, SKIPPED, DELAYED, OUT_OF_STOCK, CANCELLED, STOCK_HOLD (migration 004). skip_reason USER_SKIP | NO_RESPONSE | OUT_OF_STOCK, delay_days, delivery_slot, unit_price_at_order, notified_at, response_deadline, hold_until (004), responded_at, order_id. UNIQUE(subscription_id, scheduled_date).
- inventory_movements (migration 005): product_id, delta (≠ 0), reason RESTOCK | ADJUST | ORDER, occurrence_id, note, created_at. Every stock change is logged.
- whatsapp_messages: wa_message_id UNIQUE (for webhook dedupe), occurrence_id, customer_id, direction, message_type, payload jsonb.
RLS is enabled with no policies. Flask connects with the postgres connection string, so it bypasses RLS. React talks only to Flask, never directly to Supabase.

## Product decisions
- PDP has only a **Subscribe** button (no buy now, no add to cart). Show it only when `products.replenishable` is true.
- Subscribe leads to the dashboard: a large card for the new subscription, pre-filled with the recommended quantity and frequency (editable), and below it the existing subscriptions as cards with edit, pause/resume and cancel.
- Price: confirm with Sameer whether `price` or `discount_price` is the charged price. Store the price at subscription time and flag any change in the WhatsApp reminder; approving means accepting the new price.
- Default first delivery date: aligned to `customers.preferred_weekday`.
- Order history: read-only list of the user's orders and items.
- Subscription-generated orders go into the same `orders`/`order_items` tables with source='subscription', item_count=1, unit_count=quantity. Order ID format to confirm with Sameer (suggestion: `SUB-<occurrence_id>`).

## Scheduler (APScheduler, inside Flask)
- Job A (every few minutes): for ACTIVE subscriptions whose next_delivery_date is within the lead time (~24h) and that have no occurrence yet, create an occurrence. Soft stock check, counting stock already promised to reminders awaiting a reply: if short, the delivery is **held** (STOCK_HOLD, no message) until 18:00 the day before delivery, for the admin to restock (see "Stock holds" below). Otherwise send the reminder template, set status NOTIFIED and set response_deadline.
- Holds (each run, after job B): a hold the stock now covers gets the normal reminder; a hold past hold_until becomes OUT_OF_STOCK, the customer gets the out-of-stock notice and the date advances. `release_holds()` does the same right after an admin restock.
- Job B: occurrences still NOTIFIED after response_deadline are **auto-skipped** (status SKIPPED, skip_reason NO_RESPONSE). Send the `delivery_skipped_notice` template (it must be a template, because no 24h window is open), then advance next_delivery_date.
- Approve: in one transaction, `SELECT ... FOR UPDATE` on the inventory row, check and decrement stock (logging an ORDER movement), create the order and order_items, and set status ORDER_PLACED. If stock ran out, reply "out of stock".
- Delay: shift this delivery by N days, computed from the original schedule so the cycle doesn't drift.
- Demo endpoint `POST /api/dev/run-scheduler?now=<iso>` for "time travel" during the demo.

## WhatsApp (Meta Cloud API, free test number, up to 5 recipient numbers)
- Business-initiated messages must be **approved templates**. When the user taps a button, a 24h customer service window opens, and follow-ups can then be free-form interactive messages.
- Template `subscription_delivery_reminder` (Utility): body variables product, quantity, date, price. Three quick-reply buttons with payloads `APPROVE:<occurrence_id>`, `SKIP:<occurrence_id>`, `STOP:<occurrence_id>`.
- Approve: interactive list to pick a delivery slot, then the inventory transaction, then confirmation or out-of-stock message.
- Skip: interactive buttons "Skip this one" / "Delay 1 day" / "Delay 2 days".
- Stop: "Are you sure?" confirmation, then cancel the subscription.
- Webhook `/webhooks/whatsapp`: GET verifies hub.verify_token and echoes hub.challenge. POST validates X-Hub-Signature-256 with the app secret, dedupes on wa_message_id, returns 200 fast, then processes. Template button taps arrive as `messages[].button.payload`; interactive replies arrive as `interactive.button_reply.id` / `list_reply.id`.
- Local dev: expose Flask with `ngrok http 5000` and set the callback URL in the Meta dashboard. The temporary token expires after ~24h, so use a System User token for the demo.

## API endpoints (Shubham)
GET /api/products/:id · GET /api/subscriptions/draft?product_id=&rec_id= · GET/POST /api/subscriptions · PATCH/DELETE /api/subscriptions/:id · POST /api/subscriptions/:id/skip · GET /api/subscriptions/deliveries/pending · POST /api/subscriptions/deliveries/:occurrence_id/{approve,skip,delay} · GET /api/orders · GET/POST /webhooks/whatsapp · POST /api/dev/run-scheduler

Admin app (separate, 127.0.0.1:5002, see `docs/ADMIN_PANEL_CONTEXT.md`): GET /api/admin/at-risk · GET /api/admin/inventory · POST /api/admin/inventory/:product_id/{restock,adjust} · GET /api/admin/inventory/:product_id/movements · GET /api/admin/subscriptions

## Decisions (agreed with Sameer, 2026-11-24)

**Pricing**
- `products.price` is the regular (non-subscriber) price, and `discount_price` is the subscriber price.
- Subscriptions charge `discount_price`, stored as `unit_price_at_subscription`.
- The PDP shows `price` struck through, plus the saving per unit and per delivery.

**Auth** (Sameer's `customer_auth` + `app/auth.py`, merged in #4)
- Flask routes use `from app.auth import login_required`, which sets `g.customer_id`. **Never** read customer_id from the request body or query; another customer's subscription or order returns **404**.
- **Updated auth contract (supersedes the legacy token/demo-login details below):** FastAPI verifies MSG91 Widget access tokens server-side before login or registration. `MSG91_AUTHKEY` is server-only; widget ID/token are public browser configuration, and the widget's allowed domains must be restricted in MSG91.
- Access tokens are short-lived HS256 JWTs signed with `AUTH_SECRET_KEY` (at least 32 characters; default 15 minutes). The same access tokens work in FastAPI and Flask.
- Refresh sessions use opaque, hashed, rotating tokens in a Secure/HttpOnly/SameSite=Strict cookie. Apply `migrations/sql/003_customer_auth_sessions.sql` before using auth; refresh sessions and rate-limit buckets are stored in the shared database.
- `POST /api/v1/auth/login` accepts only a verified phone; `POST /api/v1/auth/register` creates a new customer with `behaviour_segment="new"` and no messaging consent. Existing customers need their phone already associated with `customers.phone_e164`.
- `POST /api/v1/auth/refresh` rotates the refresh cookie; `POST /api/v1/auth/logout` revokes the refresh session. `GET /api/v1/auth/me` returns the authenticated customer.
- Tokens: `Authorization: Bearer <token>`, signed with **`AUTH_SECRET_KEY`** (at least 32 characters) via itsdangerous, valid for 7 days. The same tokens work in the FastAPI recommendation API and in Flask, as long as both read the same `AUTH_SECRET_KEY`.
- Failures return 401, `WWW-Authenticate: Bearer`, and `{"error": {"code": "unauthorized" | "token_expired", "message": ...}}`.
- The customer-ID demo-login endpoint has been removed. See the updated OTP login, registration, refresh and logout contract above.
- In tests, issue tokens with `customer_auth.TokenService(AuthSettings(secret_key=...)).issue("CUST-0001")` (see `tests/subscription_db.py: bearer()`).
- Chat webhooks and the bot don't use this auth. The bot identifies customers by their linked chat (`telegram_links`).

**Subscription orders** (written on Approve)
- `order_id = SUB-<occurrence_id>`, `order_item_id = SUBI-<occurrence_id>` (IDs are at most 20 characters).
- `order_datetime` is **naive Asia/Kolkata time**, and `order_date = order_datetime.date()`. The column has no time zone and a CHECK requires them to match, so UTC would break orders between 00:00 and 05:30 IST.
- `source='subscription'`, `item_count=1`, `unit_count=quantity`, `unit_price=unit_price_at_subscription` (or the accepted new price), `line_total = quantity * unit_price` exactly, `order_total = line_total`.

**Recommendations** (Sameer)
- He learns only from `source='manual'` orders, so subscriptions don't feed back into their own cadence.
- Products with a live (non-cancelled) subscription are left out of the recommendations grid.
- For subscribed products he reads `delivery_occurrences` (skips, delays) and subscription edits to suggest adjustments. The user's edited values always win.
- He scopes the validator's three profile/price warnings to manual orders.

**Chat notifications are required to subscribe** (Shubham, 2026-11-24/25)
- A customer can be messaged when `whatsapp_opt_in = true` (general consent), `phone_e164` is set, and their chat is linked. Creating or resuming a subscription otherwise returns 409 `messaging_required`.
- Product and draft responses include `messaging_ready`.
- If a customer stops being reachable after subscribing (opt-out, phone removed, bot blocked), due deliveries are skipped (`SKIPPED` / `NO_RESPONSE`) without messaging them. Nothing is charged.

**Demo channel: Telegram bot, designed for WhatsApp** (Shubham, 2026-11-25)
- The brief only requires push notifications. The product is designed for WhatsApp (templates in `docs/WHATSAPP_TEMPLATES.md`). Because of Meta account restrictions, the demo sends the same messages and buttons through a Telegram bot.
- A bot can only message chats that started it, by chat ID. The customer taps Start, then "Share my phone number". The shared number must be their own and must match `customers.phone_e164`. The chat is stored in `telegram_links` (migration `migrations/sql/002_telegram_links.sql`; no existing table changed).
- Existing names stay: `whatsapp_opt_in` means "may be messaged" and `whatsapp_messages` logs Telegram messages too (`wa_message_id` = `tg:<chat>:<message>` outbound, `tg-update:<update_id>` inbound, for dedupe).
- Run the bot with `flask --app run telegram-poll` (long polling, so no public URL or ngrok). Reminders go through Telegram whenever `TELEGRAM_BOT_TOKEN` is set.
- Approve: slot picker, then one transaction: lock and decrement stock, create the order, set `ORDER_PLACED`. Approving accepts the current subscriber price.

**Delay = approve again** (Shubham, 2026-11-24; the problem statement requires every charge to be approved)
- "Delay N days" (N = 1 or 2) moves only this delivery. The occurrence keeps its cycle date (`scheduled_date`), gets `delay_days`, and becomes `DELAYED`. The delivery date is `scheduled_date + delay_days`.
- The day before the new date, the scheduler sends a fresh reminder (`NOTIFIED` with a new deadline). No reply means auto-skip, as usual. Nothing is charged without an Approve.
- The total delay is at most 2 days and always before the next cycle (no delay option for daily items). The next cycle stays on the original schedule.
- On Approve, the order is for the delivery date, i.e. including the delay.

**Draft pre-filled by the recommendation engine** (2026-11-25)
- `GET /api/subscriptions/draft` asks Sameer's `RecommendationEngine` for the customer's ranked list and uses this product's `suggested_quantity` and `expected_purchase_interval` (rounded to whole days). It also returns `rank`, `score`, `status` and the explanation under `recommendation`.
- The engine runs on the request's own database connection (`app/subscriptions/recommendations.py`). Its rules apply unchanged: manual orders only, the reference date from `scoring_config.json`, and subscribed products excluded.
- No recommendation (never or too rarely bought, already subscribed) or an engine error → defaults (1 pack every 7 days). The customer's edited values always win. The recommended values are stored on the subscription (`recommended_quantity`, `recommended_frequency_days`).

**Reminders answerable on the website too** (2026-11-25)
- Every reminder sent in chat also shows on the website (a "Waiting for your reply" panel on the subscriptions page, plus a count badge on the header link) with the same options: Approve with a slot, Skip, Delay 1 or 2 days, Stop. The web endpoints call the same service functions as the bot, so the rules (deadline, delay limits, stock check) are identical.
- Whichever channel answers first wins; the other gets "no longer waiting for your reply" (409 `delivery_finalised`), so a delivery can't be ordered twice.
- After a website reply, `app/notifications/chat_sync.py` removes the reminder's buttons in Telegram and posts the same confirmation the bot would send. This is best effort: a Telegram failure never fails the website action. Cancel, pause and "Skip next" on the dashboard clear an open reminder the same way.

**Product photo in Telegram messages** (2026-11-26)
- Reminder, out-of-stock and auto-skip messages are sent as the product's photo with the message text as its caption and the same buttons. The photo is the website's local copy (`frontend/public/products/<sku>.jpg`), uploaded once per process; later sends reuse Telegram's file_id.
- No image file, a caption over 1024 characters, or a failed photo send: the plain text message goes out instead, so a reminder is never lost over a picture. A blocked bot is not retried.
- WhatsApp (product) would do the same with an image header on the template.

**Stock holds instead of immediate out-of-stock** (Shubham, 2026-11-28)
- At reminder time, a delivery the stock can't cover is held (`STOCK_HOLD`, `hold_until` = 18:00 IST the day before delivery, `STOCK_HOLD_UNTIL_LOCAL_TIME`) and nobody is messaged. It shows on the admin panel's At-risk page.
- Restocking from the admin panel sends the normal reminder right away (oldest delivery first, each counting the stock promised by the ones before it). 18:00 leaves 3 hours to reply before the 21:00 deadline.
- Not restocked by 18:00: OUT_OF_STOCK and the out-of-stock notice, exactly as before. A reminder created after 18:00 the day before goes straight to out of stock.
- Free stock = on hand minus units promised to reminders awaiting a reply. Before, each subscription was checked alone, so with 5 in stock and two orders of 3 both were reminded and the second approval failed; now the second is held.
- Pause, cancel and "Skip next" clear a hold like any pending delivery. Approval keeps its own hard check, so nothing is charged without stock.

## Still open
- Nothing blocking. Demo prep: rotate the bot token, pick the demo customer, reset test data.
