"""Token issue/verification and settings. Framework-independent."""

from __future__ import annotations

import os
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from database.connection import load_env

TOKEN_SALT = "smart-reorder.customer-auth.v1"  # versioned: change it to invalidate every token
MIN_SECRET_LENGTH = 32
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600
MAX_CUSTOMER_ID_LENGTH = 64


class AuthError(Exception):
    """Base class: the request is not authenticated (HTTP 401)."""
    code = "unauthorized"


class MissingTokenError(AuthError):
    def __init__(self):
        super().__init__("Missing bearer token. Send 'Authorization: Bearer <token>'.")


class InvalidTokenError(AuthError):
    def __init__(self):
        super().__init__("Invalid token.")


class ExpiredTokenError(AuthError):
    code = "token_expired"

    def __init__(self):
        super().__init__("Token expired. Log in again.")


class AuthConfigError(RuntimeError):
    """AUTH_SECRET_KEY missing or too weak (server misconfiguration, not a client error)."""


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthSettings:
    secret_key: str
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS
    demo_login_enabled: bool = False

    def __post_init__(self):
        if self.secret_key.lower().startswith("change-me"):
            raise AuthConfigError("AUTH_SECRET_KEY still has the .env.example placeholder. Generate a real one "
                                  "with: python -c \"import secrets; print(secrets.token_hex(32))\"")
        if not self.secret_key or len(self.secret_key) < MIN_SECRET_LENGTH:
            raise AuthConfigError(f"AUTH_SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters. Generate one "
                                  "with: python -c \"import secrets; print(secrets.token_hex(32))\"")
        if self.max_age_seconds <= 0:
            raise AuthConfigError("AUTH_TOKEN_MAX_AGE_SECONDS must be > 0")

    @classmethod
    def from_env(cls) -> "AuthSettings":
        """AUTH_SECRET_KEY (required), AUTH_TOKEN_MAX_AGE_SECONDS, AUTH_DEMO_LOGIN."""
        load_env()
        max_age = (os.getenv("AUTH_TOKEN_MAX_AGE_SECONDS") or str(DEFAULT_MAX_AGE_SECONDS)).strip()
        if not max_age.isdigit():
            raise AuthConfigError(f"AUTH_TOKEN_MAX_AGE_SECONDS must be a whole number (got {max_age!r})")
        return cls(secret_key=(os.getenv("AUTH_SECRET_KEY") or "").strip(), max_age_seconds=int(max_age),
                   demo_login_enabled=_bool(os.getenv("AUTH_DEMO_LOGIN")))


class TokenService:
    def __init__(self, settings: AuthSettings):
        self.settings = settings
        self._serializer = URLSafeTimedSerializer(settings.secret_key, salt=TOKEN_SALT)

    def issue(self, customer_id: str) -> str:
        if not isinstance(customer_id, str) or not customer_id.strip() \
                or len(customer_id.strip()) > MAX_CUSTOMER_ID_LENGTH:
            raise ValueError("customer_id must be a non-empty string of at most 64 characters")
        return self._serializer.dumps({"sub": customer_id.strip()})

    def verify(self, token: str | None) -> str:
        """Return the customer ID inside a valid, unexpired token."""
        if not token:
            raise MissingTokenError()
        try:
            payload = self._serializer.loads(token, max_age=self.settings.max_age_seconds)
        except SignatureExpired:
            raise ExpiredTokenError() from None
        except BadSignature:  # tampered, wrong secret/salt, or not a token at all
            raise InvalidTokenError() from None
        sub = payload.get("sub") if isinstance(payload, dict) else None
        if not isinstance(sub, str) or not sub or len(sub) > MAX_CUSTOMER_ID_LENGTH:
            raise InvalidTokenError()
        return sub


def bearer_token_from_header(header_value: str | None) -> str:
    """Extract the token from an ``Authorization`` header value; raise MissingTokenError."""
    if not header_value:
        raise MissingTokenError()
    scheme, _, token = header_value.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise MissingTokenError()
    return token.strip()
