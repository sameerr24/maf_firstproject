"""Subscription endpoints (mounted at /api/subscriptions). Thin layer over service.py:
customer_id always comes from g.customer_id (set by @login_required), never the request.

Delivery replies (/deliveries/...) are the website version of the chat reminder's buttons and
call the same service functions as the Telegram bot. Whenever a website action settles a
delivery whose reminder is in the chat, chat_sync removes that reminder's buttons and posts
the confirmation there (after the commit)."""

from __future__ import annotations

import datetime as dt
import html

from flask import g, jsonify, request, url_for
from sqlalchemy import select

from app.auth import login_required
from app.db import db_session
from app.errors import ValidationError
from app.models import DeliveryOccurrence, Subscription
from app.notifications import chat_sync
from app.notifications import telegram_bot as bot
from app.subscriptions import bp, service
from app.timeutil import fmt_day


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


def _notified(subscription_id: int) -> list[int]:
    """This customer's deliveries of a subscription that are waiting for a chat reply."""
    return list(db_session.scalars(select(DeliveryOccurrence.occurrence_id).join(Subscription).where(
        Subscription.subscription_id == subscription_id, Subscription.customer_id == g.customer_id,
        DeliveryOccurrence.status == "NOTIFIED")))


def _settled_reminders(occurrence_ids: list[int]) -> chat_sync.ChatUpdate:
    """Before commit: the chat reminders of those deliveries that are no longer waiting."""
    if not occurrence_ids:
        return chat_sync.ChatUpdate()
    still = set(db_session.scalars(select(DeliveryOccurrence.occurrence_id).where(
        DeliveryOccurrence.occurrence_id.in_(occurrence_ids), DeliveryOccurrence.status == "NOTIFIED")))
    return chat_sync.prepare(db_session, g.customer_id, [i for i in occurrence_ids if i not in still])


@bp.get("")
@login_required
def list_subscriptions():
    include_cancelled = request.args.get("include_cancelled", "").lower() in ("1", "true", "yes")
    return jsonify(subscriptions=service.list_subscriptions(db_session, g.customer_id, include_cancelled))


@bp.get("/draft")
@login_required
def draft():
    product_id = request.args.get("product_id", "").strip()
    if not product_id:
        raise ValidationError("product_id is required", details={"field": "product_id"})
    rec_id = request.args.get("rec_id") or None
    return jsonify(draft=service.subscription_draft(db_session, g.customer_id, product_id, rec_id))


@bp.post("")
@login_required
def create_subscription():
    sub = service.create_subscription(db_session, g.customer_id, _json_body())
    db_session.commit()
    location = url_for("subscriptions.get_subscription", subscription_id=sub["subscription_id"])
    return jsonify(subscription=sub), 201, {"Location": location}


@bp.get("/<int:subscription_id>")
@login_required
def get_subscription(subscription_id: int):
    return jsonify(subscription=service.get_subscription(db_session, g.customer_id, subscription_id))


@bp.patch("/<int:subscription_id>")
@login_required
def update_subscription(subscription_id: int):
    waiting = _notified(subscription_id)
    sub = service.update_subscription(db_session, g.customer_id, subscription_id, _json_body())
    chat = _settled_reminders(waiting)                  # e.g. pausing cancels a pending reminder
    db_session.commit()
    chat_sync.send(chat)
    return jsonify(subscription=sub)


@bp.delete("/<int:subscription_id>")
@login_required
def cancel_subscription(subscription_id: int):
    waiting = _notified(subscription_id)
    sub = service.cancel_subscription(db_session, g.customer_id, subscription_id)
    chat = _settled_reminders(waiting)
    db_session.commit()
    if chat.reminders:                                  # only worth a message if a reminder was open
        chat_sync.send(chat, bot.stopped_text(html.escape(sub["product"]["product_name"], quote=False)))
    return jsonify(subscription=sub)


@bp.post("/<int:subscription_id>/skip")
@login_required
def skip_next_delivery(subscription_id: int):
    waiting = _notified(subscription_id)
    result = service.skip_next_delivery(db_session, g.customer_id, subscription_id)
    chat = _settled_reminders(waiting)
    db_session.commit()
    if chat.reminders:
        product = html.escape(result["subscription"]["product"]["product_name"], quote=False)
        chat_sync.send(chat, bot.skipped_text(product, fmt_day(dt.date.fromisoformat(result["skipped_date"])),
                                              result["subscription"]))
    return jsonify(result)


# --------------------------------------------------------------------------- #
# Replies to delivery reminders (the website's version of the chat buttons)
# --------------------------------------------------------------------------- #
@bp.get("/deliveries/pending")
@login_required
def pending_deliveries():
    return jsonify(deliveries=service.pending_deliveries(db_session, g.customer_id))


def _reply(occurrence_id: int, action):
    """Run one reply in the request's transaction, then sync the chat after the commit."""
    sub, occ = service.get_owned_delivery(db_session, g.customer_id, occurrence_id)
    product = html.escape(sub.product.product_name, quote=False)
    when = fmt_day(service.delivery_date(occ))
    chat = chat_sync.prepare(db_session, g.customer_id, [occurrence_id])
    result, text = action(product, when)
    db_session.commit()
    chat_sync.send(chat, text)
    return jsonify(result)


@bp.post("/deliveries/<int:occurrence_id>/approve")
@login_required
def approve_delivery(occurrence_id: int):
    slot = _json_body().get("slot")

    def act(product, when):
        r = service.approve_delivery(db_session, g.customer_id, occurrence_id, slot)
        return r, bot.approved_text(product, when, r)
    return _reply(occurrence_id, act)


@bp.post("/deliveries/<int:occurrence_id>/skip")
@login_required
def skip_delivery(occurrence_id: int):
    def act(product, when):
        r = service.skip_delivery(db_session, g.customer_id, occurrence_id)
        return r, bot.skipped_text(product, when, r)
    return _reply(occurrence_id, act)


@bp.post("/deliveries/<int:occurrence_id>/delay")
@login_required
def delay_delivery(occurrence_id: int):
    days = _json_body().get("days")
    if not isinstance(days, int) or isinstance(days, bool):
        raise ValidationError("days must be a whole number", details={"field": "days"})

    def act(product, when):
        occ = service.delay_delivery(db_session, g.customer_id, occurrence_id, days)
        new = service.delivery_date(occ)
        return {"delivery_date": new.isoformat(), "delayed_days": occ.delay_days}, bot.delayed_text(product, new)
    return _reply(occurrence_id, act)
