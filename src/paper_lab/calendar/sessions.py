"""Exchange trading days and session windows (minimal; extended in Stage B).

Session times are defined in America/New_York and converted per date, so DST shifts are
handled by the tz database, never by hand.
"""

from __future__ import annotations

from datetime import date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd


@lru_cache(maxsize=4)
def _calendar(name: str) -> xcals.ExchangeCalendar:
    return xcals.get_calendar(name)


def trading_days(calendar_name: str, start: date, end: date) -> list[date]:
    """Exchange sessions in [start, end] (inclusive), clipped to the calendar's range."""
    cal = _calendar(calendar_name)
    s = max(pd.Timestamp(start), cal.first_session)
    e = min(pd.Timestamp(end), cal.last_session)
    if s > e:
        return []
    return [d.date() for d in cal.sessions_in_range(s, e)]


def exchange_close_utc(calendar_name: str, day: date) -> pd.Timestamp:
    """Official close of the exchange session (early closes included)."""
    return pd.Timestamp(_calendar(calendar_name).session_close(pd.Timestamp(day)))


def window_utc(day: date, start: time, end: time, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) on ``day`` in timezone ``tz``, returned in UTC."""
    zone = ZoneInfo(tz)
    s = pd.Timestamp(datetime.combine(day, start, tzinfo=zone)).tz_convert("UTC")
    e = pd.Timestamp(datetime.combine(day, end, tzinfo=zone)).tz_convert("UTC")
    return s, e
