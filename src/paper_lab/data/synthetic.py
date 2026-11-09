"""Deterministic synthetic bars (random walk) for demos and tests of the pipeline.

Synthetic data says NOTHING about gold. It exists so commands that produce trade results
can be exercised without touching the real archive before an experiment is approved.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from paper_lab.data.bars import INDEX_NAME, interval_delta


def synthetic_bars(
    days: list[date],
    tz: str,
    start: time = time(9, 30),
    end: time = time(16, 0),
    interval: str = "5m",
    base: float = 100.0,
    sigma_pct: float = 0.08,
    seed: int = 0,
) -> pd.DataFrame:
    """Random-walk OHLCV on each day in [start, end) local time; gaps between days."""
    zone = ZoneInfo(tz)
    step = interval_delta(interval)
    stamps: list[pd.Timestamp] = []
    for d in days:
        s = pd.Timestamp(datetime.combine(d, start, tzinfo=zone))
        e = pd.Timestamp(datetime.combine(d, end, tzinfo=zone))
        stamps += list(pd.date_range(s, e, freq=step, inclusive="left").tz_convert("UTC"))
    rng = np.random.default_rng(seed)
    n = len(stamps)
    rets = rng.normal(0.0, sigma_pct / 100.0, n)
    close = base * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[base], close[:-1]])
    wick = np.abs(rng.normal(0.0, sigma_pct / 200.0, (2, n))) * close
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    vol = rng.integers(200, 2000, n).astype("int64")
    idx = pd.DatetimeIndex(stamps, name=INDEX_NAME)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx
    )


def resample_bars(bars: pd.DataFrame, interval: str, tz: str, origin: time) -> pd.DataFrame:
    """Aggregate to a coarser interval, buckets anchored at ``origin`` local time."""
    local = bars.tz_convert(tz)
    offset = pd.Timedelta(hours=origin.hour, minutes=origin.minute)
    g = local.resample(interval_delta(interval), offset=offset, label="left", closed="left")
    out = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
        }
    ).dropna()
    out["volume"] = out["volume"].astype("int64")
    out.index = pd.DatetimeIndex(pd.DatetimeIndex(out.index).tz_convert("UTC"), name=INDEX_NAME)
    return out
