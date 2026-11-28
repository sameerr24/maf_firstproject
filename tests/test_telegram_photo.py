"""Telegram messages about a delivery carry the product photo (the website's local copy) with
the text as its caption; without a photo, or if sending it fails, the plain text goes out."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Subscription, WhatsAppMessage
from app.notifications import sender
from subscription_db import CHAT, ME, test_env, admin_engine, schema_engine  # noqa: F401
from app.notifications.telegram_bot import process_update
from test_telegram import FakeTelegram, at, remind, subscribe, tap


@pytest.fixture
def engine(schema_engine):
    return schema_engine[0]


@pytest.fixture
def images(tmp_path, monkeypatch):
    """A product image folder holding a picture of the test milk (looked up by its SKU)."""
    monkeypatch.setattr(sender, "PRODUCT_IMAGES", tmp_path)
    monkeypatch.setattr(sender, "_photo_ids", {})
    return tmp_path


def milk_photo(engine, folder):
    with Session(engine) as s:
        sku = s.scalars(select(Subscription)).first().product.sku_id
    path = folder / f"{sku}.jpg"
    path.write_bytes(b"jpeg")
    return path


def photos(tg):
    return [c for c in tg.calls if c[0] == "photo"]


def test_reminder_is_sent_as_photo_with_buttons(engine, images):
    tg = FakeTelegram()
    sid = subscribe(engine)
    path = milk_photo(engine, images)
    occ = remind(engine, tg, sid)
    assert photos(tg) == [("photo", CHAT[ME], path)]
    assert "Delivery reminder" in tg.last_text()                 # the reminder text is the caption
    assert [b[1] for b in tg.last_buttons()] == [f"APPROVE:{occ}", f"SKIP:{occ}", f"STOP:{occ}"]
    with Session(engine) as s:
        row = s.scalars(select(WhatsAppMessage)).one()
        assert row.payload["photo"] is True and row.wa_message_id == f"tg:{CHAT[ME]}:100"


def test_replies_to_a_photo_reminder_work(engine, images):
    tg = FakeTelegram()
    sid = subscribe(engine)
    milk_photo(engine, images)
    occ = remind(engine, tg, sid)
    process_update(engine, tg, tap(CHAT[ME], f"SKIP:{occ}"), now=at(27, 10))   # fixed clock, before the deadline
    assert f"SKIPCONF:{occ}" in [b[1] for b in tg.last_buttons()]      # same skip menu as for a text reminder


def test_second_send_reuses_the_uploaded_file_id(engine, images):
    tg = FakeTelegram()
    sid = subscribe(engine)
    milk_photo(engine, images)
    remind(engine, tg, sid)
    with Session(engine) as s:
        occ = s.scalars(select(WhatsAppMessage)).one().occurrence
        sender.TelegramSender(tg).send_template(s, customer=occ.subscription.customer,
                                                template=sender.TEMPLATE_SKIPPED, occurrence=occ,
                                                params={"product": "Milk", "date": "Sat", "next_date": "Sat"})
    first, second = photos(tg)
    assert first[2].name.endswith(".jpg") and second[2] == f"file-{first[2].stem}"


def test_no_image_file_sends_text(engine, images):
    tg = FakeTelegram()
    remind(engine, tg, subscribe(engine))
    assert photos(tg) == [] and "Delivery reminder" in tg.last_text()


def test_photo_failure_falls_back_to_text(engine, images):
    tg = FakeTelegram()
    sid = subscribe(engine)
    milk_photo(engine, images)

    def broken(*a, **k):
        raise ConnectionError("upload failed")
    tg.send_photo = broken
    remind(engine, tg, sid)
    assert "Delivery reminder" in tg.last_text() and len(tg.sent()) == 1


def test_blocked_bot_is_not_retried_as_text(engine, images):
    tg = FakeTelegram()
    sid = subscribe(engine)
    milk_photo(engine, images)
    tg.blocked.add(CHAT[ME])
    remind(engine, tg, sid)
    assert tg.sent() == []
