"""Per-instrument cost model: spread, slippage (context dependent) and commissions.

Every number comes from config.yaml ``costs.<symbol>`` and ``execution``; the source of each
is in docs/cost_model.md. GC=F and GLD have separate blocks and separate units:
  * GC=F is traded as Micro Gold (MGC): price in USD/oz, 10 oz per contract, commission per
    contract per side.
  * GLD is a share: price in USD/share, commission per share with a per-order minimum and a
    cap, plus SEC and CAT fees.
A cost block can only be used with the symbol it is configured for (``CostModel`` refuses
any other), so GC costs cannot leak into GLD again.

``mult`` scales every component (spread, slippage, commission): 0 = cost-free,
1 = the configured model, 2 = everything twice as expensive (cost sensitivity).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.calendar.sessions import SessionWindow, session_window
from paper_lab.config import Config, ExecutionConfig, FutureCosts, SessionConfig, StockCosts
from paper_lab.execution.fills import FillKind


@dataclass
class CostModel:
    symbol: str
    costs: FutureCosts | StockCosts
    execution: ExecutionConfig
    session: SessionConfig
    news: NewsGuard  # used for its event list only, whatever the guard mode
    mult: float = 1.0

    def __post_init__(self) -> None:
        if self.mult < 0:
            raise ValueError("cost multiplier must be >= 0")
        self._windows: dict[date, SessionWindow | None] = {}

    @classmethod
    def from_config(cls, cfg: Config, symbol: str, news: NewsGuard, mult: float = 1.0) -> CostModel:
        if symbol not in cfg.costs:
            raise KeyError(f"no cost block for {symbol!r}")
        return cls(symbol, cfg.costs[symbol], cfg.execution, cfg.session, news, mult)

    @property
    def multiplier(self) -> float:
        """Quoted-price units per contract/share (MGC: 10 oz; GLD: 1 share)."""
        return self.costs.multiplier

    def _window(self, day: date) -> SessionWindow | None:
        if day not in self._windows:
            self._windows[day] = session_window(self.session, day)
        return self._windows[day]

    def slippage_mult(self, ts: pd.Timestamp) -> float:
        """Context multiplier for a fill in the bar starting at ``ts`` (max of those that
        apply): first ``open_minutes`` of the session; first ``event_minutes`` on a day with
        a morning release; within the guard window of an in-session event."""
        e = self.execution
        day = ts.tz_convert(self.session.timezone).date()
        w = self._window(day)
        if w is None:
            return 1.0
        m = 1.0
        if ts < w.open_utc + pd.Timedelta(minutes=e.open_minutes):
            m = max(m, e.open_slippage_mult)
        if self.news.is_event_day(day) and ts < w.open_utc + pd.Timedelta(minutes=e.event_minutes):
            m = max(m, e.event_slippage_mult)
        half = pd.Timedelta(minutes=self.news.cfg.in_session_window_min)
        for ev in self.news.events:
            if ev.day == day and w.contains(ev.ts_utc) and abs(ts - ev.ts_utc) <= half:
                m = max(m, e.event_slippage_mult)
        return m

    def price_cost(self, ts: pd.Timestamp, kind: FillKind) -> float:
        """Price amount per unit paid on a market/stop fill: half spread + slippage."""
        c = self.costs
        slip = c.stop_slippage_ticks if kind == "stop" else c.slippage_ticks
        return self.mult * c.tick_size * (c.half_spread_ticks + slip * self.slippage_mult(ts))

    def commission(self, qty: int, price: float, is_sale: bool) -> float:
        """USD for one side of a trade of ``qty`` contracts/shares at ``price``."""
        if qty <= 0:
            return 0.0
        c = self.costs
        if isinstance(c, FutureCosts):
            return self.mult * qty * c.commission_per_contract
        value = qty * price
        broker = max(c.commission_min, c.commission_per_share * qty)
        broker = min(broker, c.commission_max_pct / 100.0 * value)
        fees = c.cat_fee_per_share * qty + (c.sec_fee_rate * value if is_sale else 0.0)
        return self.mult * (broker + fees)


def size_position(
    capital: float,
    risk_pct: float,
    max_notional_multiple: float,
    entry_estimate: float,
    stop: float,
    multiplier: float,
) -> int:
    """Whole contracts/shares so that the loss at the stop is <= risk budget and the position
    value is <= ``max_notional_multiple`` x capital. Rounds DOWN; 0 means "too big a stop
    for one unit": the trade is skipped, never rounded up."""
    per_unit = abs(entry_estimate - stop) * multiplier
    if per_unit <= 0 or entry_estimate <= 0:
        return 0
    by_risk = math.floor(capital * risk_pct / 100.0 / per_unit + 1e-9)
    by_value = math.floor(capital * max_notional_multiple / (entry_estimate * multiplier) + 1e-9)
    return max(0, min(by_risk, by_value))
