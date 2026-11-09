"""Setup contract. Every setup is one module in this package that subclasses ``Setup``
and decorates it with ``@register`` (see registry.py). Nothing else needs editing.

Look-ahead protection is structural, not a promise:
  * ``start_day`` sees only data from BEFORE the session open of the day (``DayContext``:
    earlier intraday bars and complete daily bars of earlier days).
  * ``on_bar`` is called once per completed session bar with ``seen`` = the session bars up
    to and including that bar. The setup never receives later bars.
  * A ``Signal`` produced at bar t is a market order to be filled at the OPEN of the next
    available bar (``entry_ts`` = close time of bar t), never at bar t's own prices.
tests/test_lookahead.py additionally shuffles future data and checks signals do not move.

Order of events inside ``run_day`` for each bar:
  1. bars whose close is after ``last_entry`` (ET) end the scan;
  2. ``on_bar`` returns None, a ``Reject`` (candidate failed a setup filter, logged),
     a ``Skip`` (no more signals today, logged with a reason) or a ``Signal``;
  3. a Signal is checked by the news guard at its entry time. A blocked signal is logged,
     does NOT count toward ``max_signals_per_day`` and does NOT change setup state
     (``on_accept`` is only called for accepted signals);
  4. scanning stops after ``max_signals_per_day`` accepted signals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, time
from typing import ClassVar, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from paper_lab.calendar.news_guard import GuardDecision
from paper_lab.calendar.sessions import SessionWindow, window_utc
from paper_lab.data.bars import interval_delta

Side = Literal["long", "short"]
GuardFn = Callable[[pd.Timestamp], GuardDecision]


class SetupParams(BaseModel):
    """Parameters every setup has. Subclasses add their own; unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    k: float = Field(gt=0, le=20)  # target = k x risk, risk measured from the actual fill
    last_entry: time  # ET; a signal bar must close at or before this time
    max_signals_per_day: int = Field(ge=1, le=10)


@dataclass(frozen=True)
class DayContext:
    """Everything a setup may know before the session opens."""

    symbol: str
    day: date
    tz: str
    interval: str
    window: SessionWindow
    history: pd.DataFrame  # bars of ``interval`` that started before window.open_utc
    daily: pd.DataFrame  # complete trading days strictly before ``day`` (data/daily.py)
    prior_sessions: tuple[date, ...]  # exchange sessions before ``day``, oldest first


@dataclass(frozen=True)
class Signal:
    setup: str
    symbol: str
    day: date
    side: Side
    signal_bar_ts: pd.Timestamp  # start of the bar whose close triggered the signal
    entry_ts: pd.Timestamp  # = close of that bar; market entry at the next bar's open
    ref_price: float  # close of the signal bar (information only, NOT the fill)
    stop: float
    target_r: float  # k
    reason: str
    info: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Reject:
    """A candidate that failed one of the setup's own filters (kept for diagnostics)."""

    ts: pd.Timestamp
    reason: str


@dataclass(frozen=True)
class Skip:
    """No (more) signals today, with the reason."""

    reason: str


@dataclass
class DayState:
    """Base class for a setup's per-day state; ``info`` ends up in the day report."""

    info: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class BlockedSignal:
    signal: Signal
    reason: str


@dataclass(frozen=True)
class DayResult:
    setup: str
    symbol: str
    day: date
    status: Literal["closed", "skipped", "scanned"]
    reason: str = ""
    signals: tuple[Signal, ...] = ()
    blocked: tuple[BlockedSignal, ...] = ()
    rejected: tuple[Reject, ...] = ()
    info: dict[str, float] = field(default_factory=dict)


class Setup[S: DayState](ABC):
    name: ClassVar[str]
    params_model: ClassVar[type[SetupParams]]

    def __init__(self, params: SetupParams, interval: str) -> None:
        if not isinstance(params, self.params_model):
            raise TypeError(f"{self.name}: params must be {self.params_model.__name__}")
        self.params = params
        self.interval = interval
        self.step = interval_delta(interval)

    @classmethod
    def from_params(cls, params: dict[str, object], interval: str) -> Setup[S]:
        return cls(cls.params_model.model_validate(params), interval)

    # --- the contract -------------------------------------------------------------------
    @abstractmethod
    def start_day(self, ctx: DayContext) -> S | Skip:
        """Prepare per-day state from pre-open data only."""

    @abstractmethod
    def on_bar(
        self, ctx: DayContext, state: S, seen: pd.DataFrame
    ) -> Signal | Reject | Skip | None:
        """React to the latest completed bar ``seen.iloc[-1]``; ``seen`` holds today's
        session bars up to and including it, nothing later."""

    def on_accept(self, state: S, signal: Signal) -> None:  # noqa: B027 (optional hook)
        """Called after the news guard accepted ``signal``."""

    # --- helpers for subclasses ---------------------------------------------------------
    def make_signal(
        self,
        ctx: DayContext,
        seen: pd.DataFrame,
        side: Side,
        stop: float,
        reason: str,
        info: dict[str, float] | None = None,
    ) -> Signal:
        ts = pd.Timestamp(seen.index[-1])
        return Signal(
            setup=self.name,
            symbol=ctx.symbol,
            day=ctx.day,
            side=side,
            signal_bar_ts=ts,
            entry_ts=ts + self.step,
            ref_price=float(seen["close"].iloc[-1]),
            stop=float(stop),
            target_r=self.params.k,
            reason=reason,
            info=dict(info or {}),
        )

    # --- driver (not overridden) --------------------------------------------------------
    def run_day(self, ctx: DayContext, bars: pd.DataFrame, guard: GuardFn | None) -> DayResult:
        """Scan one session. ``bars`` may contain anything; only completed session bars are
        used, and the setup sees them one at a time."""
        w = ctx.window
        sess = bars[(bars.index >= w.open_utc) & (bars.index <= w.close_utc - self.step)]
        start = self.start_day(ctx)
        if isinstance(start, Skip):
            return DayResult(self.name, ctx.symbol, ctx.day, "skipped", start.reason)
        state = start
        _, last_entry_utc = window_utc(ctx.day, time(0), self.params.last_entry, ctx.tz)
        signals: list[Signal] = []
        blocked: list[BlockedSignal] = []
        rejected: list[Reject] = []
        status: Literal["skipped", "scanned"] = "scanned"
        reason = ""
        for i in range(len(sess)):
            seen = sess.iloc[: i + 1]
            if seen.index[-1] + self.step > last_entry_utc:
                break
            out = self.on_bar(ctx, state, seen)
            if out is None:
                continue
            if isinstance(out, Reject):
                rejected.append(out)
                continue
            if isinstance(out, Skip):
                status, reason = "skipped", out.reason
                break
            decision = guard(out.entry_ts) if guard is not None else GuardDecision(True)
            if not decision.allowed:
                blocked.append(BlockedSignal(out, decision.reason))
                continue
            self.on_accept(state, out)
            signals.append(out)
            if len(signals) >= self.params.max_signals_per_day:
                break
        if not len(sess):
            status, reason = "skipped", "no session bars"
        return DayResult(
            self.name,
            ctx.symbol,
            ctx.day,
            status,
            reason,
            tuple(signals),
            tuple(blocked),
            tuple(rejected),
            dict(state.info),
        )
