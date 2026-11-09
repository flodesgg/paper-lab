"""ORB scenario tests: hand-built bars with known correct outcomes.

Common setup unless a test says otherwise: day 2026-10-15 (Thu), ATR = 10.0, average volume
of every bar time over the past 10 sessions = 100, params pinned below (not read from
config.yaml, so a pre-registered parameter change cannot silently change these tests).
Opening range (09:30-09:44 ET, 3 bars): high 102, low 99, height 3 = 0.30 ATR.
"""

from __future__ import annotations

from datetime import date, time

import pytest
from scenario import DAY, ET, SESSION, bars, ctx, flat, history, outcome, run, ts

from paper_lab.calendar.events import Event
from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.calendar.sessions import session_window
from paper_lab.config import NewsGuardConfig, SetupConfig
from paper_lab.execution.fills import simulate_trade
from paper_lab.setups.registry import build_setup

P = {
    "or_minutes": 15,
    "k": 2.0,
    "stop_fraction": 1.0,
    "min_range_atr": 0.10,
    "max_range_atr": 0.60,
    "atr_days": 14,
    "volume_mult": 1.2,
    "volume_lookback_sessions": 10,
    "volume_min_sessions": 5,
    "last_entry": "11:30",
    "max_signals_per_day": 1,
}
OR = [
    ("09:30", 100.0, 101.0, 99.5, 100.5, 100),
    ("09:35", 100.5, 102.0, 100.0, 101.0, 100),
    ("09:40", 101.0, 101.5, 99.0, 100.0, 100),
]


def orb(**kw: object):  # type: ignore[no-untyped-def]
    return build_setup("orb", SetupConfig(enabled=True, interval="5m", params=P | kw))


def test_long_breakout_hits_target() -> None:
    day = bars(
        DAY,
        OR
        + [
            ("09:45", 100.0, 102.6, 99.8, 102.5, 200),  # close 102.5 > 102, volume 2.0x
            ("09:50", 102.6, 103.0, 102.4, 102.9, 150),  # entry at this OPEN = 102.6
            ("09:55", 102.9, 106.0, 102.8, 105.9, 150),
            ("10:00", 105.9, 109.9, 105.5, 109.5, 150),  # high 109.9 > target 109.8
        ],
    )
    r = run(orb(), day)
    assert r.status == "scanned" and len(r.signals) == 1
    s = r.signals[0]
    assert (s.side, s.stop, s.signal_bar_ts, s.entry_ts) == (
        "long",
        99.0,
        ts(DAY, "09:45"),
        ts(DAY, "09:50"),
    )
    assert s.info["vol_ratio"] == pytest.approx(2.0)
    o = outcome(r, day)
    # entry 102.6 (next open, not the 102.5 signal close); risk 3.6; target 102.6 + 7.2
    assert o.entry_price == 102.6 and o.entry_ts == ts(DAY, "09:50")
    assert o.target == pytest.approx(109.8)
    assert (o.exit_reason, o.exit_ts) == ("target", ts(DAY, "10:00"))
    assert o.r_gross == pytest.approx(2.0)


def test_short_breakout_hits_stop() -> None:
    day = bars(
        DAY,
        OR
        + [
            ("09:45", 99.5, 99.6, 98.4, 98.5, 300),  # close 98.5 < 99, volume 3x
            ("09:50", 98.4, 98.9, 98.0, 98.8, 100),  # entry 98.4, stop 102, risk 3.6
            ("09:55", 98.8, 102.1, 98.7, 101.9, 100),  # high 102.1 >= 102 -> stop
        ],
    )
    r = run(orb(), day)
    s = r.signals[0]
    assert (s.side, s.stop) == ("short", 102.0)
    o = outcome(r, day)
    assert (o.exit_reason, o.exit_price) == ("stop", 102.0)
    assert o.r_gross == pytest.approx(-1.0)


