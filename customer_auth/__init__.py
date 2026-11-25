"""Customer authentication shared by the FastAPI and Flask apps (no web framework inside).

Signed, expiring bearer tokens identify a customer:

    Authorization: Bearer <token>

Tokens are signed (HMAC-SHA1 via itsdangerous, already installed with Flask) with
``AUTH_SECRET_KEY``, so both apps accept the same token. A token contains only the
customer ID and its issue time; it is not encrypted, so never put secrets in it.

    from customer_auth import TokenService, AuthSettings
    tokens = TokenService(AuthSettings.from_env())
    token = tokens.issue("CUST-0001")
    tokens.verify(token)            # -> "CUST-0001", or raises InvalidTokenError / ExpiredTokenError

Framework adapters: ``recommendation_api/auth.py`` (FastAPI dependency) and
``app/auth.py`` (Flask decorator).
"""

from customer_auth.tokens import (  # noqa: F401
    AuthConfigError, AuthError, AuthSettings, ExpiredTokenError, InvalidTokenError, MissingTokenError,
    TokenService, bearer_token_from_header,
)
