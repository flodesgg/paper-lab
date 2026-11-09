"""Canonical bar frame used everywhere in the lab.

A bar frame is a DataFrame with:
  * index  ``ts_utc``: tz-aware UTC DatetimeIndex, the bar *start* time, sorted, unique
  * columns ``open, high, low, close`` (float64) and ``volume`` (int64)

Bars are stored in UTC; session logic converts to America/New_York only when needed.
"""

from __future__ import annotations

import pandas as pd

COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")
INDEX_NAME = "ts_utc"

_INTERVAL_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}


def interval_minutes(interval: str) -> int:
    try:
        return _INTERVAL_MINUTES[interval]
    except KeyError:
        raise ValueError(f"unsupported interval {interval!r}") from None


def interval_delta(interval: str) -> pd.Timedelta:
    return pd.Timedelta(minutes=interval_minutes(interval))


def empty_bars() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name=INDEX_NAME)
    df = pd.DataFrame(
        {c: pd.Series(dtype="float64") for c in PRICE_COLUMNS}
        | {"volume": pd.Series(dtype="int64")},
        index=idx,
    )
    return df


def ensure_bar_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Check the canonical schema. Raises ValueError; never repairs silently."""
    if list(df.columns) != list(COLUMNS):
        raise ValueError(f"bar frame columns {list(df.columns)} != {list(COLUMNS)}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("bar frame index must be a DatetimeIndex")
    if df.index.tz is None or str(df.index.tz) != "UTC":
        raise ValueError(f"bar frame index must be UTC, got {df.index.tz}")
    return df
