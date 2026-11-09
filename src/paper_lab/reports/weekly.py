"""Weekly forward report (spec Stage F.2-3): ``reports/weekly_<last session of the week>.md``.

Limitations first (spec §7), then the alarm check, cumulative forward results with a
bootstrap CI next to the backtest baseline, the week's trades and data-quality notes.

Alarm rule: forward expectancy below ``forward.alarm_expectancy_r`` after at least
``forward.alarm_min_trades`` forward trades -> ``ALARM: forward result contradicts the
backtest`` and a recommendation to freeze the setup (``paper-lab freeze``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from paper_lab.analysis.freeze import ALARM_TEXT
from paper_lab.analysis.metrics import (
    Metrics,
    bootstrap_ci,
    compute_metrics,
    sample_label,
    suspicious,
)
from paper_lab.analysis.run import CAVEATS
from paper_lab.calendar.sessions import trading_days
from paper_lab.config import Config
from paper_lab.runinfo import RunInfo

FORWARD_CAVEATS = [
    "Forward fills are simulated from the same delayed Yahoo bars with the same pessimistic "
    "rules as the backtest; no order was sent anywhere, these are not broker fills.",
    "Parameters are fixed (strategy hash in the provenance line). This report monitors; it does "
    "not decide. A decision needs a pre-registered experiment or the alarm/freeze rule.",
    "Each session is computed once, right after it completes; later data revisions do not "
    "change journal rows.",
]


@dataclass(frozen=True)
class Line:
    setup: str
    symbol: str
    m: Metrics
    ci: tuple[float, float]
    week_trades: int
    base: Metrics | None
    base_ci: tuple[float, float] | None
    alarm: bool


def week_bounds(cfg: Config, day: date) -> tuple[date, date | None]:
    """(Monday, last exchange session of that week or None)."""
    monday = day - timedelta(days=day.weekday())
    days = trading_days(cfg.session.exchange_calendar, monday, monday + timedelta(days=6))
    return monday, (days[-1] if days else None)


def finished_weeks(cfg: Config, daily: pd.DataFrame, reports_dir: Path) -> list[date]:
    """Last sessions of weeks that are fully in the journal for every instrument and have no
    report yet."""
    if daily.empty:
        return []
    have = set(daily["day_id"].astype(str))
    out: list[date] = []
    seen: set[date] = set()
    for d in sorted({date.fromisoformat(x) for x in daily["day"].astype(str)}):
        _, last = week_bounds(cfg, d)
        if last is None or last in seen:
            continue
        seen.add(last)
        if (
            all(f"{last.isoformat()}|{i.symbol}" in have for i in cfg.instruments)
            and not (reports_dir / f"weekly_{last.isoformat()}.md").exists()
        ):
            out.append(last)
    return out


def load_baseline(cfg: Config, file_stem: str) -> pd.DataFrame | None:
    path = cfg.forward.baseline_dir / f"trades_{file_stem}_guard-config_x1.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    return df[df["status"] == "filled"]


def _f(v: float, d: int = 3) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "n/a" if not (isinstance(v, float) and math.isinf(v)) else "inf"
    return f"{v:.{d}f}"


def _cell(v: object, digits: int) -> str:
    """Table cell: '-' for missing, ``digits`` decimals for floats (-1: integer)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    if isinstance(v, int | float) and digits != 0:
        return f"{v:.0f}" if digits < 0 else f"{v:.{digits}f}"
    return str(v)


def _metrics(df: pd.DataFrame, cfg: Config) -> tuple[Metrics, tuple[float, float]]:
    a = cfg.analysis
    m = compute_metrics(df["r_net"], df["exit_reason"], df["hold_min"])
    return m, bootstrap_ci(df["r_net"], a.bootstrap_samples, a.bootstrap_seed, a.ci_level)


def build_lines(cfg: Config, trades: pd.DataFrame, monday: date, week_end: date) -> list[Line]:
    f = cfg.forward
    filled = trades[trades["status"] == "filled"] if len(trades) else trades
    if len(filled):
        filled = filled[filled["day"].astype(str) <= week_end.isoformat()]
    setups = sorted({n for n, s in cfg.setups.items() if s.enabled} | set(filled.get("setup", [])))
    lines: list[Line] = []
    for setup in setups:
        for inst in cfg.instruments:
            df = filled[(filled["setup"] == setup) & (filled["symbol"] == inst.symbol)]
            m, ci = _metrics(df, cfg)
            week = df[df["day"].astype(str) >= monday.isoformat()]
            base = load_baseline(cfg, inst.file_stem)
            bm = bci = None
            if base is not None:
                bdf = base[base["setup"] == setup]
                bm, bci = _metrics(bdf, cfg)
            # R is journaled to 6 decimals: compare at that precision (20 x -0.1 is not < -0.1)
            alarm = (
                m.trades >= f.alarm_min_trades and round(m.expectancy_r, 6) < f.alarm_expectancy_r
            )
            lines.append(Line(setup, inst.symbol, m, ci, len(week), bm, bci, alarm))
    return lines


