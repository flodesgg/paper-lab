"""Gross (cost-free) trade resolution with pessimistic bar rules.

Stage C needs this to state the *known correct outcome* of hand-built scenarios. Stage D
wraps it with slippage, spread, commission, position sizing and the journal; the rules
below do not change there.

Rules, one bracket order per signal:
  * Entry: market at the OPEN of the first bar starting at or after ``signal.entry_ts`` and
    inside the session (``entry_slippage`` is added against the trader; 0 here).
    No such bar -> not filled. Entry at or beyond the stop (gap through the stop before the
    entry) -> not filled: risk would be zero or negative.
  * Risk = |entry - stop|; target = entry +/- k x risk.
  * From the entry bar onward, per bar (long; short mirrored):
      1. later bar opens at or below the stop (gap) -> exit at that OPEN (worse than stop);
      2. low <= stop -> exit at the stop. Checked BEFORE the target, so a bar touching
         both is a loss ("same-bar stop and target -> stop");
      3. high > target (strictly: a limit fills only if price trades THROUGH it) -> exit
         at the target. A gap beyond the target still fills at the target, never better.
  * Neither by the last bar that closes inside the session -> exit at that bar's close
    ("time").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from paper_lab.data.bars import interval_delta
from paper_lab.setups.base import Signal

ExitReason = Literal["stop", "stop_gap", "target", "time", "no_fill"]


@dataclass(frozen=True)
class TradeOutcome:
    filled: bool
    exit_reason: ExitReason
    note: str = ""
    entry_ts: pd.Timestamp | None = None
    entry_price: float = float("nan")
    stop: float = float("nan")
    target: float = float("nan")
    exit_ts: pd.Timestamp | None = None  # bar START of the exit bar
    exit_price: float = float("nan")
    r_gross: float = float("nan")
    bars_held: int = 0


def simulate_trade(
    signal: Signal,
    bars: pd.DataFrame,
    session_close_utc: pd.Timestamp,
    interval: str,
    entry_slippage: float = 0.0,
) -> TradeOutcome:
    step = interval_delta(interval)
    usable = bars[(bars.index >= signal.entry_ts) & (bars.index <= session_close_utc - step)]
    if usable.empty:
        return TradeOutcome(False, "no_fill", "no bar after the signal inside the session")
    long = signal.side == "long"
    sign = 1.0 if long else -1.0
    entry_ts = pd.Timestamp(usable.index[0])
    entry = float(usable["open"].iloc[0]) + sign * entry_slippage
    risk = (entry - signal.stop) * sign
    if risk <= 0:
        return TradeOutcome(
            False, "no_fill", f"entry {entry} at/beyond stop {signal.stop}", entry_ts, entry
        )
    target = entry + sign * signal.target_r * risk
    stop = signal.stop

    def done(i: int, price: float, reason: ExitReason) -> TradeOutcome:
        return TradeOutcome(
            True,
            reason,
            "",
            entry_ts,
            entry,
            stop,
            target,
            pd.Timestamp(usable.index[i]),
            price,
            (price - entry) * sign / risk,
            i + 1,
        )

    for i in range(len(usable)):
        o, h, lo = (float(usable[c].iloc[i]) for c in ("open", "high", "low"))
        if i > 0 and (o - stop) * sign <= 0:
            return done(i, o, "stop_gap")
        if (lo <= stop) if long else (h >= stop):
            return done(i, stop, "stop")
        if (h > target) if long else (lo < target):
            return done(i, target, "target")
    last = len(usable) - 1
    return done(last, float(usable["close"].iloc[last]), "time")
