"""Short-lived JWT access tokens shared by the FastAPI and Flask apps."""

from __future__ import annotations

import datetime as dt
import os
import secrets
from dataclasses import dataclass, field

import jwt

from database.connection import load_env

MIN_SECRET_LENGTH = 32
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 15 * 60
DEFAULT_REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_CUSTOMER_ID_LENGTH = 64


class AuthError(Exception):
    """Base class: the request is not authenticated (HTTP 401)."""

    code = "unauthorized"


class MissingTokenError(AuthError):
    def __init__(self):
        super().__init__("Missing bearer token. Send an Authorization: Bearer header.")


class InvalidTokenError(AuthError):
    def __init__(self):
        super().__init__("Invalid token.")


class ExpiredTokenError(AuthError):
    code = "token_expired"

    def __init__(self):
        super().__init__("Token expired. Refresh the session or sign in again.")


class AuthConfigError(RuntimeError):
    """Authentication configuration is missing or unsafe."""


def _bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: str | None, name: str, default: int) -> int:
    raw = (value or str(default)).strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise AuthConfigError(f"{name} must be a positive whole number")
    return int(raw)


@dataclass(frozen=True)
class AuthSettings:
    secret_key: str = field(repr=False)
    access_token_ttl_seconds: int = DEFAULT_ACCESS_TOKEN_TTL_SECONDS
    refresh_token_ttl_seconds: int = DEFAULT_REFRESH_TOKEN_TTL_SECONDS
    issuer: str = "smart-reorder"
    audience: str = "smart-reorder-client"
    refresh_cookie_secure: bool = True

    def __post_init__(self):
        if self.secret_key.lower().startswith("change-me"):
            raise AuthConfigError("AUTH_SECRET_KEY still has the .env.example placeholder.")
        if not self.secret_key or len(self.secret_key) < MIN_SECRET_LENGTH:
            raise AuthConfigError(f"AUTH_SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters.")
        if self.access_token_ttl_seconds <= 0 or self.refresh_token_ttl_seconds <= 0:
            raise AuthConfigError("Authentication token lifetimes must be positive.")
        if not self.issuer.strip() or not self.audience.strip():
            raise AuthConfigError("JWT issuer and audience must not be empty.")

    @classmethod
    def from_env(cls) -> "AuthSettings":
        load_env()
        return cls(
            secret_key=(os.getenv("AUTH_SECRET_KEY") or "").strip(),
            access_token_ttl_seconds=_positive_int(
                os.getenv("AUTH_ACCESS_TOKEN_TTL_SECONDS"), "AUTH_ACCESS_TOKEN_TTL_SECONDS",
                DEFAULT_ACCESS_TOKEN_TTL_SECONDS,
            ),
            refresh_token_ttl_seconds=_positive_int(
                os.getenv("AUTH_REFRESH_TOKEN_TTL_SECONDS"), "AUTH_REFRESH_TOKEN_TTL_SECONDS",
                DEFAULT_REFRESH_TOKEN_TTL_SECONDS,
            ),
            issuer=(os.getenv("AUTH_JWT_ISSUER") or "smart-reorder").strip(),
            audience=(os.getenv("AUTH_JWT_AUDIENCE") or "smart-reorder-client").strip(),
            refresh_cookie_secure=_bool(os.getenv("AUTH_COOKIE_SECURE"), True),
        )


class TokenService:
    """Issues and verifies access JWTs; refresh tokens are managed by AuthStore."""

    def __init__(self, settings: AuthSettings):
        self.settings = settings

    def issue(self, customer_id: str, *, now: dt.datetime | None = None) -> str:
        if not isinstance(customer_id, str) or not customer_id.strip() \
                or len(customer_id.strip()) > MAX_CUSTOMER_ID_LENGTH:
            raise ValueError("customer_id must be a non-empty string of at most 64 characters")
        issued_at = now or dt.datetime.now(dt.timezone.utc)
        if issued_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        issued_at = issued_at.astimezone(dt.timezone.utc)
        payload = {
            "sub": customer_id.strip(),
            "iat": issued_at,
            "exp": issued_at + dt.timedelta(seconds=self.settings.access_token_ttl_seconds),
            "iss": self.settings.issuer,
            "aud": self.settings.audience,
            "jti": secrets.token_urlsafe(16),
        }
        return jwt.encode(payload, self.settings.secret_key, algorithm="HS256")

    def verify(self, token: str | None) -> str:
        """Return the customer ID after validating signature, claims and expiration."""
        if not token:
            raise MissingTokenError()
        try:
            payload = jwt.decode(
                token,
                self.settings.secret_key,
                algorithms=["HS256"],
                issuer=self.settings.issuer,
                audience=self.settings.audience,
                options={"require": ["sub", "iat", "exp", "iss", "aud", "jti"]},
            )
        except jwt.ExpiredSignatureError:
            raise ExpiredTokenError() from None
        except jwt.InvalidTokenError:
            raise InvalidTokenError() from None
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub or len(sub) > MAX_CUSTOMER_ID_LENGTH:
            raise InvalidTokenError()
        return sub


def bearer_token_from_header(header_value: str | None) -> str:
    """Extract a bearer token from an Authorization header."""
    if not header_value:
        raise MissingTokenError()
    scheme, _, token = header_value.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise MissingTokenError()
    return token.strip()
