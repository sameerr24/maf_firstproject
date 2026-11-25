"""CORS for the React frontend (Vite dev server on :5173), which calls Flask from the browser.

Only origins in CORS_ORIGINS (env FLASK_CORS_ORIGINS, comma-separated) are allowed. Tokens
travel in the Authorization header, not cookies, so credentials mode stays off (same policy
as the FastAPI recommendation API). Preflight (OPTIONS) requests are answered by Flask's
automatic OPTIONS handling, before @login_required runs.
"""

from __future__ import annotations

from flask import Flask, request

ALLOWED_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"
ALLOWED_HEADERS = "Authorization, Content-Type"
EXPOSED_HEADERS = "Location"
PREFLIGHT_MAX_AGE = "600"


def init_cors(app: Flask) -> None:
    allowed = set(app.config.get("CORS_ORIGINS") or ())

    @app.after_request
    def _add_cors_headers(response):
        origin = request.headers.get("Origin")
        response.vary.add("Origin")
        if origin and origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Expose-Headers"] = EXPOSED_HEADERS
            if request.method == "OPTIONS":
                response.headers["Access-Control-Allow-Methods"] = ALLOWED_METHODS
                response.headers["Access-Control-Allow-Headers"] = ALLOWED_HEADERS
                response.headers["Access-Control-Max-Age"] = PREFLIGHT_MAX_AGE
        return response
