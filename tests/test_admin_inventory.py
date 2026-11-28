"""Admin stock endpoints: restock, adjust, movement history, held deliveries released by a
restock, and the ORDER movement written by approve_delivery. Throwaway schema on the test
database: MILK starts with 5 in stock, SOAP with 0, TV has no inventory row."""
import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin import create_admin_app
from app.models import DeliveryOccurrence, Inventory, InventoryMovement
from app.subscriptions import jobs
from app.subscriptions import service as svc
from subscription_db import ME, MILK, SOAP, TV, test_env, admin_engine, schema_engine  # noqa: F401
from test_scheduler import at, run, subscribe

BASE = "http://localhost:5002"


@pytest.fixture
def engine(schema_engine):
    return schema_engine[0]


@pytest.fixture
def client(schema_engine, monkeypatch):
    # Releases after a restock use the real clock; pin it to the morning the holds below are made.
    monkeypatch.setattr(jobs, "now_utc", lambda: at(27, 10))
    app = create_admin_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    yield app.test_client()
    app.extensions["db_engine"].dispose()


def post(client, path, body):
    return client.post(f"/api/admin{path}", json=body, base_url=BASE)


def stock(engine, product):
    with Session(engine) as s:
        row = s.get(Inventory, product)
        return None if row is None else row.available_qty


def moves(engine, product):
    with Session(engine) as s:
        return [(m.reason, m.delta, m.occurrence_id, m.note) for m in s.scalars(
            select(InventoryMovement).where(InventoryMovement.product_id == product).order_by(InventoryMovement.id))]


# ------------------------------------------------------------------ restock
def test_restock_adds_and_logs(engine, client):
    r = post(client, f"/inventory/{MILK}/restock", {"quantity": 7, "note": "Morning delivery"})
    assert r.status_code == 200
    assert r.json["available_qty"] == 12 and r.json["added"] == 7
    assert r.json["holds"] == {"status": "ok", "released": 0, "out_of_stock": 0}
    assert stock(engine, MILK) == 12 and moves(engine, MILK) == [("RESTOCK", 7, None, "Morning delivery")]


def test_restock_creates_a_missing_inventory_row(engine, client):
    assert stock(engine, TV) is None
    assert post(client, f"/inventory/{TV}/restock", {"quantity": 3}).json["available_qty"] == 3
    assert stock(engine, TV) == 3 and moves(engine, TV) == [("RESTOCK", 3, None, None)]


def test_restocks_add_up(engine, client):
    post(client, f"/inventory/{SOAP}/restock", {"quantity": 2})
    assert post(client, f"/inventory/{SOAP}/restock", {"quantity": 3}).json["available_qty"] == 5


@pytest.mark.parametrize("body", [{}, {"quantity": 0}, {"quantity": 10001}, {"quantity": -1}, {"quantity": "5"},
                                  {"quantity": 2.5}, {"quantity": True}, {"quantity": 1, "price": 3},
                                  {"quantity": 1, "note": "x" * 501}, {"quantity": 1, "note": 5}, []])
def test_restock_validation(engine, client, body):
    r = post(client, f"/inventory/{MILK}/restock", body)
    assert r.status_code == 400 and r.json["error"]["code"] == "validation_error"
    assert stock(engine, MILK) == 5 and moves(engine, MILK) == []


def test_restock_unknown_product(client):
    assert post(client, "/inventory/bb-nope/restock", {"quantity": 1}).status_code == 404


def test_restock_needs_json(client):
    r = client.post(f"/api/admin/inventory/{MILK}/restock", data={"quantity": "1"}, base_url=BASE)
    assert r.status_code == 415


# ------------------------------------------------------------------ restock releases holds
def test_restock_releases_held_deliveries(engine, client):
    sid = subscribe(engine, product=SOAP, quantity=2)
    assert run(engine, at(27, 9))["held"] == [sid]
    small = post(client, f"/inventory/{SOAP}/restock", {"quantity": 1})          # not enough yet
    assert small.json["holds"]["released"] == 0
    r = post(client, f"/inventory/{SOAP}/restock", {"quantity": 1})
    assert r.json["holds"] == {"status": "ok", "released": 1, "out_of_stock": 0}
    with Session(engine) as s:
        occ = s.scalars(select(DeliveryOccurrence).where(DeliveryOccurrence.subscription_id == sid)).one()
        assert occ.status == "NOTIFIED" and occ.response_deadline is not None


