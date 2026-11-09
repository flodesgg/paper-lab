"""Opening Range Breakout (ORB).

Rules (user decisions of 2026-11-09, docs/decisions.md):
  * Opening range (OR) = high/low of the session bars that START in the first ``or_minutes``
    of the NY session. All of them must be present, otherwise the day is skipped.
  * Day filter: OR height / ATR, where ATR = mean daily true range of the ``atr_days``
    complete trading days before today (daily bars derived from the 1h archive). The day is
    traded only if ``min_range_atr <= ratio <= max_range_atr`` (both inclusive).
  * Breakout = a bar CLOSES strictly above the OR high (long) or strictly below the OR low
    (short). Touching the level or a wick through it is not a breakout.
  * Volume confirmation: breakout bar volume >= ``volume_mult`` x mean volume of the bar with
    the same ET start time over the last ``volume_lookback_sessions`` exchange sessions.
    Fewer than ``volume_min_sessions`` samples -> no signal (fail safe). ``volume_mult: 0``
    disables the check.
  * Entry: market, at the open of the next bar (execution layer). Stop: OR high minus
    ``stop_fraction`` x OR height for longs (1.0 = the opposite side of the range), mirrored
    for shorts. Target = k x risk from the actual fill.
  * After an accepted signal the setup is disarmed until a bar closes back inside the range
    (only relevant when ``max_signals_per_day`` > 1). A signal blocked by the news guard does
    not disarm: the next qualifying bar after the guard window can still trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

import pandas as pd
from pydantic import Field, model_validator

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

RATIO_TOL = 1e-9  # "exactly equal to the minimum" must count as inside despite float noise


class OrbParams(SetupParams):
    or_minutes: int = Field(gt=0, le=120)
    stop_fraction: float = Field(gt=0, le=2.0)
    min_range_atr: float = Field(ge=0)
    max_range_atr: float = Field(gt=0)
    atr_days: int = Field(ge=2, le=100)
    volume_mult: float = Field(ge=0)
    volume_lookback_sessions: int = Field(ge=1, le=60)
    volume_min_sessions: int = Field(ge=1)

    @model_validator(mode="after")
    def _ranges(self) -> OrbParams:
        if self.min_range_atr > self.max_range_atr:
            raise ValueError("min_range_atr must be <= max_range_atr")
        if self.volume_min_sessions > self.volume_lookback_sessions:
            raise ValueError("volume_min_sessions must be <= volume_lookback_sessions")
        return self


@dataclass
class OrbState(DayState):
    atr: float = 0.0
    or_high: float | None = None
    or_low: float | None = None
    armed: bool = True
    volume_profile: dict[time, tuple[float, int]] | None = None


def same_time_volume(
    history: pd.DataFrame, sessions: tuple[date, ...], tz: str
) -> dict[time, tuple[float, int]]:
    """Mean volume per ET bar start time over the given session dates: {time: (mean, n)}."""
    if history.empty or not sessions:
        return {}
    et = pd.DatetimeIndex(history.index).tz_convert(tz)
    wanted = {pd.Timestamp(d) for d in sessions}
    mask = et.tz_localize(None).normalize().isin(list(wanted))
    sub = history.loc[mask, "volume"]
    if sub.empty:
        return {}
    times = pd.Index(et[mask].time)
    g = sub.groupby(times)
    means, sizes = g.mean(), g.size()
    return {t: (float(means[t]), int(sizes[t])) for t in means.index}


@register
class OpeningRangeBreakout(Setup[OrbState]):
    name = "orb"
    params_model = OrbParams
    params: OrbParams

    def __init__(self, params: OrbParams, interval: str) -> None:
        super().__init__(params, interval)
        if params.or_minutes % interval_minutes(interval):
            raise ValueError(f"orb: or_minutes={params.or_minutes} is not a multiple of {interval}")

    def start_day(self, ctx: DayContext) -> OrbState | Skip:
        p = self.params
        atr = atr_before(ctx.daily, ctx.day, p.atr_days)
        if atr is None or atr <= 0:
            return Skip(f"insufficient daily history for ATR({p.atr_days})")
        state = OrbState(atr=atr, info={"atr": atr})
        if p.volume_mult > 0:
            sessions = ctx.prior_sessions[-p.volume_lookback_sessions :]
            state.volume_profile = same_time_volume(ctx.history, sessions, ctx.tz)
        return state

    def _build_range(self, ctx: DayContext, state: OrbState, seen: pd.DataFrame) -> Skip | None:
        p = self.params
        or_start, or_end = ctx.window.first_minutes(p.or_minutes)
        or_bars = seen[(seen.index >= or_start) & (seen.index < or_end)]
        expected = p.or_minutes // interval_minutes(self.interval)
        if len(or_bars) < expected:
            return Skip(f"incomplete opening range: {len(or_bars)}/{expected} bars")
        hi, lo = float(or_bars["high"].max()), float(or_bars["low"].min())
        ratio = (hi - lo) / state.atr
        state.or_high, state.or_low = hi, lo
        state.info.update({"or_high": hi, "or_low": lo, "or_range": hi - lo, "range_atr": ratio})
        if ratio < p.min_range_atr - RATIO_TOL:
            return Skip(f"range too narrow: {ratio:.3f} ATR < {p.min_range_atr}")
        if ratio > p.max_range_atr + RATIO_TOL:
            return Skip(f"range too wide: {ratio:.3f} ATR > {p.max_range_atr}")
        return None

    def on_bar(
        self, ctx: DayContext, state: OrbState, seen: pd.DataFrame
    ) -> Signal | Reject | Skip | None:
        p = self.params
        ts = pd.Timestamp(seen.index[-1])
        _, or_end = ctx.window.first_minutes(p.or_minutes)
        if state.or_high is None:
            if ts + self.step < or_end:
                return None  # range still forming
            skip = self._build_range(ctx, state, seen)
            if skip is not None:
                return skip
        if ts < or_end:
            return None  # last range bar: it cannot be a breakout of itself
        hi, lo = state.or_high, state.or_low
        assert hi is not None and lo is not None
        close = float(seen["close"].iloc[-1])
        if not state.armed:
            if lo <= close <= hi:
                state.armed = True
            return None
        side: Side
        if close > hi:
            side, stop = "long", hi - p.stop_fraction * (hi - lo)
        elif close < lo:
            side, stop = "short", lo + p.stop_fraction * (hi - lo)
        else:
            return None
        info = {
            "or_high": hi,
            "or_low": lo,
            "range_atr": state.info["range_atr"],
            "atr": state.atr,
        }
        if p.volume_mult > 0:
            prof = state.volume_profile or {}
            t = ts.tz_convert(ctx.tz).time()
            mean, n = prof.get(t, (0.0, 0))
            if n < p.volume_min_sessions:
                return Reject(ts, f"volume profile has {n} sessions < {p.volume_min_sessions}")
            if mean <= 0:
                return Reject(ts, "volume profile mean is zero")
            vol_ratio = float(seen["volume"].iloc[-1]) / mean
            if vol_ratio < p.volume_mult:
                return Reject(ts, f"{side} break, volume {vol_ratio:.2f}x < {p.volume_mult}x")
            info["vol_ratio"] = vol_ratio
        return self.make_signal(ctx, seen, side, stop, f"close beyond OR ({side})", info)

    def on_accept(self, state: OrbState, signal: Signal) -> None:
        state.armed = False
