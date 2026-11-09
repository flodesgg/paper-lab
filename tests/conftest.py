from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from paper_lab.config import Config, PathsConfig, load_config
from paper_lab.data.bars import INDEX_NAME, interval_delta

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
ET = ZoneInfo("America/New_York")


def load_yf_fixture(name: str) -> pd.DataFrame:
    """A raw yfinance frame recorded to CSV (tz-aware America/New_York index)."""
    df = pd.read_csv(FIXTURES / name, index_col="Datetime")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    df.index.name = "Datetime"
    return df


class FakeSource:
    """Replays recorded raw frames; records every requested window."""

    def __init__(
        self,
        frames: dict[tuple[str, str], pd.DataFrame],
        fail_times: int = 0,
        exc: Exception | None = None,
    ) -> None:
        self.frames = frames
        self.calls: list[tuple[str, str, pd.Timestamp, pd.Timestamp, bool]] = []
        self.fail_times = fail_times
        self.exc = exc or ConnectionError("simulated network error")

    def history(
        self, symbol: str, interval: str, start: pd.Timestamp, end: pd.Timestamp, prepost: bool
    ) -> pd.DataFrame:
        self.calls.append((symbol, interval, start, end, prepost))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.exc
        df = self.frames.get((symbol, interval))
        if df is None:
            return pd.DataFrame()
        idx = df.index.tz_convert("UTC")
        out = df[(idx >= start) & (idx < end)].copy()
        if len(out):  # emulate Yahoo: the first row of every response has volume 0
            out.iloc[0, out.columns.get_loc("Volume")] = 0
        return out


def synth_bars(
    days: list[date],
    start: time,
    end: time,
    interval: str,
    seed: int = 0,
    base: float = 4000.0,
) -> pd.DataFrame:
    """Clean canonical bars on each ET day in [start, end) (no wrap)."""
    step = interval_delta(interval)
    stamps: list[pd.Timestamp] = []
    for d in days:
        s = pd.Timestamp(datetime.combine(d, start, tzinfo=ET))
        e = pd.Timestamp(datetime.combine(d, end, tzinfo=ET))
        stamps += list(pd.date_range(s, e, freq=step, inclusive="left").tz_convert("UTC"))
    rng = np.random.default_rng(seed)
    n = len(stamps)
    close = base + np.cumsum(rng.normal(0, 1.0, n))
    open_ = np.concatenate([[base], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.1, 1.0, n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 1.0, n)
    vol = rng.integers(100, 1000, n)
    idx = pd.DatetimeIndex(stamps, name=INDEX_NAME)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol.astype("int64")},
        index=idx,
    )


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    base, _ = load_config(REPO / "config.yaml")
    paths = PathsConfig(
        data_cache=tmp_path / "data",
        data_archive=tmp_path / "data_archive",
        journal=tmp_path / "journal",
        reports=tmp_path / "reports",
        experiments=tmp_path / "experiments",
        logs=tmp_path / "logs",
    )
    data = base.data.model_copy(update={"request_pause_sec": 0.0})
    return base.model_copy(update={"paths": paths, "data": data})


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    yield
    lg = logging.getLogger("paper_lab")
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach the network (spec section 6). Any socket connect fails loudly."""
    import socket

    def guard(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network access attempted in a test")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture(autouse=True)
def _no_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    """yfinance uses curl_cffi (C sockets), which the socket guard cannot see: block it too."""
    from paper_lab.data import fetch

    def guard(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network access attempted in a test (YFinanceSource)")

    monkeypatch.setattr(fetch.YFinanceSource, "history", guard)
