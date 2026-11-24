"""Subscription service layer, against the local PostgreSQL test database.

Each test gets a fresh schema with Sameer's tables + the subscription tables, so the real
constraints (uq_live_subscription, uq_occurrence_per_cycle, CHECKs) are exercised.
Skipped when TEST_DATABASE_URL isn't set.
"""
import datetime as dt
from decimal import Decimal

import pytest
from flask import Flask
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import constants as C
from app.errors import Conflict, NotFound, ValidationError, register_error_handlers
from app.models import DeliveryOccurrence, Subscription
from app.subscriptions import service as svc
from database.models import Customer, Product
from subscription_db import ME, MILK, NOWA, OTHER, SOAP, TODAY, TV, no_real_telegram, admin_engine, schema_engine, session  # noqa: F401


def create(s, customer=ME, **data):
    return svc.create_subscription(s, customer, {"product_id": MILK, **data}, today=TODAY)


def occurrences(s, sub_id):
    return {o.scheduled_date: o for o in s.scalars(
        select(DeliveryOccurrence).where(DeliveryOccurrence.subscription_id == sub_id))}


# ------------------------------------------------------------------ product / draft
def test_product_offer_shows_saving(session):
    o = svc.product_offer(session, MILK, customer_id=ME)
    assert (o["regular_price"], o["subscriber_price"], o["saving_per_unit"], o["saving_percent"]) == \
        ("30.00", "27.00", "3.00", 10.0)
    assert o["subscribable"] and o["in_stock"] and o["has_discount"] and o["subscription_id"] is None
    soap = svc.product_offer(session, SOAP)   # sold at MRP: no subscriber discount
    assert soap["in_stock"] is False and soap["has_discount"] is False
    assert (soap["saving_per_unit"], soap["saving_percent"]) == ("0.00", 0.0)
    assert svc.product_offer(session, TV)["subscribable"] is False
    with pytest.raises(NotFound):
        svc.product_offer(session, "nope")


def test_draft_uses_defaults_and_preferred_weekday(session):
    d = svc.subscription_draft(session, ME, MILK, today=TODAY)
    assert (d["quantity"], d["frequency_days"]) == (C.DEFAULT_QUANTITY, C.DEFAULT_FREQUENCY_DAYS)
    assert d["first_delivery_date"] == "2026-11-28"          # first Saturday >= today + 2
    assert d["recommendation"]["source"] == "default"
    assert d["saving_per_delivery"] == "3.00" and d["existing_subscription"] is None
    assert d["limits"]["quantity"] == [1, 100]


def test_draft_rejects_non_replenishable(session):
    with pytest.raises(ValidationError) as e:
        svc.subscription_draft(session, ME, TV, today=TODAY)
    assert e.value.code == "not_subscribable"


def test_default_date_respects_lead_time():
    c = Customer(preferred_weekday="Wed")
    assert svc.default_first_delivery_date(c, TODAY) == dt.date(2026, 12, 2)  # Wed the 25th is too soon
    c.preferred_weekday = "Thu"
    assert svc.default_first_delivery_date(c, TODAY) == dt.date(2026, 11, 26)


# ------------------------------------------------------------------ create
def test_create_locks_in_subscriber_price(session):
    sub = create(session, quantity=4, frequency_days=3, preferred_slot="07:00-09:00")
    assert sub["status"] == "ACTIVE" and sub["unit_price"] == "27.00"
    assert sub["total_per_delivery"] == "108.00" and sub["saving_per_delivery"] == "12.00"
    assert sub["next_delivery_date"] == "2026-11-28" and sub["created_at"]
    assert svc.subscription_draft(session, ME, MILK, today=TODAY)["existing_subscription"]["subscription_id"] == \
        sub["subscription_id"]


