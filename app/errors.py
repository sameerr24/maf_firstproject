"""Service-layer errors and their JSON form.

Responses use the same shape as auth errors: {"error": {"code": ..., "message": ...}}.
"""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify


class ServiceError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, **self.details}}


class ValidationError(ServiceError):
    status = 400
    code = "validation_error"


class NotFound(ServiceError):
    """Also used for resources owned by another customer (never reveal they exist)."""
    status = 404
    code = "not_found"


class Conflict(ServiceError):
    status = 409
    code = "conflict"


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ServiceError)
    def _service_error(e: ServiceError):
        return jsonify(e.to_dict()), e.status
