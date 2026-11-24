"""Telegram bot: sending reminders, linking chats to customers, and the reply flows
(approve -> slot -> order, skip, delay, stop). Uses a fake Telegram client (no network) and
a throwaway schema on the local test database."""
import datetime as dt
import itertools

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DeliveryOccurrence, Inventory, Subscription, TelegramLink, WhatsAppMessage
from app.notifications.sender import TelegramSender
from app.notifications.telegram_api import TelegramBlocked
from app.notifications.telegram_bot import process_update
from app.subscriptions import jobs
from app.subscriptions import service as svc
from app.timeutil import LOCAL_TZ
from database.models import Order, OrderItem
from subscription_db import CHAT, ME, MILK, OTHER, TODAY, no_real_telegram, admin_engine, schema_engine  # noqa: F401


def at(day, hour=9, minute=0, month=11):
    return dt.datetime(2026, month, day, hour, minute, tzinfo=LOCAL_TZ)


class FakeTelegram:
    def __init__(self):
        self.calls, self.blocked, self._ids = [], set(), itertools.count(100)

    def send_message(self, chat_id, text, reply_markup=None):
        if chat_id in self.blocked:
            raise TelegramBlocked(403, "Forbidden: bot was blocked by the user")
        self.calls.append(("send", chat_id, text, reply_markup))
        return {"message_id": next(self._ids)}

    def answer_callback_query(self, callback_query_id, text=None):
        self.calls.append(("answer", callback_query_id))

    def remove_buttons(self, chat_id, message_id):
        self.calls.append(("remove_buttons", chat_id, message_id))

    def sent(self):
        return [c for c in self.calls if c[0] == "send"]

    def last_text(self):
        return self.sent()[-1][2]

    def last_buttons(self):
        markup = self.sent()[-1][3] or {}
        return [(row[0]["text"], row[0].get("callback_data")) for row in markup.get("inline_keyboard", [])]


_update_ids = itertools.count(1)


def text_msg(chat, text="/start", chat_type="private"):
    return {"update_id": next(_update_ids), "message": {"message_id": 1, "chat": {"id": chat, "type": chat_type},
                                                        "from": {"id": chat}, "text": text}}


def contact_msg(chat, phone, user_id=None):
    return {"update_id": next(_update_ids), "message": {
        "message_id": 2, "chat": {"id": chat, "type": "private"}, "from": {"id": chat},
        "contact": {"phone_number": phone, "user_id": chat if user_id is None else user_id}}}


def tap(chat, data, from_id=None):
    uid = next(_update_ids)
    return {"update_id": uid, "callback_query": {"id": f"cq{uid}", "from": {"id": from_id or chat}, "data": data,
                                                 "message": {"message_id": 50, "chat": {"id": chat, "type": "private"}}}}


@pytest.fixture
def engine(schema_engine):
    return schema_engine[0]


@pytest.fixture
def tg():
    return FakeTelegram()


def subscribe(engine, customer=ME, **data):
    with Session(engine) as s:
        sid = svc.create_subscription(s, customer, {"product_id": MILK, **data}, today=TODAY)["subscription_id"]
        s.commit()
    return sid


def remind(engine, tg, sid, now=None):
    """Run the scheduler with the Telegram sender; return the new occurrence id."""
    jobs.run_scheduler(engine, now=now or at(27, 9), sender=TelegramSender(tg))
    with Session(engine) as s:
        return s.scalars(select(DeliveryOccurrence.occurrence_id).where(
            DeliveryOccurrence.subscription_id == sid).order_by(DeliveryOccurrence.occurrence_id.desc())).first()


def occ_state(engine, occ_id):
    with Session(engine) as s:
        o = s.get(DeliveryOccurrence, occ_id)
        return o.status, o.skip_reason, o.order_id, o.delay_days


def next_date(engine, sid):
    with Session(engine) as s:
        return s.get(Subscription, sid).next_delivery_date


