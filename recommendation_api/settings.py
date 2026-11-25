"""FastAPI settings from environment variables (and the project-root .env).

Database credentials are NOT read here: the recommendation engine's repository uses
``database.connection`` (DATABASE_URL), the same as the rest of the project.

    REC_API_CORS_ORIGINS   comma-separated origins allowed to call the API
                           (default: the React dev servers http://localhost:5173,http://localhost:3000)
    REC_API_LOG_LEVEL      DEBUG | INFO | WARNING | ERROR (default INFO)
    REC_API_SCORING_CONFIG path to an alternative scoring_config.json (default: the bundled one)
    REC_API_DOCS           true/false: serve /docs and /openapi.json (default true)
    REC_API_HOST / REC_API_PORT  used by `python -m recommendation_api` (default 127.0.0.1:8000)

Authentication (shared with the Flask app, see customer_auth):
    AUTH_SECRET_KEY            required, >= 32 characters; the app refuses to start without it
    AUTH_TOKEN_MAX_AGE_SECONDS token lifetime (default 7 days)
    AUTH_DEMO_LOGIN            true to enable POST /api/v1/auth/demo-login (default false)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from customer_auth import AuthSettings
from database.connection import load_env

DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://localhost:3000")


def _bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ApiSettings:
    auth: AuthSettings | None = None  # required by create_app(); there is no unauthenticated mode
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    log_level: str = "INFO"
    scoring_config_path: str | None = None
    docs_enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "ApiSettings":
        load_env()  # real environment variables win over .env
        origins = os.getenv("REC_API_CORS_ORIGINS")
        cors = tuple(o.strip() for o in origins.split(",") if o.strip()) if origins else DEFAULT_CORS_ORIGINS
        level = os.getenv("REC_API_LOG_LEVEL", "INFO").strip().upper() or "INFO"
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"REC_API_LOG_LEVEL must be a logging level name (got {level!r})")
        port = os.getenv("REC_API_PORT", "8000").strip()
        if not port.isdigit():
            raise ValueError(f"REC_API_PORT must be a number (got {port!r})")
        return cls(auth=AuthSettings.from_env(), cors_origins=cors, log_level=level,
                   scoring_config_path=(os.getenv("REC_API_SCORING_CONFIG") or "").strip() or None,
                   docs_enabled=_bool(os.getenv("REC_API_DOCS"), True),
                   host=os.getenv("REC_API_HOST", "127.0.0.1").strip() or "127.0.0.1", port=int(port))
