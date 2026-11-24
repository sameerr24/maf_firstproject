"""Flask configuration, read from environment variables (and the project-root .env)."""

from __future__ import annotations

import os

from database.connection import get_database_url, load_env


class Config:
    TESTING = False
    # Lead time / scheduler settings live here later.

    def __init__(self) -> None:
        load_env()  # real environment variables win over .env
        self.SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip() or None
        # Validated by Sameer's helper: must be set, PostgreSQL, and name a database.
        self.DATABASE_URL = get_database_url("DATABASE_URL")


class DevConfig(Config):
    DEBUG = True


class TestConfig(Config):
    TESTING = True

    def __init__(self) -> None:
        load_env()
        self.SECRET_KEY = "test"
        self.DATABASE_URL = get_database_url("TEST_DATABASE_URL")


CONFIGS = {"dev": DevConfig, "test": TestConfig, "prod": Config}
