"""Answering a delivery reminder on the website: the pending list, approve / skip / delay
through the API, and keeping the Telegram chat in step (buttons removed from the reminder,
confirmation posted). Fake Telegram client, throwaway schema on the local test database."""
import pytest
from sqlalchemy.orm import Session

from app import create_app
from app.models import DeliveryOccurrence
from app.notifications import chat_sync
from app.notifications.telegram_bot import process_update
from app.subscriptions import service as svc
from subscription_db import CHAT, ME, MILK, OTHER, bearer, test_env, admin_engine, schema_engine  # noqa: F401
from test_telegram import FakeTelegram, at, next_date, occ_state, remind, subscribe, tap

NOW = at(27, 10)                      # the morning after the reminders below are sent (at(27, 9))


@pytest.fixture
def engine(schema_engine):
    return schema_engine[0]


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(chat_sync, "_client", lambda: fake)
    monkeypatch.setattr(svc, "now_utc", lambda: NOW)          # the API's clock
    return fake


@pytest.fixture
def client(schema_engine):
    app = create_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    yield app.test_client()
    app.extensions["db_engine"].dispose()


def pending(client, who=ME):
    r = client.get("/api/subscriptions/deliveries/pending", headers=bearer(who))
    assert r.status_code == 200
    return r.json["deliveries"]


def test_pending_lists_the_reminder_with_the_chat_options(engine, tg, client):
    sid = subscribe(engine, quantity=3)
    assert pending(client) == []                        # nothing sent yet
    occ = remind(engine, tg, sid)
    (d,) = pending(client)
    assert (d["occurrence_id"], d["subscription_id"], d["quantity"]) == (occ, sid, 3)
    assert d["delivery_date"] == "2026-11-28" and d["total"] == "81.00" and d["saving"] == "9.00"
    assert d["slots"][0] == "07:00-09:00" and d["delay_options"] == [1, 2]
    assert d["reply_by"] == "2026-11-27T21:00:00+05:30"
    assert pending(client, OTHER) == []                  # someone else's reminder isn't shown


def test_approve_on_website_places_order_and_updates_chat(engine, tg, client):
    sid = subscribe(engine, quantity=2)
    occ = remind(engine, tg, sid)
    r = client.post(f"/api/subscriptions/deliveries/{occ}/approve", json={"slot": "07:00-09:00"}, headers=bearer(ME))
    assert r.status_code == 200 and r.json["status"] == "order_placed" and r.json["order_id"] == f"SUB-{occ}"
    assert occ_state(engine, occ)[0] == "ORDER_PLACED"
    assert ("remove_buttons", CHAT[ME], 100) in tg.calls           # the reminder (fake ids start at 100)
    assert "Order placed" in tg.last_text() and "Done on the website" in tg.last_text()
    assert pending(client) == []


def test_approve_needs_a_valid_slot(engine, tg, client):
    occ = remind(engine, tg, subscribe(engine))
    r = client.post(f"/api/subscriptions/deliveries/{occ}/approve", json={}, headers=bearer(ME))
    assert r.status_code == 400
    assert occ_state(engine, occ)[0] == "NOTIFIED" and len(tg.sent()) == 1   # nothing posted


def test_skip_on_website(engine, tg, client):
    sid = subscribe(engine)
    occ = remind(engine, tg, sid)
    r = client.post(f"/api/subscriptions/deliveries/{occ}/skip", headers=bearer(ME))
    assert r.status_code == 200 and r.json["skipped_date"] == "2026-11-28"
    assert occ_state(engine, occ)[:2] == ("SKIPPED", "USER_SKIP")
    assert str(next_date(engine, sid)) == "2026-12-05"
    assert "Skipped your Sat, 28 Nov delivery" in tg.last_text()