def write_weekly(
    cfg: Config,
    week_end: date,
    trades: pd.DataFrame,
    daily: pd.DataFrame,
    bar_counts: dict[str, dict[date, tuple[int, int]]],
    info: RunInfo,
    path: Path,
) -> Path:
    f = cfg.forward
    a = cfg.analysis
    monday, _ = week_bounds(cfg, week_end)
    first_day = max(monday, f.start)
    days = trading_days(cfg.session.exchange_calendar, first_day, week_end)
    all_days = trading_days(cfg.session.exchange_calendar, f.start, week_end)
    lines = build_lines(cfg, trades, monday, week_end)
    out = [
        f"# Weekly forward report — week {monday} .. {week_end}",
        "",
        "Paper forward test: simulated fills, no orders, no real money.",
        "",
        "## Limitations (read first)",
        "",
        *[f"- {c}" for c in FORWARD_CAVEATS + CAVEATS],
        "",
        "## Alarm check",
        "",
        f"Rule: forward expectancy below {f.alarm_expectancy_r:+.2f} R after at least "
        f"{f.alarm_min_trades} forward trades.",
        "",
    ]
    for ln in lines:
        tag = f"`{ln.setup}` {ln.symbol}"
        if ln.alarm:
            out.append(
                f"- **{ALARM_TEXT}** — {tag}: {_f(ln.m.expectancy_r)} R over {ln.m.trades} "
                f"forward trades. Recommendation: freeze it (`uv run paper-lab freeze --setup "
                f'{ln.setup} --reference {path.as_posix()} --reason "forward alarm"`).'
            )
        elif ln.m.trades < f.alarm_min_trades:
            out.append(
                f"- {tag}: not evaluated yet ({ln.m.trades} < {f.alarm_min_trades} forward trades)."
            )
        else:
            out.append(f"- {tag}: no alarm ({_f(ln.m.expectancy_r)} R over {ln.m.trades} trades).")
    out += [
        "",
        f"## Forward results since {f.start} ({len(all_days)} sessions; costs x1, news guard "
        "as configured)",
        "",
        f"Baseline: {f.baseline_experiment} in-sample backtest (`{f.baseline_dir.as_posix()}`).",
        "",
        f"| setup | symbol | trades (this week) | expectancy R | {a.ci_level:.0%} CI | sample | "
        f"{f.baseline_experiment} trades | {f.baseline_experiment} expectancy R | "
        f"{f.baseline_experiment} CI | forward - backtest |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for ln in lines:
        label = sample_label(ln.m.trades, a.insufficient_below, a.usable_above)
        if ln.base is not None and ln.base_ci is not None:
            diff = ln.m.expectancy_r - ln.base.expectancy_r if ln.m.trades else float("nan")
            base_cols = (
                f"{ln.base.trades} | {_f(ln.base.expectancy_r)} | "
                f"[{_f(ln.base_ci[0])}, {_f(ln.base_ci[1])}] | {_f(diff)}"
            )
        else:
            base_cols = "n/a | n/a | n/a | n/a"
        out.append(
            f"| {ln.setup} | {ln.symbol} | {ln.m.trades} ({ln.week_trades}) | "
            f"{_f(ln.m.expectancy_r)} | [{_f(ln.ci[0])}, {_f(ln.ci[1])}] | {label} | {base_cols} |"
        )
    notes = [
        f"`{ln.setup}` {ln.symbol}: {n}" for ln in lines for n in suspicious(ln.m, len(all_days))
    ]
    out += [f"\n**SUSPICIOUS:** {n}." for n in notes]
    week_trades = (
        trades[
            (trades["day"].astype(str) >= monday.isoformat())
            & (trades["day"].astype(str) <= week_end.isoformat())
        ]
        if len(trades)
        else trades
    )
    out += ["", "## This week's signals", ""]
    if len(week_trades) == 0:
        out.append("No signal this week.")
    else:
        out += [
            "| day | setup | symbol | status | side | qty | entry | exit | exit reason | R net |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for t in week_trades.sort_values(["day", "symbol"]).to_dict("records"):
            out.append(
                "| "
                + " | ".join(
                    _cell(t[c], d)
                    for c, d in (
                        ("day", 0),
                        ("setup", 0),
                        ("symbol", 0),
                        ("status", 0),
                        ("side", 0),
                        ("qty", -1),
                        ("entry", 2),
                        ("exit", 2),
                        ("exit_reason", 0),
                        ("r_net", 3),
                    )
                )
                + " |"
            )
    out += ["", "## Data quality this week", ""]
    have = set(daily["day_id"].astype(str)) if len(daily) else set()
    for inst in cfg.instruments:
        counts = bar_counts.get(inst.symbol, {})
        short = []
        for d in days:
            n, e = counts.get(d, (0, 0))
            if n < e:
                short.append(f"{d} ({n}/{e} bars)")
        missing = [str(d) for d in days if f"{d.isoformat()}|{inst.symbol}" not in have]
        dd = daily[
            (daily["symbol"] == inst.symbol) & (daily["day"].astype(str).isin(map(str, days)))
        ]
        skipped = [f"{r.day}: {r.setups}" for r in dd.itertuples(index=False) if r.signals == 0]
        out.append(
            f"- {inst.symbol}: {len(days) - len(missing)}/{len(days)} sessions in the journal"
            + (f"; not processed: {', '.join(missing)}" if missing else "")
            + (
                f"; incomplete sessions: {', '.join(short)}"
                if short
                else "; all session bars present"
            )
            + "."
        )
        out += [f"  - no signal {s}" for s in skipped]
    out += [
        "",
        f"Run `{info.run_id}`, commit `{info.git_commit}`"
        f"{' (dirty)' if info.git_dirty else ''}, strategy hash `{cfg.strategy_hash()}`, data "
        f"hash `{info.data_hash}`. Journal: `journal/trades.csv`, `journal/daily.csv`.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    return path
