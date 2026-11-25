"""FastAPI dependencies. The service (and its database engine) is created once per app,
on first use, so importing or starting the app never needs the database."""

from __future__ import annotations

import threading

from fastapi import Request

from customer_auth.msg91 import MSG91Client, MSG91Settings
from customer_auth.service import CustomerAuthService
from customer_auth.storage import AuthStore
from database.connection import make_engine
from recommendation_api.service import RecommendationService
from recommendation_engine import RecommendationEngine, load_config

_lock = threading.Lock()


def get_recommendation_service(request: Request) -> RecommendationService:
    state = request.app.state
    service = getattr(state, "recommendation_service", None)
    if service is None:
        with _lock:
            service = getattr(state, "recommendation_service", None)
            if service is None:
                config = load_config(state.settings.scoring_config_path)
                service = RecommendationService(RecommendationEngine(config=config))
                state.recommendation_service = service
    return service


def get_customer_auth_service(request: Request) -> CustomerAuthService:
    state = request.app.state
    service = getattr(state, "customer_auth_service", None)
    if service is None:
        with _lock:
            service = getattr(state, "customer_auth_service", None)
            if service is None:
                engine = make_engine()
                state.customer_auth_engine = engine
                service = CustomerAuthService(state.settings.auth, AuthStore(engine), get_msg91_client(request))
                state.customer_auth_service = service
    return service


def get_msg91_client(request: Request) -> MSG91Client:
    state = request.app.state
    client = getattr(state, "msg91_client", None)
    if client is None:
        client = MSG91Client(MSG91Settings(
            widget_id=state.settings.msg91_widget_id,
            token_auth=state.settings.msg91_token_auth,
            authkey=state.settings.msg91_authkey,
        ))
        state.msg91_client = client
    return client