def test_delay_on_website_and_bad_days(engine, tg, client):
    occ = remind(engine, tg, subscribe(engine))
    bad = client.post(f"/api/subscriptions/deliveries/{occ}/delay", json={"days": 5}, headers=bearer(ME))
    assert bad.status_code == 409 and bad.json["error"]["code"] == "delay_not_possible"
    r = client.post(f"/api/subscriptions/deliveries/{occ}/delay", json={"days": 2}, headers=bearer(ME))
    assert r.status_code == 200 and r.json == {"delivery_date": "2026-11-30", "delayed_days": 2}
    assert occ_state(engine, occ)[0] == "DELAYED" and "Moved your Milk delivery to Mon, 30 Nov" in tg.last_text()


def test_other_customers_delivery_is_not_found(engine, tg, client):
    occ = remind(engine, tg, subscribe(engine))
    r = client.post(f"/api/subscriptions/deliveries/{occ}/skip", headers=bearer(OTHER))
    assert r.status_code == 404 and occ_state(engine, occ)[0] == "NOTIFIED"


def test_answered_in_chat_first_then_website_is_refused(engine, tg, client):
    occ = remind(engine, tg, subscribe(engine))
    process_update(engine, tg, tap(CHAT[ME], f"SKIPCONF:{occ}"), now=NOW)
    r = client.post(f"/api/subscriptions/deliveries/{occ}/approve", json={"slot": "07:00-09:00"}, headers=bearer(ME))
    assert r.status_code == 409 and r.json["error"]["code"] == "delivery_finalised"


def test_answered_on_website_first_then_chat_tap_is_refused(engine, tg, client):
    occ = remind(engine, tg, subscribe(engine))
    client.post(f"/api/subscriptions/deliveries/{occ}/skip", headers=bearer(ME))
    process_update(engine, tg, tap(CHAT[ME], f"APPROVE:{occ}"), now=NOW)
    assert "no longer waiting for your reply" in tg.last_text()


def test_cancel_on_website_clears_open_reminder(engine, tg, client):
    sid = subscribe(engine)
    occ = remind(engine, tg, sid)
    assert client.delete(f"/api/subscriptions/{sid}", headers=bearer(ME)).status_code == 200
    assert occ_state(engine, occ)[0] == "CANCELLED"
    assert ("remove_buttons", CHAT[ME], 100) in tg.calls and "subscription is cancelled" in tg.last_text()


def test_cancel_without_open_reminder_sends_nothing(engine, tg, client):
    sid = subscribe(engine)
    client.delete(f"/api/subscriptions/{sid}", headers=bearer(ME))
    assert tg.calls == []


def test_dashboard_skip_next_clears_open_reminder(engine, tg, client):
    sid = subscribe(engine)
    occ = remind(engine, tg, sid)
    assert client.post(f"/api/subscriptions/{sid}/skip", headers=bearer(ME)).status_code == 200
    assert occ_state(engine, occ)[0] == "SKIPPED"
    assert ("remove_buttons", CHAT[ME], 100) in tg.calls and "Skipped your Sat, 28 Nov" in tg.last_text()


def test_pause_clears_open_reminder_buttons(engine, tg, client):
    sid = subscribe(engine)
    remind(engine, tg, sid)
    client.patch(f"/api/subscriptions/{sid}", json={"status": "PAUSED"}, headers=bearer(ME))
    assert ("remove_buttons", CHAT[ME], 100) in tg.calls


def test_telegram_failure_never_breaks_the_website(engine, tg, client):
    occ = remind(engine, tg, subscribe(engine))

    def down(*a, **k):
        raise ConnectionError("telegram unreachable")
    tg.remove_buttons = tg.send_message = down
    r = client.post(f"/api/subscriptions/deliveries/{occ}/skip", headers=bearer(ME))
    assert r.status_code == 200 and occ_state(engine, occ)[0] == "SKIPPED"


def test_no_bot_token_means_no_chat_calls(engine, client, monkeypatch):
    monkeypatch.setattr(svc, "now_utc", lambda: NOW)
    fake = FakeTelegram()
    occ = remind(engine, fake, subscribe(engine))
    before = list(fake.calls)
    assert client.post(f"/api/subscriptions/deliveries/{occ}/skip", headers=bearer(ME)).status_code == 200
    assert fake.calls == before                           # chat_sync._client() is None without a token
    with Session(engine) as s:
        assert s.get(DeliveryOccurrence, occ).status == "SKIPPED"
