"""Customer authentication for the Flask app, shared with the FastAPI API.

The Authorization bearer JWT is signed with ``AUTH_SECRET_KEY``. Refresh tokens stay
in the FastAPI auth cookie and are never accepted by this decorator. Usage:

    from flask import g
    from app.auth import login_required

    @bp.get("/")
    @login_required
    def list_subscriptions():
        customer_id = g.customer_id      # never take customer_id from the body or query string
        ...

Failure: HTTP 401, header ``WWW-Authenticate: Bearer``, body
``{"error": {"code": "unauthorized" | "token_expired", "message": "..."}}``.
Tokens are issued by the phone OTP login or registration endpoints on the recommendation API.
WhatsApp webhooks must NOT use this; they identify the customer by phone number and
verify Meta's signature instead.
"""

from __future__ import annotations

from functools import wraps

from flask import current_app, g, jsonify, request

from customer_auth import AuthError, AuthSettings, TokenService, bearer_token_from_header

EXTENSION_KEY = "customer_auth"


def get_token_service() -> TokenService:
    """The app's TokenService, created from the environment on first use."""
    service = current_app.extensions.get(EXTENSION_KEY)
    if service is None:
        service = TokenService(AuthSettings.from_env())
        current_app.extensions[EXTENSION_KEY] = service
    return service


def get_current_customer_id() -> str:
    """Customer ID from the request's bearer token. Raises customer_auth.AuthError."""
    return get_token_service().verify(bearer_token_from_header(request.headers.get("Authorization")))


def unauthorized_response(exc: AuthError):
    response = jsonify(error={"code": exc.code, "message": str(exc)})
    response.status_code = 401
    response.headers["WWW-Authenticate"] = "Bearer"
    return response


def login_required(view):
    """Reject requests without a valid token; otherwise set ``g.customer_id``."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            g.customer_id = get_current_customer_id()
        except AuthError as exc:
            return unauthorized_response(exc)
        return view(*args, **kwargs)
    return wrapper
