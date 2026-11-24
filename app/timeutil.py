"""Clock helpers. Business dates use app.constants.TIMEZONE; stored timestamps use UTC."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.constants import TIMEZONE

LOCAL_TZ = ZoneInfo(TIMEZONE)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def today_local() -> dt.date:
    return dt.datetime.now(LOCAL_TZ).date()
