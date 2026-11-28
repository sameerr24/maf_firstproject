# Admin Panel (Inventory Management): Context

Read this, plus `docs/SUBSCRIPTIONS_CONTEXT.md`, before working on the admin panel.
Owner: Shubham. Sameer works on other features in the same repo. **Do not modify auth
(`app/auth.py`, `customer_auth/`), `frontend/` or Sameer's files.**
How to run it: `admin-frontend/README.md`.

## Goal
A local-only admin panel with three pages:
1. **At risk**: deliveries held because the stock can't cover them, before the customer is told,
   so the admin can restock in time.
2. **Inventory**: stock per product against open demand; restock, adjust, movement history.
3. **Subscriptions**: active and paused subscriptions, searchable by product or subscriber.

On At risk and Subscriptions, a delivery nobody has approved yet can be **delayed** (1-2 days) or
**skipped**; the customer is told on Telegram that it's due to operational delays. On Subscriptions,
the admin can also **request cancellation**: the customer confirms or declines on Telegram or the dashboard.

## Key decisions
- **Separate Flask app and separate Vite app, no auth, by design.** Only the admin is assumed to have
  access to the machine. Production would add role-based admin login.
- **Bound to 127.0.0.1 only** (Flask 5002, Vite 5174). Never 0.0.0.0 or `--host`; never tunnel the port.
- The customer app (`app.create_app`) doesn't register the admin blueprint; the admin app never imports
  `app.auth` or `customer_auth` (a test checks this in a fresh interpreter). To keep it that way,
  `app/subscriptions/__init__.py` no longer imports its routes; `create_app` imports
  `app.subscriptions.routes` directly.
- **Stock holds, no forecast.** An earlier draft projected 7 days of demand. It was dropped: it didn't
  match the demo, and the scheduler already knows exactly which deliveries are short at reminder time.
  Instead the scheduler holds them (next section). At-risk = the held deliveries.
- Subscriptions are never edited from the admin panel: the admin can delay or skip a delivery that hasn't
  been approved yet, and ask the customer to confirm cancelling (only the customer's answer cancels).
  Approved (paid) orders are out of scope.

## Stock holds (scheduler, `app/subscriptions/jobs.py`)
- At reminder time (the day before delivery), the soft stock check counts **free stock = on hand minus
  units promised to reminders awaiting a reply** (NOTIFIED occurrences of ACTIVE subscriptions).
- Short: the occurrence becomes `STOCK_HOLD` with `hold_until` = 18:00 IST the day before delivery
  (`STOCK_HOLD_UNTIL_LOCAL_TIME`). **No message is sent.** If 18:00 has already passed, it goes straight to
  OUT_OF_STOCK with the out-of-stock notice (the old behaviour).
- Each scheduler run, after auto-skip: holds past `hold_until` become OUT_OF_STOCK (customer told, next
  cycle); holds the stock now covers get the normal reminder (NOTIFIED, 21:00 deadline). Oldest delivery
  first, then subscription id; each release counts the stock promised by the ones before it.
- `release_holds(engine, product_id)` does the same on demand; the admin restock and adjust-up call it
  after committing. It waits for the scheduler's advisory lock instead of skipping, so a restock is never
  ignored. A failed release never undoes the restock.
- `STOCK_HOLD` is a pending status (`PENDING_OCCURRENCE_STATUSES`): pause, cancel and "Skip next" clear it.
- Approval keeps its own hard check (`approve_delivery` locks the inventory row), so nothing is charged
  without stock.
- **Time travel:** holds are real rows written at the simulated time, so the At-risk page always matches the
  scheduler. A restock releases with the real clock, which is fine when travelling forward (the real time
  is before the simulated hold time).

## Delay or skip a delivery (operational reasons)
- Acts on the subscription's **open delivery** (STOCK_HOLD, NOTIFIED, DELAYED; at most one) or, if there is
  none, its upcoming `next_delivery_date` (an occurrence is created). Locks the subscription, then the delivery,
  in the same order as the scheduler and customer actions. 409 for paused/cancelled subscriptions
  (`not_active`), a delivery dated today or earlier or already settled (`delivery_finalised`), and a delay
  beyond the limit (`delay_not_possible`, with the remaining `options`).
- **Delay** by 1 or 2 days, the customer's own limits (`DELAY_OPTIONS_DAYS`, `MAX_TOTAL_DELAY_DAYS`, before
  the next cycle): status DELAYED, delay_days added, hold and reply deadline cleared, cycle unchanged. The
  scheduler's remind-delayed step sends a fresh reminder the day before the new date (or holds it again if
  still short), so the charge is still approved by the customer. An old reminder's buttons stop working.
