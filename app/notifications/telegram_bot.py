"""Telegram bot: account linking and the reply flows (stand-in for WhatsApp in the demo).

Linking: a bot can only message chats that started it, by chat id. The customer taps
Start, then "Share my phone number" (Telegram's built-in contact button); the shared
number must be their own and must match customers.phone_e164. The chat is then stored in
telegram_links.

Reply flows (buttons on the delivery reminder; callback data "<ACTION>:<occurrence_id>[:arg]"):
  APPROVE  -> slot picker (SLOT:<occ>:<slot index>) -> stock check + order, or out of stock
  SKIP     -> "Skip this delivery" (SKIPCONF) / "Delay N days" (DELAY:<occ>:<n>) / Back
  STOP     -> "Are you sure?" -> STOPCONF cancels the subscription / BACK keeps it
Every action checks that the delivery belongs to the customer linked to this chat.

Updates are processed by process_update(): one transaction per update, deduplicated on
update_id (logged in whatsapp_messages); Telegram calls are made only after the commit, so
a confirmation is never sent for something that was rolled back.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.constants import DELIVERY_SLOTS
from app.errors import ServiceError, ValidationError
from app.models import TelegramLink, WhatsAppMessage
from app.notifications.sender import BUTTON_LABELS
from app.subscriptions import service as svc
from app.timeutil import fmt_day, now_utc
from database.models import Customer

log = logging.getLogger(__name__)

CONTACT_KEYBOARD = {"keyboard": [[{"text": "Share my phone number", "request_contact": True}]],
                    "resize_keyboard": True, "one_time_keyboard": True}
REMOVE_KEYBOARD = {"remove_keyboard": True}


@dataclass
class Out:
    """A Bot API call to make after the database transaction has committed."""
    method: str
    params: dict[str, Any] = field(default_factory=dict)


def _send(chat_id: int, text: str, markup: dict | None = None) -> Out:
    return Out("send_message", {"chat_id": chat_id, "text": text, "reply_markup": markup})


def _buttons(*rows: tuple[str, str]) -> dict:
    return {"inline_keyboard": [[{"text": label, "callback_data": data}] for label, data in rows]}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=False)   # Telegram HTML only needs < > &


def normalize_phone(raw: str | None) -> str | None:
    """Telegram sends numbers with or without '+'; customers.phone_e164 always has it."""
    digits = re.sub(r"\D", "", raw or "")
    return f"+{digits}" if digits else None


def _link_for_chat(session: Session, chat_id: int) -> TelegramLink | None:
    return session.scalars(select(TelegramLink).where(TelegramLink.chat_id == chat_id)).one_or_none()


def _customer_label(c: Customer) -> str:
    return f"<b>{_esc(c.customer_name or c.customer_id)}</b> ({_esc(c.customer_id)})"


# --------------------------------------------------------------------------- #
# Messages: /start and contact sharing
# --------------------------------------------------------------------------- #
def _on_text(session: Session, chat_id: int) -> list[Out]:
    link = _link_for_chat(session, chat_id)
    if link is not None:
        customer = session.get(Customer, link.customer_id)
        return [_send(chat_id, f"You're connected as {_customer_label(customer)}. "
                               "Your delivery reminders will arrive here.")]
    return [_send(chat_id, "Welcome to Smart Reorder! To get your subscription reminders here, share the "
                           "phone number registered with your account.", CONTACT_KEYBOARD)]


def _on_contact(session: Session, msg: dict, chat_id: int) -> list[Out]:
    contact, sender_id = msg["contact"], msg["from"]["id"]
    if contact.get("user_id") != sender_id:
        return [_send(chat_id, "Please share <b>your own</b> number using the button below.", CONTACT_KEYBOARD)]
    phone = normalize_phone(contact.get("phone_number"))
    customer = session.scalars(select(Customer).where(Customer.phone_e164 == phone)).one_or_none()
    if customer is None:
        return [_send(chat_id, "This number isn't registered with a Smart Reorder account.", REMOVE_KEYBOARD)]
    # One chat per customer and one customer per chat: replace any previous link of either.
    session.execute(delete(TelegramLink).where(or_(TelegramLink.chat_id == chat_id,
                                                   TelegramLink.customer_id == customer.customer_id)))
    session.add(TelegramLink(customer_id=customer.customer_id, chat_id=chat_id, telegram_user_id=sender_id))
    session.flush()
    note = ("" if customer.whatsapp_opt_in else
            "\n\nNotifications are turned off for this account, so reminders won't arrive until they're "
            "turned on.")
    return [_send(chat_id, f"Linked to {_customer_label(customer)}. Your delivery reminders will arrive "
                           f"here.{note}", REMOVE_KEYBOARD)]


# --------------------------------------------------------------------------- #
# Button taps
# --------------------------------------------------------------------------- #
def _next_day(r: dict) -> str:
    return fmt_day(dt.date.fromisoformat(r["next_delivery_date"])) if r["next_delivery_date"] else "-"


# Confirmation texts, shared with the website (app/notifications/chat_sync.py) so a reply made
# on either channel reads the same in the chat. `product` is already HTML-escaped.
def approved_text(product: str, when: str, r: dict) -> str:
    if r["status"] == "out_of_stock":
        return (f"Sorry, {product} just went out of stock, so this delivery couldn't be placed. "
                f"You have not been charged.\n\nNext delivery: {_next_day(r)}.")
    return (f"✅ <b>Order placed</b>\n{r['quantity']} x {product}\nDelivery: {when}, {_esc(r['slot'])}\n"
            f"Total: ₹{r['total']}\nOrder ID: {_esc(r['order_id'])}\n\nNext delivery: {_next_day(r)}.")


def skipped_text(product: str, when: str, r: dict) -> str:
    return f"Skipped your {when} delivery of {product}. You have not been charged.\n\nNext delivery: {_next_day(r)}."


def delayed_text(product: str, new: dt.date) -> str:
    return (f"Moved your {product} delivery to {fmt_day(new)}. We'll ask you to approve it again on "
            f"{fmt_day(new - dt.timedelta(days=1))}; nothing is charged until you approve.")


def stopped_text(product: str) -> str:
    return f"Your {product} subscription is cancelled. You won't be charged again."


def _reminder_buttons(occ_id: int) -> dict:
    return _buttons(*((BUTTON_LABELS[a], f"{a}:{occ_id}") for a in ("APPROVE", "SKIP", "STOP")))


def _dispatch(session: Session, customer_id: str, chat_id: int, data: str, now) -> list[Out]:
    try:
        action, occ_id, *rest = data.split(":")
        occ_id = int(occ_id)
        arg = int(rest[0]) if rest else None
    except (ValueError, IndexError):
        raise ValidationError("Sorry, I didn't understand that button.") from None

    sub, occ = svc.get_owned_delivery(session, customer_id, occ_id)
    product, when = _esc(sub.product.product_name), fmt_day(svc.delivery_date(occ))

    if action == "APPROVE":
        svc.ensure_awaiting_reply(sub, occ, now)
        slots = [(slot, f"SLOT:{occ_id}:{i}") for i, slot in enumerate(DELIVERY_SLOTS)]
        return [_send(chat_id, f"Pick a delivery slot for {sub.quantity} x {product} on {when}:",
                      _buttons(*slots, ("Back", f"BACK:{occ_id}")))]

    if action == "SLOT":
        if arg is None or not 0 <= arg < len(DELIVERY_SLOTS):
            raise ValidationError("Please pick one of the slots shown.")
        r = svc.approve_delivery(session, customer_id, occ_id, DELIVERY_SLOTS[arg], now=now)
        return [_send(chat_id, approved_text(product, when, r))]

    if action == "SKIP":
        svc.ensure_awaiting_reply(sub, occ, now)
        rows = [("Skip this delivery", f"SKIPCONF:{occ_id}")]
        rows += [(f"Delay {n} day{'s' if n > 1 else ''}", f"DELAY:{occ_id}:{n}") for n in svc.delay_options(sub, occ)]
        rows.append(("Back", f"BACK:{occ_id}"))
        return [_send(chat_id, f"What would you like to do with your {when} delivery of {product}?", _buttons(*rows))]

    if action == "SKIPCONF":
        r = svc.skip_delivery(session, customer_id, occ_id, now=now)
        return [_send(chat_id, skipped_text(product, when, r))]

    if action == "DELAY":
        occ = svc.delay_delivery(session, customer_id, occ_id, arg or 0, now=now)
        return [_send(chat_id, delayed_text(product, svc.delivery_date(occ)))]

    if action == "STOP":
        if sub.status == "CANCELLED":
            raise ValidationError("This subscription is already cancelled.")
        return [_send(chat_id, f"Stop your {product} subscription? All future deliveries will be cancelled.",
                      _buttons(("Yes, stop it", f"STOPCONF:{occ_id}"), ("No, keep it", f"BACK:{occ_id}")))]

    if action == "STOPCONF":
        svc.cancel_subscription(session, customer_id, sub.subscription_id)
        return [_send(chat_id, stopped_text(product))]

    if action == "BACK":
        svc.ensure_awaiting_reply(sub, occ, now)
        return [_send(chat_id, f"Your {when} delivery of {sub.quantity} x {product} is waiting for your reply.",
                      _reminder_buttons(occ_id))]

    raise ValidationError("Sorry, I didn't understand that button.")


def _on_callback(session: Session, cq: dict, now) -> list[Out]:
    # The tap was already acknowledged and its buttons removed (process_update -> _ack_tap).
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return []
    link = _link_for_chat(session, chat_id)
    if link is None or link.telegram_user_id != cq["from"]["id"]:
        return [_send(chat_id, "Please link your account first: send /start.")]
    try:
        with session.begin_nested():               # a refused action leaves no partial changes
            return _dispatch(session, link.customer_id, chat_id, cq.get("data") or "", now)
    except ServiceError as e:
        return [_send(chat_id, _esc(e.message))]


def handle_update(session: Session, update: dict, now=None) -> list[Out]:
    """Apply one Telegram update to the database; return the Bot API calls to make after commit."""
    now = now or now_utc()
    if "callback_query" in update:
        return _on_callback(session, update["callback_query"], now)
    msg = update.get("message")
    if not msg or (msg.get("chat") or {}).get("type") != "private":
        return []
    chat_id = msg["chat"]["id"]
    if "contact" in msg:
        return _on_contact(session, msg, chat_id)
    return _on_text(session, chat_id)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def _chat_id(update: dict) -> int | None:
    msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    return (msg.get("chat") or {}).get("id")


def _ack_tap(client, cq: dict) -> None:
    """Stop the button's spinner and remove the tapped message's buttons right away, before any
    database work: Telegram expects an answer within seconds, and removing the buttons
    prevents double taps. Failures here are cosmetic (e.g. buttons already removed)."""
    try:
        client.answer_callback_query(cq["id"])
    except Exception:
        log.debug("answerCallbackQuery failed", exc_info=True)
    msg = cq.get("message") or {}
    if (msg.get("chat") or {}).get("id") is not None and msg.get("message_id") is not None:
        try:
            client.remove_buttons(msg["chat"]["id"], msg["message_id"])
        except Exception:
            log.debug("removing buttons failed", exc_info=True)


def _apply(engine: Engine, update: dict, chat_id: int | None, now) -> list[Out]:
    """The database part of one update, in its own transaction."""
    with Session(engine, expire_on_commit=False) as s, s.begin():
        link = _link_for_chat(s, chat_id) if chat_id is not None else None
        s.add(WhatsAppMessage(wa_message_id=f"tg-update:{update['update_id']}", direction="INBOUND",
                              message_type="callback" if "callback_query" in update else "message",
                              customer_id=link.customer_id if link else None, payload=update))
        s.flush()                                   # duplicate update_id -> IntegrityError
        return handle_update(s, update, now)


def process_update(engine: Engine, client, update: dict, now=None) -> str:
    """Process one update in its own transaction, then make the Telegram calls.
    Returns "processed", "duplicate" or "error"."""
    chat_id = _chat_id(update)
    status = "processed"
    if "callback_query" in update:
        _ack_tap(client, update["callback_query"])
    try:
        try:
            outs = _apply(engine, update, chat_id, now)
        except IntegrityError:
            raise
        except DBAPIError:
            # Usually a pooled connection that died (laptop slept, network dropped). Nothing
            # was committed; drop the pool and try this update once more on fresh connections.
            log.warning("Database error on update %s; reconnecting and retrying once", update.get("update_id"))
            engine.dispose()
            outs = _apply(engine, update, chat_id, now)
    except IntegrityError as e:
        if "wa_message_id" in str(e.orig):
            return "duplicate"
        log.exception("Telegram update %s failed", update.get("update_id"))
        outs, status = [], "error"
    except Exception:
        log.exception("Telegram update %s failed", update.get("update_id"))
        outs, status = [], "error"
    if status == "error" and chat_id is not None:
        # The tapped message's buttons were already removed: offer the same action again.
        data = (update.get("callback_query") or {}).get("data")
        retry = _buttons(("Try again", data)) if data else None
        outs = [_send(chat_id, "Sorry, something went wrong. Please try again in a moment.", retry)]
    for o in outs:
        try:
            getattr(client, o.method)(**o.params)
        except Exception:
            log.warning("Telegram %s failed for update %s", o.method, update.get("update_id"), exc_info=True)
    return status


def poll_forever(engine: Engine, client, stop: threading.Event | None = None) -> None:
    """Long-poll Telegram for updates (no public URL or ngrok needed)."""
    me = client.get_me()
    log.info("Telegram bot @%s polling. Customers link at https://t.me/%s", me.get("username"), me.get("username"))
    offset = None
    while stop is None or not stop.is_set():
        try:
            updates = client.get_updates(offset)
        except Exception as e:
            # Typically a dead connection after sleep or a network blip: start a fresh one.
            log.warning("Polling Telegram failed (%s); reconnecting in 2s", type(e).__name__)
            if hasattr(client, "reset"):
                client.reset()
            time.sleep(2)
            continue
        for update in updates:
            process_update(engine, client, update)
            offset = update["update_id"] + 1
