"""FastAPI dependencies. The service (and its database engine) is created once per app,
on first use, so importing or starting the app never needs the database."""

from __future__ import annotations

import threading

from fastapi import Request

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

