"""FastAPI app factory for the recommendation engine (the entry point is main.py).

Runs as its own process, separate from the Flask app (``run.py``, port 5000):

    uvicorn recommendation_api.main:app --reload --port 8000
    # or
    python -m recommendation_api

Creating the app does not connect to the database; the first recommendation request does.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from customer_auth import AuthConfigError, TokenService
from recommendation_api.middleware import register_exception_handlers, register_middleware
from recommendation_api.routes import router
from recommendation_api.service import RecommendationService
from recommendation_api.settings import ApiSettings


def create_app(settings: ApiSettings | None = None, service: RecommendationService | None = None) -> FastAPI:
    """Build the app. ``service`` can be injected (tests, alternative repositories)."""
    settings = settings or ApiSettings.from_env()
    if settings.auth is None:
        raise AuthConfigError("ApiSettings.auth is required: set AUTH_SECRET_KEY (see .env.example).")
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("recommendation_api").setLevel(settings.log_level)

    app = FastAPI(
        title="Smart Reorder: Recommendation API",
        version="1.0.0",
        description="Personalized replenishment recommendations. Business logic lives in `recommendation_engine`.",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.recommendation_service = service
    app.state.token_service = TokenService(settings.auth)

    register_middleware(app)
    # Tokens travel in the Authorization header (not cookies), so credentials mode stays off.
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type",
                                                                                "X-Request-ID"],
                       expose_headers=["X-Request-ID", "X-Process-Time-Ms"])
    register_exception_handlers(app)
    app.include_router(router)
    return app


