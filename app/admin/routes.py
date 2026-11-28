"""Admin endpoints, mounted at /api/admin (inventory, at-risk deliveries, subscriptions)."""

from __future__ import annotations

import html

from flask import Blueprint, current_app, jsonify, request

from app.admin import service as svc
from app.db import db_session
from app.notifications import chat_sync
from app.subscriptions import cancellation

bp = Blueprint("admin", __name__)


def _body():
    return request.get_json(silent=True)


@bp.post("/inventory/<product_id>/restock")
def restock(product_id: str):
    """{"quantity": 1..10000, "note": str?}. Commits, then releases held deliveries the new
    stock covers (their reminders go out)."""
    result = svc.restock(db_session, product_id, _body())
    db_session.commit()
    result["holds"] = svc.release_after_restock(current_app.extensions["db_engine"], product_id)
    return jsonify(result)


@bp.post("/inventory/<product_id>/adjust")
def adjust(product_id: str):
    """{"available_qty": int >= 0, "reason": str} sets an absolute count (corrections)."""
    result = svc.adjust(db_session, product_id, _body())
    db_session.commit()
    if result["delta"] > 0:
        result["holds"] = svc.release_after_restock(current_app.extensions["db_engine"], product_id)
    return jsonify(result)


def _after_delivery_change(result: dict) -> dict:
    """A reminder awaiting a reply promised stock; once it's delayed or skipped that stock is
    free again, so held deliveries of the product may now be covered."""
    if result["previous_status"] == "NOTIFIED":
        result["holds"] = svc.release_after_restock(current_app.extensions["db_engine"], result["product_id"])
    return result


@bp.post("/subscriptions/<int:subscription_id>/delay")
def delay_delivery(subscription_id: int):
    """{"days": 1|2}: delay the open (or upcoming) delivery for operational reasons; the customer
    is told on Telegram and must approve again before the new date."""
    result = svc.delay_delivery(db_session, subscription_id, _body())
    db_session.commit()
    return jsonify(_after_delivery_change(result))


@bp.post("/subscriptions/<int:subscription_id>/skip")
def skip_delivery(subscription_id: int):
    """{}: skip the open (or upcoming) delivery for operational reasons; next cycle; customer told."""
    result = svc.skip_delivery(db_session, subscription_id, _body())
    db_session.commit()
    return jsonify(_after_delivery_change(result))


@bp.post("/subscriptions/<int:subscription_id>/cancel-request")
def request_cancellation(subscription_id: int):
    """{}: ask the customer to confirm cancelling (Telegram buttons + dashboard banner). Open
    deliveries not yet approved are cancelled; their chat reminders lose their buttons."""
    result = svc.request_cancellation(db_session, subscription_id, _body())
    reminders = [c["occurrence_id"] for c in result["cancelled_deliveries"] if c["status"] == "NOTIFIED"]
    chat = chat_sync.prepare(db_session, result["customer_id"], reminders)
    db_session.commit()
    chat_sync.send(chat)                                   # just removes the stale reminder buttons
    if reminders:                                          # their promised stock is free again
        result["holds"] = svc.release_after_restock(current_app.extensions["db_engine"], result["product_id"])
    return jsonify(result)


@bp.post("/subscriptions/<int:subscription_id>/cancel-request/withdraw")
def withdraw_cancellation(subscription_id: int):
    """{}: the subscription carries on; the chat's Yes / Keep buttons go and the customer is told."""
    result = svc.withdraw_cancellation(db_session, subscription_id, _body())
    chat = chat_sync.prepare_cancel_request(db_session, result["customer_id"], result["request"]["request_id"])
    db_session.commit()
    chat_sync.send(chat, cancellation.withdrawn_text(html.escape(result["product_name"], quote=False)), footer=None)
    return jsonify(result)


@bp.get("/inventory/<product_id>/movements")
def movements(product_id: str):
    return jsonify(svc.movements(db_session, product_id, request.args.get("limit")))


@bp.get("/at-risk")
def at_risk():
    """Deliveries held for stock, grouped by product, most urgent first; total for the nav badge."""
    return jsonify(svc.at_risk(db_session))


@bp.get("/inventory")
def inventory():
    """?q= (name, brand, id) &status=short|low|ok. Short first."""
    return jsonify(svc.inventory(db_session, request.args.get("q"), request.args.get("status")))


@bp.get("/subscriptions")
def subscriptions():
    """?q=&product_id=&customer_id=&status=ACTIVE|PAUSED&page=1&page_size=25 (read-only)."""
    return jsonify(svc.search_subscriptions(db_session, request.args))