def test_same_bar_stop_and_target_counts_as_stop() -> None:
    day = bars(
        DAY,
        OR
        + [
            ("09:45", 100.0, 102.6, 99.8, 102.5, 200),
            ("09:50", 102.6, 103.0, 102.4, 102.9, 100),  # entry 102.6, target 109.8, stop 99
            ("09:55", 102.9, 110.5, 98.5, 104.0, 500),  # touches both in one bar
        ],
    )
    o = outcome(run(orb(), day), day)
    assert (o.exit_reason, o.exit_price, o.exit_ts) == ("stop", 99.0, ts(DAY, "09:55"))
    assert o.r_gross == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open_beyond_stop() -> None:
    day = bars(
        DAY,
        OR
        + [
            ("09:45", 100.0, 102.6, 99.8, 102.5, 200),
            ("09:50", 102.6, 103.0, 102.4, 102.9, 100),  # entry 102.6, stop 99, risk 3.6
            ("09:55", 97.2, 97.5, 96.8, 97.0, 900),  # opens 1.8 below the stop
        ],
    )
    o = outcome(run(orb(), day), day)
    # exit at the gap open 97.2, not at 99: R = (97.2 - 102.6) / 3.6 = -1.5
    assert (o.exit_reason, o.exit_price) == ("stop_gap", 97.2)
    assert o.r_gross == pytest.approx(-1.5)


def _or_with_range(height: float) -> list[tuple[str, float, float, float, float, int]]:
    lo = 100.0
    return [
        ("09:30", 100.2, lo + height, lo, 100.3, 100),
        ("09:35", 100.3, 100.4, 100.1, 100.2, 100),
        ("09:40", 100.2, 100.3, 100.1, 100.2, 100),
    ]


def test_range_exactly_at_minimum_is_traded() -> None:
    # height 1.0 / ATR 10.0 = 0.10 = min_range_atr (inclusive)
    rows = _or_with_range(1.0) + [("09:45", 100.5, 101.3, 100.4, 101.2, 200)]
    r = run(orb(), bars(DAY, rows))
    assert r.info["range_atr"] == pytest.approx(0.10)
    assert len(r.signals) == 1 and r.signals[0].stop == 100.0


def test_range_just_below_minimum_skips_day() -> None:
    rows = _or_with_range(0.99) + [("09:45", 100.5, 101.3, 100.4, 101.2, 200)]
    r = run(orb(), bars(DAY, rows))
    assert r.status == "skipped" and "too narrow" in r.reason and not r.signals


def test_range_exactly_at_maximum_is_traded() -> None:
    rows = _or_with_range(6.0) + [("09:45", 105.0, 106.5, 104.9, 106.4, 200)]
    r = run(orb(), bars(DAY, rows))
    assert r.info["range_atr"] == pytest.approx(0.60)
    assert len(r.signals) == 1


def test_range_above_maximum_skips_day() -> None:
    rows = _or_with_range(6.5) + [("09:45", 105.0, 107.0, 104.9, 106.9, 200)]
    r = run(orb(), bars(DAY, rows))
    assert r.status == "skipped" and "too wide" in r.reason


def test_holiday_produces_no_signal() -> None:
    """Thanksgiving 2026-11-26: NYSE closed. COMEX gold still prints bars; they are ignored."""
    from scenario import REPO

    from paper_lab.config import load_config
    from paper_lab.setups.runner import run_setup

    cfg, _ = load_config(REPO / "config.yaml")
    holiday = date(2026, 11, 26)
    assert session_window(SESSION, holiday) is None
    b = bars(holiday, OR + [("09:45", 100.0, 102.6, 99.8, 102.5, 900)])
    res = run_setup(
        orb(),
        instrument=cfg.instrument("GLD"),
        session=SESSION,
        bars=b,
        bars_1h=b.iloc[:0],
        days=[holiday],
        guard=None,
    )
    assert [(x.status, x.reason, len(x.signals)) for x in res] == [("closed", "exchange closed", 0)]


def test_half_day_inside_configured_session_is_unchanged() -> None:
    """2026-11-27 (day after Thanksgiving): NYSE closes 13:00 ET, after our 12:00 end."""
    half = date(2026, 11, 27)
    w = session_window(SESSION, half)
    assert w is not None and not w.early_close and w.close_utc == ts(half, "12:00")
    rows = OR + [("09:45", 100.0, 102.6, 99.8, 102.5, 200)] + flat("09:50", "12:00", 103.0)
    day = bars(half, rows)
    r = run(orb(), day, ctx(half, hist=history(half)))
    o = simulate_trade(r.signals[0], day, w.close_utc, "5m")
    assert (o.exit_reason, o.exit_ts) == ("time", ts(half, "11:55"))


