"""TEMPORARY stand-in for Sameer's app/auth.py. Delete this file (and app/auth_bridge.py)
once his lands.

Implements the interface agreed on 2026-11-24 so routes and tests can be written now:
  - token in `Authorization: Bearer <token>`, signed with FLASK_SECRET_KEY via itsdangerous,
    valid for 7 days
  - @login_required sets g.customer_id; failures return
    401 {"error": {"code": "unauthorized", "message": ...}}
  - issue_token(customer_id) for tests (needs an app context)
There is deliberately no demo-login endpoint here; that's part of Sameer's module.
"""

from __future__ import annotations

from functools import wraps

from flask import current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

TOKEN_MAX_AGE_SECONDS = 7 * 24 * 3600
_SALT = "auth-token"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALT)


def issue_token(customer_id: str) -> str:
    return _serializer().dumps({"customer_id": customer_id})


def _customer_from_request() -> tuple[str | None, str | None]:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None, "Missing bearer token"
    try:
        data = _serializer().loads(token.strip(), max_age=TOKEN_MAX_AGE_SECONDS)
    except SignatureExpired:
        return None, "Token expired"
    except BadSignature:
        return None, "Invalid token"
    customer_id = data.get("customer_id") if isinstance(data, dict) else None
    if not isinstance(customer_id, str) or not customer_id:
        return None, "Invalid token"
    return customer_id, None


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        customer_id, error = _customer_from_request()
        if error:
            return jsonify(error={"code": "unauthorized", "message": error}), 401
        g.customer_id = customer_id
        return view(*args, **kwargs)
    return wrapper


def get_current_customer_id() -> str:
    return g.customer_id
