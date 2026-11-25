"""Flask app factory for the subscriptions / notifications / order-history module."""

from __future__ import annotations

import os

from flask import Flask, jsonify
from sqlalchemy import text

from app.config import CONFIGS
from app.db import db_session, init_db
from app.errors import register_error_handlers


def create_app(config_name: str | None = None, overrides: dict | None = None) -> Flask:
    config_name = config_name or os.getenv("APP_ENV", "dev")
    cfg = CONFIGS[config_name]()

    app = Flask(__name__)
    app.config.from_object(cfg)
    app.config.update(overrides or {})
    if not app.config.get("SECRET_KEY") and not app.config.get("TESTING"):
        raise RuntimeError("FLASK_SECRET_KEY is not set. Add it to .env (see .env.example).")

    init_db(app)
    register_error_handlers(app)

    from app.notifications import bp as notifications_bp
    from app.orders import bp as orders_bp
    from app.products import bp as products_bp
    from app.subscriptions import bp as subscriptions_bp

    app.register_blueprint(products_bp, url_prefix="/api/products")
    app.register_blueprint(subscriptions_bp, url_prefix="/api/subscriptions")
    app.register_blueprint(orders_bp, url_prefix="/api/orders")
    app.register_blueprint(notifications_bp, url_prefix="/webhooks")
    if app.config.get("DEV_ENDPOINTS"):
        from app.dev import bp as dev_bp
        app.register_blueprint(dev_bp, url_prefix="/api/dev")

    from app.scheduler import init_scheduler
    init_scheduler(app)

    @app.cli.command("telegram-poll")
    def telegram_poll():
        """Run the Telegram bot (long polling): account linking and reminder replies."""
        import logging

        from app.notifications.telegram_api import TelegramClient
        from app.notifications.telegram_bot import poll_forever

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
        poll_forever(app.extensions["db_engine"], TelegramClient(os.getenv("TELEGRAM_BOT_TOKEN", "").strip()))

    @app.get("/api/health")
    def health():
        db_session.execute(text("SELECT 1"))
        return jsonify(status="ok")

    return app
