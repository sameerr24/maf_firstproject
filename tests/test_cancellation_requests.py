"""Cancellation requests: the admin asks, the customer confirms or declines on Telegram or the
dashboard (first answer wins), the admin can withdraw. While pending, no reminders go out.
Fake Telegram client, throwaway schema. ME (Saturdays, first delivery Sat 28 Nov) has MILK."""
import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import create_app
from app.admin import create_admin_app
from app.admin import service as admin_svc
from app.models import CancellationRequest, Subscription
from app.notifications import chat_sync
from app.notifications.sender import TelegramSender
from app.notifications.telegram_bot import process_update
from app.subscriptions import cancellation, jobs
from app.subscriptions import service as svc
from subscription_db import CHAT, ME, MILK, OTHER, bearer, test_env, admin_engine, schema_engine  # noqa: F401
from test_telegram import FakeTelegram, at, occ_state, remind, subscribe, tap

NOW = at(27, 10)
BASE = "http://localhost:5002"
MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "sql" / "007_cancellation_requests.sql"


@pytest.fixture
def engine(schema_engine):
    return schema_engine[0]


@pytest.fixture
def tg(monkeypatch):
    """Fake Telegram for everything: requests, the bot, and the chat sync after website/admin actions."""
    fake = FakeTelegram()
    monkeypatch.setattr(chat_sync, "_client", lambda: fake)
    monkeypatch.setattr(cancellation, "get_sender", lambda: TelegramSender(fake))
    for module in (svc, cancellation, jobs, admin_svc):
        monkeypatch.setattr(module, "now_utc", lambda: NOW)
    return fake


@pytest.fixture
def web(schema_engine):
    app = create_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    yield app.test_client()
    app.extensions["db_engine"].dispose()


@pytest.fixture
def admin(schema_engine):
    app = create_admin_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    yield app.test_client()
    app.extensions["db_engine"].dispose()


def ask(admin, sid, body=None):
    return admin.post(f"/api/admin/subscriptions/{sid}/cancel-request", json={} if body is None else body,
                      base_url=BASE)


def withdraw(admin, sid):
    return admin.post(f"/api/admin/subscriptions/{sid}/cancel-request/withdraw", json={}, base_url=BASE)


def answer_web(web, sid, verb, who=ME):
    return web.post(f"/api/subscriptions/{sid}/cancel-request/{verb}", headers=bearer(who))


def sub_status(engine, sid):
    with Session(engine) as s:
        sub = s.get(Subscription, sid)
        return sub.status, sub.next_delivery_date


def requests(engine, sid):
    with Session(engine) as s:
        return [(r.status, r.channel) for r in s.scalars(
            select(CancellationRequest).where(CancellationRequest.subscription_id == sid).order_by(CancellationRequest.id))]


def request_message(tg):
    """(message_id, text, [(label, data)]) of the last cancellation request sent."""
    sends = [(i, c) for i, c in enumerate(tg.calls) if c[0] == "send" and c[3] and "CXL_YES" in str(c[3])]
    i, (_, _, body, markup) = sends[-1]
    buttons = [(b["text"], b["callback_data"]) for row in markup["inline_keyboard"] for b in row]
    message_id = 100 + sum(1 for c in tg.calls[:i] if c[0] == "send")      # fake ids count sends from 100
    return message_id, body, buttons


# ------------------------------------------------------------------ the request
def test_request_sends_telegram_buttons_and_shows_on_dashboard(engine, tg, admin, web):
    sid = subscribe(engine)
    r = ask(admin, sid)
    assert r.status_code == 200 and r.json["customer_notified"] is True
    assert r.json["request"]["status"] == "PENDING" and r.json["cancelled_deliveries"] == []
    _, body, buttons = request_message(tg)
    rid = r.json["request"]["request_id"]
    assert buttons == [("Yes, cancel it", f"CXL_YES:{rid}"), ("Keep my subscription", f"CXL_NO:{rid}")]
    assert "Due to operational reasons" in body and "Milk subscription (1 every week)" in body
    assert "Would you like to cancel it?" in body
    (row,) = web.get("/api/subscriptions", headers=bearer(ME)).json["subscriptions"]
    assert row["cancel_request"]["request_id"] == rid
    assert sub_status(engine, sid) == ("ACTIVE", dt.date(2026, 11, 28))      # nothing changes until answered


