"""Background scheduler (APScheduler) running the subscription jobs inside the Flask process.

Enabled with SCHEDULER_ENABLED=1. Safe with several processes (e.g. the Flask reloader):
run_scheduler() takes a PostgreSQL advisory lock, so overlapping runs just skip.
"""

from __future__ import annotations

import atexit
import logging

from flask import Flask

log = logging.getLogger(__name__)


def init_scheduler(app: Flask):
    if not app.config.get("SCHEDULER_ENABLED"):
        return None
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.subscriptions.jobs import run_scheduler
    from app.timeutil import now_utc

    engine = app.extensions["db_engine"]
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(lambda: run_scheduler(engine), "interval", id="subscriptions",
                      minutes=app.config["SCHEDULER_INTERVAL_MINUTES"], next_run_time=now_utc(),
                      max_instances=1, coalesce=True)
    scheduler.start()

    def _shutdown() -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)

    atexit.register(_shutdown)
    app.extensions["scheduler"] = scheduler
    log.info("Scheduler started: every %s min", app.config["SCHEDULER_INTERVAL_MINUTES"])
    return scheduler
