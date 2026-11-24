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
- delivery_occurrences: one row per delivery cycle. Status SCHEDULED, NOTIFIED, APPROVED, ORDER_PLACED, SKIPPED, DELAYED, OUT_OF_STOCK, CANCELLED. skip_reason USER_SKIP | NO_RESPONSE | OUT_OF_STOCK, delay_days, delivery_slot, unit_price_at_order, notified_at, response_deadline, responded_at, order_id. UNIQUE(subscription_id, scheduled_date).
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
- Job A (every few minutes): for ACTIVE subscriptions whose next_delivery_date is within the lead time (~24h) and that have no occurrence yet, create an occurrence. Soft stock check: if out of stock, mark OUT_OF_STOCK, notify, and advance the date. Otherwise send the reminder template, set status NOTIFIED and set response_deadline.
- Job B: occurrences still NOTIFIED after response_deadline are **auto-skipped** (status SKIPPED, skip_reason NO_RESPONSE). Send the `delivery_skipped_notice` template (it must be a template, because no 24h window is open), then advance next_delivery_date.
- Approve: in one transaction, `SELECT ... FOR UPDATE` on the inventory row, check and decrement stock, create the order and order_items, and set status ORDER_PLACED. If stock ran out, reply "out of stock".
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
GET /api/products/:id · GET /api/subscriptions/draft?product_id=&rec_id= · GET/POST /api/subscriptions · PATCH/DELETE /api/subscriptions/:id · POST /api/subscriptions/:id/skip · GET /api/orders · GET/POST /webhooks/whatsapp · POST /api/dev/run-scheduler

## Open questions for Sameer
1. Recommendation output: endpoint or table? Shape: recommendation_id, customer_id, product_id, recommended_quantity, recommended_frequency_days, score, rank, reason. (Don't just read back `customer_product_profiles.intended_*`; those are the parameters used to generate the synthetic data.)
2. Auth: how the token reaches Flask, what it contains, and how to get customer_id from it.
3. price vs discount_price; order ID format; existing models and repo layout.
4. Allowed quantity and frequency bounds; fallback when there's no recommendation.
