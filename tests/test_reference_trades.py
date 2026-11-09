"""Reference trades (spec Stage D.5): hand-computed, the simulator must reproduce them.

Cost model used (config.yaml, docs/cost_model.md):
  MGC (GC=F prices): tick 0.10 USD/oz, 10 oz/contract, commission 0.96 USD/contract/side.
    entry / time exit: 0.5 tick spread + 1 tick slippage x ctx = 0.05 + 0.10 x ctx
    stop exit:         0.5 tick spread + 2 tick slippage x ctx = 0.05 + 0.20 x ctx
    ctx = 2 in the first 15 min of the session, 3 in the first 60 min of a morning-release
    day, else 1. Target exits are limit orders: no cost.
  GLD: tick 0.01; entry 0.005 + 0.01 = 0.015, stop 0.005 + 0.02 = 0.025 (ctx 1);
    commission/side = max(1.00, 0.005 x qty) capped at 1 % of value, + CAT 0.000003 x qty,
    + SEC 0.0000206 x value on the SELL side only.
Ordinary day 2026-10-15; CPI day 2026-10-14 (released 08:30 ET).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from scenario import CFG, DAY, REPO, SESSION, bars, ts

from paper_lab.calendar.events import load_events
from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.calendar.sessions import session_window
from paper_lab.execution.backtest import Execution, execute
from paper_lab.execution.costs import CostModel, size_position
from paper_lab.setups.base import Side, Signal

CPI = date(2026, 10, 14)
NEWS = NewsGuard(
    CFG.news_guard, SESSION, load_events([REPO / p for p in CFG.calendar.events_files])
)
A = pytest.approx


def sig(
    day: date, side: Side, stop: float, entry: str, k: float = 2.0, sym: str = "GC=F"
) -> Signal:
    e = ts(day, entry)
    return Signal("ref", sym, day, side, e - pd.Timedelta(minutes=5), e, 0.0, stop, k, "ref")


def run(
    s: Signal, rows: list[tuple[str, float, float, float, float, int]], qty: int, mult: float = 1.0
) -> Execution:
    w = session_window(SESSION, s.day)
    assert w is not None
    cost = CostModel.from_config(CFG, s.symbol, NEWS, mult)
    return execute(s, bars(s.day, rows), w, "5m", cost, qty)


def flat_until_close(start: str, price: float) -> list[tuple[str, float, float, float, float, int]]:
    from scenario import times

    return [(t, price, price, price, price, 10) for t in times(start, "12:00")]


# ---------------------------------------------------------------- MGC (GC=F prices) ----


def test_ref01_mgc_long_target() -> None:
    """3 MGC long, stop 4090, k 2, entry bar 10:00 open 4100.00 (ctx 1).
    entry = 4100.00 + 0.15 = 4100.15; risk = 10.15; target = 4100.15 + 20.30 = 4120.45
    10:05 high 4121.00 > 4120.45 -> exit 4120.45 (limit, no cost)
    price P&L = 20.30 x 10 x 3 = 609.00; commission = 2 x 3 x 0.96 = 5.76
    net = 603.24; risk USD = 10.15 x 30 = 304.50; R net = 603.24 / 304.50 = 1.981083...
    gross: entry 4100, risk 10, target 4120 -> R 2.0, P&L 600.00"""
    ex = run(
        sig(DAY, "long", 4090.0, "10:00"),
        [
            ("10:00", 4100.0, 4105.0, 4099.0, 4104.0, 10),
            ("10:05", 4104.0, 4121.0, 4103.0, 4118.0, 10),
        ],
        qty=3,
    )
    assert (ex.net.entry_price, ex.net.exit_price, ex.net.exit_reason) == (
        A(4100.15),
        A(4120.45),
        "target",
    )
    assert ex.commission_usd == A(5.76) and ex.pnl_net_usd == A(603.24)
    assert ex.risk_usd == A(304.5) and ex.r_net == A(603.24 / 304.5)
    assert ex.gross.r_price == A(2.0) and ex.pnl_gross_usd == A(600.0)


def test_ref02_mgc_long_stop() -> None:
    """3 MGC long, stop 4090; entry 4100.15 (risk 10.15). 10:05 low 4089.50 <= 4090.
    exit = 4090 - 0.25 = 4089.75; price P&L = (4089.75 - 4100.15) x 30 = -312.00
    net = -312.00 - 5.76 = -317.76; R net = -317.76 / 304.50 = -1.043547...; gross R -1.0"""
    ex = run(
        sig(DAY, "long", 4090.0, "10:00"),
        [
            ("10:00", 4100.0, 4103.0, 4095.0, 4096.0, 10),
            ("10:05", 4096.0, 4097.0, 4089.5, 4090.5, 10),
        ],
        qty=3,
    )
    assert (ex.net.exit_reason, ex.net.exit_price) == ("stop", A(4089.75))
    assert ex.pnl_net_usd == A(-317.76) and ex.r_net == A(-317.76 / 304.5)
    assert ex.gross.r_price == A(-1.0)


def test_ref03_mgc_short_gap_through_stop() -> None:
    """3 MGC short, stop 4110; entry 4100.00 - 0.15 = 4099.85; risk 10.15.
    10:05 opens 4116.00 >= stop -> exit at the open + 0.25 = 4116.25 (not at 4110)
    price P&L = (4099.85 - 4116.25) x 30 = -492.00; net = -497.76
    R net = -497.76 / 304.50 = -1.634680...; gross: (4100 - 4116) / 10 = -1.6"""
    ex = run(
        sig(DAY, "short", 4110.0, "10:00"),
        [
            ("10:00", 4100.0, 4104.0, 4098.0, 4103.0, 10),
            ("10:05", 4116.0, 4118.0, 4114.0, 4117.0, 10),
        ],
        qty=3,
    )
    assert (ex.net.exit_reason, ex.net.exit_price) == ("stop_gap", A(4116.25))
    assert ex.pnl_net_usd == A(-497.76) and ex.r_net == A(-497.76 / 304.5)
    assert ex.gross.r_price == A(-1.6)


def test_ref04_mgc_same_bar_stop_and_target() -> None:
    """Entry bar 10:00 (open 4100, high 4125 > target 4120.45, low 4085 < stop 4090):
    the stop is assumed first. Same numbers as ref02: net -317.76, R -1.043547..."""
    ex = run(sig(DAY, "long", 4090.0, "10:00"), [("10:00", 4100.0, 4125.0, 4085.0, 4110.0, 10)], 3)
    assert (ex.net.exit_reason, ex.net.exit_price) == ("stop", A(4089.75))
    assert ex.pnl_net_usd == A(-317.76) and ex.gross.exit_reason == "stop"


def test_ref05_mgc_time_exit() -> None:
    """3 MGC long, entry 4100.15; price drifts to 4104.00 and stays; last session bar 11:55
    closes 4104.00 -> market exit 4104.00 - 0.15 = 4103.85
    price P&L = 3.70 x 30 = 111.00; net = 105.24; R net = 105.24 / 304.50 = 0.345616...
    gross: (4104 - 4100) / 10 = 0.4"""
    rows = [("10:00", 4100.0, 4102.0, 4098.0, 4101.0, 10)] + flat_until_close("10:05", 4104.0)
    ex = run(sig(DAY, "long", 4090.0, "10:00"), rows, 3)
    assert (ex.net.exit_reason, ex.net.exit_ts, ex.net.exit_price) == (
        "time",
        ts(DAY, "11:55"),
        A(4103.85),
    )
    assert ex.pnl_net_usd == A(105.24) and ex.gross.r_price == A(0.4)


def test_ref06_mgc_entry_in_opening_minutes() -> None:
    """Entry bar 09:40 is inside the first 15 min: slippage x2 -> entry cost 0.05 + 0.20.
    entry 4100.25; risk 10.25; target 4120.75; 09:45 high 4121 > 4120.75 -> target
    price P&L = 20.50 x 30 = 615.00; net 609.24; risk USD 307.50; R 1.981268..."""
    ex = run(
        sig(DAY, "long", 4090.0, "09:40"),
        [
            ("09:40", 4100.0, 4102.0, 4099.0, 4101.0, 10),
            ("09:45", 4101.0, 4121.0, 4100.0, 4120.0, 10),
        ],
        qty=3,
    )
    assert ex.net.entry_price == A(4100.25) and ex.net.target == A(4120.75)
    assert ex.pnl_net_usd == A(609.24) and ex.r_net == A(609.24 / 307.5)


def test_ref07_mgc_cpi_day_entry_and_stop() -> None:
    """CPI day, entry 10:00 and stop 10:10 are within the first 60 min: slippage x3.
    entry = 4100 + 0.05 + 0.30 = 4100.35; risk 10.35; exit = 4090 - (0.05 + 0.60) = 4089.35
    price P&L = -11.00 x 30 = -330.00; net -335.76; risk USD 310.50; R -1.081353..."""
    ex = run(
        sig(CPI, "long", 4090.0, "10:00"),
        [
            ("10:00", 4100.0, 4103.0, 4097.0, 4098.0, 10),
            ("10:05", 4098.0, 4099.0, 4094.0, 4095.0, 10),
            ("10:10", 4095.0, 4096.0, 4089.0, 4090.0, 10),
        ],
        qty=3,
    )
    assert (ex.net.entry_price, ex.net.exit_price) == (A(4100.35), A(4089.35))
    assert ex.pnl_net_usd == A(-335.76) and ex.r_net == A(-335.76 / 310.5)


def test_ref08_mgc_costs_x0_equal_gross() -> None:
    """ref01 with every cost x0: entry 4100, exit 4120, commission 0 -> net 600.00, R 2.0"""
    ex = run(
        sig(DAY, "long", 4090.0, "10:00"),
        [
            ("10:00", 4100.0, 4105.0, 4099.0, 4104.0, 10),
            ("10:05", 4104.0, 4121.0, 4103.0, 4118.0, 10),
        ],
        qty=3,
        mult=0.0,
    )
    assert ex.commission_usd == 0.0 and ex.pnl_net_usd == A(600.0) and ex.r_net == A(2.0)


def test_ref09_mgc_costs_x2_target() -> None:
    """ref01 with every cost x2: entry 4100 + 0.30 = 4100.30; risk 10.30; target 4120.90
    (high 4121 still above it). price P&L = 20.60 x 30 = 618.00; commission 11.52
    net 606.48; risk USD 309.00; R 1.962718... (more USD, fewer R: target is further away)"""
    ex = run(
        sig(DAY, "long", 4090.0, "10:00"),
        [
            ("10:00", 4100.0, 4105.0, 4099.0, 4104.0, 10),
            ("10:05", 4104.0, 4121.0, 4103.0, 4118.0, 10),
        ],
        qty=3,
        mult=2.0,
    )
    assert ex.net.exit_reason == "target" and ex.commission_usd == A(11.52)
    assert ex.pnl_net_usd == A(606.48) and ex.r_net == A(606.48 / 309.0)


def test_ref10_mgc_costs_x2_stop() -> None:
    """ref02 with costs x2: entry 4100.30, exit 4090 - 0.50 = 4089.50
    price P&L = -10.80 x 30 = -324.00; net -335.52; R = -335.52 / 309.00 = -1.085825..."""
    ex = run(
        sig(DAY, "long", 4090.0, "10:00"),
        [
            ("10:00", 4100.0, 4103.0, 4095.0, 4096.0, 10),
            ("10:05", 4096.0, 4097.0, 4089.5, 4090.5, 10),
        ],
        qty=3,
        mult=2.0,
    )
    assert ex.net.exit_price == A(4089.5) and ex.pnl_net_usd == A(-335.52)


# ------------------------------------------------------------------------------ GLD ----


def test_ref11_gld_long_target() -> None:
    """300 GLD long, stop 378.00; entry 380.00 + 0.015 = 380.015; risk 2.015;
    target 384.045; 10:05 high 384.10 -> exit 384.045. price P&L = 4.03 x 300 = 1209.00
    buy side:  max(1.00, 1.50) + CAT 0.0009 = 1.5009
    sell side: 1.50 + 0.0009 + SEC 0.0000206 x 300 x 384.045 (= 2.3733981) = 3.8742981
    commission 5.3751981; net 1203.6248019; risk USD 604.50; R 1.991108...; gross R 2.0"""
    ex = run(
        sig(DAY, "long", 378.0, "10:00", sym="GLD"),
        [("10:00", 380.0, 381.0, 379.5, 380.8, 10), ("10:05", 380.8, 384.10, 380.7, 384.0, 10)],
        qty=300,
    )
    assert ex.net.exit_price == A(384.045) and ex.commission_usd == A(5.3751981)
    assert ex.pnl_net_usd == A(1203.6248019) and ex.r_net == A(1203.6248019 / 604.5)
    assert ex.gross.r_price == A(2.0)


def test_ref12_gld_short_stop_sec_fee_on_entry() -> None:
    """300 GLD short (the entry is the SALE), stop 382; entry 380 - 0.015 = 379.985;
    risk 2.015; 10:05 high 382.20 -> exit 382 + 0.025 = 382.025
    price P&L = (379.985 - 382.025) x 300 = -612.00
    sell (entry): 1.50 + 0.0009 + 0.0000206 x 300 x 379.985 (= 2.3483073) = 3.8492073
    buy (exit): 1.5009 -> commission 5.3501073; net -617.3501073; R -1.021257..."""
    ex = run(
        sig(DAY, "short", 382.0, "10:00", sym="GLD"),
        [("10:00", 380.0, 381.0, 379.0, 380.5, 10), ("10:05", 380.5, 382.2, 380.4, 382.1, 10)],
        qty=300,
    )
    assert ex.net.exit_price == A(382.025) and ex.commission_usd == A(5.3501073)
    assert ex.pnl_net_usd == A(-617.3501073)


def test_ref13_gld_commission_minimum() -> None:
    """ref11 with 100 shares: 0.005 x 100 = 0.50 < minimum 1.00 per order.
    buy 1.00 + 0.0003; sell 1.00 + 0.0003 + 0.0000206 x 100 x 384.045 (= 0.7911327)
    commission 2.7917327; price P&L 403.00; net 400.2082673; risk 201.50"""
    ex = run(
        sig(DAY, "long", 378.0, "10:00", sym="GLD"),
        [("10:00", 380.0, 381.0, 379.5, 380.8, 10), ("10:05", 380.8, 384.10, 380.7, 384.0, 10)],
        qty=100,
    )
    assert ex.commission_usd == A(2.7917327) and ex.pnl_net_usd == A(400.2082673)
    assert ex.r_net == A(400.2082673 / 201.5)


def test_ref14_gld_time_exit() -> None:
    """300 GLD long, entry 380.015, last bar closes 381.00 -> exit 381.00 - 0.015 = 380.985
    price P&L = 0.97 x 300 = 291.00; buy 1.5009; sell 1.5009 + 0.0000206 x 300 x 380.985
    (= 2.3544873) -> commission 5.3562873; net 285.6437127; R 0.472529..."""
    rows = [("10:00", 380.0, 380.5, 379.6, 380.2, 10)] + flat_until_close("10:05", 381.0)
    ex = run(sig(DAY, "long", 378.0, "10:00", sym="GLD"), rows, 300)
    assert (ex.net.exit_reason, ex.net.exit_price) == ("time", A(380.985))
    assert ex.pnl_net_usd == A(285.6437127)


def test_ref15_gld_short_gap_through_stop() -> None:
    """300 GLD short, stop 382, entry 379.985; 10:05 opens 383.00 -> exit 383.025
    price P&L = -3.04 x 300 = -912.00; commission 5.3501073 (as ref12)
    net -917.3501073; R = -917.3501073 / 604.50 = -1.517535..."""
    ex = run(
        sig(DAY, "short", 382.0, "10:00", sym="GLD"),
        [("10:00", 380.0, 381.0, 379.0, 380.5, 10), ("10:05", 383.0, 383.5, 382.8, 383.2, 10)],
        qty=300,
    )
    assert (ex.net.exit_reason, ex.net.exit_price) == ("stop_gap", A(383.025))
    assert ex.pnl_net_usd == A(-917.3501073)


def test_ref16_stock_commission_cap_one_percent() -> None:
    """10 shares at 5.00: max(1.00, 0.05) = 1.00 but capped at 1 % of 50.00 = 0.50;
    + CAT 0.00003 = 0.50003 (buy side, no SEC fee)"""
    cost = CostModel.from_config(CFG, "GLD", NEWS)
    assert cost.commission(10, 5.0, is_sale=False) == A(0.50003)


def test_ref17_gc_costs_never_used_for_gld() -> None:
    """Regression for the lost version (GC costs on GLD overstated GLD costs ~10x):
    300 GLD bought at 380 cost 1.50 + 0.0009 = 1.5009, not 300 x 0.96 = 288.00."""
    gld = CostModel.from_config(CFG, "GLD", NEWS)
    gc = CostModel.from_config(CFG, "GC=F", NEWS)
    assert gld.commission(300, 380.0, is_sale=False) == A(1.5009)
    assert gc.commission(3, 4100.0, is_sale=False) == A(2.88)
    assert gld.price_cost(ts(DAY, "10:00"), "entry") == A(0.015)
    assert gc.price_cost(ts(DAY, "10:00"), "entry") == A(0.15)
    assert (gld.multiplier, gc.multiplier) == (1.0, 10.0)


# ---------------------------------------------------------------------- sizing ----


def test_sizing_by_risk_and_by_value() -> None:
    """Budget 0.5 % x 100 000 = 500 USD.
    MGC: per contract (4100.15 - 4090) x 10 = 101.50 -> 4 by risk; value cap
         100 000 / (4100.15 x 10) = 2.44 -> 2 contracts.
    GLD: per share 380.015 - 378.50 = 1.515 -> 330 by risk; value cap 100 000 / 380.015
         = 263.15 -> 263 shares.
    MGC with a 60 USD stop: 600 per contract > 500 -> 0 (skipped, never rounded up)."""
    assert size_position(100_000, 0.5, 1.0, 4100.15, 4090.0, 10) == 2
    assert size_position(100_000, 0.5, 10.0, 4100.15, 4090.0, 10) == 4
    assert size_position(100_000, 0.5, 1.0, 380.015, 378.5, 1) == 263
    assert size_position(100_000, 0.5, 4.0, 380.015, 378.5, 1) == 330
    assert size_position(100_000, 0.5, 1.0, 4100.0, 4040.0, 10) == 0
