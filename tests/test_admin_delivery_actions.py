"""Admin Delay / Skip of a delivery for operational reasons: held, reminded, delayed and
upcoming deliveries; the Telegram notices; stock freed by a skipped reminder; limits and
errors. Throwaway schema: MILK 5 in stock, SOAP 0. ME's deliveries are on Saturdays (first
Sat 28 Nov), OTHER's on Mondays. The admin's clock is pinned to Fri 27 Nov 10:00 IST."""
import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.admin import create_admin_app
from app.admin import service as admin_svc
from app.errors import Conflict
from app.models import Customer, DeliveryOccurrence, Subscription, WhatsAppMessage
from app.notifications.sender import TEMPLATE_OPS_DELAYED, TEMPLATE_OPS_SKIPPED, render_text
from app.subscriptions import jobs
from app.subscriptions import service as svc
from subscription_db import ME, MILK, OTHER, SOAP, test_env, admin_engine, schema_engine  # noqa: F401
from test_scheduler import at, run, subscribe

BASE = "http://localhost:5002"
SAT, SUN, MON = dt.date(2026, 11, 28), dt.date(2026, 11, 29), dt.date(2026, 11, 30)
MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "sql" / "006_operational_skip.sql"


@pytest.fixture
def engine(schema_engine):
    return schema_engine[0]


@pytest.fixture
def client(schema_engine, monkeypatch):
    monkeypatch.setattr(jobs, "now_utc", lambda: at(27, 10))
    monkeypatch.setattr(admin_svc, "now_utc", lambda: at(27, 10))
    app = create_admin_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    yield app.test_client()
    app.extensions["db_engine"].dispose()


def delay(client, sid, days=1, **extra):
    return client.post(f"/api/admin/subscriptions/{sid}/delay", json={"days": days, **extra}, base_url=BASE)


def skip(client, sid, body=None):
    return client.post(f"/api/admin/subscriptions/{sid}/skip", json={} if body is None else body, base_url=BASE)


def occs(engine, sid):
    with Session(engine) as s:
        return {o.scheduled_date: (o.status, o.skip_reason, o.delay_days) for o in s.scalars(
            select(DeliveryOccurrence).where(DeliveryOccurrence.subscription_id == sid))}


def next_date(engine, sid):
    with Session(engine) as s:
        return s.get(Subscription, sid).next_delivery_date


def notices(engine, customer):
    """Operational notices sent to a customer: (template, params)."""
    with Session(engine) as s:
        return [(m.payload["template"], m.payload["params"]) for m in s.scalars(
            select(WhatsAppMessage).where(WhatsAppMessage.customer_id == customer).order_by(WhatsAppMessage.id))
            if m.payload.get("template") in (TEMPLATE_OPS_DELAYED, TEMPLATE_OPS_SKIPPED)]


# ------------------------------------------------------------------ messages
def test_notice_texts_cite_operational_delays():
    delayed = render_text(TEMPLATE_OPS_DELAYED, {"product": "Milk", "date": "Sat, 28 Nov", "new_date": "Sun, 29 Nov"})
    skipped = render_text(TEMPLATE_OPS_SKIPPED, {"product": "Milk", "date": "Sat, 28 Nov", "next_date": "Sat, 5 Dec"})
    assert delayed.startswith("Due to operational delays, your Milk delivery for Sat, 28 Nov has moved to Sun, 29 Nov.")
    assert "not been charged" in delayed and "reminder" in delayed
    assert skipped.startswith("Due to operational delays, we can't deliver your Milk on Sat, 28 Nov")
    assert "Sat, 5 Dec" in skipped and "not been charged" in skipped


# ------------------------------------------------------------------ delay
def test_delay_a_reminded_delivery(engine, client):
    sid = subscribe(engine)
    run(engine, at(27, 9))                                            # reminder for Sat 28 is out
    r = delay(client, sid, 1)
    assert r.status_code == 200
    assert (r.json["action"], r.json["previous_status"], r.json["delivery_date"], r.json["new_delivery_date"]) == \
        ("delayed", "NOTIFIED", "2026-11-28", "2026-11-29")
    assert r.json["customer_notified"] is True and r.json["holds"]["status"] == "ok"
    assert occs(engine, sid) == {SAT: ("DELAYED", None, 1)}
    assert next_date(engine, sid) == SAT                              # the cycle doesn't move
    assert notices(engine, ME) == [(TEMPLATE_OPS_DELAYED, {"product": "Milk", "date": "Sat, 28 Nov",
                                                           "new_date": "Sun, 29 Nov"})]
    with Session(engine) as s:                                        # the old reminder's buttons are dead
        occ_id = r.json["occurrence_id"]
        with pytest.raises(Conflict):
            svc.approve_delivery(s, ME, occ_id, "09:00-12:00", now=at(27, 11))
    # a fresh reminder goes out the day before the new date, to be approved again
    assert run(engine, at(28, 9))["reminded"] == [sid]
    assert occs(engine, sid) == {SAT: ("NOTIFIED", None, 1)}