def test_request_cancels_an_open_reminder_and_pauses_reminders(engine, tg, admin):
    sid = subscribe(engine)
    occ = remind(engine, tg, sid)                                      # reminder for Sat 28 is out
    r = ask(admin, sid)
    assert r.json["cancelled_deliveries"] == [{"occurrence_id": occ, "status": "NOTIFIED"}]
    assert occ_state(engine, occ)[0] == "CANCELLED"
    assert ("remove_buttons", CHAT[ME], 100) in tg.calls               # the reminder's buttons are gone
    assert sub_status(engine, sid) == ("ACTIVE", dt.date(2026, 12, 5))
    assert jobs.run_scheduler(engine, now=at(4, 9, month=12))["reminded"] == []   # no reminder while pending


def test_request_frees_stock_for_a_held_delivery(engine, tg, admin):
    """MILK has 5: ME's reminder for 3 is out, OTHER's 3 is held. The request frees ME's 3."""
    mine = subscribe(engine, quantity=3)
    held = subscribe(engine, customer=OTHER, quantity=3, first_delivery_date="2026-11-28")
    assert jobs.run_scheduler(engine, now=at(27, 9))["held"] == [held]
    r = ask(admin, mine)
    assert r.json["holds"] == {"status": "ok", "released": 1, "out_of_stock": 0}


def test_admin_errors(engine, tg, admin, web):
    sid = subscribe(engine)
    assert ask(admin, 999999).status_code == 404
    assert ask(admin, sid, {"reason": "x"}).status_code == 400
    assert withdraw(admin, sid).json["error"]["code"] == "no_pending_request"
    assert ask(admin, sid).status_code == 200
    r = ask(admin, sid)
    assert r.status_code == 409 and r.json["error"]["code"] == "already_requested"
    web.delete(f"/api/subscriptions/{sid}", headers=bearer(ME))
    assert ask(admin, sid).json["error"]["code"] == "not_active"


def test_delay_and_skip_are_blocked_while_pending(engine, tg, admin):
    sid = subscribe(engine)
    ask(admin, sid)
    for verb, body in (("delay", {"days": 1}), ("skip", {})):
        r = admin.post(f"/api/admin/subscriptions/{sid}/{verb}", json=body, base_url=BASE)
        assert r.status_code == 409 and r.json["error"]["code"] == "cancel_requested"
    (row,) = admin.get("/api/admin/subscriptions", base_url=BASE).json["items"]
    assert row["cancel_request"]["status"] == "PENDING" and row["upcoming"] is None


# ------------------------------------------------------------------ answering on Telegram
def test_yes_on_telegram_cancels(engine, tg, admin, web):
    sid = subscribe(engine)
    rid = ask(admin, sid).json["request"]["request_id"]
    process_update(engine, tg, tap(CHAT[ME], f"CXL_YES:{rid}"), now=NOW)
    assert "cancelled, as requested" in tg.last_text()
    assert sub_status(engine, sid) == ("CANCELLED", None) and requests(engine, sid) == [("CONFIRMED", "telegram")]
    (row,) = web.get("/api/subscriptions?include_cancelled=true", headers=bearer(ME)).json["subscriptions"]
    assert row["cancel_request"] is None
    process_update(engine, tg, tap(CHAT[ME], f"CXL_NO:{rid}"), now=NOW)     # an old button tapped later
    assert "already cancelled" in tg.last_text() and sub_status(engine, sid)[0] == "CANCELLED"


def test_keep_on_telegram_resumes_reminders(engine, tg, admin):
    sid = subscribe(engine)
    rid = ask(admin, sid).json["request"]["request_id"]
    process_update(engine, tg, tap(CHAT[ME], f"CXL_NO:{rid}"), now=NOW)
    assert "continues" in tg.last_text() and "Sat, 28 Nov" in tg.last_text()
    assert sub_status(engine, sid) == ("ACTIVE", dt.date(2026, 11, 28)) and requests(engine, sid) == [("DECLINED", "telegram")]
    process_update(engine, tg, tap(CHAT[ME], f"CXL_YES:{rid}"), now=NOW)    # changing their mind later: refused
    assert "already chose to keep" in tg.last_text() and sub_status(engine, sid)[0] == "ACTIVE"
    assert jobs.run_scheduler(engine, now=at(27, 11))["reminded"] == [sid]


def test_someone_elses_request_is_refused(engine, tg, admin):
    sid = subscribe(engine)
    rid = ask(admin, sid).json["request"]["request_id"]
    process_update(engine, tg, tap(CHAT[OTHER], f"CXL_YES:{rid}"), now=NOW)
    assert "not found" in tg.last_text().lower()
    assert sub_status(engine, sid)[0] == "ACTIVE" and requests(engine, sid) == [("PENDING", None)]


