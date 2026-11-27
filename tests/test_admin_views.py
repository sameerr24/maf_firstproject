"""Admin read endpoints: at-risk (held deliveries), the inventory list and the subscriptions
search. Throwaway schema on the test database: MILK 5 in stock, SOAP 0, TV no inventory row.
ME's phone is +919800000001, OTHER's +919800000002 (only masked numbers may come back)."""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.admin import create_admin_app
from app.admin import service as admin_svc
from app.models import Customer, Product
from app.subscriptions import jobs
from app.subscriptions import service as svc
from subscription_db import ME, MILK, OTHER, SOAP, TODAY, TV, test_env, admin_engine, schema_engine  # noqa: F401
from test_scheduler import at, run, subscribe

BASE = "http://localhost:5002"
SAT = "2026-11-28"
RAW_PHONES = ("+919800000001", "+919800000002", "9800000001", "9800000002")


@pytest.fixture
def engine(schema_engine):
    eng = schema_engine[0]
    with Session(eng) as s:
        s.get(Customer, ME).customer_name = "Asha Rao"
        s.get(Customer, OTHER).customer_name = "Vikram 50%_Off"
        s.commit()
    return eng


@pytest.fixture
def client(schema_engine, monkeypatch):
    monkeypatch.setattr(jobs, "now_utc", lambda: at(27, 10))
    monkeypatch.setattr(admin_svc, "now_utc", lambda: at(27, 10))
    app = create_admin_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    yield app.test_client()
    app.extensions["db_engine"].dispose()


def get(client, path, **params):
    r = client.get(f"/api/admin{path}", query_string=params, base_url=BASE)
    return r


def no_raw_phone(r):
    assert not any(p in r.get_data(as_text=True) for p in RAW_PHONES)


# ------------------------------------------------------------------ at risk
def test_at_risk_empty(engine, client):
    assert get(client, "/at-risk").json == {"total": 0, "products": []}


def test_at_risk_groups_held_deliveries(engine, client):
    a = subscribe(engine, product=SOAP, quantity=2)
    b = subscribe(engine, customer=OTHER, product=SOAP, quantity=1, first_delivery_date=SAT)
    run(engine, at(27, 9))
    r = get(client, "/at-risk")
    assert r.status_code == 200 and r.json["total"] == 2
    (g,) = r.json["products"]
    assert g["product"]["product_id"] == SOAP and g["product"]["image_url"]
    assert (g["stock_now"], g["promised"], g["free_stock"], g["held_qty"], g["add_to_release_all"]) == (0, 0, 0, 3, 3)
    assert g["earliest_delivery_date"] == SAT and g["earliest_hold_until"] == at(27, 18).isoformat()
    assert [(l["subscription_id"], l["quantity"], l["delivery_date"]) for l in g["lines"]] == [(a, 2, SAT), (b, 1, SAT)]
    line = g["lines"][0]
    assert line["customer"] == {"customer_id": ME, "customer_name": "Asha Rao", "phone": "+91******0001"}
    assert line["expired"] is False and line["occurrence_id"]
    no_raw_phone(r)


def test_at_risk_counts_stock_promised_to_open_reminders(engine, client):
    reminded = subscribe(engine, quantity=3)
    held = subscribe(engine, customer=OTHER, quantity=3, first_delivery_date=SAT)
    r = run(engine, at(27, 9))
    assert r["reminded"] == [reminded] and r["held"] == [held]
    (g,) = get(client, "/at-risk").json["products"]
    assert (g["stock_now"], g["promised"], g["free_stock"], g["held_qty"], g["add_to_release_all"]) == (5, 3, 2, 3, 1)


