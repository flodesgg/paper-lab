"""Run a setup over many days and tabulate its signals (no trade outcomes here).

Each day gets a ``DayContext`` holding only data from before that day's open, and its
session bars are fed to the setup one completed bar at a time (``Setup.run_day``).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.calendar.sessions import session_window, trading_days
from paper_lab.config import InstrumentConfig, SessionConfig
from paper_lab.data.bars import interval_delta
from paper_lab.data.daily import daily_from_intraday
from paper_lab.setups.base import DayContext, DayResult, Setup

PRIOR_SESSIONS = 60  # how many earlier exchange sessions a context lists


def session_days(
    bars: pd.DataFrame, session: SessionConfig, interval: str
) -> tuple[list[date], list[date]]:
    """(complete, incomplete) exchange sessions between the first and last bar (ET dates).

    A session is incomplete when the data ends before its last bar, e.g. today's session
    while it is still running (data is also delayed ~20 min). Running a setup on it would
    report a different result tomorrow, so it is left out.
    """
    if bars.empty:
        return [], []
    et = pd.DatetimeIndex(bars.index).tz_convert(session.timezone)
    days = trading_days(session.exchange_calendar, et[0].date(), et[-1].date())
    data_end = pd.Timestamp(bars.index.max()) + interval_delta(interval)
    complete: list[date] = []
    incomplete: list[date] = []
    for d in days:
        w = session_window(session, d)
        (incomplete if w is not None and data_end < w.close_utc else complete).append(d)
    return complete, incomplete


def run_setup(
    setup: Setup[Any],
    *,
    instrument: InstrumentConfig,
    session: SessionConfig,
    bars: pd.DataFrame,
    bars_1h: pd.DataFrame,
    days: list[date],
    guard: NewsGuard | None,
) -> list[DayResult]:
    daily_all = daily_from_intraday(bars_1h, instrument.trading_hours, session.timezone)
    cal = session.exchange_calendar
    out: list[DayResult] = []
    for day in days:
        window = session_window(session, day)
        if window is None:
            out.append(DayResult(setup.name, instrument.symbol, day, "closed", "exchange closed"))
            continue
        prior = trading_days(cal, day - timedelta(days=PRIOR_SESSIONS * 2), day - timedelta(1))
        ctx = DayContext(
            symbol=instrument.symbol,
            day=day,
            tz=session.timezone,
            interval=setup.interval,
            window=window,
            history=bars[bars.index < window.open_utc],
            daily=daily_all[daily_all.index < pd.Timestamp(day)],
            prior_sessions=tuple(prior[-PRIOR_SESSIONS:]),
        )
        day_bars = bars[(bars.index >= window.open_utc) & (bars.index < window.close_utc)]
        out.append(setup.run_day(ctx, day_bars, guard.check_entry if guard else None))
    return out


SIGNAL_COLUMNS = [
    "day",
    "setup",
    "symbol",
    "status",
    "side",
    "signal_bar_utc",
    "entry_utc",
    "ref_price",
    "stop",
    "target_r",
    "reason",
    "info",
]


def results_to_frames(results: list[DayResult]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(signals incl. guard-blocked ones, one row per day)."""
    sig_rows: list[dict[str, object]] = []
    day_rows: list[dict[str, object]] = []
    for r in results:
        for status, s in [("accepted", s) for s in r.signals] + [
            (f"blocked: {b.reason}", b.signal) for b in r.blocked
        ]:
            sig_rows.append(
                {
                    "day": r.day.isoformat(),
                    "setup": s.setup,
                    "symbol": s.symbol,
                    "status": status,
                    "side": s.side,
                    "signal_bar_utc": s.signal_bar_ts.isoformat(),
                    "entry_utc": s.entry_ts.isoformat(),
                    "ref_price": s.ref_price,
                    "stop": round(s.stop, 6),
                    "target_r": s.target_r,
                    "reason": s.reason,
                    "info": ";".join(f"{k}={v:.6g}" for k, v in sorted(s.info.items())),
                }
            )
        day_rows.append(
            {
                "day": r.day.isoformat(),
                "setup": r.setup,
                "symbol": r.symbol,
                "status": r.status,
                "reason": r.reason,
                "signals": len(r.signals),
                "blocked": len(r.blocked),
                "rejected": len(r.rejected),
                "reject_reasons": " | ".join(x.reason for x in r.rejected),
                "info": ";".join(f"{k}={v:.6g}" for k, v in sorted(r.info.items())),
            }
        )
    return pd.DataFrame(sig_rows, columns=SIGNAL_COLUMNS), pd.DataFrame(day_rows)