# ------------------------------------------------------------------ sending
def test_reminder_sent_to_linked_chat_with_buttons(engine, tg):
    sid = subscribe(engine, quantity=3)
    occ_id = remind(engine, tg, sid)
    (_, chat, text, markup), = tg.sent()
    assert chat == CHAT[ME]
    assert "<b>Delivery reminder</b>" in text and "3 x Milk" in text and "Total: ₹81.00" in text
    assert "9:00 PM on Fri, 27 Nov" in text and text.endswith("<i>Smart Reorder subscriptions</i>")
    assert tg.last_buttons() == [("Approve", f"APPROVE:{occ_id}"), ("Skip or delay", f"SKIP:{occ_id}"),
                                 ("Stop subscription", f"STOP:{occ_id}")]
    with Session(engine) as s:
        msg = s.scalars(select(WhatsAppMessage)).one()
        assert msg.wa_message_id == f"tg:{CHAT[ME]}:100" and msg.payload["status"] == "sent"
        assert msg.payload["channel"] == "telegram" and msg.occurrence_id == occ_id


def test_blocked_bot_unlinks_chat(engine, tg):
    sid = subscribe(engine)
    tg.blocked.add(CHAT[ME])
    remind(engine, tg, sid)
    assert tg.sent() == []
    with Session(engine) as s:
        assert s.get(TelegramLink, ME) is None
        assert svc.can_message(s, s.get(svc.Customer, ME)) is False


# ------------------------------------------------------------------ linking
def test_start_asks_for_phone_and_contact_links(engine, tg):
    new_chat = 5555
    process_update(engine, tg, text_msg(new_chat))
    assert "share the phone number" in tg.last_text() and tg.sent()[-1][3]["keyboard"][0][0]["request_contact"]
    process_update(engine, tg, contact_msg(new_chat, "919800000001"))          # no '+', as Telegram often sends
    assert "Linked to" in tg.last_text() and ME in tg.last_text()
    with Session(engine) as s:
        link = s.get(TelegramLink, ME)
        assert (link.chat_id, link.telegram_user_id) == (new_chat, new_chat)   # moved from the old chat
        assert s.scalar(select(func.count()).select_from(TelegramLink).where(TelegramLink.chat_id == CHAT[ME])) == 0
    process_update(engine, tg, text_msg(new_chat, "hi"))
    assert "You're connected as" in tg.last_text()


def test_contact_must_be_own_and_registered(engine, tg):
    process_update(engine, tg, contact_msg(7777, "+919800000001", user_id=8888))  # forwarded someone else's card
    assert "your own" in tg.last_text()
    process_update(engine, tg, contact_msg(7777, "+15550001111"))
    assert "isn't registered" in tg.last_text()
    with Session(engine) as s:
        assert s.get(TelegramLink, ME).chat_id == CHAT[ME]


def test_group_messages_ignored(engine, tg):
    assert process_update(engine, tg, text_msg(-100, chat_type="group")) == "processed" and tg.calls == []


# ------------------------------------------------------------------ approve
def test_approve_pick_slot_places_order(engine, tg):
    sid = subscribe(engine, quantity=3)
    occ_id = remind(engine, tg, sid)
    process_update(engine, tg, tap(CHAT[ME], f"APPROVE:{occ_id}"), now=at(27, 10))
    assert ("answer", tg.calls[-3][1]) == tg.calls[-3] and tg.calls[-2][0] == "remove_buttons"
    assert "Pick a delivery slot" in tg.last_text()
    assert tg.last_buttons()[0] == ("07:00-09:00", f"SLOT:{occ_id}:0") and tg.last_buttons()[-1][0] == "Back"

    process_update(engine, tg, tap(CHAT[ME], f"SLOT:{occ_id}:1"), now=at(27, 10, 1))
    assert "Order placed" in tg.last_text() and f"SUB-{occ_id}" in tg.last_text() and "₹81.00" in tg.last_text()
    with Session(engine) as s:
        order = s.get(Order, f"SUB-{occ_id}")
        assert (order.source, order.delivery_slot, order.item_count, order.unit_count) == \
            ("subscription", "09:00-12:00", 1, 3)
        assert order.order_datetime == dt.datetime(2026, 11, 27, 10, 1) and order.order_date == dt.date(2026, 11, 27)
        item = s.get(OrderItem, f"SUBI-{occ_id}")
        assert (item.quantity, str(item.unit_price), str(item.line_total)) == (3, "27.00", "81.00")
        assert s.get(Inventory, MILK).available_qty == 2
    assert occ_state(engine, occ_id) == ("ORDER_PLACED", None, f"SUB-{occ_id}", None)
    assert next_date(engine, sid) == dt.date(2026, 12, 5)

    process_update(engine, tg, tap(CHAT[ME], f"SLOT:{occ_id}:0"), now=at(27, 10, 2))   # stale double tap
    assert "no longer waiting" in tg.last_text()
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Order)) == 1