def test_at_risk_products_in_order_and_clear_after_restock(engine, client):
    """Same hold time (27 Nov 18:00): products by name. Restocking one removes its group."""
    subscribe(engine, product=SOAP)
    subscribe(engine, customer=OTHER, product=MILK, quantity=6, first_delivery_date=SAT)   # MILK has 5
    run(engine, at(27, 9))
    r = get(client, "/at-risk").json
    assert r["total"] == 2 and [g["product"]["product_id"] for g in r["products"]] == [MILK, SOAP]
    milk = r["products"][0]
    assert (milk["stock_now"], milk["held_qty"], milk["add_to_release_all"]) == (5, 6, 1)
    restock = client.post(f"/api/admin/inventory/{MILK}/restock", json={"quantity": 1}, base_url=BASE)
    assert restock.json["holds"]["released"] == 1
    r = get(client, "/at-risk").json
    assert r["total"] == 1 and [g["product"]["product_id"] for g in r["products"]] == [SOAP]


def test_at_risk_flags_expired_holds(engine, client, monkeypatch):
    subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    monkeypatch.setattr(admin_svc, "now_utc", lambda: at(27, 18, 1))
    (g,) = get(client, "/at-risk").json["products"]
    assert g["lines"][0]["expired"] is True


# ------------------------------------------------------------------ inventory
def test_inventory_statuses_and_order(engine, client):
    subscribe(engine, product=SOAP, quantity=2)
    run(engine, at(27, 9))                                              # SOAP: 0 in stock, 2 held
    r = get(client, "/inventory")
    assert r.status_code == 200
    rows = {i["product"]["product_id"]: i for i in r.json["items"]}
    assert (rows[SOAP]["status"], rows[SOAP]["held_qty"], rows[SOAP]["left_after_demand"]) == ("short", 2, -2)
    assert rows[SOAP]["active_subscriptions"] == 1
    assert (rows[MILK]["status"], rows[MILK]["stock_now"]) == ("ok", 5)          # 5 left = LOW_STOCK_BUFFER
    assert (rows[TV]["status"], rows[TV]["stock_now"], rows[TV]["updated_at"]) == ("low", 0, None)
    assert [i["status"] for i in r.json["items"]] == ["short", "low", "ok"]       # short first
    assert r.json["counts"] == {"short": 1, "low": 1, "ok": 1}


def test_inventory_low_counts_open_reminders(engine, client):
    subscribe(engine, quantity=2)
    run(engine, at(27, 9))                                              # 2 of MILK's 5 promised
    milk = next(i for i in get(client, "/inventory").json["items"] if i["product"]["product_id"] == MILK)
    assert (milk["promised"], milk["left_after_demand"], milk["status"]) == (2, 3, "low")


def test_inventory_search_and_filter(engine, client):
    assert [i["product"]["product_id"] for i in get(client, "/inventory", q="MIL").json["items"]] == [MILK]
    assert [i["product"]["product_id"] for i in get(client, "/inventory", q="bb-tv").json["items"]] == [TV]
    assert get(client, "/inventory", q="%").json["items"] == []                 # wildcard matched literally
    assert [i["product"]["product_id"] for i in get(client, "/inventory", status="ok").json["items"]] == [MILK]
    assert get(client, "/inventory", q="milk", status="low").json["items"] == []


@pytest.mark.parametrize("params", [{"status": "bad"}, {"q": "x" * 101}])
def test_inventory_validation(engine, client, params):
    assert get(client, "/inventory", **params).status_code == 400


# ------------------------------------------------------------------ subscriptions search
def ids(r):
    return [i["subscription_id"] for i in r.json["items"]]


@pytest.fixture
def subs(engine):
    milk_me = subscribe(engine, quantity=2)
    soap_other = subscribe(engine, customer=OTHER, product=SOAP, first_delivery_date=SAT)
    milk_other = subscribe(engine, customer=OTHER)
    cancelled = subscribe(engine, product=SOAP)
    with Session(engine) as s:
        svc.update_subscription(s, OTHER, milk_other, {"status": "PAUSED"}, today=TODAY)
        svc.cancel_subscription(s, ME, cancelled)
        s.commit()
    return {"milk_me": milk_me, "soap_other": soap_other, "milk_other": milk_other, "cancelled": cancelled}