def test_half_day_cuts_a_longer_session_at_the_early_close() -> None:
    """With a 09:30-14:00 session the 13:00 early close ends the day; a trade still open
    exits at the close of the 12:55 bar and bars after 13:00 are never used."""
    half = date(2026, 11, 27)
    long_session = SESSION.model_copy(update={"end": time(14, 0)})
    w = session_window(long_session, half)
    assert w is not None and w.early_close and w.close_utc == ts(half, "13:00")
    rows = OR + [("09:45", 100.0, 102.6, 99.8, 102.5, 200)] + flat("09:50", "13:00", 103.0)
    rows += [("13:00", 103.0, 120.0, 103.0, 119.0, 100)]  # after the early close: ignored
    day = bars(half, rows)
    r = run(orb(), day, ctx(half, session=long_session, hist=history(half)))
    o = simulate_trade(r.signals[0], day, w.close_utc, "5m")
    assert (o.exit_reason, o.exit_ts, o.exit_price) == ("time", ts(half, "12:55"), 103.0)


def test_low_volume_break_is_rejected_then_confirmed_break_triggers() -> None:
    rows = OR + [
        ("09:45", 100.0, 102.6, 99.8, 102.5, 110),  # 1.1x < 1.2x -> rejected
        ("09:50", 102.5, 103.2, 102.3, 103.0, 130),  # 1.3x -> signal
    ]
    r = run(orb(), bars(DAY, rows))
    assert [x.ts for x in r.rejected] == [ts(DAY, "09:45")]
    assert "1.10x < 1.2x" in r.rejected[0].reason
    assert r.signals[0].signal_bar_ts == ts(DAY, "09:50")


def test_volume_profile_too_short_rejects() -> None:
    rows = OR + [("09:45", 100.0, 102.6, 99.8, 102.5, 900)]
    r = run(orb(), bars(DAY, rows), ctx(hist=history(DAY, n_sessions=4)))
    assert not r.signals and "4 sessions < 5" in r.rejected[0].reason


def test_volume_check_disabled_with_zero_mult() -> None:
    rows = OR + [("09:45", 100.0, 102.6, 99.8, 102.5, 1)]
    r = run(orb(volume_mult=0.0), bars(DAY, rows), ctx(hist=history(DAY, n_sessions=0)))
    assert len(r.signals) == 1 and "vol_ratio" not in r.signals[0].info


def test_wick_through_level_or_close_at_level_is_not_a_breakout() -> None:
    rows = OR + [
        ("09:45", 101.0, 103.5, 100.5, 101.8, 500),  # wick above 102, close inside
        ("09:50", 101.8, 102.4, 101.5, 102.0, 500),  # close exactly AT the high: not beyond
    ]
    r = run(orb(), bars(DAY, rows))
    assert r.status == "scanned" and not r.signals and not r.rejected


def test_incomplete_opening_range_skips_day() -> None:
    rows = [OR[0], OR[2], ("09:45", 100.0, 102.6, 99.8, 102.5, 200)]  # 09:35 bar missing
    r = run(orb(), bars(DAY, rows))
    assert r.status == "skipped" and r.reason == "incomplete opening range: 2/3 bars"


def test_insufficient_atr_history_skips_day() -> None:
    rows = OR + [("09:45", 100.0, 102.6, 99.8, 102.5, 200)]
    r = run(orb(), bars(DAY, rows), ctx(atr_days_available=13))
    assert r.status == "skipped" and "ATR(14)" in r.reason


def test_last_entry_cutoff() -> None:
    quiet = flat("09:45", "11:25", 101.0)
    at_cutoff = bars(DAY, OR + quiet + [("11:25", 101.0, 102.6, 100.9, 102.5, 200)])
    late = bars(
        DAY,
        OR + quiet + flat("11:25", "11:30", 101.0) + [("11:30", 101.0, 102.6, 100.9, 102.5, 200)],
    )
    # the 11:25 bar closes at 11:30 = last_entry -> allowed; the 11:30 bar closes 11:35 -> not
    assert run(orb(), at_cutoff).signals[0].entry_ts == ts(DAY, "11:30")
    assert not run(orb(), late).signals


