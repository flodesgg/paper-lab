"""Forward paper loop (spec Stage F.1).

Every run (scheduled by launchd, or by hand) is idempotent:
  1. (optionally) fetch new bars into the archive;
  2. for each instrument, find the COMPLETE exchange sessions since ``forward.start`` that are
     not yet in ``journal/daily.csv`` -- today after the close, plus any day missed while the
     Mac was asleep or off (catch-up);
  3. run the enabled setups on those days with the same code, costs (x1) and risk gate as the
     backtest, and upsert the rows into ``journal/trades.csv`` / ``journal/daily.csv``;
  4. write the weekly report for every finished week that has none yet;
  5. append a line to ``journal/runs.csv``.
A day already in the journal is never recomputed, so a later data revision cannot change a
forward result after the fact. Parameters are fixed by the config (pinned by EXP-001); the
forward journal is out-of-sample data and is never used to tune them.
"""

from __future__ import annotations

import fcntl
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.calendar.sessions import session_window
from paper_lab.config import Config
from paper_lab.data.bars import interval_delta
from paper_lab.execution.backtest import run_backtest
from paper_lab.journal.writer import DAILY_COLUMNS, TRADE_COLUMNS, upsert
from paper_lab.runinfo import RunInfo
from paper_lab.setups.base import Setup
from paper_lab.setups.registry import build_all
from paper_lab.setups.runner import session_days

log = logging.getLogger("paper_lab.forward")

Loader = Callable[[str, list[str]], tuple[dict[str, pd.DataFrame], pd.DataFrame]]
RUN_COLUMNS = [
    "run_id",
    "started_utc",
    "as_of_utc",
    "git_commit",
    "strategy_hash",
    "data_hash",
    "fetch_errors",
    "days_processed",
    "trades",
    "waiting",
    "weekly_reports",
    "committed",
    "pushed",
]


class ForwardBusyError(RuntimeError):
    """Another forward run holds the lock."""


@contextmanager
def forward_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise ForwardBusyError(f"another forward run is active ({path})") from e
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@dataclass
class SymbolRun:
    symbol: str
    processed: list[date] = field(default_factory=list)
    waiting: list[date] = field(default_factory=list)  # session not complete in the data yet
    trades: int = 0


@dataclass
class ForwardResult:
    symbols: list[SymbolRun]
    bars: dict[str, pd.DataFrame]  # symbol -> trading-interval bars (for the weekly report)

    @property
    def days_processed(self) -> int:
        return sum(len(s.processed) for s in self.symbols)

    @property
    def trades(self) -> int:
        return sum(s.trades for s in self.symbols)


def processed_keys(daily_path: Path) -> set[str]:
    if not daily_path.exists():
        return set()
    return set(pd.read_csv(daily_path, dtype=str, usecols=["day_id"])["day_id"])


def _cut(bars: pd.DataFrame, as_of: pd.Timestamp, interval: str) -> pd.DataFrame:
    """Only bars that had ended by ``as_of`` (a simulated 'now' in tests and replays)."""
    if bars.empty:
        return bars
    return bars[bars.index <= as_of - interval_delta(interval)]


def pending_days(
    cfg: Config, symbol: str, bars: pd.DataFrame, interval: str, done: set[str]
) -> tuple[list[date], list[date]]:
    """(complete sessions to process, sessions still waiting for data)."""
    complete, incomplete = session_days(bars, cfg.session, interval)
    start = cfg.forward.start
    todo = [d for d in complete if d >= start and f"{d.isoformat()}|{symbol}" not in done]
    return todo, [d for d in incomplete if d >= start]


def run_forward(
    cfg: Config,
    info: RunInfo,
    news: NewsGuard,
    loader: Loader,
    as_of: pd.Timestamp,
) -> ForwardResult:
    setups: list[Setup[Any]] = list(build_all(cfg).values())
    guard = news if cfg.news_guard.mode != "off" else None
    journal = cfg.paths.journal
    trades_path, daily_path = journal / "trades.csv", journal / "daily.csv"
    done = processed_keys(daily_path)
    out = ForwardResult([], {})
    if not setups:
        log.warning("no enabled setup: the forward loop only archives data")
    intervals = sorted({s.interval for s in setups}) or ["5m"]
    main_iv = intervals[0]
    for inst in cfg.instruments:
        run = SymbolRun(inst.symbol)
        out.symbols.append(run)
        raw, bars_1h = loader(inst.symbol, intervals)
        bars = {iv: _cut(b, as_of, iv) for iv, b in raw.items()}
        bars_1h = _cut(bars_1h, as_of, "1h")
        out.bars[inst.symbol] = bars[main_iv]
        todo, run.waiting = pending_days(cfg, inst.symbol, bars[main_iv], main_iv, done)
        if not todo or not setups:
            continue
        res = run_backtest(cfg, setups, inst, bars, bars_1h, todo, guard, news, 1.0, info.run_id)
        if len(res.trades):
            upsert(trades_path, res.trades, "trade_id", TRADE_COLUMNS)
        upsert(daily_path, res.daily, "day_id", DAILY_COLUMNS)
        run.processed = todo
        run.trades = int((res.trades["status"] == "filled").sum()) if len(res.trades) else 0
        log.info(
            "forward %s: processed %s, %d trade(s); waiting for data: %s",
            inst.symbol,
            [d.isoformat() for d in todo],
            run.trades,
            [d.isoformat() for d in run.waiting],
        )
    return out


def session_bar_counts(
    cfg: Config, bars: pd.DataFrame, days: list[date], interval: str
) -> dict[date, tuple[int, int]]:
    """day -> (bars present in the session, bars expected)."""
    step = interval_delta(interval)
    out: dict[date, tuple[int, int]] = {}
    for d in days:
        w = session_window(cfg.session, d)
        if w is None:
            continue
        n = int(((bars.index >= w.open_utc) & (bars.index < w.close_utc)).sum())
        out[d] = (n, int((w.close_utc - w.open_utc) / step))
    return out


def log_run(path: Path, row: dict[str, object]) -> None:
    """Append one line per run, in run order (a log, not a keyed table)."""
    df = pd.DataFrame([row])[RUN_COLUMNS]
    if path.exists():
        with path.open() as fh:
            if fh.readline().strip().split(",") != RUN_COLUMNS:
                raise ValueError(f"{path}: columns differ from the run-log schema")
        df.to_csv(path, mode="a", header=False, index=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
