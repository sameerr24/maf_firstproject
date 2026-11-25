"""Keep the chat in step with replies made on the website.

A delivery reminder can be answered in Telegram or on the website. When the customer answers on
the website, the reminder still sitting in their chat must not stay tappable, and the chat
should show what happened. So, after the website's transaction commits:
  1. the Approve / Skip / Stop buttons are removed from the reminder message(s), and
  2. the same confirmation the bot would send is posted to the chat.

Best effort: a Telegram failure never undoes or fails the website action (a stale button
tapped later is refused anyway: "This delivery is no longer waiting for your reply").
Does nothing when TELEGRAM_BOT_TOKEN isn't set.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WhatsAppMessage
from app.notifications.sender import TEMPLATE_REMINDER, telegram_link

log = logging.getLogger(__name__)

_clients: dict[str, object] = {}


@dataclass
class ChatUpdate:
    """Telegram calls to make once the website's transaction has committed."""
    chat_id: int | None = None
    reminders: list[tuple[int, int]] = field(default_factory=list)     # (chat_id, message_id)
    text: str | None = None


def prepare(session: Session, customer_id: str, occurrence_ids: list[int]) -> ChatUpdate:
    """Read (before commit) where the reminders for these deliveries were sent."""
    link = telegram_link(session, customer_id)
    update = ChatUpdate(chat_id=link.chat_id if link else None)
    if not occurrence_ids:
        return update
    ids = session.scalars(select(WhatsAppMessage.wa_message_id).where(
        WhatsAppMessage.occurrence_id.in_(occurrence_ids), WhatsAppMessage.direction == "OUTBOUND",
        WhatsAppMessage.wa_message_id.like("tg:%"),
        WhatsAppMessage.payload["template"].astext == TEMPLATE_REMINDER)).all()
    for mid in ids:
        try:
            _, chat, msg = mid.split(":")
            update.reminders.append((int(chat), int(msg)))
        except ValueError:
            log.debug("Unexpected Telegram message id %r", mid)
    return update


def _client():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return None
    if token not in _clients:
        from app.notifications.telegram_api import TelegramClient
        _clients[token] = TelegramClient(token, timeout=5.0)
    return _clients[token]


def send(update: ChatUpdate, text: str | None = None) -> None:
    """Make the Telegram calls (after commit). Never raises."""
    client = _client()
    if client is None:
        return
    for chat_id, message_id in update.reminders:
        try:
            client.remove_buttons(chat_id, message_id)
        except Exception:
            log.debug("Removing reminder buttons failed (already removed?)", exc_info=True)
    text = text or update.text
    if text and update.chat_id is not None:
        try:
            client.send_message(update.chat_id, f"{text}\n\n<i>Done on the website.</i>")
        except Exception:
            log.warning("Couldn't post the website reply to chat %s", update.chat_id, exc_info=True)
