"""Look-ahead test (spec Stage C): scramble or delete all data from a cutoff onward and
prove that every signal decided before the cutoff is unchanged.

A signal at bar t may use data up to the close of bar t (= its ``entry_ts``). So for a
cutoff c, the signals with ``entry_ts <= c`` must be identical whatever happens at or after
c. Intraday bars starting at or after c, and 1h bars that END after c, are scrambled.
The test also checks that the scramble really changed later signals: otherwise it would
prove nothing.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest
from conftest import REPO, synth_bars

from paper_lab.calendar.events import load_events
from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.calendar.sessions import trading_days
from paper_lab.config import Config, SetupConfig, load_config
from paper_lab.setups.base import DayContext, DayResult, DayState, Setup, SetupParams, Signal
from paper_lab.setups.registry import build_setup
from paper_lab.setups.runner import run_setup

CFG: Config = load_config(REPO / "config.yaml")[0]
DAYS = trading_days("XNYS", date(2026, 8, 3), date(2026, 10, 30))
TEST_DAYS = [d for d in DAYS if d >= date(2026, 9, 21)]  # earlier days = ATR/volume history

LOOSE = {
    "orb": {  # loose filters so random data produces many signals
        "or_minutes": 15,
        "k": 2.0,
        "stop_fraction": 1.0,
        "min_range_atr": 0.0,
        "max_range_atr": 10.0,
        "atr_days": 14,
        "volume_mult": 0.8,
        "volume_lookback_sessions": 10,
        "volume_min_sessions": 5,
        "last_entry": "11:30",
        "max_signals_per_day": 3,
    },
    "momentum_vwap": {
        "first_window_minutes": 60,
        "move_pct": 0.02,
        "anchor": "vwap",
        "ema_length": 20,
        "k": 2.0,
        "stop_buffer_atr": 0.05,
        "atr_days": 14,
        "last_entry": "11:30",
        "max_signals_per_day": 3,
    },
}
LOOSE["momentum_ema"] = LOOSE["momentum_vwap"] | {"anchor": "ema"}


def _data() -> tuple[pd.DataFrame, pd.DataFrame]:
    from datetime import time

    b5 = synth_bars(DAYS, time(9, 30), time(16, 0), "5m", seed=7, base=300.0)
    b1 = synth_bars(DAYS, time(9, 30), time(16, 0), "1h", seed=8, base=300.0)
    return b5, b1


def _setup(key: str) -> Any:
    if key == "cheat":
        return CheatSetup.from_params(
            {"k": 2.0, "last_entry": "11:30", "max_signals_per_day": 3}, "5m"
        )
    name = "orb" if key == "orb" else "momentum"
    return build_setup(name, SetupConfig(enabled=True, interval="5m", params=LOOSE[key]))


def _run(key: str, b5: pd.DataFrame, b1: pd.DataFrame) -> list[DayResult]:
    events = load_events([REPO / p for p in CFG.calendar.events_files])
    guard = NewsGuard(CFG.news_guard, CFG.session, events)
    global FUTURE
    FUTURE = b5
    return run_setup(
        _setup(key),
        instrument=CFG.instrument("GLD"),
        session=CFG.session,
        bars=b5,
        bars_1h=b1,
        days=TEST_DAYS,
        guard=guard,
    )


FUTURE = pd.DataFrame()


class CheatSetup(Setup[DayState]):
    """Negative control, NOT registered: peeks at the next bar's close through a global
    (the kind of bug a cache or a precomputed column can introduce)."""

    name = "cheat"
    params_model = SetupParams

    def start_day(self, ctx: DayContext) -> DayState:
        return DayState()

    def on_bar(self, ctx: DayContext, state: DayState, seen: pd.DataFrame) -> Signal | None:
        t = pd.Timestamp(seen.index[-1])
        nxt = t + pd.Timedelta(minutes=5)
        if nxt in FUTURE.index and FUTURE.loc[nxt, "close"] > seen["close"].iloc[-1] + 1.0:
            return self.make_signal(ctx, seen, "long", float(seen["low"].iloc[-1]) - 1, "cheat")
        return None


def _decisions(results: list[DayResult], cutoff: pd.Timestamp | None = None) -> list[Any]:
    out: list[Any] = []
    for r in results:
        for kind, sigs in (("ok", r.signals), ("blocked", [b.signal for b in r.blocked])):
            for s in sigs:
                if cutoff is None or s.entry_ts <= cutoff:
                    out.append(
                        (
                            kind,
                            s.day,
                            s.side,
                            s.signal_bar_ts,
                            s.entry_ts,
                            s.stop,
                            s.ref_price,
                            tuple(sorted(s.info.items())),
                        )
                    )
    return out


def _scramble(df: pd.DataFrame, cutoff: pd.Timestamp, bar: pd.Timedelta, seed: int) -> pd.DataFrame:
    out = df.copy()
    future = out.index + bar > cutoff if bar > pd.Timedelta(minutes=5) else out.index >= cutoff
    n = int(future.sum())
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    for col in ("open", "high", "low", "close"):
        vals = out.loc[future, col].to_numpy()[perm] * rng.uniform(0.97, 1.03, n)
        out.loc[future, col] = vals
    out.loc[future, "volume"] = out.loc[future, "volume"].to_numpy()[perm] * 3
    return out


def _violations(key: str) -> tuple[list[str], int, bool]:
    """(cutoffs where pre-cutoff decisions moved, number of signals, scramble effective)."""
    b5, b1 = _data()
    base = _run(key, b5, b1)
    all_sigs = [s for r in base for s in r.signals]
    # cutoffs: exactly at several signals' entry times, plus one bar before them
    picks = all_sigs[:: max(1, len(all_sigs) // 5)][:5]
    cutoffs = [s.entry_ts for s in picks] + [s.entry_ts - pd.Timedelta(minutes=5) for s in picks]
    bad: list[str] = []
    changed_after = False
    for i, c in enumerate(cutoffs):
        expected = _decisions(base, c)
        scrambled = _run(
            key,
            _scramble(b5, c, pd.Timedelta(minutes=5), i),
            _scramble(b1, c, pd.Timedelta(hours=1), 100 + i),
        )
        if _decisions(scrambled, c) != expected:
            bad.append(f"scramble@{c}")
        truncated = _run(key, b5[b5.index < c], b1[b1.index + pd.Timedelta(hours=1) <= c])
        if _decisions(truncated, c) != expected:
            bad.append(f"truncate@{c}")
        changed_after |= _decisions(scrambled) != _decisions(base)
    return bad, len(all_sigs), changed_after


@pytest.mark.parametrize("key", ["orb", "momentum_vwap", "momentum_ema"])
def test_signals_before_cutoff_ignore_future_data(key: str) -> None:
    bad, n, changed_after = _violations(key)
    assert n >= 10, "test data must produce enough signals to be meaningful"
    assert bad == [], f"look-ahead detected: {bad}"
    assert changed_after, "scrambling never changed a later signal: the test would be vacuous"


def test_negative_control_cheating_setup_is_caught() -> None:
    bad, n, _ = _violations("cheat")
    assert n >= 10 and bad, "the look-ahead harness failed to catch a setup that peeks ahead"


def test_signal_entry_is_after_its_bar() -> None:
    b5, b1 = _data()
    for r in _run("orb", b5, b1):
        for s in r.signals:
            assert s.entry_ts == s.signal_bar_ts + pd.Timedelta(minutes=5)
            assert s.ref_price == b5.loc[s.signal_bar_ts, "close"]
