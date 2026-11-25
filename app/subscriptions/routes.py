"""Subscription endpoints (mounted at /api/subscriptions). Thin layer over service.py:
customer_id always comes from g.customer_id (set by @login_required), never the request."""

from __future__ import annotations

from flask import g, jsonify, request, url_for

from app.auth import login_required
from app.db import db_session
from app.errors import ValidationError
from app.subscriptions import bp, service


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


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
    sub = service.update_subscription(db_session, g.customer_id, subscription_id, _json_body())
    db_session.commit()
    return jsonify(subscription=sub)


@bp.delete("/<int:subscription_id>")
@login_required
def cancel_subscription(subscription_id: int):
    sub = service.cancel_subscription(db_session, g.customer_id, subscription_id)
    db_session.commit()
    return jsonify(subscription=sub)


@bp.post("/<int:subscription_id>/skip")
@login_required
def skip_next_delivery(subscription_id: int):
    result = service.skip_next_delivery(db_session, g.customer_id, subscription_id)
    db_session.commit()
    return jsonify(result)
