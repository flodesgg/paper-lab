"""Builders for hand-made scenario days (ORB / momentum tests).

Prices are around 100, 5-minute bars, times are ET wall-clock strings like "09:45".
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from conftest import REPO

from paper_lab.calendar.sessions import session_window, trading_days
from paper_lab.config import SessionConfig, SetupConfig, load_config
from paper_lab.data.bars import INDEX_NAME
from paper_lab.execution.fills import TradeOutcome, simulate_trade
from paper_lab.setups.base import DayContext, DayResult, GuardFn, Setup
from paper_lab.setups.registry import build_setup

ET = ZoneInfo("America/New_York")
DAY = date(2026, 10, 15)  # an ordinary Thursday: no holiday, no morning release
CFG, _ = load_config(REPO / "config.yaml")
SESSION: SessionConfig = CFG.session

Row = tuple[str, float, float, float, float, int]  # (ET time, open, high, low, close, volume)


def ts(day: date, hhmm: str) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime.combine(day, time(h, m), tzinfo=ET)).tz_convert("UTC")


def bars(day: date, rows: list[Row]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([ts(day, r[0]) for r in rows], name=INDEX_NAME)
    df = pd.DataFrame(
        {
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [int(r[5]) for r in rows],
        },
        index=idx,
    )
    df["volume"] = df["volume"].astype("int64")
    return df


def times(start: str, end: str, step: int = 5) -> list[str]:
    """ET times in [start, end) every ``step`` minutes."""
    h, m = map(int, start.split(":"))
    cur = datetime(2000, 1, 1, h, m)
    h2, m2 = map(int, end.split(":"))
    stop = datetime(2000, 1, 1, h2, m2)
    out = []
    while cur < stop:
        out.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=step)
    return out


def flat(start: str, end: str, price: float, vol: int = 100) -> list[Row]:
    """Quiet bars at ``price`` (high/low +/- 0.1)."""
    return [(t, price, price + 0.1, price - 0.1, price, vol) for t in times(start, end)]


def daily(day: date, n: int = 20, rng: float = 10.0, close: float = 100.0) -> pd.DataFrame:
    """``n`` complete days before ``day`` whose true range is exactly ``rng`` -> ATR = rng."""
    days = [day - timedelta(days=i) for i in range(n, 0, -1)]
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="day")
    return pd.DataFrame(
        {
            "open": close,
            "high": close + rng / 2,
            "low": close - rng / 2,
            "close": close,
            "volume": 1000.0,
            "n_bars": 78.0,
        },
        index=idx,
    )


def history(day: date, n_sessions: int = 10, vol: int = 100, price: float = 100.0) -> pd.DataFrame:
    """Flat full sessions (09:30-16:00) on the ``n_sessions`` exchange days before ``day``."""
    prior = trading_days(SESSION.exchange_calendar, day - timedelta(days=40), day - timedelta(1))
    frames = [bars(d, flat("09:30", "16:00", price, vol)) for d in prior[-n_sessions:]]
    return pd.concat(frames) if frames else bars(day, [])


def ctx(
    day: date = DAY,
    *,
    atr: float = 10.0,
    atr_days_available: int = 20,
    hist: pd.DataFrame | None = None,
    session: SessionConfig = SESSION,
    symbol: str = "TEST",
) -> DayContext:
    w = session_window(session, day)
    assert w is not None, f"{day} is not a trading day"
    prior = trading_days(session.exchange_calendar, day - timedelta(days=120), day - timedelta(1))
    return DayContext(
        symbol=symbol,
        day=day,
        tz=session.timezone,
        interval="5m",
        window=w,
        history=history(day) if hist is None else hist,
        daily=daily(day, atr_days_available, atr),
        prior_sessions=tuple(prior[-60:]),
    )


def make(name: str, **overrides: Any) -> Setup[Any]:
    sc = CFG.setups[name]
    params = dict(sc.params) | overrides
    return build_setup(name, SetupConfig(enabled=True, interval="5m", params=params))


def run(
    setup: Setup[Any],
    day_bars: pd.DataFrame,
    c: DayContext | None = None,
    guard: GuardFn | None = None,
) -> DayResult:
    return setup.run_day(c or ctx(), day_bars, guard)


def outcome(result: DayResult, day_bars: pd.DataFrame, i: int = 0) -> TradeOutcome:
    w = session_window(SESSION, result.day)
    assert w is not None
    return simulate_trade(result.signals[i], day_bars, w.close_utc, "5m")
