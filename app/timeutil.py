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


def local_date(moment: dt.datetime) -> dt.date:
    """Business date of an aware datetime."""
    return moment.astimezone(LOCAL_TZ).date()


def parse_moment(value: str) -> dt.datetime:
    """ISO datetime; a value without an offset is taken as local business time."""
    moment = dt.datetime.fromisoformat(value)
    return moment.replace(tzinfo=LOCAL_TZ) if moment.tzinfo is None else moment


def fmt_day(d: dt.date) -> str:
    """Customer-facing date, e.g. "Sat, 28 Nov"."""
    return d.strftime("%a, %d %b")
