"""Admin panel API (inventory management): a separate Flask app on 127.0.0.1:ADMIN_PORT.

Local-only, with no login by design for the POC: we assume only the admin can use the machine
running it (production would add role-based admin login). It reuses the customer app's
database, error and CORS setup, but never imports app.auth, never starts the scheduler, and
the customer app (app.create_app) never registers this blueprint. See app/admin/guard.py for
the host and JSON checks that replace auth here.
"""

from __future__ import annotations

import os

from flask import Flask, jsonify
from sqlalchemy import text

from app.admin.config import ADMIN_CONFIGS
from app.admin.guard import init_guard
from app.cors import init_cors
from app.db import db_session, init_db
from app.errors import register_error_handlers


def create_admin_app(config_name: str | None = None, overrides: dict | None = None) -> Flask:
    config_name = config_name or os.getenv("APP_ENV", "dev")
    app = Flask(__name__)
    app.config.from_object(ADMIN_CONFIGS[config_name]())
    app.config.update(overrides or {})

    init_db(app)
    register_error_handlers(app)
    init_cors(app)
    init_guard(app)

    from app.admin.routes import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    if app.config.get("DEV_ENDPOINTS"):            # demo time travel next to the admin controls
        from app.dev import bp as dev_bp
        app.register_blueprint(dev_bp, url_prefix="/api/dev")

    @app.errorhandler(404)
    def _not_found(_e):
        return jsonify(error={"code": "not_found", "message": "Not found"}), 404

    @app.errorhandler(405)
    def _method_not_allowed(_e):
        return jsonify(error={"code": "method_not_allowed", "message": "Method not allowed"}), 405

    @app.get("/api/admin/health")
    def health():
        db_session.execute(text("SELECT 1"))
        return jsonify(status="ok")

    return app
