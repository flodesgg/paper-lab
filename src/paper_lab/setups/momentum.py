"""Momentum continuation. Shipped DISABLED (config ``setups.momentum.enabled: false``) until
the user approves an experiment for it.

Rules (defaults proposed 2026-11-09, docs/decisions.md):
  * First window = session bars starting in the first ``first_window_minutes`` (all must be
    present). Move = (close of the last window bar - open of the first) / that open, in %.
    If |move| < ``move_pct`` the day is skipped. Direction = sign of the move.
  * Anchor, recomputed on every completed bar after the window:
      vwap - session VWAP from the open: sum(typical price x volume) / sum(volume),
             typical price = (high + low + close) / 3;
      ema  - EMA(``ema_length``) of closes over earlier bars plus today's bars
             (needs at least ``ema_length`` earlier bars).
  * Pullback signal (long; short mirrored): the bar's LOW touches or crosses the anchor and
    the bar CLOSES above it. Entry at the next bar's open (execution layer).
  * Stop: the lowest low since the first window ended (the pullback extreme, signal bar
    included) minus ``stop_buffer_atr`` x ATR(``atr_days``). Target = k x risk from the fill.
  * After an accepted signal the setup re-arms only after a bar trades entirely on the trend
    side of the anchor (only relevant when ``max_signals_per_day`` > 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd
from pydantic import Field

from paper_lab.data.bars import interval_minutes
from paper_lab.data.daily import atr_before
from paper_lab.setups.base import (
    DayContext,
    DayState,
    Reject,
    Setup,
    SetupParams,
    Side,
    Signal,
    Skip,
)
from paper_lab.setups.registry import register

MOVE_TOL = 1e-9  # a move exactly equal to move_pct qualifies despite float noise


class MomentumParams(SetupParams):
    first_window_minutes: int = Field(gt=0, le=150)
    move_pct: float = Field(gt=0, le=10)
    anchor: Literal["vwap", "ema"]
    ema_length: int = Field(ge=2, le=500)
    stop_buffer_atr: float = Field(ge=0, le=2)
    atr_days: int = Field(ge=2, le=100)


@dataclass
class MomentumState(DayState):
    atr: float | None = None
    side: Side | None = None
    armed: bool = True


@register
class MomentumContinuation(Setup[MomentumState]):
    name = "momentum"
    params_model = MomentumParams
    params: MomentumParams

    def __init__(self, params: MomentumParams, interval: str) -> None:
        super().__init__(params, interval)
        if params.first_window_minutes % interval_minutes(interval):
            raise ValueError(
                f"momentum: first_window_minutes={params.first_window_minutes} "
                f"is not a multiple of {interval}"
            )

    def start_day(self, ctx: DayContext) -> MomentumState | Skip:
        p = self.params
        atr = atr_before(ctx.daily, ctx.day, p.atr_days)
        if p.stop_buffer_atr > 0 and (atr is None or atr <= 0):
            return Skip(f"insufficient daily history for ATR({p.atr_days})")
        if p.anchor == "ema" and len(ctx.history) < p.ema_length:
            return Skip(f"insufficient history for EMA({p.ema_length})")
        state = MomentumState(atr=atr)
        if atr is not None:
            state.info["atr"] = atr
        return state

    def _anchor(self, ctx: DayContext, seen: pd.DataFrame) -> float | None:
        if self.params.anchor == "vwap":
            vol = seen["volume"].astype("float64")
            if float(vol.sum()) <= 0:
                return None
            tp = (seen["high"] + seen["low"] + seen["close"]) / 3.0
            return float((tp * vol).sum() / vol.sum())
        closes = pd.concat([ctx.history["close"], seen["close"]])
        return float(closes.ewm(span=self.params.ema_length, adjust=False).mean().iloc[-1])

    def on_bar(
        self, ctx: DayContext, state: MomentumState, seen: pd.DataFrame
    ) -> Signal | Reject | Skip | None:
        p = self.params
        ts = pd.Timestamp(seen.index[-1])
        w_start, w_end = ctx.window.first_minutes(p.first_window_minutes)
        if state.side is None:
            if ts + self.step < w_end:
                return None
            fw = seen[(seen.index >= w_start) & (seen.index < w_end)]
            expected = p.first_window_minutes // interval_minutes(self.interval)
            if len(fw) < expected:
                return Skip(f"incomplete first window: {len(fw)}/{expected} bars")
            o, c = float(fw["open"].iloc[0]), float(fw["close"].iloc[-1])
            move = (c - o) / o * 100.0
            state.info["first_move_pct"] = move
            if abs(move) < p.move_pct - MOVE_TOL:
                return Skip(f"first-window move {move:+.3f}% below {p.move_pct}%")
            state.side = "long" if move > 0 else "short"
        if ts < w_end:
            return None
        anchor = self._anchor(ctx, seen)
        if anchor is None:
            return Reject(ts, "anchor undefined (zero session volume)")
        bar = seen.iloc[-1]
        lo, hi, close = float(bar["low"]), float(bar["high"]), float(bar["close"])
        after = seen[seen.index >= w_end]
        long = state.side == "long"
        if not state.armed:
            if (long and lo > anchor) or (not long and hi < anchor):
                state.armed = True
            return None
        touched = lo <= anchor < close if long else close < anchor <= hi
        if not touched:
            return None
        buffer = p.stop_buffer_atr * (state.atr or 0.0)
        extreme = float(after["low"].min()) if long else float(after["high"].max())
        stop = extreme - buffer if long else extreme + buffer
        info = {"anchor": anchor, "first_move_pct": state.info["first_move_pct"]}
        assert state.side is not None
        return self.make_signal(ctx, seen, state.side, stop, f"pullback to {p.anchor}", info)

    def on_accept(self, state: MomentumState, signal: Signal) -> None:
        state.armed = False