def test_order_time_is_ist_after_midnight(engine, tg):
    sid = subscribe(engine)
    occ_id = remind(engine, tg, sid, now=at(27, 23, 50))           # late reminder: deadline 00:50 IST
    process_update(engine, tg, tap(CHAT[ME], f"SLOT:{occ_id}:0"), now=at(28, 0, 30))
    with Session(engine) as s:
        order = s.get(Order, f"SUB-{occ_id}")
        assert order.order_date == dt.date(2026, 11, 28)           # UTC would say 27 Nov
        assert order.order_datetime == dt.datetime(2026, 11, 28, 0, 30)


def test_out_of_stock_at_approval(engine, tg):
    sid = subscribe(engine, quantity=3)
    occ_id = remind(engine, tg, sid)
    with Session(engine) as s:
        s.get(Inventory, MILK).available_qty = 1
        s.commit()
    process_update(engine, tg, tap(CHAT[ME], f"SLOT:{occ_id}:0"), now=at(27, 10))
    assert "out of stock" in tg.last_text() and "not been charged" in tg.last_text()
    assert occ_state(engine, occ_id)[:2] == ("OUT_OF_STOCK", "OUT_OF_STOCK")
    assert next_date(engine, sid) == dt.date(2026, 12, 5)
    with Session(engine) as s:
        assert s.get(Order, f"SUB-{occ_id}") is None and s.get(Inventory, MILK).available_qty == 1


def test_cannot_approve_after_deadline(engine, tg):
    sid = subscribe(engine)
    occ_id = remind(engine, tg, sid)
    process_update(engine, tg, tap(CHAT[ME], f"SLOT:{occ_id}:0"), now=at(27, 21, 5))
    assert "time to reply" in tg.last_text() and occ_state(engine, occ_id)[0] == "NOTIFIED"


# ------------------------------------------------------------------ skip / delay / stop
def test_skip_menu_and_skip(engine, tg):
    sid = subscribe(engine)
    occ_id = remind(engine, tg, sid)
    process_update(engine, tg, tap(CHAT[ME], f"SKIP:{occ_id}"), now=at(27, 10))
    assert [b[0] for b in tg.last_buttons()] == ["Skip this delivery", "Delay 1 day", "Delay 2 days", "Back"]
    process_update(engine, tg, tap(CHAT[ME], f"SKIPCONF:{occ_id}"), now=at(27, 10, 1))
    assert "Skipped" in tg.last_text() and "Sat, 05 Dec" in tg.last_text()
    assert occ_state(engine, occ_id)[:2] == ("SKIPPED", "USER_SKIP") and next_date(engine, sid) == dt.date(2026, 12, 5)


