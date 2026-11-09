"""Exchange trading days and the analysis session.

Session times are defined in America/New_York and converted per date, so daylight-saving
changes are handled by the tz database, never by hand. The US and EU switch clocks on
different dates, so the 09:30 ET open is 15:30 in Copenhagen most of the year but 14:30
during the few weeks in March and October/November when only one side has switched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from paper_lab.config import SessionConfig

# The library's default range ends one year after *today*, which would make results depend on
# the run date. Fix it explicitly; holidays come from the library's rules for every year.
CALENDAR_START = "2000-01-03"
CALENDAR_END = "2030-12-31"


@lru_cache(maxsize=4)
def _calendar(name: str) -> xcals.ExchangeCalendar:
    return xcals.get_calendar(name, start=CALENDAR_START, end=CALENDAR_END)


def _check_in_range(calendar_name: str, day: date) -> None:
    cal = _calendar(calendar_name)
    ts = pd.Timestamp(day)
    if not cal.first_session <= ts <= cal.last_session:
        raise ValueError(
            f"{day} is outside the {calendar_name} calendar range "
            f"{cal.first_session.date()}..{cal.last_session.date()}"
        )


def trading_days(calendar_name: str, start: date, end: date) -> list[date]:
    """Exchange sessions in [start, end] (inclusive), clipped to the calendar's range."""
    cal = _calendar(calendar_name)
    s = max(pd.Timestamp(start), cal.first_session)
    e = min(pd.Timestamp(end), cal.last_session)
    if s > e:
        return []
    return [d.date() for d in cal.sessions_in_range(s, e)]


def is_trading_day(calendar_name: str, day: date) -> bool:
    _check_in_range(calendar_name, day)
    return bool(_calendar(calendar_name).is_session(pd.Timestamp(day)))


def exchange_close_utc(calendar_name: str, day: date) -> pd.Timestamp:
    """Official close of the exchange session (early closes included)."""
    return pd.Timestamp(_calendar(calendar_name).session_close(pd.Timestamp(day)))


def holidays(calendar_name: str, year: int) -> list[date]:
    """Weekdays in ``year`` on which the exchange is closed."""
    days = pd.bdate_range(f"{year}-01-01", f"{year}-12-31")
    sessions = set(trading_days(calendar_name, date(year, 1, 1), date(year, 12, 31)))
    return [d.date() for d in days if d.date() not in sessions]


def early_closes(calendar_name: str, year: int) -> dict[date, pd.Timestamp]:
    """Days in ``year`` with an early close, mapped to the close time in UTC."""
    cal = _calendar(calendar_name)
    out = {}
    for ts in cal.early_closes:
        if ts.year == year:
            out[ts.date()] = exchange_close_utc(calendar_name, ts.date())
    return out


def window_utc(day: date, start: time, end: time, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) on ``day`` in timezone ``tz``, returned in UTC."""
    zone = ZoneInfo(tz)
    s = pd.Timestamp(datetime.combine(day, start, tzinfo=zone)).tz_convert("UTC")
    e = pd.Timestamp(datetime.combine(day, end, tzinfo=zone)).tz_convert("UTC")
    return s, e


@dataclass(frozen=True)
class SessionWindow:
    day: date
    open_utc: pd.Timestamp
    close_utc: pd.Timestamp  # configured end, cut short by an early exchange close
    early_close: bool

    def first_minutes(self, n: int) -> tuple[pd.Timestamp, pd.Timestamp]:
        """[open, open + n min), never beyond the session close."""
        return self.open_utc, min(self.open_utc + pd.Timedelta(minutes=n), self.close_utc)

    def contains(self, ts_utc: pd.Timestamp) -> bool:
        return bool(self.open_utc <= ts_utc < self.close_utc)


def session_window(cfg: SessionConfig, day: date) -> SessionWindow | None:
    """The analysis session on ``day`` in UTC, or None if the exchange is closed."""
    if not is_trading_day(cfg.exchange_calendar, day):
        return None
    s, e = window_utc(day, cfg.start, cfg.end, cfg.timezone)
    close = exchange_close_utc(cfg.exchange_calendar, day)
    if close <= s:
        return None
    return SessionWindow(day, s, min(e, close), early_close=close < e)
