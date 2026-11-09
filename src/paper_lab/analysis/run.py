"""Run a pre-registered experiment and write the one-page backtest report.

Variants per symbol: news guard (as configured / off) x cost multipliers. The decision rule
of the experiment is evaluated on the primary symbol with the guard as configured and costs
x1 (x2 for the robustness condition). Every variant is logged to the trial log first, so the
report can state how many trials exist in total.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from paper_lab.analysis.experiment import Criteria, Experiment
from paper_lab.analysis.metrics import (
    Metrics,
    bonferroni_level,
    bootstrap_ci,
    compute_metrics,
    deflated_sharpe,
    sample_label,
    verdict,
)
from paper_lab.analysis.trials import count_trials, log_trials, params_hash, trial_sharpes
from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.config import Config, SetupConfig
from paper_lab.execution.backtest import run_backtest
from paper_lab.runinfo import RunInfo
from paper_lab.setups.base import Setup
from paper_lab.setups.registry import build_setup

Loader = Callable[[str, list[str]], tuple[dict[str, pd.DataFrame], pd.DataFrame]]


@dataclass
class VariantResult:
    symbol: str
    news_guard: str
    cost_mult: float
    metrics: Metrics
    ci: tuple[float, float]
    trades: pd.DataFrame
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentResult:
    exp: Experiment
    label: str
    days: dict[str, list[date]]
    event_days: dict[str, int]
    variants: list[VariantResult]
    grid: list[VariantResult]
    n_trials: int
    decision: str
    decision_reason: str
    deflated_sharpe: float
    bonferroni_ci: tuple[float, float]
    bonferroni_level: float

    def primary(self, guard: str = "config", mult: float = 1.0) -> VariantResult:
        for v in self.variants:
            if (v.symbol, v.news_guard, v.cost_mult) == (self.exp.primary_symbol, guard, mult):
                return v
        raise KeyError((guard, mult))


def decide(c: Criteria, n: int, ci: tuple[float, float], mean_x2: float) -> tuple[str, str]:
    """Pre-registered decision rule. Order matters: sample size first."""
    lo, hi = ci
    if n < c.min_trades:
        return "INCONCLUSIVE", f"{n} trades < pre-registered minimum {c.min_trades}"
    if lo > c.success_ci_low_above and mean_x2 > c.success_mean_x2_above:
        return "SUCCESS", (
            f"CI lower bound {lo:.3f} > {c.success_ci_low_above} and mean R at costs x2 "
            f"{mean_x2:.3f} > {c.success_mean_x2_above}"
        )
    if hi < c.failure_ci_high_below:
        return "FAILURE", f"CI upper bound {hi:.3f} < {c.failure_ci_high_below}"
    return "INCONCLUSIVE", "no demonstrated edge: the CI does not meet the success or failure rule"


def _setups_with(cfg: Config, names: list[str], override: dict[str, float]) -> list[Setup[Any]]:
    out = []
    for n in names:
        sc = cfg.setups[n]
        extra = {k.split(".")[1]: v for k, v in override.items() if k.split(".")[0] == n}
        params = dict(sc.params) | extra
        out.append(build_setup(n, SetupConfig(enabled=True, interval=sc.interval, params=params)))
    return out


def _evaluate(trades: pd.DataFrame, cfg: Config) -> tuple[Metrics, tuple[float, float]]:
    f = trades[trades["status"] == "filled"]
    r = f["r_net"].astype(float).to_numpy()
    a = cfg.analysis
    m = compute_metrics(r, f["exit_reason"].to_numpy(), f["hold_min"].astype(float).to_numpy())
    return m, bootstrap_ci(r, a.bootstrap_samples, a.bootstrap_seed, a.ci_level)


def run_experiment(
    cfg: Config,
    exp: Experiment,
    label: str,
    window: tuple[date, date],
    days_for: Callable[[str, dict[str, pd.DataFrame]], list[date]],
    loader: Loader,
    news: NewsGuard,
    info: RunInfo,
    out_dir: Path,
    trial_log: Path,
) -> ExperimentResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    base_setups = _setups_with(cfg, exp.setups, {})
    intervals = sorted({s.interval for s in base_setups})
    variants: list[VariantResult] = []
    grid: list[VariantResult] = []
    days_by: dict[str, list[date]] = {}
    events: dict[str, int] = {}
    trial_rows: list[dict[str, object]] = []
    params_all = {n: cfg.setups[n].params for n in exp.setups}

    def trial(sym: str, guard: str, mult: float, params: dict[str, Any], m: Metrics) -> None:
        trial_rows.append(
            {
                "logged_utc": info.started_utc,
                "experiment": exp.id if label != "synthetic" else "synthetic-demo",
                "setup": "+".join(exp.setups),
                "symbol": sym,
                "window": exp.window if label != "synthetic" else "synthetic",
                "window_start": window[0].isoformat(),
                "window_end": window[1].isoformat(),
                "news_guard": guard,
                "cost_mult": mult,
                "params_hash": params_hash(params),
                "params_json": json.dumps(params, sort_keys=True, default=str),
                "trades": m.trades,
                "mean_r_net": m.expectancy_r,
                "sharpe_per_trade": m.sharpe_per_trade,
                "run_id": info.run_id,
                "git_commit": info.git_commit,
                "data_hash": info.data_hash,
            }
        )

    for sym in exp.symbols:
        inst = cfg.instrument(sym)
        bars, bars_1h = loader(sym, intervals)
        days = days_for(sym, bars)
        days_by[sym] = days
        events[sym] = sum(1 for d in days if news.is_event_day(d))
        for guard_v in exp.news_guard_variants:
            guard = news if guard_v == "config" and cfg.news_guard.mode != "off" else None
            for mult in exp.cost_mults:
                res = run_backtest(
                    cfg, base_setups, inst, bars, bars_1h, days, guard, news, mult, info.run_id
                )
                stem = f"{inst.file_stem}_guard-{guard_v}_x{mult:g}"
                res.trades.to_csv(out_dir / f"trades_{stem}.csv", index=False)
                res.daily.to_csv(out_dir / f"daily_{stem}.csv", index=False)
                m, ci = _evaluate(res.trades, cfg)
                variants.append(VariantResult(sym, guard_v, mult, m, ci, res.trades, params_all))
                trial(sym, guard_v, mult, params_all, m)
        if sym == exp.primary_symbol and exp.grid:
            keys = sorted(exp.grid)
            guard = news if cfg.news_guard.mode != "off" else None
            for combo in itertools.product(*(exp.grid[k] for k in keys)):
                override = dict(zip(keys, combo, strict=True))
                setups = _setups_with(cfg, exp.setups, override)
                res = run_backtest(
                    cfg, setups, inst, bars, bars_1h, days, guard, news, 1.0, info.run_id
                )
                m, ci = _evaluate(res.trades, cfg)
                p = {n: dict(cfg.setups[n].params) for n in exp.setups}
                for k, v in override.items():
                    s_name, param = k.split(".")
                    p[s_name][param] = v
                grid.append(VariantResult(sym, "config", 1.0, m, ci, res.trades, override))
                trial(sym, "config", 1.0, p, m)

    log_trials(trial_log, trial_rows)
    n_trials = count_trials(trial_log)
    sharpes = [s for s in trial_sharpes(trial_log) if not math.isnan(s)]
    sr_var = float(np.var(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0

    result = ExperimentResult(
        exp,
        label,
        days_by,
        events,
        variants,
        grid,
        n_trials,
        "",
        "",
        float("nan"),
        (float("nan"), float("nan")),
        0.0,
    )
    prim = result.primary()
    f = prim.trades[prim.trades["status"] == "filled"]
    r = f["r_net"].astype(float).to_numpy()
    mean_x2 = result.primary("config", 2.0).metrics.expectancy_r
    result.decision, result.decision_reason = decide(
        exp.criteria, prim.metrics.trades, prim.ci, mean_x2
    )
    result.deflated_sharpe = deflated_sharpe(r, n_trials, sr_var)
    result.bonferroni_level = bonferroni_level(n_trials, 1.0 - cfg.analysis.ci_level)
    result.bonferroni_ci = bootstrap_ci(
        r, cfg.analysis.bootstrap_samples, cfg.analysis.bootstrap_seed, result.bonferroni_level
    )
    return result


def _f(v: float, digits: int = 3) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    return f"{v:.{digits}f}"


CAVEATS = [
    "Data: Yahoo Finance (unofficial, delayed, revisable); GC=F is a continuous front-month "
    "series and roll days are not detected.",
    "Costs: commissions are sourced, spread and slippage are assumptions (docs/cost_model.md); "
    "GC=F prices are traded as Micro Gold, which can trade slightly differently.",
    "Fills are simulated on 5-minute bars with pessimistic rules; the order of prices inside a "
    "bar is unknown.",
    "Universe chosen with hindsight risk: gold, the NY open and the ORB idea come from popular "
    "trading practice, not from a search, but that preference itself cannot be corrected "
    "statistically.",
    "One market regime: a few weeks of data in one period cannot show how the rules behave in "
    "other volatility regimes.",
]


def write_report(res: ExperimentResult, cfg: Config, info: RunInfo, path: Path) -> Path:
    exp = res.exp
    prim = res.primary()
    m = prim.metrics
    a = cfg.analysis
    days = res.days[exp.primary_symbol]
    label = sample_label(m.trades, a.insufficient_below, a.usable_above)
    window_name = "synthetic" if res.label == "synthetic" else exp.window.replace("_", "-")
    v = verdict(prim.ci, m.trades, a.insufficient_below)
    lines = [
        f"# Backtest {exp.id}: {exp.title}",
        "",
        (
            "**SYNTHETIC random-walk data — this page demonstrates the report and says "
            "nothing about gold.**"
            if res.label == "synthetic"
            else f"Experiment file: `experiments/{exp.id}.md` (approved by {exp.approved_by} "
            f"on {exp.approved_on})."
        ),
        "",
        f"**Verdict:** {v}. **Pre-registered decision:** {res.decision} — {res.decision_reason}.",
        "",
        f"**Data:** {window_name} window, {len(days)} trading days "
        f"({days[0] if days else '-'} .. {days[-1] if days else '-'}), 5-minute bars. "
        f"**Sample:** {m.trades} trades on {exp.primary_symbol} → `{label}`.",
        "",
        f"## {exp.primary_symbol}, news guard as configured, costs x1",
        "",
        "| trades | win rate | avg win R | avg loss R | expectancy R | "
        f"{a.ci_level:.0%} CI | profit factor | max DD R | longest losing streak | "
        "time exits | avg hold min |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        f"| {m.trades} | {_f(m.win_rate * 100, 1)}% | {_f(m.avg_win_r)} | {_f(m.avg_loss_r)} | "
        f"{_f(m.expectancy_r)} | [{_f(prim.ci[0])}, {_f(prim.ci[1])}] | {_f(m.profit_factor, 2)} "
        f"| {_f(m.max_drawdown_r, 2)} | {m.longest_losing_streak} | "
        f"{_f(m.time_exit_share * 100, 0)}% | {_f(m.avg_hold_min, 0)} |",
        "",
        "## Variants (expectancy R net per trade)",
        "",
        "| symbol | news guard | costs x | trades | expectancy R | CI |",
        "|---|---|---|---|---|---|",
    ]
    for vr in res.variants:
        lines.append(
            f"| {vr.symbol} | {vr.news_guard} | {vr.cost_mult:g} | {vr.metrics.trades} | "
            f"{_f(vr.metrics.expectancy_r)} | [{_f(vr.ci[0])}, {_f(vr.ci[1])}] |"
        )
    ev = res.event_days[exp.primary_symbol]
    guard_line = f"**News filter on vs off:** {ev} morning-release day(s) in the window."
    if "off" in exp.news_guard_variants:
        off = res.primary("off", 1.0).metrics
        guard_line += (
            f" {exp.primary_symbol}, costs x1: guard as configured {_f(m.expectancy_r)} R over "
            f"{m.trades} trades, guard off {_f(off.expectancy_r)} R over {off.trades} trades "
            f"(difference {_f(m.expectancy_r - off.expectancy_r)} R per trade)."
        )
    if ev < 5:
        guard_line += " Too few release days: the filter's effect cannot be judged on this sample."
    lines += ["", guard_line]
    if res.grid:
        keys = sorted(res.grid[0].params)
        lines += [
            "",
            f"## Parameter grid (in-sample, {exp.primary_symbol}, costs x1)",
            "",
            "| " + " | ".join(keys) + " | trades | expectancy R | CI |",
            "|" + "---|" * (len(keys) + 3),
        ]
        for g in res.grid:
            lines.append(
                "| "
                + " | ".join(f"{g.params[k]:g}" for k in keys)
                + f" | {g.metrics.trades} | {_f(g.metrics.expectancy_r)} | "
                f"[{_f(g.ci[0])}, {_f(g.ci[1])}] |"
            )
    bl, bh = res.bonferroni_ci
    log_name = (
        "trial_log.csv in this folder" if res.label == "synthetic" else "experiments/trial_log.csv"
    )
    lines += [
        "",
        "## Multiple testing",
        "",
        f"Trials logged so far: **{res.n_trials}** (`{log_name}`). "
        f"Deflated Sharpe probability: {_f(res.deflated_sharpe)}. Bonferroni "
        f"{res.bonferroni_level:.2%} CI: [{_f(bl)}, {_f(bh)}]"
        + (" — includes zero." if not (bl > 0 or bh < 0) else "."),
        "",
        "## Caveats",
        "",
        *[f"- {c}" for c in CAVEATS],
        "",
        f"Run `{info.run_id}`, commit `{info.git_commit}`{' (dirty)' if info.git_dirty else ''}, "
        f"strategy hash `{cfg.strategy_hash()}`, data hash `{info.data_hash}`. Trade files: "
        f"`{path.parent.name}/{path.stem}/`.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
