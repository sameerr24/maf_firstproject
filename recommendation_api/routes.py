"""HTTP routes. Thin: validate parameters, check the caller, call the service."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Path, Query, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from customer_auth import TokenService
from recommendation_api.auth import current_customer_id, get_token_service, require_same_customer
from recommendation_api.dependencies import get_recommendation_service
from recommendation_api.schemas import (
    CurrentCustomer, DemoLoginRequest, ErrorResponse, HealthResponse, RecommendationResponse, TokenResponse,
)
from recommendation_api.service import RecommendationService
from recommendation_engine.engine import MAX_CUSTOMER_ID_LENGTH
from recommendation_engine.errors import CustomerNotFoundError

router = APIRouter(prefix="/api/v1")

AUTH_ERRORS = {401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"}}
ERRORS = {**AUTH_ERRORS,
          403: {"model": ErrorResponse, "description": "Token belongs to another customer"},
          404: {"model": ErrorResponse, "description": "Customer not found"},
          422: {"model": ErrorResponse, "description": "Invalid parameters"},
          503: {"model": ErrorResponse, "description": "Database unavailable"}}

LimitQuery = Query(None, ge=1, description="Number of recommendations (default 10, max 50; set in scoring_config.json)")
AsOfQuery = Query(None, description="Evaluate as of this date (YYYY-MM-DD). Default: the configured reference date")


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Liveness check. No authentication, no database."""
    return HealthResponse(status="ok", service="recommendation-api")


# ------------------------------------------------------------------ auth
def _require_demo(tokens: TokenService) -> None:
    if not tokens.settings.demo_login_enabled:
        raise StarletteHTTPException(status_code=404, detail="Not Found")  # behave as if the route doesn't exist


@router.post("/auth/demo-login", response_model=TokenResponse, tags=["auth"],
             responses={404: {"model": ErrorResponse, "description": "Unknown customer, or demo login disabled"}})
def demo_login(body: DemoLoginRequest, request: Request,
               tokens: TokenService = Depends(get_token_service),
               service: RecommendationService = Depends(get_recommendation_service)) -> TokenResponse:
    """Demo only (AUTH_DEMO_LOGIN=true): issue a token for an existing customer, without a password."""
    _require_demo(tokens)
    customer_id = body.customer_id.strip()
    if not service.customer_exists(customer_id):
        raise CustomerNotFoundError(customer_id)
    return TokenResponse(access_token=tokens.issue(customer_id), token_type="bearer",
                         expires_in=tokens.settings.max_age_seconds, customer_id=customer_id)


@router.get("/auth/me", response_model=CurrentCustomer, responses=AUTH_ERRORS, tags=["auth"])
def who_am_i(caller_id: str = Depends(current_customer_id)) -> CurrentCustomer:
    return CurrentCustomer(customer_id=caller_id)


# ------------------------------------------------------------------ recommendations
@router.get("/recommendations/me", response_model=RecommendationResponse, responses=ERRORS,
            tags=["recommendations"])
def get_my_recommendations(limit: int | None = LimitQuery, as_of: dt.date | None = AsOfQuery,
                           caller_id: str = Depends(current_customer_id),
                           service: RecommendationService = Depends(get_recommendation_service)):
    """Recommendations for the logged-in customer (preferred by the frontend)."""
    return service.get_recommendations(caller_id, limit=limit, as_of=as_of)


@router.get("/recommendations/{customer_id}", response_model=RecommendationResponse, responses=ERRORS,
            tags=["recommendations"])
def get_recommendations(
    customer_id: str = Path(min_length=1, max_length=MAX_CUSTOMER_ID_LENGTH, description="e.g. CUST-0001"),
    limit: int | None = LimitQuery,
    as_of: dt.date | None = AsOfQuery,
    caller_id: str = Depends(current_customer_id),
    service: RecommendationService = Depends(get_recommendation_service),
) -> RecommendationResponse:
    """Top-N replenishment recommendations, highest final score first. Only for the caller's own ID."""
    require_same_customer(customer_id, caller_id)
    return service.get_recommendations(customer_id, limit=limit, as_of=as_of)
