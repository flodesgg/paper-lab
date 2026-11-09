"""Signals -> sized, cost-adjusted, risk-gated trades -> journal rows.

Per instrument account and day, signals of all given setups are processed in entry-time
order. For each signal:
  1. size: whole units so that the loss at the stop (from the estimated entry fill) is
     <= risk_per_trade_pct of capital and the position value <= max_notional_multiple x
     capital (execution/costs.size_position); 0 units -> ``skipped_size``;
  2. risk gate (risk/manager.DayRisk) on trades already closed -> ``skipped_risk``;
  3. fill twice with the same rules (execution/fills.py): cost-free (R gross) and with
     spread + slippage (R before commission); commissions are added per side.
R net = net USD P&L / (qty x multiplier x |entry fill - stop|).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.calendar.sessions import SessionWindow, session_window
from paper_lab.config import Config, FutureCosts, InstrumentConfig
from paper_lab.execution.costs import CostModel, size_position
from paper_lab.execution.fills import TradeOutcome, simulate_trade
from paper_lab.journal.writer import DAILY_COLUMNS, TRADE_COLUMNS
from paper_lab.risk.manager import DayRisk
from paper_lab.setups.base import DayResult, Setup, Signal
from paper_lab.setups.runner import run_setup


@dataclass(frozen=True)
class Execution:
    """One signal executed with a given size and cost model."""

    net: TradeOutcome
    gross: TradeOutcome
    qty: int
    multiplier: float
    commission_usd: float

    @property
    def risk_usd(self) -> float:
        return self.qty * self.multiplier * self.net.risk

    @property
    def spread_slippage_usd(self) -> float:
        return self.qty * self.multiplier * (self.net.entry_cost + self.net.exit_cost)

    @property
    def pnl_net_usd(self) -> float:
        return self.qty * self.multiplier * self.net.r_price * self.net.risk - self.commission_usd

    @property
    def pnl_gross_usd(self) -> float:
        return self.qty * self.multiplier * self.gross.r_price * self.gross.risk

    @property
    def r_net(self) -> float:
        return self.pnl_net_usd / self.risk_usd


def execute(
    signal: Signal,
    bars: pd.DataFrame,
    window: SessionWindow,
    interval: str,
    cost: CostModel,
    qty: int,
) -> Execution:
    """Fill ``qty`` units of ``signal`` with and without costs. ``net.filled`` may be False."""
    net = simulate_trade(signal, bars, window.close_utc, interval, cost.price_cost)
    gross = simulate_trade(signal, bars, window.close_utc, interval, None)
    commission = 0.0
    if net.filled:
        long = signal.side == "long"
        commission = cost.commission(qty, net.entry_price, is_sale=not long) + cost.commission(
            qty, net.exit_price, is_sale=long
        )
    return Execution(net, gross, qty, cost.multiplier, commission)


def _trade_id(s: Signal) -> str:
    return f"{s.day.isoformat()}|{s.symbol}|{s.setup}|{s.entry_ts.isoformat()}"


def _base_row(s: Signal, run_id: str, trade_as: str, mult: float) -> dict[str, Any]:
    row: dict[str, Any] = {c: None for c in TRADE_COLUMNS}
    row.update(
        trade_id=_trade_id(s),
        run_id=run_id,
        day=s.day.isoformat(),
        setup=s.setup,
        symbol=s.symbol,
        trade_as=trade_as,
        side=s.side,
        qty=0,
        signal_bar_utc=s.signal_bar_ts.isoformat(),
        entry_utc=s.entry_ts.isoformat(),
        stop=s.stop,
        cost_mult=mult,
        comment=s.reason,
    )
    return row


@dataclass(frozen=True)
class BacktestResult:
    trades: pd.DataFrame
    daily: pd.DataFrame
    day_results: list[DayResult]


def run_backtest(
    cfg: Config,
    setups: list[Setup[Any]],
    instrument: InstrumentConfig,
    bars: dict[str, pd.DataFrame],
    bars_1h: pd.DataFrame,
    days: list[date],
    guard: NewsGuard | None,
    news: NewsGuard,
    cost_mult: float,
    run_id: str,
) -> BacktestResult:
    """``bars`` maps interval -> bars; ``guard`` None = news filter off; ``news`` is always
    needed for event-dependent slippage."""
    cost = CostModel.from_config(cfg, instrument.symbol, news, cost_mult)
    c = cfg.costs[instrument.symbol]
    trade_as = c.trade_as if isinstance(c, FutureCosts) else instrument.symbol
    results: list[DayResult] = []
    by_day: dict[date, list[tuple[Signal, str, str]]] = {}
    for setup in setups:
        res = run_setup(
            setup,
            instrument=instrument,
            session=cfg.session,
            bars=bars[setup.interval],
            bars_1h=bars_1h,
            days=days,
            guard=guard,
        )
        results += res
        for r in res:
            for s in r.signals:
                by_day.setdefault(r.day, []).append((s, setup.interval, ""))
            for b in r.blocked:
                by_day.setdefault(r.day, []).append((b.signal, setup.interval, b.reason))

    rows: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    risk_cfg = cfg.risk
    for day in days:
        window = session_window(cfg.session, day)
        dr = DayRisk(risk_cfg)
        day_rows: list[dict[str, Any]] = []
        items = sorted(by_day.get(day, []), key=lambda x: (x[0].entry_ts, x[0].setup))
        for s, interval, blocked in items:
            row = _base_row(s, run_id, trade_as, cost_mult)
            day_rows.append(row)
            if blocked:
                row.update(status="blocked_guard", comment=f"{s.reason}; {blocked}")
                continue
            assert window is not None
            sign = 1.0 if s.side == "long" else -1.0
            est = s.ref_price + sign * cost.price_cost(s.entry_ts, "entry")
            qty = size_position(
                risk_cfg.paper_capital_usd,
                risk_cfg.risk_per_trade_pct,
                risk_cfg.max_notional_multiple,
                est,
                s.stop,
                cost.multiplier,
            )
            if qty == 0:
                row.update(status="skipped_size", comment=f"{s.reason}; 0 units fit the budget")
                continue
            planned = qty * cost.multiplier * abs(est - s.stop)
            why = dr.block_reason(s.entry_ts, planned)
            if why:
                row.update(status="skipped_risk", qty=qty, comment=f"{s.reason}; {why}")
                continue
            day_bars = bars[interval]
            day_bars = day_bars[
                (day_bars.index >= window.open_utc) & (day_bars.index < window.close_utc)
            ]
            ex = execute(s, day_bars, window, interval, cost, qty)
            if not ex.net.filled:
                row.update(status="no_fill", qty=qty, comment=f"{s.reason}; {ex.net.note}")
                continue
            n, g = ex.net, ex.gross
            assert n.exit_ts is not None and n.entry_ts is not None and n.exit_done_ts
            row.update(
                status="filled",
                qty=qty,
                entry_utc=n.entry_ts.isoformat(),
                entry=round(n.entry_price, 6),
                target=round(n.target, 6),
                exit_utc=n.exit_ts.isoformat(),
                exit=round(n.exit_price, 6),
                exit_reason=n.exit_reason,
                risk_usd=round(ex.risk_usd, 4),
                r_gross=round(g.r_price, 6) if g.filled else None,
                r_net=round(ex.r_net, 6),
                pnl_gross_usd=round(ex.pnl_gross_usd, 4) if g.filled else None,
                pnl_net_usd=round(ex.pnl_net_usd, 4),
                commission_usd=round(ex.commission_usd, 4),
                spread_slippage_usd=round(ex.spread_slippage_usd, 4),
                costs_usd=round(ex.commission_usd + ex.spread_slippage_usd, 4),
                comment=(f"{s.reason}; gross exit {g.exit_reason}" if g.filled else s.reason),
            )
            dr.record(ex.pnl_net_usd, n.exit_reason in ("stop", "stop_gap"), n.exit_done_ts)
        rows += day_rows
        filled = [r for r in day_rows if r["status"] == "filled"]
        daily.append(
            {
                "day_id": f"{day.isoformat()}|{instrument.symbol}",
                "run_id": run_id,
                "day": day.isoformat(),
                "symbol": instrument.symbol,
                "setups": ";".join(
                    f"{r.setup}:{r.status}{'(' + r.reason + ')' if r.reason else ''}"
                    for r in results
                    if r.day == day
                ),
                "signals": len(day_rows),
                "trades": len(filled),
                "wins": sum(1 for r in filled if r["r_net"] > 0),
                "losses": sum(1 for r in filled if r["r_net"] <= 0),
                "stops": sum(1 for r in filled if r["exit_reason"] in ("stop", "stop_gap")),
                "time_exits": sum(1 for r in filled if r["exit_reason"] == "time"),
                "r_gross_sum": round(sum(r["r_gross"] or 0.0 for r in filled), 6),
                "r_net_sum": round(sum(r["r_net"] for r in filled), 6),
                "pnl_net_usd": round(sum(r["pnl_net_usd"] for r in filled), 4),
                "halted": dr.halted,
                "comment": "" if window is not None else "exchange closed",
            }
        )
    return BacktestResult(
        pd.DataFrame(rows, columns=TRADE_COLUMNS),
        pd.DataFrame(daily, columns=DAILY_COLUMNS),
        results,
    )