@pytest.mark.parametrize("data,field", [
    ({"quantity": 0}, "quantity"), ({"quantity": 101}, "quantity"), ({"quantity": True}, "quantity"),
    ({"quantity": "5"}, "quantity"), ({"quantity": 2.5}, "quantity"),
    ({"frequency_days": 91}, "frequency_days"), ({"frequency_days": 0}, "frequency_days"),
    ({"first_delivery_date": "2026-11-25"}, "first_delivery_date"),   # inside lead time
    ({"first_delivery_date": "2027-12-01"}, "first_delivery_date"),   # too far ahead
    ({"first_delivery_date": "28/11/2026"}, "first_delivery_date"),
    ({"preferred_slot": "anytime"}, "preferred_slot"),
])
def test_create_validation(session, data, field):
    with pytest.raises(ValidationError) as e:
        create(session, **data)
    assert e.value.details.get("field") == field


def test_create_accepts_limits(session):
    assert create(session, quantity=100, frequency_days=90)["quantity"] == 100


def test_create_rejects_unknown_fields_and_customer_id_in_body(session):
    with pytest.raises(ValidationError, match="customer_id"):
        create(session, customer_id=OTHER)


def test_one_live_subscription_per_product(session):
    first = create(session)
    with pytest.raises(Conflict) as e:
        create(session, quantity=2)
    assert e.value.code == "already_subscribed" and e.value.details["subscription_id"] == first["subscription_id"]
    svc.cancel_subscription(session, ME, first["subscription_id"])
    assert create(session, quantity=2)["quantity"] == 2       # allowed again after cancelling
    assert create(session, customer=OTHER)["status"] == "ACTIVE"  # other customers unaffected


def test_live_subscription_index_enforced_in_db(session):
    create(session)
    session.add(Subscription(customer_id=ME, product_id=MILK, quantity=1, frequency_days=7,
                             unit_price_at_subscription=Decimal("27"), status="PAUSED"))
    with pytest.raises(IntegrityError, match="uq_live_subscription"):
        session.flush()


# ------------------------------------------------------------------ ownership
def test_other_customers_subscription_is_not_found(session):
    sid = create(session)["subscription_id"]
    for call in (lambda: svc.get_subscription(session, OTHER, sid),
                 lambda: svc.update_subscription(session, OTHER, sid, {"quantity": 2}, today=TODAY),
                 lambda: svc.skip_next_delivery(session, OTHER, sid, today=TODAY),
                 lambda: svc.cancel_subscription(session, OTHER, sid)):
        with pytest.raises(NotFound):
            call()
    assert svc.get_subscription(session, ME, sid)["status"] == "ACTIVE"
    assert svc.list_subscriptions(session, OTHER) == []


# ------------------------------------------------------------------ update / pause / resume
def test_update_fields(session):
    sid = create(session)["subscription_id"]
    sub = svc.update_subscription(session, ME, sid, {"quantity": 3, "frequency_days": 14,
                                                     "preferred_slot": "18:00-21:00",
                                                     "next_delivery_date": "2026-12-01"}, today=TODAY)
    assert (sub["quantity"], sub["frequency_days"], sub["preferred_slot"], sub["next_delivery_date"]) == \
        (3, 14, "18:00-21:00", "2026-12-01")
    assert sub["unit_price"] == "27.00"   # price stays locked


@pytest.mark.parametrize("data", [{}, {"status": "CANCELLED"}, {"paused_until": "2026-12-10"},
                                  {"status": "ACTIVE", "paused_until": "2026-12-10"},
                                  {"status": "PAUSED", "paused_until": "2026-11-24"}, {"product_id": SOAP}])
def test_update_rejects(session, data):
    sid = create(session)["subscription_id"]
    with pytest.raises(ValidationError):
        svc.update_subscription(session, ME, sid, data, today=TODAY)


def test_cancelled_subscription_cannot_be_edited(session):
    sid = create(session)["subscription_id"]
    svc.cancel_subscription(session, ME, sid)
    with pytest.raises(Conflict):
        svc.update_subscription(session, ME, sid, {"quantity": 2}, today=TODAY)