def test_delay_then_reminded_again(engine, tg):
    sid = subscribe(engine)
    occ_id = remind(engine, tg, sid)
    process_update(engine, tg, tap(CHAT[ME], f"DELAY:{occ_id}:2"), now=at(27, 10))
    assert "Mon, 30 Nov" in tg.last_text() and "approve it again on Sun, 29 Nov" in tg.last_text()
    assert occ_state(engine, occ_id) == ("DELAYED", None, None, 2)
    jobs.run_scheduler(engine, now=at(29, 9), sender=TelegramSender(tg))
    assert "Mon, 30 Nov" in tg.last_text() and tg.last_buttons()[0] == ("Approve", f"APPROVE:{occ_id}")


def test_daily_items_offer_no_delay(engine, tg):
    sid = subscribe(engine, frequency_days=1)
    occ_id = remind(engine, tg, sid)
    process_update(engine, tg, tap(CHAT[ME], f"SKIP:{occ_id}"), now=at(27, 10))
    assert [b[0] for b in tg.last_buttons()] == ["Skip this delivery", "Back"]


def test_stop_needs_confirmation(engine, tg):
    sid = subscribe(engine)
    occ_id = remind(engine, tg, sid)
    process_update(engine, tg, tap(CHAT[ME], f"STOP:{occ_id}"), now=at(27, 10))
    assert [b[0] for b in tg.last_buttons()] == ["Yes, stop it", "No, keep it"]
    process_update(engine, tg, tap(CHAT[ME], f"BACK:{occ_id}"), now=at(27, 10, 1))   # kept: options again
    assert [b[1] for b in tg.last_buttons()] == [f"APPROVE:{occ_id}", f"SKIP:{occ_id}", f"STOP:{occ_id}"]
    process_update(engine, tg, tap(CHAT[ME], f"STOPCONF:{occ_id}"), now=at(27, 10, 2))
    assert "cancelled" in tg.last_text()
    with Session(engine) as s:
        assert s.get(Subscription, sid).status == "CANCELLED"
    assert occ_state(engine, occ_id)[0] == "CANCELLED"


# ------------------------------------------------------------------ safety
def test_cannot_act_on_someone_elses_delivery(engine, tg):
    sid = subscribe(engine)
    occ_id = remind(engine, tg, sid)
    process_update(engine, tg, tap(CHAT[OTHER], f"SLOT:{occ_id}:0"), now=at(27, 10))
    assert "not found" in tg.last_text().lower() and occ_state(engine, occ_id)[0] == "NOTIFIED"
    process_update(engine, tg, tap(9999, f"APPROVE:{occ_id}"), now=at(27, 10))          # unlinked chat
    assert "link your account first" in tg.last_text()
    process_update(engine, tg, tap(CHAT[ME], f"APPROVE:{occ_id}", from_id=4242), now=at(27, 10))  # other user
    assert "link your account first" in tg.last_text()


def test_duplicate_update_processed_once(engine, tg):
    sid = subscribe(engine)
    occ_id = remind(engine, tg, sid)
    update = tap(CHAT[ME], f"SKIPCONF:{occ_id}")
    assert process_update(engine, tg, update, now=at(27, 10)) == "processed"
    sent = len(tg.sent())
    assert process_update(engine, tg, update, now=at(27, 10)) == "duplicate"
    assert len(tg.sent()) == sent                   # acknowledged again (harmless), nothing new sent


def test_bad_button_data(engine, tg):
    process_update(engine, tg, tap(CHAT[ME], "APPROVE:abc"))
    assert "didn't understand" in tg.last_text()


def test_unexpected_error_rolls_back_and_apologises(engine, tg, monkeypatch):
    sid = subscribe(engine)
    occ_id = remind(engine, tg, sid)

    def boom(*a, **k):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(svc, "skip_delivery", boom)
    update = tap(CHAT[ME], f"SKIPCONF:{occ_id}")
    assert process_update(engine, tg, update, now=at(27, 10)) == "error"
    assert "something went wrong" in tg.last_text() and occ_state(engine, occ_id)[0] == "NOTIFIED"
    monkeypatch.undo()
    assert process_update(engine, tg, update, now=at(27, 10)) == "processed"   # not recorded, so retryable
    assert occ_state(engine, occ_id)[0] == "SKIPPED"
