"""Scheduler jobs (remind, out of stock, auto-skip, resume, missed dates) and the dev
time-travel endpoint, against a throwaway schema on the local test database."""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import create_app
from app.models import DeliveryOccurrence, Inventory, Subscription, WhatsAppMessage
from app.subscriptions import jobs
from app.subscriptions import service as svc
from app.timeutil import LOCAL_TZ
from database.models import Customer, Product
from subscription_db import ME, MILK, OTHER, SOAP, TODAY, admin_engine, schema_engine  # noqa: F401


def at(day: int, hour: int = 9, minute: int = 0, month: int = 11) -> dt.datetime:
    """A moment in Asia/Kolkata, 2026."""
    return dt.datetime(2026, month, day, hour, minute, tzinfo=LOCAL_TZ)


@pytest.fixture
def engine(schema_engine):
    return schema_engine[0]          # ME and OTHER have WhatsApp; NOWA doesn't


def subscribe(engine, customer=ME, product=MILK, **data) -> int:
    """Created on TODAY (Tue 24 Nov). ME's preferred weekday is Saturday -> first delivery Sat 28 Nov."""
    with Session(engine) as s:
        sid = svc.create_subscription(s, customer, {"product_id": product, **data}, today=TODAY)["subscription_id"]
        s.commit()
    return sid


def run(engine, now):
    return jobs.run_scheduler(engine, now=now)


def state(engine, sid):
    with Session(engine) as s:
        sub = s.get(Subscription, sid)
        occs = {o.scheduled_date: (o.status, o.skip_reason) for o in s.scalars(
            select(DeliveryOccurrence).where(DeliveryOccurrence.subscription_id == sid))}
        msgs = [m.payload for m in s.scalars(select(WhatsAppMessage).order_by(WhatsAppMessage.id))]
        return sub.status, sub.next_delivery_date, occs, msgs


# ------------------------------------------------------------------ remind (job A)
def test_no_reminder_before_the_day_before(engine):
    sid = subscribe(engine)
    r = run(engine, at(26, 23, 59))
    assert r["reminded"] == [] and state(engine, sid)[2] == {}


def test_reminder_the_day_before(engine):
    sid = subscribe(engine, quantity=3)
    r = run(engine, at(27, 9))
    assert r["status"] == "ok" and r["reminded"] == [sid]
    _, next_date, occs, msgs = state(engine, sid)
    assert occs == {dt.date(2026, 11, 28): ("NOTIFIED", None)} and next_date == dt.date(2026, 11, 28)
    with Session(engine) as s:
        occ = s.scalars(select(DeliveryOccurrence)).one()
        assert occ.response_deadline == at(27, 21)                       # 21:00 IST the day before
    (msg,) = msgs
    assert msg["template"] == "subscription_delivery_reminder" and msg["to"] == "+919800000001"
    assert msg["params"] == {"product": "Milk", "quantity": 3, "date": "Sat, 28 Nov", "price": "₹81.00"}
    assert msg["buttons"] == [f"APPROVE:{occ.occurrence_id}", f"SKIP:{occ.occurrence_id}", f"STOP:{occ.occurrence_id}"]
    # idempotent: a second run (or the other process) does nothing
    assert run(engine, at(27, 9, 5))["reminded"] == [] and len(state(engine, sid)[3]) == 1


def test_late_reminder_still_gets_a_response_window(engine):
    subscribe(engine)
    run(engine, at(27, 20, 50))
    with Session(engine) as s:
        assert s.scalars(select(DeliveryOccurrence)).one().response_deadline == at(27, 21, 50)


def test_opted_out_after_subscribing_skips_without_message(engine):
    sid = subscribe(engine, customer=OTHER, frequency_days=7)   # OTHER prefers Monday -> 30 Nov
    with Session(engine) as s:
        s.get(Customer, OTHER).whatsapp_opt_in = False
        s.commit()
    r = run(engine, at(29, 9))
    assert r["no_whatsapp"] == [sid] and r["reminded"] == []
    _, next_date, occs, msgs = state(engine, sid)
    assert occs == {dt.date(2026, 11, 30): ("SKIPPED", "NO_RESPONSE")} and msgs == []
    assert next_date == dt.date(2026, 12, 7)
    with Session(engine) as s:                                  # opting back in: next cycle is reminded
        s.get(Customer, OTHER).whatsapp_opt_in = True
        s.commit()
    assert run(engine, at(6, 9, month=12))["reminded"] == [sid]


def test_price_change_is_flagged_in_reminder(engine):
    sid = subscribe(engine, quantity=2)
    with Session(engine) as s:
        s.get(Product, MILK).discount_price = Decimal("28.50")
        s.commit()
    run(engine, at(27, 9))
    assert state(engine, sid)[3][0]["params"]["price"] == "₹57.00 (price changed, was ₹54.00)"


@pytest.mark.parametrize("product,qty", [(SOAP, 1), (MILK, 6)])    # SOAP stock 0; MILK stock 5
def test_out_of_stock(engine, product, qty):
    sid = subscribe(engine, product=product, quantity=qty, frequency_days=3)
    r = run(engine, at(27, 9))
    assert r["out_of_stock"] == [sid]
    status, next_date, occs, msgs = state(engine, sid)
    assert occs == {dt.date(2026, 11, 28): ("OUT_OF_STOCK", "OUT_OF_STOCK")}
    assert status == "ACTIVE" and next_date == dt.date(2026, 12, 1)
    assert msgs[0]["template"] == "delivery_out_of_stock_notice" and msgs[0]["params"]["next_date"] == "Tue, 01 Dec"


