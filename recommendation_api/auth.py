"""FastAPI adapter for ``customer_auth``: bearer-token dependency and ownership check."""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from customer_auth import MissingTokenError, TokenService

# auto_error=False: we raise our own errors so every 401 has the standard JSON shape.
bearer_scheme = HTTPBearer(auto_error=False, description="Token from POST /api/v1/auth/demo-login")


class ForbiddenError(Exception):
    """Authenticated, but not allowed to see this resource (HTTP 403)."""


def get_token_service(request: Request) -> TokenService:
    return request.app.state.token_service


def current_customer_id(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
                        tokens: TokenService = Depends(get_token_service)) -> str:
    """The customer ID of the caller. Raises AuthError (401) without a valid token."""
    if credentials is None or not credentials.credentials:
        raise MissingTokenError()
    return tokens.verify(credentials.credentials)


def require_same_customer(customer_id: str, caller_id: str) -> None:
    """Customers may only read their own data."""
    if customer_id.strip() != caller_id:
        raise ForbiddenError("You can only view your own recommendations.")
