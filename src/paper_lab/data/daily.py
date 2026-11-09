"""Daily bars derived from intraday bars, and a look-ahead-free ATR.

A "trading day" follows the instrument's own hours (config ``trading_hours``, ET):
  * GLD 09:30-16:00: the ET calendar date.
  * GC=F 18:00-17:00 (wraps midnight): bars from 18:00 ET belong to the NEXT date, so the
    trading day D runs from 18:00 ET on D-1 to 17:00 ET on D (CME convention).

ATR here is the simple mean of the last ``n`` true ranges of COMPLETE days strictly before
the day being traded. The current day never enters its own ATR.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from paper_lab.config import HoursConfig

DAILY_COLUMNS = ("open", "high", "low", "close", "volume", "n_bars")


def trading_dates(index: pd.DatetimeIndex, hours: HoursConfig, tz: str) -> pd.DatetimeIndex:
    """Trading date (naive midnight) of every bar start in ``index``."""
    et = index.tz_convert(tz)
    dates = et.tz_localize(None).normalize()
    if hours.end <= hours.start:  # session wraps midnight
        start_min = hours.start.hour * 60 + hours.start.minute
        minute = et.hour * 60 + et.minute
        dates = dates + pd.to_timedelta(np.where(minute >= start_min, 1, 0), unit="D")
    return pd.DatetimeIndex(dates, name="day")


def daily_from_intraday(bars: pd.DataFrame, hours: HoursConfig, tz: str) -> pd.DataFrame:
    """OHLCV per trading day; index = naive midnight DatetimeIndex named ``day``."""
    if bars.empty:
        idx = pd.DatetimeIndex([], name="day")
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in DAILY_COLUMNS}, index=idx)
    key = trading_dates(pd.DatetimeIndex(bars.index), hours, tz)
    g = bars.groupby(key, sort=True)
    out = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "n_bars": g["open"].size(),
        }
    )
    out.index.name = "day"
    return out


def true_range(daily: pd.DataFrame) -> pd.Series:
    prev_close = daily["close"].shift(1)
    hl = daily["high"] - daily["low"]
    hc = (daily["high"] - prev_close).abs()
    lc = (daily["low"] - prev_close).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1)  # NaN prev close -> H-L


def atr_before(daily: pd.DataFrame, day: date, n: int) -> float | None:
    """Mean true range of the last ``n`` trading days strictly before ``day``.

    Returns None when fewer than ``n`` prior days exist (callers must then skip the day:
    a missing ATR is never replaced by a guess).
    """
    prior = daily[daily.index < pd.Timestamp(day)]
    if len(prior) < n:
        return None
    tr = true_range(prior.tail(n + 1)).tail(n)
    return float(tr.mean())
