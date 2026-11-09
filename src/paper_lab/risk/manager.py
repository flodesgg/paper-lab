"""Per-day risk gate for one instrument account (spec Stage D.3).

Checked before every new entry, using only trades that are already closed:
  * one position at a time per instrument;
  * at most ``max_trades_per_day`` entries;
  * ``max_stops_per_day`` stop-outs end the day ("3 stops = day is over");
  * daily loss limit: no entry once the realized net loss reaches the limit, and no entry
    whose planned risk would take the loss beyond it if stopped out.
Position size itself is computed by execution/costs.size_position (risk per trade).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from paper_lab.config import RiskConfig

LIMIT_TOL = 1e-6  # USD


@dataclass
class DayRisk:
    cfg: RiskConfig
    trades: int = 0
    stops: int = 0
    realized_usd: float = 0.0
    flat_after: pd.Timestamp | None = None  # end of the bar in which the open trade exited
    halted: str = ""  # first reason the day stopped accepting entries

    @property
    def limit_usd(self) -> float:
        return self.cfg.paper_capital_usd * self.cfg.daily_loss_limit_pct / 100.0

    def block_reason(self, entry_ts: pd.Timestamp, planned_risk_usd: float) -> str | None:
        if self.flat_after is not None and entry_ts < self.flat_after:
            return "position already open"
        loss = -self.realized_usd
        if self.stops >= self.cfg.max_stops_per_day:
            reason = f"{self.stops} stops: day is over"
        elif loss >= self.limit_usd - LIMIT_TOL:
            reason = f"daily loss limit reached ({loss:.2f} >= {self.limit_usd:.2f} USD)"
        elif self.trades >= self.cfg.max_trades_per_day:
            reason = f"max {self.cfg.max_trades_per_day} trades per day"
        elif loss + planned_risk_usd > self.limit_usd + LIMIT_TOL:
            return (
                f"a stop-out would breach the daily loss limit "
                f"({loss:.2f} + {planned_risk_usd:.2f} > {self.limit_usd:.2f} USD)"
            )
        else:
            return None
        self.halted = self.halted or reason
        return reason

    def record(self, net_pnl_usd: float, was_stop: bool, flat_after: pd.Timestamp) -> None:
        self.trades += 1
        self.stops += int(was_stop)
        self.realized_usd += net_pnl_usd
        self.flat_after = flat_after