def test_date_change_blocked_while_reminder_is_out(session):
    sid = create(session)["subscription_id"]
    session.add(DeliveryOccurrence(subscription_id=sid, scheduled_date=dt.date(2026, 11, 28), status="NOTIFIED"))
    session.flush()
    with pytest.raises(Conflict) as e:
        svc.update_subscription(session, ME, sid, {"next_delivery_date": "2026-12-01"}, today=TODAY)
    assert e.value.code == "delivery_in_progress"
    # other edits still work
    assert svc.update_subscription(session, ME, sid, {"quantity": 2}, today=TODAY)["quantity"] == 2


def test_pause_cancels_pending_and_resume_keeps_cadence(session):
    sid = create(session)["subscription_id"]          # next 2026-11-28, weekly
    session.add(DeliveryOccurrence(subscription_id=sid, scheduled_date=dt.date(2026, 11, 28), status="NOTIFIED"))
    session.flush()
    sub = svc.update_subscription(session, ME, sid, {"status": "PAUSED", "paused_until": "2026-12-20"}, today=TODAY)
    assert sub["status"] == "PAUSED" and sub["paused_until"] == "2026-12-20"
    assert occurrences(session, sid)[dt.date(2026, 11, 28)].status == "CANCELLED"

    # Resume on 2026-12-03: 11-28 has an occurrence and is in the past -> next free weekly slot >= today + 2
    sub = svc.update_subscription(session, ME, sid, {"status": "ACTIVE"}, today=dt.date(2026, 12, 3))
    assert sub["status"] == "ACTIVE" and sub["paused_until"] is None
    assert sub["next_delivery_date"] == "2026-12-05"


def test_resume_right_away_skips_the_cancelled_cycle(session):
    sid = create(session)["subscription_id"]
    session.add(DeliveryOccurrence(subscription_id=sid, scheduled_date=dt.date(2026, 11, 28), status="NOTIFIED"))
    session.flush()
    svc.update_subscription(session, ME, sid, {"status": "PAUSED"}, today=TODAY)
    sub = svc.update_subscription(session, ME, sid, {"status": "ACTIVE"}, today=TODAY)
    assert sub["next_delivery_date"] == "2026-12-05"   # 11-28 already has a (cancelled) occurrence


# ------------------------------------------------------------------ skip
def test_skip_creates_skipped_occurrence_and_advances(session):
    sid = create(session, frequency_days=3)["subscription_id"]
    r = svc.skip_next_delivery(session, ME, sid, today=TODAY)
    assert r["skipped_date"] == "2026-11-28" and r["subscription"]["next_delivery_date"] == "2026-12-01"
    occ = occurrences(session, sid)[dt.date(2026, 11, 28)]
    assert (occ.status, occ.skip_reason) == ("SKIPPED", "USER_SKIP") and occ.responded_at is not None
    r = svc.skip_next_delivery(session, ME, sid, today=TODAY)
    assert r["skipped_date"] == "2026-12-01" and r["subscription"]["next_delivery_date"] == "2026-12-04"


def test_skip_marks_notified_occurrence(session):
    sid = create(session)["subscription_id"]
    session.add(DeliveryOccurrence(subscription_id=sid, scheduled_date=dt.date(2026, 11, 28), status="NOTIFIED"))
    session.flush()
    svc.skip_next_delivery(session, ME, sid, today=TODAY)
    assert occurrences(session, sid)[dt.date(2026, 11, 28)].status == "SKIPPED"


def test_skip_refused_when_finalised_or_not_active(session):
    sid = create(session)["subscription_id"]
    session.add(DeliveryOccurrence(subscription_id=sid, scheduled_date=dt.date(2026, 11, 28), status="OUT_OF_STOCK",
                                   skip_reason="OUT_OF_STOCK"))
    session.flush()
    with pytest.raises(Conflict) as e:
        svc.skip_next_delivery(session, ME, sid, today=TODAY)
    assert e.value.code == "delivery_finalised"
    svc.update_subscription(session, ME, sid, {"status": "PAUSED"}, today=TODAY)
    with pytest.raises(Conflict) as e:
        svc.skip_next_delivery(session, ME, sid, today=TODAY)
    assert e.value.code == "not_active"