def test_delay_a_held_delivery_leaves_at_risk_and_is_checked_again(engine, client):
    sid = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))                                            # SOAP has no stock: held
    assert client.get("/api/admin/at-risk", base_url=BASE).json["total"] == 1
    r = delay(client, sid, 2)
    assert r.status_code == 200 and r.json["previous_status"] == "STOCK_HOLD" and "holds" not in r.json
    assert client.get("/api/admin/at-risk", base_url=BASE).json["total"] == 0
    with Session(engine) as s:
        occ = s.get(DeliveryOccurrence, r.json["occurrence_id"])
        assert (occ.status, occ.hold_until, jobs.delivery_date(occ)) == ("DELAYED", None, MON)
    assert run(engine, at(29, 9))["held"] == [sid]                    # still short the day before: held again


def test_delay_the_upcoming_delivery_before_any_reminder(engine, client):
    sid = subscribe(engine, customer=OTHER)                           # Mon 30 Nov, no reminder yet on Fri
    r = delay(client, sid, 2)
    assert r.status_code == 200 and r.json["previous_status"] == "UPCOMING"
    assert occs(engine, sid) == {MON: ("DELAYED", None, 2)} and next_date(engine, sid) == MON
    assert run(engine, at(29, 9))["reminded"] == []                   # no reminder for Monday any more
    assert run(engine, at(1, 9, month=12))["reminded"] == [sid]       # but one for Wed 2 Dec


def test_delay_limits(engine, client):
    sid = subscribe(engine)
    assert delay(client, sid, 2).status_code == 200
    r = delay(client, sid, 1)                                         # 2 days is the most in total
    assert r.status_code == 409 and r.json["error"]["code"] == "delay_not_possible"
    assert r.json["error"]["options"] == []
    assert occs(engine, sid) == {SAT: ("DELAYED", None, 2)}


@pytest.mark.parametrize("body", [{"days": 3}, {"days": 0}, {"days": "1"}, {"days": True}, {},
                                  {"days": 1, "note": "x"}])
def test_delay_validation(engine, client, body):
    sid = subscribe(engine)
    r = client.post(f"/api/admin/subscriptions/{sid}/delay", json=body, base_url=BASE)
    assert r.status_code == 400 and occs(engine, sid) == {}


# ------------------------------------------------------------------ skip
def test_skip_a_reminded_delivery(engine, client):
    sid = subscribe(engine)
    run(engine, at(27, 9))
    r = skip(client, sid)
    assert r.status_code == 200
    assert (r.json["action"], r.json["previous_status"], r.json["delivery_date"], r.json["next_delivery_date"]) == \
        ("skipped", "NOTIFIED", "2026-11-28", "2026-12-05")
    assert occs(engine, sid) == {SAT: ("SKIPPED", "OPERATIONAL", None)}
    assert notices(engine, ME) == [(TEMPLATE_OPS_SKIPPED, {"product": "Milk", "date": "Sat, 28 Nov",
                                                           "next_date": "Sat, 05 Dec"})]


def test_skip_a_held_delivery(engine, client):
    sid = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    r = skip(client, sid)
    assert r.status_code == 200 and r.json["previous_status"] == "STOCK_HOLD"
    assert occs(engine, sid) == {SAT: ("SKIPPED", "OPERATIONAL", None)} and next_date(engine, sid) == dt.date(2026, 12, 5)
    assert client.get("/api/admin/at-risk", base_url=BASE).json["total"] == 0


def test_skip_a_delayed_delivery(engine, client):
    sid = subscribe(engine)
    delay(client, sid, 1)
    r = skip(client, sid)
    assert r.status_code == 200 and r.json["previous_status"] == "DELAYED" and r.json["delivery_date"] == "2026-11-29"
    assert occs(engine, sid) == {SAT: ("SKIPPED", "OPERATIONAL", 1)} and next_date(engine, sid) == dt.date(2026, 12, 5)


def test_skip_the_upcoming_delivery_before_any_reminder(engine, client):
    sid = subscribe(engine, customer=OTHER)
    r = skip(client, sid)
    assert r.status_code == 200 and r.json["previous_status"] == "UPCOMING"
    assert occs(engine, sid) == {MON: ("SKIPPED", "OPERATIONAL", None)}
    assert next_date(engine, sid) == dt.date(2026, 12, 7)
    assert run(engine, at(29, 9))["reminded"] == []


def test_skipping_a_reminder_frees_its_stock_for_a_held_delivery(engine, client):
    """MILK has 5: ME's reminder for 3 is out, so OTHER's 3 is held. Skipping ME's frees the stock."""
    reminded = subscribe(engine, quantity=3)
    held = subscribe(engine, customer=OTHER, quantity=3, first_delivery_date="2026-11-28")
    assert run(engine, at(27, 9))["held"] == [held]
    r = skip(client, reminded)
    assert r.json["holds"] == {"status": "ok", "released": 1, "out_of_stock": 0}
    assert occs(engine, held) == {SAT: ("NOTIFIED", None, None)}


