"""Flask app factory for the subscriptions / notifications / order-history module."""

from __future__ import annotations

import os

from flask import Flask, jsonify
from sqlalchemy import text

from app.config import CONFIGS
from app.db import db_session, init_db


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.getenv("APP_ENV", "dev")
    cfg = CONFIGS[config_name]()

    app = Flask(__name__)
    app.config.from_object(cfg)
    if not app.config.get("SECRET_KEY") and not app.config.get("TESTING"):
        raise RuntimeError("FLASK_SECRET_KEY is not set. Add it to .env (see .env.example).")

    init_db(app)

    from app.notifications import bp as notifications_bp
    from app.orders import bp as orders_bp
    from app.subscriptions import bp as subscriptions_bp

    app.register_blueprint(subscriptions_bp, url_prefix="/api/subscriptions")
    app.register_blueprint(orders_bp, url_prefix="/api/orders")
    app.register_blueprint(notifications_bp, url_prefix="/webhooks")

    @app.get("/api/health")
    def health():
        db_session.execute(text("SELECT 1"))
        return jsonify(status="ok")

    return app