- **Skip**: SKIPPED with skip_reason `OPERATIONAL` (migration 006) and `advance_cycle`.
- The customer gets `delivery_delayed_operational_notice` or `delivery_skipped_operational_notice`
  ("Due to operational delays, ...", with the product photo) in the same transaction; `customer_notified`
  is false when they can't be messaged (the change still happens).
- A reminder awaiting a reply had promised stock; after delaying or skipping one, the route releases held
  deliveries of that product (`holds` in the response), as a restock does.

## Cancellation requests (`app/subscriptions/cancellation.py`, migration 007)
- `POST /subscriptions/<id>/cancel-request` `{}` (ACTIVE or PAUSED; 409 `already_requested` / `not_active`):
  creates a PENDING `cancellation_requests` row, cancels unapproved open deliveries (their chat reminders lose
  their buttons; promised stock is released to holds, `holds` in the response) and sends
  `subscription_cancellation_request` with Yes, cancel it / Keep my subscription buttons (`CXL_YES:<id>`,
  `CXL_NO:<id>`). Returns `request`, `cancelled_deliveries`, `customer_notified`.
- While pending: no reminders (scheduler), Delay / Skip refused (`cancel_requested`), the dashboard card shows a
  "Please confirm" banner instead of its usual buttons.
- The customer answers in Telegram or on the dashboard; the first answer wins. Yes = the normal cancel;
  Keep = DECLINED, reminders resume.
- `POST /subscriptions/<id>/cancel-request/withdraw` `{}` (409 `no_pending_request`): WITHDRAWN; the chat buttons
  are removed and the customer is told the subscription continues.
- Subscriptions rows carry `cancel_request` (latest request, any status, or null); `upcoming` is null while one is
  pending. The page shows "Cancellation requested" with Withdraw, or "Customer kept it".

## Inventory movements
Every stock change is logged in `inventory_movements` (migration 005): RESTOCK and ADJUST from the admin
panel, ORDER from `approve_delivery` (delta = −quantity, occurrence_id, same transaction as the decrement).

## Backend (`app/admin/`)
```
app/admin/
  __init__.py   create_admin_app(): admin config, init_db, error handlers, CORS, guard, the admin
                blueprint at /api/admin, JSON 404/405, /api/admin/health, and the dev blueprint
                (/api/dev, time travel) when DEV_ENDPOINTS=1
  config.py     AdminConfig = Config with SCHEDULER_ENABLED=False, ADMIN_PORT, CORS from ADMIN_CORS_ORIGINS
  guard.py      before_request checks (below)
  service.py    queries and stock changes (no Flask)
  routes.py
run_admin.py    create_admin_app().run(host="127.0.0.1", port=ADMIN_PORT)
migrations/sql/004_stock_hold.sql, 005_inventory_movements.sql   (both applied on Supabase, 2026-11-27)
migrations/sql/006_operational_skip.sql, 007_cancellation_requests.sql   (applied on Supabase, 2026-11-28)
```
Env (optional, defaults shown): `ADMIN_PORT=5002`,
`ADMIN_CORS_ORIGINS=http://localhost:5174,http://127.0.0.1:5174` (browsers treat the two hosts as
different origins). Constants: `STOCK_HOLD_UNTIL_LOCAL_TIME = "18:00"`, `LOW_STOCK_BUFFER = 5`.

