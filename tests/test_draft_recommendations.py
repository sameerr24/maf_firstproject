"""Subscription drafts pre-filled by Sameer's recommendation engine, on a throwaway schema with
real purchase history (orders + order_items), so the engine runs its normal SQL path."""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app import create_app
from app.models import Subscription
from app.subscriptions import recommendations
from app.subscriptions import service as svc
from database.models import Order, OrderItem
from recommendation_engine import DataSourceError
from subscription_db import ME, MILK, OTHER, SOAP, TODAY, bearer, test_env, admin_engine, schema_engine  # noqa: F401


def _history(s: Session, customer: str, product: str, ptype: str, price: str, days_ago: list[int], qty: int,
             source: str = "manual"):
    for n, d in enumerate(days_ago):
        when = dt.datetime(2026, 11, 15, 10, 0) - dt.timedelta(days=d)
        oid = f"H{source[0]}-{customer[-2:]}-{product[-4:]}-{n}"
        total = Decimal(price) * qty
        s.add(Order(order_id=oid, customer_id=customer, order_date=when.date(), order_datetime=when, item_count=1,
                    unit_count=qty, order_total=total, source=source, items=[
                        OrderItem(order_item_id=f"I{oid}", product_id=product, product_type=ptype, quantity=qty,
                                  unit_price=Decimal(price), line_total=total)]))


@pytest.fixture
def engine(schema_engine):
    eng = schema_engine[0]
    with Session(eng) as s:
        _history(s, ME, MILK, "milk", "27.00", [0, 7, 14, 21, 28, 35, 42], qty=2)    # weekly, 2 packs
        _history(s, ME, SOAP, "soap", "99.00", [10, 40, 70], qty=1)                  # monthly, 1 pack
        s.commit()                                    # the engine reads on its own connection
    return eng


def test_draft_uses_engine_quantity_and_frequency(engine):
    with Session(engine) as s:
        d = svc.subscription_draft(s, ME, MILK, today=TODAY)
    assert (d["quantity"], d["frequency_days"]) == (2, 7)
    r = d["recommendation"]
    assert r["source"] == "recommendation" and (r["quantity"], r["frequency_days"]) == (2, 7)
    assert r["rank"] >= 1 and 0 < r["score"] <= 1 and r["status"] and r["reason"]
    assert d["total_per_delivery"] == "54.00" and d["saving_per_delivery"] == "6.00"
    assert d["subscription_fee_due"] is False and d["subscription_fee_amount"] == "0.00"


def test_engine_values_match_what_the_recommendations_page_shows(engine):
    """Same engine, same data: the draft equals the FastAPI recommendation for that product."""
    with Session(engine) as s:
        d = svc.subscription_draft(s, ME, SOAP, today=TODAY)
        page = recommendations._engine_for(s).generate_recommendations(ME, limit=50)
    soap = next(r for r in page.recommendations if r.product_id == SOAP)
    assert d["quantity"] == soap.suggested_quantity == 1
    assert d["frequency_days"] == round(soap.expected_purchase_interval) == 30
    assert d["recommendation"]["rank"] == soap.rank


def test_create_stores_the_recommendation_and_user_values_win(engine):
    with Session(engine) as s:
        sub = svc.create_subscription(s, ME, {"product_id": MILK, "quantity": 5}, today=TODAY)
        row = s.get(Subscription, sub["subscription_id"])
        assert (sub["quantity"], sub["frequency_days"]) == (5, 7)                # edited quantity, engine frequency
        assert (row.recommended_quantity, row.recommended_frequency_days) == (2, 7)


def test_no_history_or_subscribed_falls_back_to_defaults(engine):
    with Session(engine) as s:
        other = svc.subscription_draft(s, OTHER, MILK, today=TODAY)              # never bought milk
        assert other["recommendation"]["source"] == "default"
        assert (other["quantity"], other["frequency_days"]) == (1, 7)
        svc.create_subscription(s, ME, {"product_id": MILK}, today=TODAY)
        s.commit()
        d = svc.subscription_draft(s, ME, MILK, today=TODAY)                     # engine hides subscribed products
        assert d["recommendation"]["source"] == "default" and d["existing_subscription"] is not None


def test_subscription_orders_do_not_feed_the_engine(engine):
    """Only manual orders count as history (Sameer's rule): a subscription order of 10 packs
    must not change the suggested quantity."""
    with Session(engine) as s:
        _history(s, ME, MILK, "milk", "27.00", [1], qty=10, source="subscription")
        s.commit()
        assert svc.subscription_draft(s, ME, MILK, today=TODAY)["quantity"] == 2


def test_engine_failure_never_breaks_the_draft(engine, monkeypatch):
    def broken(session):
        raise DataSourceError("database connection failed")
    monkeypatch.setattr(recommendations, "_engine_for", broken)
    with Session(engine) as s:
        d = svc.subscription_draft(s, ME, MILK, today=TODAY)
    assert d["recommendation"]["source"] == "default" and (d["quantity"], d["frequency_days"]) == (1, 7)


def test_draft_endpoint(engine, schema_engine):
    app = create_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    try:
        r = app.test_client().get(f"/api/subscriptions/draft?product_id={MILK}", headers=bearer(ME))
        assert r.status_code == 200
        assert (r.json["draft"]["quantity"], r.json["draft"]["frequency_days"]) == (2, 7)
        assert r.json["draft"]["recommendation"]["source"] == "recommendation"
    finally:
        app.extensions["db_engine"].dispose()
