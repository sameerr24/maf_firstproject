"""Request checks for the admin app, which has no login (localhost-only by design; production
would add role-based admin login). Both run before every request:

1. Host must be localhost:<ADMIN_PORT> or 127.0.0.1:<ADMIN_PORT>, else 403. A page on another
   site can't reach us through DNS rebinding, because its requests carry its own host name.
2. POST / PUT / PATCH / DELETE must have a JSON body (Content-Type: application/json), else 415.
   Plain HTML forms can't send JSON, and a cross-site JSON request needs a CORS preflight,
   which only the admin frontend's origins pass.
"""

from __future__ import annotations

from flask import Flask, request

from app.errors import ServiceError

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class Forbidden(ServiceError):
    status = 403
    code = "forbidden"


class UnsupportedMediaType(ServiceError):
    status = 415
    code = "unsupported_media_type"


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset({f"localhost:{port}", f"127.0.0.1:{port}"})


def init_guard(app: Flask) -> None:
    hosts = allowed_hosts(app.config["ADMIN_PORT"])

    @app.before_request
    def _admin_guard():
        if request.host.lower() not in hosts:
            raise Forbidden("The admin panel only answers on this computer (localhost)")
        if request.method in WRITE_METHODS and not request.is_json:
            raise UnsupportedMediaType("Send a JSON body (Content-Type: application/json)")
