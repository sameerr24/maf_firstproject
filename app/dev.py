"""Dev/demo-only endpoints (mounted at /api/dev, only when DEV_ENDPOINTS=1)."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from app.errors import ValidationError
from app.subscriptions.jobs import run_scheduler
from app.timeutil import parse_moment

bp = Blueprint("dev", __name__)


@bp.post("/run-scheduler")
def run_scheduler_now():
    """Run the scheduler jobs once. ?now=<ISO datetime> pretends it is that moment ("time
    travel" for the demo); without an offset it's taken as Asia/Kolkata time."""
    raw = request.args.get("now")
    now = None
    if raw:
        try:
            now = parse_moment(raw)
        except ValueError:
            raise ValidationError("now must be an ISO datetime, e.g. 2026-11-27T09:00",
                                  details={"field": "now"}) from None
    return jsonify(run_scheduler(current_app.extensions["db_engine"], now=now))
