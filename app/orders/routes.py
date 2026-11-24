"""Order history endpoints (mounted at /api/orders). Read-only."""

from __future__ import annotations

from flask import g, jsonify, request

from app.auth_bridge import login_required
from app.db import db_session
from app.orders import bp, service


@bp.get("")
@login_required
def list_orders():
    return jsonify(service.list_orders(db_session, g.customer_id, limit=request.args.get("limit"),
                                       offset=request.args.get("offset"),
                                       source=request.args.get("source") or None))


@bp.get("/<order_id>")
@login_required
def get_order(order_id: str):
    return jsonify(order=service.get_order(db_session, g.customer_id, order_id))