# ------------------------------------------------------------------ what can't be changed
def test_approved_delivery_is_never_touched(engine, client):
    sid = subscribe(engine)
    run(engine, at(27, 9))
    with Session(engine) as s:
        occ_id = s.scalar(select(DeliveryOccurrence.occurrence_id).where(DeliveryOccurrence.subscription_id == sid))
        svc.approve_delivery(s, ME, occ_id, "09:00-12:00", now=at(27, 9, 30))
        s.commit()
    r = skip(client, sid)                                             # acts on the next cycle (Sat 5 Dec)
    assert r.status_code == 200 and r.json["delivery_date"] == "2026-12-05"
    assert occs(engine, sid)[SAT] == ("ORDER_PLACED", None, None)


def test_delivery_today_or_past_cannot_change(engine, client, monkeypatch):
    sid = subscribe(engine)
    run(engine, at(27, 9))
    monkeypatch.setattr(admin_svc, "now_utc", lambda: at(28, 8))      # the delivery day
    for r in (delay(client, sid), skip(client, sid)):
        assert r.status_code == 409 and r.json["error"]["code"] == "delivery_finalised"
    assert occs(engine, sid) == {SAT: ("NOTIFIED", None, None)}


def test_paused_and_cancelled_subscriptions(engine, client):
    paused, cancelled = subscribe(engine), subscribe(engine, product=SOAP)
    with Session(engine) as s:
        svc.update_subscription(s, ME, paused, {"status": "PAUSED"}, today=dt.date(2026, 11, 27))
        svc.cancel_subscription(s, ME, cancelled)
        s.commit()
    for sid in (paused, cancelled):
        for r in (delay(client, sid), skip(client, sid)):
            assert r.status_code == 409 and r.json["error"]["code"] == "not_active"


def test_unknown_subscription_and_bad_bodies(engine, client):
    assert delay(client, 999999).status_code == 404 and skip(client, 999999).status_code == 404
    sid = subscribe(engine)
    assert skip(client, sid, {"reason": "x"}).status_code == 400
    assert skip(client, sid, []).status_code == 400
    r = client.post(f"/api/admin/subscriptions/{sid}/skip", base_url=BASE)   # no JSON body: guard
    assert r.status_code == 415 and occs(engine, sid) == {}


def test_unreachable_customer_still_changes_but_is_not_notified(engine, client):
    sid = subscribe(engine)
    with Session(engine) as s:
        s.execute(update(Customer).where(Customer.customer_id == ME).values(whatsapp_opt_in=False))
        s.commit()
    r = skip(client, sid)
    assert r.status_code == 200 and r.json["customer_notified"] is False and notices(engine, ME) == []


# ------------------------------------------------------------------ what the pages show
def test_views_show_what_delay_and_skip_act_on(engine, client):
    reminded = subscribe(engine)
    held = subscribe(engine, customer=OTHER, product=SOAP, first_delivery_date="2026-11-28")
    upcoming = subscribe(engine, customer=OTHER)
    run(engine, at(27, 9))
    delay(client, reminded, 1)
    rows = {i["subscription_id"]: i for i in client.get("/api/admin/subscriptions", base_url=BASE).json["items"]}
    assert rows[reminded]["upcoming"] == {"delivery_date": "2026-11-29", "status": "DELAYED", "delay_options": [1]}
    assert rows[reminded]["open_delivery"] == "DELAYED"
    assert rows[held]["upcoming"] == {"delivery_date": "2026-11-28", "status": "STOCK_HOLD", "delay_options": [1, 2]}
    assert rows[upcoming]["upcoming"] == {"delivery_date": "2026-11-30", "status": "UPCOMING", "delay_options": [1, 2]}
    (g,) = client.get("/api/admin/at-risk", base_url=BASE).json["products"]
    assert g["lines"][0]["delay_options"] == [1, 2]


def test_migration_006_applies_and_constraint_holds(engine):
    sql = MIGRATION.read_text().replace("BEGIN;", "").replace("COMMIT;", "")
    with engine.begin() as conn:
        conn.execute(text(sql))
    sid = subscribe(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO delivery_occurrences (subscription_id, scheduled_date, status, skip_reason) "
                          "VALUES (:s, '2026-11-28', 'SKIPPED', 'OPERATIONAL')"), {"s": sid})
        bad = conn.begin_nested()
        with pytest.raises(Exception, match="delivery_occurrences_skip_reason_check"):
            conn.execute(text("UPDATE delivery_occurrences SET skip_reason = 'BOGUS' WHERE subscription_id = :s"),
                         {"s": sid})
        bad.rollback()
