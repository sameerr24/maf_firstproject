"""Outbound notifications.

The product is designed for WhatsApp (templates in docs/WHATSAPP_TEMPLATES.md); the demo
sends the same messages through a Telegram bot. A customer can be messaged only when
whatsapp_opt_in = true (the general "may be messaged" consent), they have a phone number,
and their Telegram chat is linked (telegram_links). All of this is read fresh for every
message, so changes made live in Supabase apply on the next scheduler run.

Senders:
- TelegramSender: used when TELEGRAM_BOT_TOKEN is set. Sends the template text with inline
  buttons and logs the message in whatsapp_messages.
- LoggingSender: fallback without a token. Only records what it would send
  (payload.status = "not_sent").
"""

from __future__ import annotations

import html
import logging
import os
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.models import Customer, DeliveryOccurrence, TelegramLink, WhatsAppMessage

log = logging.getLogger(__name__)

TEMPLATE_REMINDER = "subscription_delivery_reminder"
TEMPLATE_SKIPPED = "delivery_skipped_notice"
TEMPLATE_OUT_OF_STOCK = "delivery_out_of_stock_notice"

# Body variables of each template, in {{1}}, {{2}}, ... order. Must match the WhatsApp
# templates submitted to Meta (docs/WHATSAPP_TEMPLATES.md).
TEMPLATE_BODY_PARAMS: dict[str, tuple[str, ...]] = {
    TEMPLATE_REMINDER: ("quantity", "product", "date", "price", "reply_by"),
    TEMPLATE_SKIPPED: ("product", "date", "next_date"),
    TEMPLATE_OUT_OF_STOCK: ("product", "quantity", "date", "next_date"),
}
# Quick-reply buttons per template, in button order (payloads are set per message).
TEMPLATE_BUTTONS: dict[str, int] = {TEMPLATE_REMINDER: 3, TEMPLATE_SKIPPED: 0, TEMPLATE_OUT_OF_STOCK: 0}

# The same wording as the WhatsApp templates, for channels that send plain text (Telegram).
TEMPLATE_TEXT: dict[str, str] = {
    TEMPLATE_REMINDER: (
        "<b>Delivery reminder</b>\n\n"
        "Your subscription delivery of {quantity} x {product} is scheduled for {date}.\n\n"
        "Total: {price}\n\n"
        "Please approve to confirm this delivery. You will not be charged unless you approve. "
        "If we don't hear from you by {reply_by}, this delivery will be skipped."
    ),
    TEMPLATE_SKIPPED: (
        "Your {product} delivery for {date} was skipped because we did not receive your approval "
        "in time. You have not been charged.\n\n"
        "Your next delivery is scheduled for {next_date}. We will send you a reminder before then."
    ),
    TEMPLATE_OUT_OF_STOCK: (
        "Sorry, {product} is out of stock, so we could not schedule your delivery of {quantity} for "
        "{date}. You have not been charged.\n\n"
        "Your next delivery is scheduled for {next_date}. We will send you a reminder before then."
    ),
}
FOOTER = "<i>Smart Reorder subscriptions</i>"
BUTTON_LABELS = {"APPROVE": "Approve", "SKIP": "Skip or delay", "STOP": "Stop subscription"}


def body_parameters(template: str, params: dict[str, Any], buttons: list[str] | None = None) -> list[str]:
    """Template variables as Meta expects them ({{1}}..{{n}}); refuses anything that would
    not match the approved template."""
    if template not in TEMPLATE_BODY_PARAMS:
        raise ValueError(f"Unknown template {template!r}")
    order = TEMPLATE_BODY_PARAMS[template]
    missing = [k for k in order if params.get(k) in (None, "")]
    extra = sorted(set(params) - set(order))
    if missing or extra:
        raise ValueError(f"{template}: missing {missing}, unexpected {extra}")
    if len(buttons or []) != TEMPLATE_BUTTONS[template]:
        raise ValueError(f"{template}: expects {TEMPLATE_BUTTONS[template]} buttons, got {len(buttons or [])}")
    return [str(params[k]) for k in order]


