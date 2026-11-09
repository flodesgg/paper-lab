"""Momentum-continuation scenario tests: hand-built bars with known correct outcomes.

Common setup unless a test says otherwise: day 2026-10-15 (Thu), ATR = 10.0, params pinned
below. ``first_hour(a, b)``: 12 doji bars (open = high = low = close) stepping linearly from
``a`` (09:30) to ``b`` (10:25), volume 100 each, so the session VWAP after the first hour is
exactly (a + b) / 2 and the first-hour move is (b - a) / a.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from scenario import CFG, DAY, REPO, SESSION, bars, ctx, flat, history, outcome, run, ts

from paper_lab.calendar.sessions import session_window
from paper_lab.config import SetupConfig, load_config
from paper_lab.execution.fills import simulate_trade
from paper_lab.setups.registry import build_all, build_setup
from paper_lab.setups.runner import run_setup

P = {
    "first_window_minutes": 60,
    "move_pct": 0.40,
    "anchor": "vwap",
    "ema_length": 20,
    "k": 2.0,
    "stop_buffer_atr": 0.0,
    "atr_days": 14,
    "last_entry": "11:30",
    "max_signals_per_day": 1,
}
Row = tuple[str, float, float, float, float, int]


def mom(**kw: object) -> Any:
    return build_setup("momentum", SetupConfig(enabled=True, interval="5m", params=P | kw))


def first_hour(a: float, b: float, vol: int = 100) -> list[Row]:
    out: list[Row] = []
    for i in range(12):
        p = round(a + (b - a) * i / 11, 10)
        out.append((f"{9 + (30 + 5 * i) // 60:02d}:{(30 + 5 * i) % 60:02d}", p, p, p, p, vol))
    return out


UP = first_hour(100.0, 101.1)  # +1.1 %, VWAP 100.55
DOWN = first_hour(100.0, 98.9)  # -1.1 %, VWAP 99.45
# 10:30 long pullback: tp = 100.7667, VWAP = (100.55 x 12 + 100.7667) / 13 = 100.5667;
# low 100.4 <= 100.5667 < close 100.9
PULL_LONG: Row = ("10:30", 101.0, 101.0, 100.4, 100.9, 100)
# 10:30 short pullback: tp = 99.2333, VWAP = 99.4333; high 99.6 >= 99.4333 > close 99.1
PULL_SHORT: Row = ("10:30", 99.0, 99.6, 99.0, 99.1, 100)


def test_long_pullback_to_vwap_hits_target() -> None:
    rows = UP + [
        PULL_LONG,
        ("10:35", 100.9, 101.2, 100.8, 101.1, 100),  # entry 100.9, stop 100.4, risk 0.5
        ("10:40", 101.1, 102.0, 101.0, 101.9, 100),  # target 101.9, high 102.0 trades through
    ]
    day = bars(DAY, rows)
    r = run(mom(), day)
    s = r.signals[0]
    assert (s.side, s.stop, s.entry_ts) == ("long", 100.4, ts(DAY, "10:35"))
    assert s.info["anchor"] == pytest.approx(100.566667, abs=1e-5)
    assert r.info["first_move_pct"] == pytest.approx(1.1)
    o = outcome(r, day)
    assert (o.entry_price, o.exit_reason) == (100.9, "target")
    assert o.target == pytest.approx(101.9) and o.r_price == pytest.approx(2.0)


def test_short_pullback_to_vwap_hits_stop() -> None:
    rows = DOWN + [
        PULL_SHORT,
        ("10:35", 99.1, 99.3, 98.9, 99.0, 100),  # entry 99.1, stop 99.6, risk 0.5
        ("10:40", 99.0, 99.7, 98.95, 99.65, 100),  # high 99.7 >= 99.6 -> stop
    ]
    day = bars(DAY, rows)
    r = run(mom(), day)
    assert (r.signals[0].side, r.signals[0].stop) == ("short", 99.6)
    o = outcome(r, day)
    assert (o.exit_reason, o.exit_price) == ("stop", 99.6) and o.r_price == pytest.approx(-1.0)


def test_move_below_threshold_skips_day() -> None:
    r = run(mom(), bars(DAY, first_hour(100.0, 100.39) + [PULL_LONG]))
    assert r.status == "skipped" and "below 0.4%" in r.reason


def test_move_exactly_at_threshold_is_traded() -> None:
    # VWAP after the 10:30 bar = (100.2 x 12 + 100.2667) / 13 = 100.2051
    rows = first_hour(100.0, 100.4) + [("10:30", 100.4, 100.4, 100.1, 100.3, 100)]
    r = run(mom(), bars(DAY, rows))
    assert r.info["first_move_pct"] == pytest.approx(0.4)
    assert len(r.signals) == 1 and r.signals[0].stop == 100.1


def test_touch_but_close_below_anchor_is_no_signal() -> None:
    rows = UP + [("10:30", 101.0, 101.0, 100.3, 100.4, 100)]  # closes under the VWAP
    r = run(mom(), bars(DAY, rows))
    assert r.status == "scanned" and not r.signals


def test_no_touch_of_anchor_is_no_signal() -> None:
    rows = UP + [("10:30", 101.1, 101.3, 100.9, 101.2, 100)]  # low 100.9 > VWAP ~100.6
    assert not run(mom(), bars(DAY, rows)).signals


def test_incomplete_first_window_skips_day() -> None:
    rows = UP[:5] + UP[6:] + [PULL_LONG]
    r = run(mom(), bars(DAY, rows))
    assert r.status == "skipped" and r.reason == "incomplete first window: 11/12 bars"


def test_same_bar_stop_and_target_counts_as_stop() -> None:
    rows = UP + [PULL_LONG, ("10:35", 100.9, 102.5, 100.0, 101.0, 100)]
    day = bars(DAY, rows)
    o = outcome(run(mom(), day), day)
    assert (o.exit_reason, o.exit_price) == ("stop", 100.4) and o.r_price == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open() -> None:
    rows = UP + [
        PULL_LONG,
        ("10:35", 100.9, 101.0, 100.8, 100.9, 100),  # entry 100.9, risk 0.5
        ("10:40", 100.0, 100.1, 99.8, 99.9, 100),  # gap open 100.0 < stop 100.4
    ]
    day = bars(DAY, rows)
    o = outcome(run(mom(), day), day)
    # R = (100.0 - 100.9) / 0.5 = -1.8
    assert (o.exit_reason, o.exit_price) == ("stop_gap", 100.0)
    assert o.r_price == pytest.approx(-1.8)


def test_ema_anchor_long_signal() -> None:
    """History is flat at 100 and the first hour climbs to 101.1, so EMA(20) at 10:30 is
    strictly between 100 and 101.5: the bar with low 100.0 and close 101.5 must trigger."""
    rows = UP + [("10:30", 101.1, 101.6, 100.0, 101.5, 100)]
    r = run(mom(anchor="ema"), bars(DAY, rows))
    s = r.signals[0]
    assert s.reason == "pullback to ema" and 100.0 < s.info["anchor"] < 101.5
    assert s.stop == 100.0


def test_ema_without_enough_history_skips_day() -> None:
    r = run(mom(anchor="ema"), bars(DAY, UP + [PULL_LONG]), ctx(hist=bars(DAY, [])))
    assert r.status == "skipped" and "EMA(20)" in r.reason


def test_last_entry_cutoff() -> None:
    quiet = [
        (t, 101.5, 101.6, 101.4, 101.5, 100) for t in [r[0] for r in flat("10:30", "11:30", 0)]
    ]
    late = UP + quiet + [("11:30", 101.0, 101.0, 100.0, 101.2, 100)]
    assert not run(mom(), bars(DAY, late)).signals


def test_zero_session_volume_rejects() -> None:
    rows = first_hour(100.0, 101.1, vol=0) + [("10:30", 101.0, 101.0, 100.4, 100.9, 0)]
    r = run(mom(), bars(DAY, rows))
    assert not r.signals and r.rejected[0].reason == "anchor undefined (zero session volume)"


def test_holiday_produces_no_signal() -> None:
    cfg, _ = load_config(REPO / "config.yaml")
    holiday = date(2026, 11, 26)
    b = bars(holiday, UP + [PULL_LONG])
    res = run_setup(
        mom(),
        instrument=cfg.instrument("GLD"),
        session=SESSION,
        bars=b,
        bars_1h=b.iloc[:0],
        days=[holiday],
        guard=None,
    )
    assert [(x.status, len(x.signals)) for x in res] == [("closed", 0)]


def test_half_day_window_unchanged_and_time_exit() -> None:
    half = date(2026, 11, 27)
    w = session_window(SESSION, half)
    assert w is not None and w.close_utc == ts(half, "12:00")
    rows = (
        UP
        + [PULL_LONG]
        + [(t, 101.0, 101.1, 100.9, 101.0, 100) for t in [r[0] for r in flat("10:35", "12:00", 0)]]
    )
    day = bars(half, rows)
    r = run(mom(), day, ctx(half, hist=history(half)))
    o = simulate_trade(r.signals[0], day, w.close_utc, "5m")
    # entry 101.0, exit 11:55 close 101.0, risk 0.6 -> R = 0
    assert (o.exit_reason, o.exit_ts) == ("time", ts(half, "11:55"))
    assert o.r_price == pytest.approx(0.0)


def test_stop_buffer_in_atr() -> None:
    s = run(mom(stop_buffer_atr=0.1), bars(DAY, UP + [PULL_LONG])).signals[0]
    assert s.stop == pytest.approx(100.4 - 0.1 * 10.0)


def test_stop_buffer_without_atr_history_skips_day() -> None:
    r = run(mom(stop_buffer_atr=0.1), bars(DAY, UP + [PULL_LONG]), ctx(atr_days_available=5))
    assert r.status == "skipped" and "ATR(14)" in r.reason


def test_one_signal_per_day_and_rearm_when_two_allowed() -> None:
    rows = UP + [
        PULL_LONG,
        ("10:35", 100.9, 101.0, 100.5, 100.9, 100),  # touches VWAP again: not re-armed yet
        ("10:40", 101.0, 101.4, 100.9, 101.3, 100),  # low above VWAP -> re-armed
        ("10:45", 101.3, 101.3, 100.5, 101.0, 100),  # second pullback
    ]
    one = run(mom(), bars(DAY, rows))
    two = run(mom(max_signals_per_day=2), bars(DAY, rows))
    assert len(one.signals) == 1
    assert [s.signal_bar_ts for s in two.signals] == [ts(DAY, "10:30"), ts(DAY, "10:45")]
    assert two.signals[1].stop == 100.4  # lowest low since 10:30


def test_short_stop_is_highest_high_since_window() -> None:
    rows = DOWN + [
        ("10:30", 98.9, 99.2, 98.8, 98.9, 100),  # no touch (VWAP ~99.4)
        ("10:35", 99.0, 99.7, 99.0, 99.1, 100),  # touches, closes below -> short
    ]
    s = run(mom(), bars(DAY, rows)).signals[0]
    assert (s.side, s.stop) == ("short", 99.7)


def test_shipped_disabled_in_config() -> None:
    assert CFG.setups["momentum"].enabled is False
    assert "momentum" not in build_all(CFG)
    assert "momentum" in build_all(CFG, enabled_only=False)  # but its params still validate