# ------------------------------------------------------------------ cancel / list
def test_cancel_is_idempotent_and_cancels_pending(session):
    sid = create(session)["subscription_id"]
    session.add(DeliveryOccurrence(subscription_id=sid, scheduled_date=dt.date(2026, 11, 28), status="SCHEDULED"))
    session.flush()
    first = svc.cancel_subscription(session, ME, sid)
    assert first["status"] == "CANCELLED" and first["cancelled_at"] and first["next_delivery_date"] is None
    assert occurrences(session, sid)[dt.date(2026, 11, 28)].status == "CANCELLED"
    assert svc.cancel_subscription(session, ME, sid)["cancelled_at"] == first["cancelled_at"]


def test_list_order_and_cancelled_filter(session):
    milk = create(session)["subscription_id"]
    soap = svc.create_subscription(session, ME, {"product_id": SOAP, "first_delivery_date": "2026-11-27"},
                                   today=TODAY)["subscription_id"]
    assert [s["subscription_id"] for s in svc.list_subscriptions(session, ME)] == [soap, milk]
    svc.update_subscription(session, ME, soap, {"status": "PAUSED"}, today=TODAY)
    assert [s["subscription_id"] for s in svc.list_subscriptions(session, ME)] == [milk, soap]
    svc.cancel_subscription(session, ME, milk)
    assert [s["subscription_id"] for s in svc.list_subscriptions(session, ME)] == [soap]
    assert len(svc.list_subscriptions(session, ME, include_cancelled=True)) == 2


def test_price_change_is_flagged(session):
    sid = create(session)["subscription_id"]
    session.get(Product, MILK).discount_price = Decimal("28.50")
    session.flush()
    sub = svc.get_subscription(session, ME, sid)
    assert sub["price_changed"] and sub["unit_price"] == "27.00" and sub["current_subscriber_price"] == "28.50"


# ------------------------------------------------------------------ error JSON (no DB needed)
def test_error_json_shape():
    app = Flask(__name__)
    register_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise Conflict("nope", code="already_subscribed", details={"subscription_id": 7})

    r = app.test_client().get("/boom")
    assert r.status_code == 409
    assert r.json == {"error": {"code": "already_subscribed", "message": "nope", "subscription_id": 7}}


# ------------------------------------------------------------------ WhatsApp required
def test_messaging_required_to_subscribe(session):
    assert svc.subscription_draft(session, NOWA, MILK, today=TODAY)["messaging_ready"] is False
    assert svc.product_offer(session, MILK, customer_id=NOWA)["messaging_ready"] is False
    assert svc.product_offer(session, MILK, customer_id=ME)["messaging_ready"] is True
    with pytest.raises(Conflict) as e:
        create(session, customer=NOWA)
    assert e.value.code == "messaging_required"


@pytest.mark.parametrize("change", [{"whatsapp_opt_in": False}, {"phone_e164": None}])
def test_resume_requires_whatsapp(session, change):
    sid = create(session)["subscription_id"]
    svc.update_subscription(session, ME, sid, {"status": "PAUSED"}, today=TODAY)
    for k, v in change.items():
        setattr(session.get(Customer, ME), k, v)
    session.flush()
    with pytest.raises(Conflict) as e:
        svc.update_subscription(session, ME, sid, {"status": "ACTIVE"}, today=TODAY)
    assert e.value.code == "messaging_required"
    # other edits and cancelling still work without WhatsApp
    assert svc.update_subscription(session, ME, sid, {"quantity": 2}, today=TODAY)["quantity"] == 2
    assert svc.cancel_subscription(session, ME, sid)["status"] == "CANCELLED"