# ------------------------------------------------------------------ answering on the dashboard
def test_confirm_on_dashboard_cancels_and_updates_chat(engine, tg, admin, web):
    sid = subscribe(engine)
    rid = ask(admin, sid).json["request"]["request_id"]
    message_id, _, _ = request_message(tg)
    r = answer_web(web, sid, "confirm")
    assert r.status_code == 200 and r.json["outcome"] == "cancelled"
    assert r.json["subscription"]["status"] == "CANCELLED" and requests(engine, sid) == [("CONFIRMED", "dashboard")]
    assert ("remove_buttons", CHAT[ME], message_id) in tg.calls
    assert "cancelled, as requested" in tg.last_text() and "Done on the website" in tg.last_text()
    process_update(engine, tg, tap(CHAT[ME], f"CXL_NO:{rid}"), now=NOW)     # first answer won
    assert "already cancelled" in tg.last_text()


def test_decline_on_dashboard_keeps_it(engine, tg, admin, web):
    sid = subscribe(engine)
    ask(admin, sid)
    r = answer_web(web, sid, "decline")
    assert r.status_code == 200 and r.json["outcome"] == "kept" and r.json["subscription"]["cancel_request"] is None
    assert "continues" in tg.last_text() and requests(engine, sid) == [("DECLINED", "dashboard")]
    r = answer_web(web, sid, "confirm")                                   # nothing pending any more
    assert r.status_code == 409 and r.json["error"]["code"] == "request_closed"
    assert sub_status(engine, sid)[0] == "ACTIVE"


def test_dashboard_answer_is_owner_only(engine, tg, admin, web):
    sid = subscribe(engine)
    ask(admin, sid)
    assert answer_web(web, sid, "confirm", who=OTHER).status_code == 404
    assert web.post(f"/api/subscriptions/{sid}/cancel-request/confirm").status_code == 401
    assert requests(engine, sid) == [("PENDING", None)]


def test_normal_cancel_while_pending_confirms_the_request(engine, tg, admin, web):
    sid = subscribe(engine)
    ask(admin, sid)
    message_id, _, _ = request_message(tg)
    assert web.delete(f"/api/subscriptions/{sid}", headers=bearer(ME)).status_code == 200
    assert requests(engine, sid) == [("CONFIRMED", None)]
    assert ("remove_buttons", CHAT[ME], message_id) in tg.calls


def test_paused_subscription_kept_stays_paused(engine, tg, admin, web):
    sid = subscribe(engine)
    web.patch(f"/api/subscriptions/{sid}", json={"status": "PAUSED"}, headers=bearer(ME))
    ask(admin, sid)
    assert answer_web(web, sid, "decline").json["subscription"]["status"] == "PAUSED"


# ------------------------------------------------------------------ withdraw
def test_withdraw_tells_the_customer_and_resumes(engine, tg, admin, web):
    sid = subscribe(engine)
    rid = ask(admin, sid).json["request"]["request_id"]
    message_id, _, _ = request_message(tg)
    r = withdraw(admin, sid)
    assert r.status_code == 200 and r.json["request"]["status"] == "WITHDRAWN"
    assert ("remove_buttons", CHAT[ME], message_id) in tg.calls
    assert "no longer need to cancel" in tg.last_text() and "Done on the website" not in tg.last_text()
    assert requests(engine, sid) == [("WITHDRAWN", "admin")]
    (row,) = web.get("/api/subscriptions", headers=bearer(ME)).json["subscriptions"]
    assert row["cancel_request"] is None
    process_update(engine, tg, tap(CHAT[ME], f"CXL_YES:{rid}"), now=NOW)
    assert "no longer need to cancel" in tg.last_text() and sub_status(engine, sid)[0] == "ACTIVE"
    assert ask(admin, sid).status_code == 200                              # can ask again later


# ------------------------------------------------------------------ schema
def test_migration_007_one_pending_per_subscription(engine):
    sql = MIGRATION.read_text().replace("BEGIN;", "").replace("COMMIT;", "")
    with engine.begin() as conn:
        conn.execute(text(sql))                                          # idempotent on an existing schema
    sid = subscribe(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO cancellation_requests (subscription_id) VALUES (:s)"), {"s": sid})
        conn.execute(text("INSERT INTO cancellation_requests (subscription_id, status) VALUES (:s, 'DECLINED')"),
                     {"s": sid})
        bad = conn.begin_nested()
        with pytest.raises(Exception, match="ux_cancellation_requests_one_pending"):
            conn.execute(text("INSERT INTO cancellation_requests (subscription_id) VALUES (:s)"), {"s": sid})
        bad.rollback()
