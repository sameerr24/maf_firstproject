"""Outbound WhatsApp messages.

Only customers with whatsapp_opt_in = true and a phone number are ever messaged; the
contact details are read fresh for every message, so a phone/opt-in change made live in
Supabase takes effect on the next scheduler run.

LoggingSender is the stand-in until the Meta Cloud API client exists: it records the
message it *would* send in whatsapp_messages (payload.status = "not_sent") and logs it.
The real client will implement the same send_template() and store Meta's wa_message_id.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.models import Customer, DeliveryOccurrence, WhatsAppMessage

log = logging.getLogger(__name__)

TEMPLATE_REMINDER = "subscription_delivery_reminder"
TEMPLATE_SKIPPED = "delivery_skipped_notice"
TEMPLATE_OUT_OF_STOCK = "delivery_out_of_stock_notice"

# Body variables of each approved template, in {{1}}, {{2}}, ... order. Must match the
# templates submitted to Meta (docs/WHATSAPP_TEMPLATES.md).
TEMPLATE_BODY_PARAMS: dict[str, tuple[str, ...]] = {
    TEMPLATE_REMINDER: ("quantity", "product", "date", "price", "reply_by"),
    TEMPLATE_SKIPPED: ("product", "date", "next_date"),
    TEMPLATE_OUT_OF_STOCK: ("product", "quantity", "date", "next_date"),
}
# Quick-reply buttons per template, in button order (payloads are set per message).
TEMPLATE_BUTTONS: dict[str, int] = {TEMPLATE_REMINDER: 3, TEMPLATE_SKIPPED: 0, TEMPLATE_OUT_OF_STOCK: 0}


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


def mask_phone(phone: str | None) -> str:
    return f"{phone[:3]}{'*' * (len(phone) - 7)}{phone[-4:]}" if phone and len(phone) > 7 else "-"


def can_message(customer: Customer) -> bool:
    return bool(customer.whatsapp_opt_in and customer.phone_e164)


class MessageSender(Protocol):
    def send_template(self, session: Session, *, customer: Customer, template: str, params: dict[str, Any],
                      occurrence: DeliveryOccurrence | None = None,
                      buttons: list[str] | None = None) -> WhatsAppMessage | None: ...


class LoggingSender:
    def send_template(self, session, *, customer, template, params, occurrence=None, buttons=None):
        body = body_parameters(template, params, buttons)       # validate even when not sending
        if not can_message(customer):
            log.debug("Not messaging %s (%s): no WhatsApp opt-in/phone", customer.customer_id, template)
            return None
        msg = WhatsAppMessage(
            customer_id=customer.customer_id,
            occurrence_id=occurrence.occurrence_id if occurrence is not None else None,
            direction="OUTBOUND", message_type="template",
            payload={"template": template, "to": customer.phone_e164, "params": params,
                     "body_parameters": body, "buttons": buttons or [], "status": "not_sent",
                     "reason": "whatsapp_client_not_configured"},
        )
        session.add(msg)
        log.info("[whatsapp stub] %s -> %s %s %s", template, mask_phone(customer.phone_e164), params, buttons or "")
        return msg


def get_sender() -> MessageSender:
    return LoggingSender()