def test_one_signal_per_day_and_rearm_when_two_allowed() -> None:
    rows = OR + [
        ("09:45", 100.0, 102.6, 99.8, 102.5, 200),  # 1st long break
        ("09:50", 102.5, 103.0, 102.2, 102.8, 200),  # still beyond: no re-trigger
        ("09:55", 102.8, 102.9, 101.0, 101.5, 200),  # closes back inside -> re-armed
        ("10:00", 101.5, 101.6, 98.0, 98.5, 200),  # short break
    ]
    one = run(orb(), bars(DAY, rows))
    two = run(orb(max_signals_per_day=2), bars(DAY, rows))
    assert [s.side for s in one.signals] == ["long"]
    assert [(s.side, s.signal_bar_ts) for s in two.signals] == [
        ("long", ts(DAY, "09:45")),
        ("short", ts(DAY, "10:00")),
    ]


def test_stop_fraction_half_puts_stop_mid_range() -> None:
    rows = OR + [("09:45", 100.0, 102.6, 99.8, 102.5, 200)]
    s = run(orb(stop_fraction=0.5), bars(DAY, rows)).signals[0]
    assert s.stop == pytest.approx(100.5)  # 102 - 0.5 x 3


def test_time_exit_at_last_session_bar_close() -> None:
    rows = OR + [("09:45", 100.0, 102.6, 99.8, 102.5, 200)] + flat("09:50", "12:00", 103.0)
    rows += [("12:00", 103.0, 130.0, 103.0, 129.0, 100)]  # after the session: never used
    day = bars(DAY, rows)
    o = outcome(run(orb(), day), day)
    # entry 103.0, exit at the 11:55 bar close 103.0; risk 4.0 -> R = 0
    assert (o.exit_reason, o.exit_ts, o.exit_price) == ("time", ts(DAY, "11:55"), 103.0)
    assert o.r_gross == pytest.approx(0.0)


def test_entry_gap_beyond_stop_is_not_filled() -> None:
    rows = OR + [
        ("09:45", 99.5, 99.6, 98.4, 98.5, 300),  # short signal, stop 102
        ("09:50", 102.5, 103.0, 102.0, 102.8, 300),  # opens above the stop
    ]
    day = bars(DAY, rows)
    o = outcome(run(orb(), day), day)
    assert not o.filled and o.exit_reason == "no_fill" and "beyond stop" in o.note


def test_signal_on_last_session_bar_has_no_fill() -> None:
    rows = OR + flat("09:45", "11:55", 101.0) + [("11:55", 101.0, 102.6, 100.9, 102.5, 200)]
    day = bars(DAY, rows)
    r = run(orb(last_entry="12:00"), day)
    assert r.signals[0].entry_ts == ts(DAY, "12:00")
    o = outcome(r, day)
    assert not o.filled and o.exit_reason == "no_fill"


def test_news_guard_blocks_early_break_then_allows_later_one() -> None:
    """CPI 08:30 ET on 2026-10-14 -> entries before 10:00 blocked (open_guard_min 30).
    The 09:45 break (entry 09:50) is blocked and does not use up the daily quota; the bar
    closing at 10:00 is still beyond the range with volume -> accepted."""
    cpi_day = date(2026, 10, 14)
    ev = Event(
        ts_et=__import__("datetime").datetime(2026, 10, 14, 8, 30, tzinfo=ET),
        event="CPI",
        status="released",
        source_url="https://www.bls.gov/news.release/archives/cpi_10142026.htm",
    )
    guard = NewsGuard(
        NewsGuardConfig(
            mode="open_guard",
            open_guard_min=30,
            morning_events=["CPI", "NFP"],
            in_session_window_min=15,
        ),
        SESSION,
        [ev],
    )
    rows = OR + [
        ("09:45", 100.0, 102.6, 99.8, 102.5, 200),
        ("09:50", 102.5, 103.0, 102.2, 102.8, 200),
        ("09:55", 102.8, 103.5, 102.6, 103.2, 200),
    ]
    r = run(orb(), bars(cpi_day, rows), ctx(cpi_day, hist=history(cpi_day)), guard.check_entry)
    assert [b.signal.entry_ts for b in r.blocked] == [ts(cpi_day, "09:50"), ts(cpi_day, "09:55")]
    assert "open guard: CPI" in r.blocked[0].reason
    assert [s.entry_ts for s in r.signals] == [ts(cpi_day, "10:00")]