def test_stock_check_does_not_reserve(engine):
    subscribe(engine, quantity=5)
    run(engine, at(27, 9))
    with Session(engine) as s:
        assert s.get(Inventory, MILK).available_qty == 5


def test_paused_and_cancelled_are_not_reminded(engine):
    paused, cancelled = subscribe(engine), subscribe(engine, product=SOAP)
    with Session(engine) as s:
        svc.update_subscription(s, ME, paused, {"status": "PAUSED"}, today=TODAY)
        svc.cancel_subscription(s, ME, cancelled)
        s.commit()
    r = run(engine, at(27, 9))
    assert r["reminded"] == r["out_of_stock"] == []


def test_missed_delivery_rolls_forward(engine):
    sid = subscribe(engine)                                   # 28 Nov, weekly
    r = run(engine, at(3, 9, month=12))                       # scheduler was down for a week
    assert r["missed"] == [sid] and r["reminded"] == []
    _, next_date, occs, _ = state(engine, sid)
    assert occs == {} and next_date == dt.date(2026, 12, 5)   # first weekly date >= today (3 Dec) + 2


# ------------------------------------------------------------------ auto-skip (job B)
def test_auto_skip_after_deadline(engine):
    sid = subscribe(engine, frequency_days=7)
    run(engine, at(27, 9))
    assert run(engine, at(27, 20, 59))["auto_skipped"] == []         # still time to reply
    r = run(engine, at(27, 21, 1))
    assert len(r["auto_skipped"]) == 1
    status, next_date, occs, msgs = state(engine, sid)
    assert occs[dt.date(2026, 11, 28)] == ("SKIPPED", "NO_RESPONSE")
    assert status == "ACTIVE" and next_date == dt.date(2026, 12, 5)
    assert msgs[-1]["template"] == "delivery_skipped_notice"
    assert msgs[-1]["params"] == {"product": "Milk", "date": "Sat, 28 Nov", "next_date": "Sat, 05 Dec"}


def test_full_cycle_with_time_travel(engine):
    sid = subscribe(engine, frequency_days=7)
    run(engine, at(27, 9))                      # reminder for 28 Nov
    run(engine, at(27, 22))                     # no reply -> skipped, next 5 Dec
    r = run(engine, at(4, 9, month=12))         # reminder for 5 Dec
    assert r["reminded"] == [sid]
    assert set(state(engine, sid)[2]) == {dt.date(2026, 11, 28), dt.date(2026, 12, 5)}


# ------------------------------------------------------------------ resume
def test_paused_until_resumes_and_gets_reminded(engine):
    sid = subscribe(engine)
    with Session(engine) as s:
        svc.update_subscription(s, ME, sid, {"status": "PAUSED", "paused_until": "2026-12-08"}, today=TODAY)
        s.commit()
    assert run(engine, at(7, 9, month=12))["resumed"] == []
    r = run(engine, at(8, 9, month=12))
    assert r["resumed"] == [sid]
    status, next_date, _, _ = state(engine, sid)
    assert status == "ACTIVE" and next_date == dt.date(2026, 12, 12)  # weekly cadence from 28 Nov, >= today + 2


# ------------------------------------------------------------------ concurrency / errors
def test_concurrent_run_is_skipped(engine):
    subscribe(engine)
    with engine.connect() as other:
        other.execute(text(f"SELECT pg_advisory_lock({jobs.ADVISORY_LOCK_KEY})"))
        assert run(engine, at(27, 9))["status"] == "busy"
        other.execute(text(f"SELECT pg_advisory_unlock({jobs.ADVISORY_LOCK_KEY})"))
    assert run(engine, at(27, 9))["status"] == "ok"


def test_one_failure_does_not_stop_the_run(engine):
    good = subscribe(engine, first_delivery_date="2026-11-30")
    bad = subscribe(engine, customer=OTHER)  # also 30 Nov (Monday)

    class FailingForOther:
        def send_template(self, session, *, customer, **kw):
            if customer.customer_id == OTHER:
                raise RuntimeError("boom")

    r = jobs.run_scheduler(engine, now=at(29, 9), sender=FailingForOther())
    assert r["status"] == "partial" and r["reminded"] == [good] and "boom" in r["errors"][0]
    assert state(engine, bad)[2] == {}             # rolled back; retried on the next run


def test_now_must_be_aware(engine):
    with pytest.raises(ValueError):
        jobs.run_scheduler(engine, now=dt.datetime(2026, 11, 27, 9))


# ------------------------------------------------------------------ dev endpoint
def test_dev_endpoint(schema_engine, engine):
    _, schema = schema_engine
    sid = subscribe(engine)
    off = create_app("test", overrides={"DB_SEARCH_PATH": schema})
    assert off.test_client().post("/api/dev/run-scheduler").status_code == 404
    off.extensions["db_engine"].dispose()

    app = create_app("test", overrides={"DB_SEARCH_PATH": schema, "DEV_ENDPOINTS": True})
    c = app.test_client()
    assert c.post("/api/dev/run-scheduler?now=yesterday").status_code == 400
    r = c.post("/api/dev/run-scheduler?now=2026-11-27T09:00")       # no offset -> IST
    assert r.status_code == 200 and r.json["reminded"] == [sid] and r.json["today"] == "2026-11-27"
    app.extensions["db_engine"].dispose()
