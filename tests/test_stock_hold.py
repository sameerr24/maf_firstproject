"""Stock holds: a delivery the stock can't cover at reminder time is held (STOCK_HOLD, no
message) until 18:00 IST the day before delivery. A restock releases it (the normal reminder
goes out); an unresolved hold becomes OUT_OF_STOCK and the customer is told. Throwaway schema
on the local test database; MILK starts with 5 in stock, SOAP with 0."""
import datetime as dt
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import DeliveryOccurrence, Inventory, Subscription, WhatsAppMessage
from app.subscriptions import jobs
from app.subscriptions import service as svc
from subscription_db import ME, MILK, OTHER, SOAP, TODAY, test_env, admin_engine, schema_engine  # noqa: F401
from test_scheduler import at, engine, run, state, subscribe  # noqa: F401

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "sql" / "004_stock_hold.sql"
SAT = dt.date(2026, 11, 28)          # ME's first delivery; the reminder run is at(27, 9)


def set_stock(engine, product, qty):
    with Session(engine) as s:
        s.get(Inventory, product).available_qty = qty
        s.commit()


def occurrence(engine, sid) -> DeliveryOccurrence:
    with Session(engine, expire_on_commit=False) as s:
        return s.scalars(select(DeliveryOccurrence).where(DeliveryOccurrence.subscription_id == sid)
                         .order_by(DeliveryOccurrence.occurrence_id.desc())).first()


def templates(engine) -> list[str]:
    with Session(engine) as s:
        return [m.payload["template"] for m in s.scalars(select(WhatsAppMessage).order_by(WhatsAppMessage.id))]


def test_hold_records_until_18_00_the_day_before(engine):
    sid = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    occ = occurrence(engine, sid)
    assert occ.status == "STOCK_HOLD" and occ.hold_until == at(27, 18)
    assert occ.notified_at is None and occ.response_deadline is None and occ.skip_reason is None


def test_restock_releases_the_hold_with_the_normal_reminder(engine):
    sid = subscribe(engine, product=SOAP, quantity=2)
    run(engine, at(27, 9))
    set_stock(engine, SOAP, 2)
    r = jobs.release_holds(engine, SOAP, now=at(27, 11))
    assert r["released"] == [sid] and r["status"] == "ok"
    occ = occurrence(engine, sid)
    assert occ.status == "NOTIFIED" and occ.notified_at == at(27, 11)
    assert occ.response_deadline == at(27, 21)                     # the usual 21:00 deadline
    assert templates(engine) == ["subscription_delivery_reminder"]
    with Session(engine) as s:                                     # and it can be approved as usual
        assert svc.approve_delivery(s, ME, occ.occurrence_id, "07:00-09:00", now=at(27, 12))["status"] == "order_placed"
        s.commit()
        assert s.get(Inventory, SOAP).available_qty == 0


def test_scheduler_run_also_releases_after_restock(engine):
    sid = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    set_stock(engine, SOAP, 1)
    r = run(engine, at(27, 9, 5))
    assert r["released"] == [sid] and occurrence(engine, sid).status == "NOTIFIED"


def test_restock_too_small_keeps_the_hold(engine):
    sid = subscribe(engine, product=SOAP, quantity=3)
    run(engine, at(27, 9))
    set_stock(engine, SOAP, 2)
    r = jobs.release_holds(engine, SOAP, now=at(27, 11))
    assert r["released"] == [] and occurrence(engine, sid).status == "STOCK_HOLD" and templates(engine) == []


def test_too_late_to_hold_goes_straight_to_out_of_stock(engine):
    sid = subscribe(engine, product=SOAP)
    r = run(engine, at(27, 19))                                    # first run after 18:00 the day before
    assert r["out_of_stock"] == [sid] and r["held"] == []
    assert templates(engine) == ["delivery_out_of_stock_notice"]


def test_release_after_hold_time_expires_instead(engine):
    sid = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    set_stock(engine, SOAP, 1)
    r = jobs.release_holds(engine, SOAP, now=at(27, 18, 30))       # restocked too late
    assert r["out_of_stock"] == [sid] and r["released"] == []
    _, next_date, occs, _ = state(engine, sid)
    assert occs == {SAT: ("OUT_OF_STOCK", "OUT_OF_STOCK")} and next_date == dt.date(2026, 12, 5)


