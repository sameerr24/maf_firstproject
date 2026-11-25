"""Flask configuration, read from environment variables (and the project-root .env)."""

from __future__ import annotations

import os

from database.connection import get_database_url, load_env


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3000"   # same defaults as the FastAPI app


def _origins() -> tuple[str, ...]:
    raw = os.getenv("FLASK_CORS_ORIGINS") or DEFAULT_CORS_ORIGINS
    return tuple(o.strip().rstrip("/") for o in raw.split(",") if o.strip())


class Config:
    TESTING = False

    def __init__(self) -> None:
        load_env()  # real environment variables win over .env
        self.SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip() or None
        # Validated by Sameer's helper: must be set, PostgreSQL, and name a database.
        self.DATABASE_URL = get_database_url("DATABASE_URL")
        # Background scheduler (APScheduler) inside the Flask process. Off unless enabled.
        self.SCHEDULER_ENABLED = _flag("SCHEDULER_ENABLED")
        self.SCHEDULER_INTERVAL_MINUTES = int(os.getenv("SCHEDULER_INTERVAL_MINUTES") or 5)
        # Dev-only endpoints such as POST /api/dev/run-scheduler (demo "time travel").
        self.DEV_ENDPOINTS = _flag("DEV_ENDPOINTS")
        # Browser origins allowed to call the API (the React frontend).
        self.CORS_ORIGINS = _origins()


class DevConfig(Config):
    DEBUG = True


class TestConfig(Config):
    TESTING = True

    def __init__(self) -> None:
        load_env()
        self.SECRET_KEY = "test"
        self.DATABASE_URL = get_database_url("TEST_DATABASE_URL")
        self.SCHEDULER_ENABLED = False
        self.SCHEDULER_INTERVAL_MINUTES = 5
        self.DEV_ENDPOINTS = False
        self.CORS_ORIGINS = ("http://localhost:5173",)


CONFIGS = {"dev": DevConfig, "test": TestConfig, "prod": Config}
