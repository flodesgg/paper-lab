"""News guard: a protection, not a strategy. Decides whether a new entry is allowed.

Timing facts this encodes (tested in tests/test_calendar.py):
  * CPI and NFP are released at 08:30 ET, one hour BEFORE the 09:30 session opens, yet they
    make the open violent. A "+/-15 min around the event" window never touches the session,
    so the guard instead blocks the first ``open_guard_min`` minutes of the session on days
    with a morning release.
  * FOMC statements come at 14:00 ET, AFTER the 12:00 session end: they never block anything.
  * Any event released *during* the session is blocked +/- ``in_session_window_min``.

``mode: off`` is the "no filter" variant, so the filter's effect can be measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from paper_lab.calendar.events import Event, covered_years, events_on
from paper_lab.calendar.sessions import SessionWindow, session_window
from paper_lab.config import NewsGuardConfig, SessionConfig


class CalendarCoverageError(ValueError):
    """Asked about a year the event calendar does not cover: refusing to assume 'no news'."""


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


class NewsGuard:
    def __init__(self, cfg: NewsGuardConfig, session: SessionConfig, events: list[Event]) -> None:
        self.cfg = cfg
        self.session = session
        self.events = events
        self._years = covered_years(events)

    def _check_coverage(self, day: date) -> None:
        if day.year not in self._years:
            raise CalendarCoverageError(
                f"no event calendar for {day.year}; add data/calendar/events_{day.year}.csv"
            )

    def morning_events(self, day: date, window: SessionWindow) -> list[Event]:
        return [
            e
            for e in events_on(self.events, day)
            if e.event in self.cfg.morning_events and e.ts_utc < window.open_utc
        ]

    def is_event_day(self, day: date) -> bool:
        """A day with a morning release that the open guard reacts to."""
        self._check_coverage(day)
        w = session_window(self.session, day)
        return w is not None and bool(self.morning_events(day, w))

    def check_entry(self, ts_utc: pd.Timestamp) -> GuardDecision:
        if self.cfg.mode == "off":
            return GuardDecision(True)
        day = ts_utc.tz_convert(self.session.timezone).date()
        self._check_coverage(day)
        w = session_window(self.session, day)
        if w is None or not w.contains(ts_utc):
            return GuardDecision(False, "outside session")
        morning = self.morning_events(day, w)
        if morning:
            g_start, g_end = w.first_minutes(self.cfg.open_guard_min)
            if g_start <= ts_utc < g_end:
                names = "+".join(sorted({e.event for e in morning}))
                return GuardDecision(
                    False,
                    f"open guard: {names} released before the open "
                    f"(first {self.cfg.open_guard_min} min blocked)",
                )
        half = pd.Timedelta(minutes=self.cfg.in_session_window_min)
        for e in events_on(self.events, day):
            if w.contains(e.ts_utc) and abs(ts_utc - e.ts_utc) <= half:
                return GuardDecision(False, f"in-session event {e.event} at {e.ts_et:%H:%M} ET")
        return GuardDecision(True)
