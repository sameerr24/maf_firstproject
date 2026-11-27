# Admin panel (inventory management)

A local-only admin panel for the subscriptions module: deliveries **at risk** of being missed for
lack of stock, **inventory** with restock / adjust / history, and a read-only **subscriptions** search.
Design notes: `docs/ADMIN_PANEL_CONTEXT.md`.

> **Localhost only, no login, by design.** This proof of concept assumes only the admin can use the
> machine it runs on. Both servers bind to `127.0.0.1`; the API refuses any other `Host` header and
> any write without a JSON body. Don't expose or tunnel ports 5002 / 5174. A production version would
> add role-based admin login.

## Run it

Needs the same `.env` as the customer app (`DATABASE_URL`, and `TELEGRAM_BOT_TOKEN` so released
reminders reach Telegram). From the repository root:

```bash
# 1. Admin API on http://127.0.0.1:5002 (separate from the customer API on 5001)
.venv/bin/python run_admin.py
#    with demo time travel at /api/dev/run-scheduler:
DEV_ENDPOINTS=1 .venv/bin/python run_admin.py

# 2. Admin frontend on http://127.0.0.1:5174
cd admin-frontend
npm install
npm run dev
```

Optional settings (defaults shown) in `.env`:

```
ADMIN_PORT=5002
ADMIN_CORS_ORIGINS=http://localhost:5174,http://127.0.0.1:5174
```

and for the frontend, `VITE_ADMIN_API_URL=http://127.0.0.1:5002` in `admin-frontend/.env.local`.

The admin app never runs the scheduler; the customer app (or time travel) does. The full local
setup is: customer API (5001), customer frontend (5173), admin API (5002), admin frontend (5174),
and the Telegram bot (`flask --app run telegram-poll`, one machine only).

Database: migrations `migrations/sql/004_stock_hold.sql` and `005_inventory_movements.sql` must be
applied (they are, on the shared Supabase database).

## Pages

- **At risk** (`#/at-risk`, default): when a reminder is due but the stock can't cover the delivery,
  the scheduler holds it (no message to the customer) until 18:00 the day before delivery. Each
  product card shows stock, demand and how much to add; **Restock** is pre-filled with that amount.
  Once restocked, the customer's normal reminder goes out right away. Not restocked by 18:00: the
  delivery is skipped and the customer gets the out-of-stock message.
- **Inventory** (`#/inventory`): every product with stock, units promised to reminders awaiting a
  reply, held units, what's left and a status (short / low / ok). Restock, Adjust (absolute count,
  reason required) and History (restocks, adjustments, subscription orders).
- **Subscriptions** (`#/subscriptions`): active and paused subscriptions; search by product, brand,
  customer name or ID, or phone digits; click a product or customer to filter. Phone numbers are
  always masked.

## Demo

1. **Inventory → Adjust** a product below one subscription's quantity (reason: "demo").
2. Time-travel to the morning before that delivery:
   ```bash
   curl -X POST "http://127.0.0.1:5002/api/dev/run-scheduler?now=2026-12-04T09:00" \
        -H "Content-Type: application/json" -d '{}'
   ```
   (the admin API requires a JSON body on every POST; times without an offset are IST)
3. **At risk** shows the held delivery and when the customer would be told. No Telegram message yet.
4. **Restock** from the card: the row clears and the reminder arrives in Telegram.

## Scripts

```bash
npm run dev     # dev server, 127.0.0.1:5174
npm run build   # production build in dist/
npm test        # unit tests for the URL, routing and formatting helpers (node --test)
```

Backend tests (from the repository root): `.venv/bin/python -m pytest tests/test_admin_*.py tests/test_stock_hold.py`.