def test_search_lists_active_and_paused_not_cancelled(engine, client, subs):
    r = get(client, "/subscriptions")
    assert r.status_code == 200 and r.json["total"] == 3 and r.json["page"] == 1 and r.json["page_size"] == 25
    assert subs["cancelled"] not in ids(r)
    assert ids(get(client, "/subscriptions", status="PAUSED")) == [subs["milk_other"]]
    assert set(ids(get(client, "/subscriptions", status="ACTIVE"))) == {subs["milk_me"], subs["soap_other"]}
    no_raw_phone(r)


@pytest.mark.parametrize("q,expected", [
    ("milk", {"milk_me", "milk_other"}), ("SOAP", {"soap_other"}), ("bb-soap", {"soap_other"}),
    ("asha", {"milk_me"}), ("CUST-0002", {"soap_other", "milk_other"}), ("0001", {"milk_me"}),
    ("+91 98000 00002", {"soap_other", "milk_other"}), ("50%_", {"soap_other", "milk_other"}),
    ("%", {"soap_other", "milk_other"}),          # literal % (in OTHER's name), not a wildcard
    ("x%y", set()), ("nothing", set()),
])
def test_search_q(engine, client, subs, q, expected):
    assert set(ids(get(client, "/subscriptions", q=q))) == {subs[k] for k in expected}


def test_search_filters_by_product_and_customer(engine, client, subs):
    assert set(ids(get(client, "/subscriptions", product_id=MILK))) == {subs["milk_me"], subs["milk_other"]}
    assert ids(get(client, "/subscriptions", customer_id=ME)) == [subs["milk_me"]]
    assert ids(get(client, "/subscriptions", customer_id=OTHER, product_id=SOAP)) == [subs["soap_other"]]


def test_search_row_contents(engine, client, subs):
    with Session(engine) as s:
        s.get(Product, MILK).discount_price = Decimal("29.00")
        s.commit()
    row = next(i for i in get(client, "/subscriptions").json["items"] if i["subscription_id"] == subs["milk_me"])
    assert row["customer"] == {"customer_id": ME, "customer_name": "Asha Rao", "phone": "+91******0001"}
    assert (row["quantity"], row["frequency_days"], row["next_delivery_date"]) == (2, 7, SAT)
    assert (row["unit_price_at_subscription"], row["current_price"], row["price_changed"]) == ("27.00", "29.00", True)
    assert row["at_risk"] is False and row["open_delivery"] is None


def test_search_at_risk_flag(engine, client, subs):
    run(engine, at(27, 9))                                    # SOAP has no stock: OTHER's soap is held
    rows = {i["subscription_id"]: i for i in get(client, "/subscriptions").json["items"]}
    assert rows[subs["soap_other"]]["at_risk"] is True and rows[subs["soap_other"]]["open_delivery"] == "STOCK_HOLD"
    assert rows[subs["milk_me"]]["open_delivery"] == "NOTIFIED" and rows[subs["milk_me"]]["at_risk"] is False


def test_search_pagination(engine, client, subs):
    first = get(client, "/subscriptions", page_size=2)
    second = get(client, "/subscriptions", page_size=2, page=2)
    assert first.json["total"] == second.json["total"] == 3
    assert len(ids(first)) == 2 and len(ids(second)) == 1 and not set(ids(first)) & set(ids(second))
    assert get(client, "/subscriptions", page=5).json["items"] == []


@pytest.mark.parametrize("params", [{"status": "CANCELLED"}, {"page": "0"}, {"page_size": "101"},
                                   {"page": "x"}, {"q": "x" * 101}])
def test_search_validation(engine, client, params):
    assert get(client, "/subscriptions", **params).status_code == 400


def test_search_is_read_only(client):
    assert client.post("/api/admin/subscriptions", json={}, base_url=BASE).status_code == 405