### Guard (no auth, but required)
1. `Host` must be `localhost:<ADMIN_PORT>` or `127.0.0.1:<ADMIN_PORT>`, else 403 (blocks DNS rebinding).
2. POST, PUT, PATCH and DELETE need a JSON body, else 415 (blocks cross-site form posts; a cross-origin JSON
   request needs a CORS preflight, which only the admin frontend's origins pass). So time travel through
   the admin app needs `-H "Content-Type: application/json" -d '{}'`.

## Endpoints (`/api/admin`, JSON; errors `{"error": {"code", "message"}}`)
- `GET /at-risk`: held deliveries grouped by product, most urgent first (earliest hold_until, then name), and
  `total` for the nav badge. Per product: `stock_now`, `promised`, `free_stock`, `held_qty`,
  `add_to_release_all`, `earliest_hold_until`, `earliest_delivery_date`. Per line: occurrence and
  subscription ids, delivery_date, quantity, customer (id, name, masked phone), preferred_slot,
  held_since, hold_until, `expired` (past hold_until; the next run skips it).
- `GET /inventory?q=&status=short|low|ok`: every product with stock_now, promised, held_qty,
  `left_after_demand` = stock − promised − held, status (short < 0 ≤ low < LOW_STOCK_BUFFER ≤ ok),
  active_subscriptions, updated_at; short first; `counts` per status. `q`: name, brand, product id.
- `POST /inventory/<product_id>/restock` `{"quantity": 1..10000, "note"?}`: atomic
  `INSERT … ON CONFLICT DO UPDATE SET available_qty = available_qty + n` (creates a missing row) plus a
  RESTOCK movement; then releases holds. Returns `available_qty`, `added`, `holds: {status, released, out_of_stock}`.
- `POST /inventory/<product_id>/adjust` `{"available_qty": int >= 0, "reason": str}`: locks the row, sets the
  count, logs ADJUST with delta = new − old (nothing when unchanged); releases holds when it went up.
- `GET /inventory/<product_id>/movements?limit=50` (max 200): newest first.
- `POST /subscriptions/<id>/delay` `{"days": 1|2}` and `POST /subscriptions/<id>/skip` `{}`: see "Delay or skip"
  above. Return `action`, `previous_status` (STOCK_HOLD | NOTIFIED | DELAYED | UPCOMING), `delivery_date`,
  `new_delivery_date` or `next_delivery_date`, `customer_notified`, and `holds` when stock was freed.
- `GET /subscriptions?q=&product_id=&customer_id=&status=ACTIVE|PAUSED&page=1&page_size=25` (max 100):
  `q` matches product name, brand, product id, customer id, customer name (ILIKE, `%`/`_` literal) and
  phone digits (3+ digits). Rows: customer (masked phone), product, quantity, frequency_days,
  next_delivery_date, paused_until, preferred_slot, unit_price_at_subscription vs current_price,
  price_changed, `open_delivery` (STOCK_HOLD | NOTIFIED | DELAYED | null), `at_risk`, and `upcoming`
  (`{delivery_date, status, delay_options}`: what Delay / Skip would act on, null if nothing). Returns total + page.
  At-risk lines also carry `delay_options`.

Every phone number returned goes through `mask_phone`.

## Frontend (`admin-frontend/`)
Vite + React on 127.0.0.1:5174, API base `VITE_ADMIN_API_URL` (default `http://127.0.0.1:5002`). Product
images come from `../frontend/public` (served read-only via `publicDir`). Hash routes `#/at-risk` (default),
`#/inventory`, `#/subscriptions?product_id=&customer_id=&q=&status=&page=`. The nav shows a red At-risk count
(polls every 15 s and on window focus).

## Tests
`tests/test_stock_hold.py` (scheduler holds, releases, migration 004), `tests/test_admin_app.py` (factory,
guard, CORS, no auth import), `tests/test_admin_inventory.py` (restock, adjust, movements, ORDER movement),
`tests/test_admin_views.py` (at-risk, inventory, subscriptions search), `tests/test_admin_delivery_actions.py`
(delay, skip, notices, freed stock, limits, migration 006), `tests/test_cancellation_requests.py` (request,
Telegram and dashboard answers, first answer wins, withdraw, migration 007), `admin-frontend/tests` (helpers).

## Demo script
1. Lower one product's stock below a subscription's quantity (Inventory → Adjust).
2. Time-travel to the morning before that delivery:
   `curl -X POST "http://127.0.0.1:5002/api/dev/run-scheduler?now=<day-before>T09:00" -H "Content-Type: application/json" -d '{}'`
3. At risk shows the delivery, "customers are told at 6:00 PM", and no Telegram message was sent.
4. Restock from the At-risk card (pre-filled with the shortfall): the row clears and the normal reminder
   arrives in Telegram.
5. Optional: hold another one and time-travel past 18:00 to show the out-of-stock notice.
6. Optional: on another held or upcoming delivery, **Delay** 1 day or **Skip**: the customer's Telegram gets the
   "Due to operational delays" message right away.
7. Optional: **Subscriptions → Request cancellation**: Telegram shows "Please confirm" with Yes / Keep, and the
   customer's dashboard card shows the same question. Answer in either place; the other one updates.
