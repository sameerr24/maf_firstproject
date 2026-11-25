"""Product detail page data (mounted at /api/products)."""

from flask import Blueprint, g, jsonify

from app.auth import login_required
from app.db import db_session
from app.subscriptions.service import product_offer

bp = Blueprint("products", __name__)


@bp.get("/<product_id>")
@login_required
def get_product(product_id: str):
    return jsonify(product=product_offer(db_session, product_id, customer_id=g.customer_id))