def test_restock_release_failure_still_restocks(engine, client, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(jobs, "release_holds", broken)
    r = post(client, f"/inventory/{SOAP}/restock", {"quantity": 4})
    assert r.status_code == 200 and r.json["available_qty"] == 4 and r.json["holds"]["status"] == "failed"
    assert stock(engine, SOAP) == 4


# ------------------------------------------------------------------ adjust
def test_adjust_sets_an_absolute_count(engine, client):
    r = post(client, f"/inventory/{MILK}/adjust", {"available_qty": 2, "reason": "Stock take: 3 damaged"})
    assert r.status_code == 200 and r.json == {"product_id": MILK, "available_qty": 2, "previous_qty": 5, "delta": -3}
    assert moves(engine, MILK) == [("ADJUST", -3, None, "Stock take: 3 damaged")]


def test_adjust_to_the_same_count_logs_nothing(engine, client):
    r = post(client, f"/inventory/{MILK}/adjust", {"available_qty": 5, "reason": "Counted"})
    assert r.json["delta"] == 0 and moves(engine, MILK) == []


def test_adjust_creates_a_missing_row(engine, client):
    assert post(client, f"/inventory/{TV}/adjust", {"available_qty": 4, "reason": "Found"}).json["delta"] == 4
    assert stock(engine, TV) == 4


@pytest.mark.parametrize("body", [{"available_qty": 3}, {"available_qty": 3, "reason": "  "},
                                  {"available_qty": -1, "reason": "x"}, {"reason": "x"},
                                  {"available_qty": "3", "reason": "x"}, {"available_qty": 3, "reason": "x", "y": 1}])
def test_adjust_validation(engine, client, body):
    assert post(client, f"/inventory/{MILK}/adjust", body).status_code == 400
    assert stock(engine, MILK) == 5


def test_adjust_up_releases_holds_but_down_does_not_try(engine, client):
    sid = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    down = post(client, f"/inventory/{MILK}/adjust", {"available_qty": 1, "reason": "Damaged"})
    assert "holds" not in down.json
    up = post(client, f"/inventory/{SOAP}/adjust", {"available_qty": 1, "reason": "Found a box"})
    assert up.json["holds"]["released"] == 1
    with Session(engine) as s:
        assert s.scalars(select(DeliveryOccurrence.status).where(DeliveryOccurrence.subscription_id == sid)).one() == "NOTIFIED"


# ------------------------------------------------------------------ ORDER movement + history
def test_approval_logs_an_order_movement(engine, client):
    sid = subscribe(engine, quantity=2)
    run(engine, at(27, 9))
    with Session(engine) as s:
        occ_id = s.scalars(select(DeliveryOccurrence.occurrence_id).where(DeliveryOccurrence.subscription_id == sid)).one()
        assert svc.approve_delivery(s, ME, occ_id, "07:00-09:00", now=at(27, 10))["status"] == "order_placed"
        s.commit()
    assert stock(engine, MILK) == 3 and moves(engine, MILK) == [("ORDER", -2, occ_id, None)]


def test_out_of_stock_approval_logs_nothing(engine, client):
    sid = subscribe(engine, quantity=2)
    run(engine, at(27, 9))
    with Session(engine) as s:
        s.get(Inventory, MILK).available_qty = 1
        s.commit()
        occ_id = s.scalars(select(DeliveryOccurrence.occurrence_id).where(DeliveryOccurrence.subscription_id == sid)).one()
        assert svc.approve_delivery(s, ME, occ_id, "07:00-09:00", now=at(27, 10))["status"] == "out_of_stock"
        s.commit()
    assert moves(engine, MILK) == []


def test_movement_history_newest_first_with_limit(engine, client):
    post(client, f"/inventory/{MILK}/restock", {"quantity": 1, "note": "first"})
    post(client, f"/inventory/{MILK}/adjust", {"available_qty": 2, "reason": "second"})
    post(client, f"/inventory/{MILK}/restock", {"quantity": 3, "note": "third"})
    r = client.get(f"/api/admin/inventory/{MILK}/movements", base_url=BASE)
    assert r.status_code == 200
    assert [(m["reason"], m["delta"], m["note"]) for m in r.json["movements"]] == [
        ("RESTOCK", 3, "third"), ("ADJUST", -4, "second"), ("RESTOCK", 1, "first")]
    assert dt.datetime.fromisoformat(r.json["movements"][0]["created_at"]).tzinfo is not None
    one = client.get(f"/api/admin/inventory/{MILK}/movements?limit=1", base_url=BASE)
    assert [m["note"] for m in one.json["movements"]] == ["third"]


@pytest.mark.parametrize("limit", ["0", "201", "abc", "-5"])
def test_movement_history_bad_limit(client, limit):
    assert client.get(f"/api/admin/inventory/{MILK}/movements?limit={limit}", base_url=BASE).status_code == 400


def test_movement_history_unknown_product(client):
    assert client.get("/api/admin/inventory/bb-nope/movements", base_url=BASE).status_code == 404


def test_restock_after_hold_time_expires_the_hold(engine, client, monkeypatch):
    sid = subscribe(engine, product=SOAP)
    run(engine, at(27, 9))
    monkeypatch.setattr(jobs, "now_utc", lambda: at(27, 18, 30))          # past 18:00 the day before
    r = post(client, f"/inventory/{SOAP}/restock", {"quantity": 5})
    assert r.json["holds"] == {"status": "ok", "released": 0, "out_of_stock": 1}
    with Session(engine) as s:
        assert s.scalars(select(DeliveryOccurrence.status).where(DeliveryOccurrence.subscription_id == sid)).one() == "OUT_OF_STOCK"
