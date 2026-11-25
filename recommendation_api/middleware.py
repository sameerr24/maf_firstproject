"""Request logging/timing middleware and centralized error handling.

Every response carries ``X-Request-ID`` and ``X-Process-Time-Ms``. Errors always use one
JSON shape and never include stack traces, SQL or connection details:

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from customer_auth import AuthError
from database.connection import DatabaseConfigError
from recommendation_api.auth import ForbiddenError
from recommendation_engine.errors import ConfigError, CustomerNotFoundError, DataSourceError, InvalidRequestError

log = logging.getLogger("recommendation_api")

_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def error_response(request: Request, status: int, code: str, message: str, details=None) -> JSONResponse:
    body = {"code": code, "message": message, "request_id": _request_id(request)}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status, content=jsonable_encoder({"error": body}))


def register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_and_time(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _REQUEST_ID_OK.match(incoming) else uuid.uuid4().hex[:16]
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # last line of defence: never leak a traceback to the client
            log.exception("unhandled error request_id=%s %s %s", request.state.request_id,
                          request.method, request.url.path)
            response = error_response(request, 500, "internal_error", "An unexpected error occurred.")
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        log.info("%s %s -> %s in %.1f ms request_id=%s", request.method, request.url.path,
                 response.status_code, elapsed_ms, request.state.request_id)
        return response


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg", "")}
                   for e in exc.errors()]
        return error_response(request, 422, "validation_error", "Invalid request parameters.", details)

    @app.exception_handler(AuthError)
    async def unauthorized(request: Request, exc: AuthError):
        response = error_response(request, 401, exc.code, str(exc))
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(ForbiddenError)
    async def forbidden(request: Request, exc: ForbiddenError):
        return error_response(request, 403, "forbidden", str(exc))

    @app.exception_handler(InvalidRequestError)
    async def invalid_request(request: Request, exc: InvalidRequestError):
        return error_response(request, 422, "invalid_request", str(exc))

    @app.exception_handler(CustomerNotFoundError)
    async def customer_not_found(request: Request, exc: CustomerNotFoundError):
        return error_response(request, 404, "customer_not_found", str(exc))

    async def unavailable(request: Request, exc: Exception):
        cause = type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__
        log.error("data source unavailable request_id=%s (%s: %s)", _request_id(request), type(exc).__name__, cause)
        return error_response(request, 503, "service_unavailable",
                              "Recommendation data is temporarily unavailable. Please try again later.")

    for exc_type in (DataSourceError, DatabaseConfigError, ConfigError):
        app.add_exception_handler(exc_type, unavailable)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        codes = {404: "not_found", 405: "method_not_allowed"}
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return error_response(request, exc.status_code, codes.get(exc.status_code, "http_error"), message)
