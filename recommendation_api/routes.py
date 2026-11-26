"""HTTP routes. Thin: validate parameters, check the caller, call the service."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Cookie, Depends, Path, Query, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from customer_auth.msg91 import MSG91Client, MSG91Unavailable
from customer_auth.service import (
    CustomerAuthService, InvalidRefreshSession, RateLimitExceeded, RefreshSessionReuse,
)
from recommendation_api.auth import current_customer_id, require_same_customer
from recommendation_api.dependencies import get_customer_auth_service, get_msg91_client, get_recommendation_service
from recommendation_api.schemas import (
    AuthResponse, CurrentCustomer, ErrorResponse, HealthResponse, PhoneAuthRequest, RecommendationResponse,
    RegistrationRequest, WidgetConfig,
)
from recommendation_api.service import RecommendationService
from recommendation_engine.engine import MAX_CUSTOMER_ID_LENGTH
from recommendation_engine.errors import InvalidRequestError

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
REFRESH_COOKIE = "smart_reorder_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_cookie_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in request.app.state.settings.cors_origins:
        raise StarletteHTTPException(status_code=403, detail="Request origin is not allowed.")


def _auth_response(session, response: Response, service: CustomerAuthService) -> AuthResponse:
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=service.settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=service.settings.refresh_cookie_secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )
    return AuthResponse(
        access_token=session.access_token,
        expires_in=service.settings.access_token_ttl_seconds,
        customer_id=session.customer_id,
        customer_name=service.store.find_customer_name(session.customer_id),
        created=session.created,
    )


@router.get("/auth/widget-config", response_model=WidgetConfig, tags=["auth"])
def get_widget_config(msg91: MSG91Client = Depends(get_msg91_client)) -> WidgetConfig:
    """Public SDK config. The widget-scoped token is intentionally browser-visible."""
    try:
        return WidgetConfig(**msg91.widget_config())
    except MSG91Unavailable as exc:
        raise StarletteHTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/auth/login", response_model=AuthResponse, tags=["auth"],
             responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def phone_login(body: PhoneAuthRequest, request: Request, response: Response,
                service: CustomerAuthService = Depends(get_customer_auth_service)) -> AuthResponse:
    try:
        session = service.login(
            msg91_access_token=body.msg91_access_token,
            phone_number=body.phone_number,
            region=body.region,
            client_address=_client_address(request),
        )
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return _auth_response(session, response, service)


@router.post("/auth/register", response_model=AuthResponse, tags=["auth"],
             responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                        429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def phone_register(body: RegistrationRequest, request: Request, response: Response,
                   service: CustomerAuthService = Depends(get_customer_auth_service)) -> AuthResponse:
    try:
        session = service.register(
            msg91_access_token=body.msg91_access_token,
            phone_number=body.phone_number,
            region=body.region,
            customer_name=body.customer_name,
            household_size=body.household_size,
            preferred_weekday=body.preferred_weekday,
            client_address=_client_address(request),
        )
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc
    if session.created:
        response.status_code = 201
    return _auth_response(session, response, service)


@router.post("/auth/refresh", response_model=AuthResponse, tags=["auth"],
             responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}})
def refresh_session(request: Request, response: Response,
                    service: CustomerAuthService = Depends(get_customer_auth_service),
                    refresh_token: str | None = Cookie(None, alias=REFRESH_COOKIE)) -> AuthResponse:
    _check_cookie_origin(request)
    if not refresh_token:
        raise InvalidRefreshSession()
    try:
        session = service.refresh(refresh_token, client_address=_client_address(request))
    except (InvalidRefreshSession, RefreshSessionReuse):
        response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH,
                               secure=service.settings.refresh_cookie_secure, httponly=True, samesite="strict")
        raise
    return _auth_response(session, response, service)


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request, response: Response,
           service: CustomerAuthService = Depends(get_customer_auth_service),
           refresh_token: str | None = Cookie(None, alias=REFRESH_COOKIE)) -> None:
    _check_cookie_origin(request)
    service.logout(refresh_token)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH,
                           secure=service.settings.refresh_cookie_secure, httponly=True, samesite="strict")
    response.status_code = 204


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