def test_stock_promised_to_open_reminders_is_counted(engine):
    """Stock 5, two subscriptions of 3 due the same day: the first is reminded, the second is
    held (before, both were reminded and the second approval failed)."""
    first = subscribe(engine, quantity=3)
    second = subscribe(engine, customer=OTHER, quantity=3, first_delivery_date=SAT.isoformat())
    r = run(engine, at(27, 9))
    assert r["reminded"] == [first] and r["held"] == [second]
    with Session(engine) as s:                                     # first approves: 2 left, still short
        svc.approve_delivery(s, ME, occurrence(engine, first).occurrence_id, "07:00-09:00", now=at(27, 10))
        s.commit()
    assert jobs.release_holds(engine, MILK, now=at(27, 10))["released"] == []
    set_stock(engine, MILK, 3)
    assert jobs.release_holds(engine, MILK, now=at(27, 11))["released"] == [second]


def test_expired_hold_never_takes_stock_from_a_later_one(engine):
    later = subscribe(engine, customer=OTHER, product=SOAP, quantity=2,
                      first_delivery_date=(SAT + dt.timedelta(days=1)).isoformat())
    sooner = subscribe(engine, product=SOAP, quantity=2)
    assert run(engine, at(27, 9))["held"] == [sooner]              # 28 Nov, held until 27 Nov 18:00
    r = run(engine, at(28, 9))
    assert r["out_of_stock"] == [sooner] and r["held"] == [later]  # expired first, then 29 Nov held
    set_stock(engine, SOAP, 2)                                     # enough for one: goes to the live hold
    assert jobs.release_holds(engine, SOAP, now=at(28, 10))["released"] == [later]


def test_release_order_same_day_is_by_subscription(engine):
    a = subscribe(engine, product=SOAP)
    b = subscribe(engine, customer=OTHER, product=SOAP, first_delivery_date=SAT.isoformat())
    run(engine, at(27, 9))
    set_stock(engine, SOAP, 1)
    r = jobs.release_holds(engine, SOAP, now=at(27, 11))
    assert r["released"] == [min(a, b)] and occurrence(engine, max(a, b)).status == "STOCK_HOLD"


def test_release_for_one_product_leaves_others(engine):
    soap = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    set_stock(engine, SOAP, 1)
    assert jobs.release_holds(engine, MILK, now=at(27, 11))["released"] == []
    assert occurrence(engine, soap).status == "STOCK_HOLD"


def test_pause_cancel_and_skip_clear_a_hold(engine):
    paused = subscribe(engine, product=SOAP)
    cancelled = subscribe(engine, customer=OTHER, product=SOAP, first_delivery_date=SAT.isoformat())
    run(engine, at(27, 9))
    with Session(engine) as s:
        svc.update_subscription(s, ME, paused, {"status": "PAUSED"}, today=TODAY)
        svc.cancel_subscription(s, OTHER, cancelled)
        s.commit()
    assert occurrence(engine, paused).status == "CANCELLED" and occurrence(engine, cancelled).status == "CANCELLED"
    set_stock(engine, SOAP, 5)
    assert jobs.release_holds(engine, now=at(27, 11))["released"] == [] and templates(engine) == []


def test_skip_next_while_held(engine):
    sid = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    with Session(engine) as s:
        svc.skip_next_delivery(s, ME, sid, today=dt.date(2026, 11, 27))
        s.commit()
        assert s.get(Subscription, sid).next_delivery_date == dt.date(2026, 12, 5)
    assert occurrence(engine, sid).status == "SKIPPED"


def test_migration_004_applies_to_an_existing_schema(engine):
    """The SQL migration runs cleanly (idempotent column/index, constraint swap) and the
    constraint accepts STOCK_HOLD but still rejects unknown statuses."""
    sql = MIGRATION.read_text().replace("BEGIN;", "").replace("COMMIT;", "")
    with engine.begin() as conn:
        conn.execute(text(sql))
    sid = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    with engine.begin() as conn:
        bad = conn.begin_nested()
        try:
            conn.execute(text("UPDATE delivery_occurrences SET status = 'BOGUS' WHERE subscription_id = :s"), {"s": sid})
            raise AssertionError("constraint accepted an unknown status")
        except Exception as e:
            assert "delivery_occurrences_status_check" in str(e)
            bad.rollback()
    assert occurrence(engine, sid).status == "STOCK_HOLD"
