"""Admin endpoints, mounted at /api/admin (inventory, at-risk deliveries, subscriptions)."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from app.admin import service as svc
from app.db import db_session

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
