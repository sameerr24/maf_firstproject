"""Order history: service + HTTP, against a throwaway schema on the local test database."""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app import create_app
from app.errors import NotFound, ValidationError
from app.models import DeliveryOccurrence, Subscription
from app.orders import service as svc
from database.models import Order, OrderItem
from subscription_db import ME, MILK, OTHER, SOAP, bearer, test_env, admin_engine, schema_engine  # noqa: F401


def _order(oid, customer, when, lines, source="manual", slot=None):
    """lines: [(item_id, product_id, product_type, qty, unit_price)]"""
    items = [OrderItem(order_item_id=i, order_id=oid, product_id=p, product_type=t, quantity=q,
                       unit_price=Decimal(u), line_total=Decimal(u) * q) for i, p, t, q, u in lines]
    return Order(order_id=oid, customer_id=customer, order_date=when.date(), order_datetime=when,
                 item_count=len(items), unit_count=sum(i.quantity for i in items),
                 order_total=sum(i.line_total for i in items), source=source, delivery_slot=slot, items=items)


@pytest.fixture
def seeded(schema_engine):
    """ME: 25 manual orders (one per day) + 1 subscription order; OTHER: 1 order."""
    eng, schema = schema_engine
    with Session(eng) as s:
        start = dt.datetime(2026, 10, 1, 9, 30)
        for n in range(25):
            s.add(_order(f"ORD-{n:04d}", ME, start + dt.timedelta(days=n),
                         [(f"OI-{n:04d}-1", MILK, "milk", 2, "27.00"), (f"OI-{n:04d}-2", SOAP, "soap", 1, "99.00")],
                         slot="09:00-12:00"))
        s.add(_order("ORD-OTHER", OTHER, start, [("OI-OTHER-1", MILK, "milk", 1, "27.00")]))
        sub = Subscription(customer_id=ME, product_id=MILK, quantity=3, frequency_days=7,
                           unit_price_at_subscription=Decimal("27.00"), status="ACTIVE",
                           next_delivery_date=dt.date(2026, 12, 5))
        s.add(sub)
        s.flush()
        occ = DeliveryOccurrence(subscription_id=sub.subscription_id, scheduled_date=dt.date(2026, 11, 28),
                                 status="ORDER_PLACED")
        s.add(occ)
        s.flush()
        s.add(_order(f"SUB-{occ.occurrence_id}", ME, dt.datetime(2026, 11, 27, 18, 5),
                     [(f"SUBI-{occ.occurrence_id}", MILK, "milk", 3, "27.00")], source="subscription",
                     slot="07:00-09:00"))
        s.flush()
        occ.order_id = f"SUB-{occ.occurrence_id}"
        s.commit()
        ids = {"subscription_id": sub.subscription_id, "sub_order": f"SUB-{occ.occurrence_id}"}
    return eng, schema, ids


# ------------------------------------------------------------------ service
def test_newest_first_with_items_and_subscription_link(seeded):
    eng, _, ids = seeded
    with Session(eng) as s:
        page = svc.list_orders(s, ME)
    assert (page["total"], page["limit"], page["offset"], page["has_more"]) == (26, 20, 0, True)
    first, second = page["orders"][:2]
    assert first["order_id"] == ids["sub_order"] and first["source"] == "subscription"
    assert first["subscription_id"] == ids["subscription_id"] and first["delivery_slot"] == "07:00-09:00"
    assert first["order_total"] == "81.00" and first["items"][0]["quantity"] == 3
    assert first["subscription_fee"] == "0.00"
    assert second["order_id"] == "ORD-0024" and second["subscription_id"] is None
    assert [i["line_total"] for i in second["items"]] == ["54.00", "99.00"] and second["order_total"] == "153.00"
    assert second["items"][0]["product"]["product_id"] == MILK
    assert second["items"][0]["product"]["category"] == "Cat"            # for the image fallback tile


def test_pagination(seeded):
    eng, _, _ = seeded
    with Session(eng) as s:
        page = svc.list_orders(s, ME, limit="10", offset="20")
        assert [o["order_id"] for o in page["orders"]] == [f"ORD-{n:04d}" for n in range(5, -1, -1)]
        assert page["has_more"] is False
        assert svc.list_orders(s, ME, offset=100)["orders"] == []


def test_source_filter(seeded):
    eng, _, ids = seeded
    with Session(eng) as s:
        subs = svc.list_orders(s, ME, source="subscription")
        assert subs["total"] == 1 and subs["orders"][0]["order_id"] == ids["sub_order"]
        assert svc.list_orders(s, ME, source="manual")["total"] == 25


@pytest.mark.parametrize("kwargs,field", [({"limit": 0}, "limit"), ({"limit": 101}, "limit"),
                                          ({"limit": "abc"}, "limit"), ({"offset": -1}, "offset"),
                                          ({"source": "web"}, "source")])
def test_invalid_params(seeded, kwargs, field):
    eng, _, _ = seeded
    with Session(eng) as s, pytest.raises(ValidationError) as e:
        svc.list_orders(s, ME, **kwargs)
    assert e.value.details["field"] == field


def test_only_own_orders(seeded):
    eng, _, _ = seeded
    with Session(eng) as s:
        assert [o["order_id"] for o in svc.list_orders(s, OTHER)["orders"]] == ["ORD-OTHER"]
        assert svc.get_order(s, ME, "ORD-0003")["order_id"] == "ORD-0003"
        with pytest.raises(NotFound):
            svc.get_order(s, OTHER, "ORD-0003")
        with pytest.raises(NotFound):
            svc.get_order(s, ME, "ORD-OTHER")


# ------------------------------------------------------------------ HTTP
@pytest.fixture
def client(seeded):
    _, schema, _ = seeded
    app = create_app("test", overrides={"DB_SEARCH_PATH": schema})
    tokens = {c: bearer(c) for c in (ME, OTHER)}
    yield app.test_client(), tokens
    app.extensions["db_engine"].dispose()


def test_http(client):
    c, tokens = client
    assert c.get("/api/orders").status_code == 401
    assert c.get("/api/orders/ORD-0001").status_code == 401
    r = c.get("/api/orders?limit=5&source=manual", headers=tokens[ME])
    assert r.status_code == 200 and len(r.json["orders"]) == 5 and r.json["total"] == 25
    assert c.get("/api/orders?limit=500", headers=tokens[ME]).status_code == 400
    assert c.get("/api/orders/ORD-0001", headers=tokens[ME]).json["order"]["order_total"] == "153.00"
    r = c.get("/api/orders/ORD-0001", headers=tokens[OTHER])
    assert r.status_code == 404 and r.json["error"]["code"] == "not_found"