def render_text(template: str, params: dict[str, Any]) -> str:
    safe = {k: html.escape(str(v), quote=False) for k, v in params.items()}
    return f"{TEMPLATE_TEXT[template].format(**safe)}\n\n{FOOTER}"


def inline_keyboard(buttons: list[str]) -> dict | None:
    """One button per row; each payload (e.g. "APPROVE:12") becomes the callback data."""
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": BUTTON_LABELS[b.split(":", 1)[0]], "callback_data": b}]
                                for b in buttons]}


def mask_phone(phone: str | None) -> str:
    return f"{phone[:3]}{'*' * (len(phone) - 7)}{phone[-4:]}" if phone and len(phone) > 7 else "-"


def telegram_link(session: Session, customer_id: str) -> TelegramLink | None:
    return session.get(TelegramLink, customer_id)


def can_message(session: Session, customer: Customer) -> bool:
    return bool(customer.whatsapp_opt_in and customer.phone_e164
                and telegram_link(session, customer.customer_id) is not None)


class MessageSender(Protocol):
    def send_template(self, session: Session, *, customer: Customer, template: str, params: dict[str, Any],
                      occurrence: DeliveryOccurrence | None = None,
                      buttons: list[str] | None = None) -> WhatsAppMessage | None: ...


def _log_row(session: Session, customer: Customer, occurrence: DeliveryOccurrence | None,
             payload: dict[str, Any], message_id: str | None = None) -> WhatsAppMessage:
    msg = WhatsAppMessage(wa_message_id=message_id, customer_id=customer.customer_id,
                          occurrence_id=occurrence.occurrence_id if occurrence is not None else None,
                          direction="OUTBOUND", message_type="template", payload=payload)
    session.add(msg)
    return msg


class LoggingSender:
    """Records what it would send; sends nothing."""

    def send_template(self, session, *, customer, template, params, occurrence=None, buttons=None):
        body = body_parameters(template, params, buttons)       # validate even when not sending
        if not can_message(session, customer):
            log.debug("Not messaging %s (%s): not reachable", customer.customer_id, template)
            return None
        log.info("[notify stub] %s -> %s %s %s", template, mask_phone(customer.phone_e164), params, buttons or "")
        return _log_row(session, customer, occurrence, {
            "channel": "log", "template": template, "to": customer.phone_e164, "params": params,
            "body_parameters": body, "buttons": buttons or [], "status": "not_sent",
            "reason": "no_messaging_client_configured"})


class TelegramSender:
    def __init__(self, client):
        self.client = client

    def send_template(self, session, *, customer, template, params, occurrence=None, buttons=None):
        from app.notifications.telegram_api import TelegramBlocked

        body = body_parameters(template, params, buttons)
        if not can_message(session, customer):
            log.debug("Not messaging %s (%s): not reachable", customer.customer_id, template)
            return None
        link = telegram_link(session, customer.customer_id)
        try:
            sent = self.client.send_message(link.chat_id, render_text(template, params), inline_keyboard(buttons or []))
        except TelegramBlocked:
            log.warning("Customer %s blocked the bot; unlinking their chat", customer.customer_id)
            session.delete(link)
            return None
        log.info("[telegram] %s -> chat %s (%s)", template, link.chat_id, customer.customer_id)
        return _log_row(session, customer, occurrence, {
            "channel": "telegram", "template": template, "chat_id": link.chat_id, "params": params,
            "body_parameters": body, "buttons": buttons or [], "status": "sent"},
            message_id=f"tg:{link.chat_id}:{sent['message_id']}")


def get_sender() -> MessageSender:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        from app.notifications.telegram_api import TelegramClient
        return TelegramSender(TelegramClient(token))
    return LoggingSender()
