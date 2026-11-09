"""Trade resolution with pessimistic bar rules (spec Stage D.1).

Rules, one bracket order per signal (long; short mirrored):
  * Entry: market, at the OPEN of the first bar starting at or after ``signal.entry_ts`` and
    inside the session, plus the entry cost (half spread + slippage) against the trader.
    No such bar -> not filled. Entry at or beyond the stop -> not filled (risk <= 0).
  * Risk = |entry fill - stop|; target = entry fill +/- k x risk.
  * From the entry bar onward, per bar:
      1. a later bar OPENS at or beyond the stop (gap / news jump) -> exit at that open,
         minus the stop cost: worse than the stop, never at it;
      2. low <= stop -> exit at the stop minus the stop cost. Checked BEFORE the target, so a
         bar touching both is a loss;
      3. high > target (strictly: a limit fills only if price trades THROUGH it) -> exit at
         the target exactly (limit order: no spread, no slippage, never a better price).
  * Neither by the last bar that closes inside the session -> market exit at that bar's
    close minus the market cost ("time").

Costs come from ``cost(ts, kind)``: a price amount per unit for the fill in the bar starting
at ``ts``; ``kind`` is "entry", "stop" or "market". ``cost=None`` gives the cost-free path.
Commissions are not prices and are handled by the caller (execution/backtest.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from paper_lab.data.bars import interval_delta
from paper_lab.setups.base import Signal

ExitReason = Literal["stop", "stop_gap", "target", "time", "no_fill"]
FillKind = Literal["entry", "stop", "market"]
CostFn = Callable[[pd.Timestamp, FillKind], float]


def _no_cost(ts: pd.Timestamp, kind: FillKind) -> float:
    return 0.0


@dataclass(frozen=True)
class TradeOutcome:
    filled: bool
    exit_reason: ExitReason
    note: str = ""
    entry_ts: pd.Timestamp | None = None  # START of the entry bar (fill at its open)
    entry_price: float = float("nan")  # fill, costs included
    stop: float = float("nan")
    target: float = float("nan")
    exit_ts: pd.Timestamp | None = None  # START of the exit bar
    exit_done_ts: pd.Timestamp | None = None  # END of the exit bar: position surely flat
    exit_price: float = float("nan")  # fill, costs included
    risk: float = float("nan")  # |entry fill - stop| per unit
    r_price: float = float("nan")  # (exit - entry) x side / risk, before commissions
    entry_cost: float = 0.0  # price cost per unit paid on entry
    exit_cost: float = 0.0  # price cost per unit paid on exit
    bars_held: int = 0


def simulate_trade(
    signal: Signal,
    bars: pd.DataFrame,
    session_close_utc: pd.Timestamp,
    interval: str,
    cost: CostFn | None = None,
) -> TradeOutcome:
    step = interval_delta(interval)
    c = cost or _no_cost
    usable = bars[(bars.index >= signal.entry_ts) & (bars.index <= session_close_utc - step)]
    if usable.empty:
        return TradeOutcome(False, "no_fill", "no bar after the signal inside the session")
    long = signal.side == "long"
    sign = 1.0 if long else -1.0
    entry_ts = pd.Timestamp(usable.index[0])
    entry_cost = c(entry_ts, "entry")
    entry = float(usable["open"].iloc[0]) + sign * entry_cost
    risk = (entry - signal.stop) * sign
    if risk <= 0:
        return TradeOutcome(
            False, "no_fill", f"entry {entry} at/beyond stop {signal.stop}", entry_ts, entry
        )
    target = entry + sign * signal.target_r * risk
    stop = signal.stop

    def done(i: int, raw: float, reason: ExitReason, kind: FillKind | None) -> TradeOutcome:
        ts = pd.Timestamp(usable.index[i])
        exit_cost = c(ts, kind) if kind is not None else 0.0
        price = raw - sign * exit_cost
        return TradeOutcome(
            filled=True,
            exit_reason=reason,
            entry_ts=entry_ts,
            entry_price=entry,
            stop=stop,
            target=target,
            exit_ts=ts,
            exit_done_ts=ts + step,
            exit_price=price,
            risk=risk,
            r_price=(price - entry) * sign / risk,
            entry_cost=entry_cost,
            exit_cost=exit_cost,
            bars_held=i + 1,
        )

    for i in range(len(usable)):
        o, h, lo = (float(usable[col].iloc[i]) for col in ("open", "high", "low"))
        if i > 0 and (o - stop) * sign <= 0:
            return done(i, o, "stop_gap", "stop")
        if (lo <= stop) if long else (h >= stop):
            return done(i, stop, "stop", "stop")
        if (h > target) if long else (lo < target):
            return done(i, target, "target", None)
    last = len(usable) - 1
    return done(last, float(usable["close"].iloc[last]), "time", "market")
