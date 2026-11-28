"""Configuration for the admin app: the customer app's Config with two changes. The scheduler
never runs here (only in the customer app), and CORS allows the admin frontend instead of the
customer one."""

from __future__ import annotations

import os

from app.config import Config, TestConfig

DEFAULT_ADMIN_PORT = 5002
# Both spellings of the admin Vite dev server: browsers treat localhost and 127.0.0.1 as
# different origins, and Vite binds 127.0.0.1.
DEFAULT_ADMIN_CORS_ORIGINS = "http://localhost:5174,http://127.0.0.1:5174"


def _admin_settings(cfg: Config) -> None:
    cfg.SCHEDULER_ENABLED = False
    cfg.ADMIN_PORT = int(os.getenv("ADMIN_PORT") or DEFAULT_ADMIN_PORT)
    raw = os.getenv("ADMIN_CORS_ORIGINS") or DEFAULT_ADMIN_CORS_ORIGINS
    cfg.CORS_ORIGINS = tuple(o.strip().rstrip("/") for o in raw.split(",") if o.strip())


class AdminConfig(Config):
    def __init__(self) -> None:
        super().__init__()
        _admin_settings(self)


class AdminDevConfig(AdminConfig):
    DEBUG = True


class AdminTestConfig(TestConfig):
    def __init__(self) -> None:
        super().__init__()
        _admin_settings(self)
        self.ADMIN_PORT = DEFAULT_ADMIN_PORT
        self.CORS_ORIGINS = tuple(DEFAULT_ADMIN_CORS_ORIGINS.split(","))


ADMIN_CONFIGS = {"dev": AdminDevConfig, "test": AdminTestConfig, "prod": AdminConfig}
