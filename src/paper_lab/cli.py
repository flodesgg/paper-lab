"""Command-line entry point: `paper-lab <command>`.

Commands
  check-config         validate config.yaml and print run metadata
  fetch --update       fetch new bars, append to data_archive/, rebuild manifest + cache
  fetch --backfill     same, but request the provider's full lookback window
  validate             data-quality report into reports/ (exit 1 if any error)
  calendar             validate the event calendar, show upcoming events
  signals              run enabled setups over the archive -> reports/signals/ (no P&L)
  costs --synthetic    cost sensitivity (costs x0/x1/x2) on synthetic data (pipeline demo)
  backtest --synthetic one-page backtest report on synthetic data (pipeline demo)
  backtest --experiment F   run approved, pre-registered experiment F on the real archive
  forward              daily forward paper loop: fetch, new sessions -> journal/, weekly report
  freeze               disable a setup that failed (experiment FAILURE or forward ALARM)
  agent-plist          print the launchd LaunchAgent (installed by scripts/agent.sh)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import shutil
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

from paper_lab import __version__
from paper_lab.analysis.experiment import (
    Criteria,
    Experiment,
    ExperimentError,
    check_runnable,
    load_experiment,
)
from paper_lab.analysis.freeze import FreezeError, freeze, frozen_violations
from paper_lab.analysis.run import Loader, run_experiment, write_report
from paper_lab.analysis.trials import OosLockedError, open_oos
from paper_lab.calendar.events import load_events
from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.calendar.sessions import session_window, trading_days
from paper_lab.config import Config, load_config
from paper_lab.data.archive import archive_hash, load_archive
from paper_lab.data.fetch import BarSource, YFinanceSource, run_update
from paper_lab.data.synthetic import resample_bars, synthetic_bars
from paper_lab.data.validate import overall_status, validate_series, write_reports
from paper_lab.execution.backtest import run_backtest
from paper_lab.execution.sensitivity import summarize, table
from paper_lab.live.agent import CommitError, commit_outputs, launch_agent_plist, push
from paper_lab.live.forward import (
    ForwardBusyError,
    forward_lock,
    log_run,
    run_forward,
    session_bar_counts,
)
from paper_lab.logging_setup import setup_logging
from paper_lab.reports.weekly import finished_weeks, write_weekly
from paper_lab.runinfo import RunInfo, new_run_info
from paper_lab.setups.registry import build_all
from paper_lab.setups.runner import results_to_frames, run_setup, session_days


def _source_factory() -> BarSource:
    """Indirection so tests can replace the network source."""
    return YFinanceSource()


def _start(args: argparse.Namespace) -> tuple[Config, RunInfo]:
    cfg, chash = load_config(args.config)
    info = new_run_info(chash, data_hash=archive_hash(cfg.paths.data_archive))
    log = setup_logging(cfg.logging, cfg.paths.logs, info.run_id)
    # provenance in the log file too: unattended runs lose stdout
    log.info("run start command=%s %s", args.command, json.dumps(info.as_dict()))
    return cfg, info


def _frozen_refusal(cfg: Config) -> bool:
    """True (and a message) if a frozen setup is enabled without a new experiment."""
    problems = frozen_violations(cfg, cfg.paths.experiments)
    for p in problems:
        print(f"refused: {p}", file=sys.stderr)
    return bool(problems)


def _cmd_check_config(args: argparse.Namespace) -> int:
    cfg, info = _start(args)
    build_all(cfg, enabled_only=False)  # every setup's params must validate too
    if _frozen_refusal(cfg):
        return 1
    # strategy_hash: what an experiment file must pin (see experiments/README.md)
    print(json.dumps(info.as_dict() | {"strategy_hash": cfg.strategy_hash()}, indent=2))
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    cfg, info = _start(args)
    as_of = pd.Timestamp.now(tz="UTC")
    stats = run_update(
        cfg,
        _source_factory(),
        as_of,
        symbols=args.symbols,
        intervals=args.intervals,
        full=args.backfill,
    )
    summary = {
        "run": info.as_dict(),
        "as_of_utc": as_of.isoformat(timespec="seconds"),
        "data_hash_after": archive_hash(cfg.paths.data_archive),
        "series": [
            {k: v for k, v in asdict(s).items() if k != "files_written"}
            | {"files_written": len(s.files_written)}
            for s in stats
        ],
    }
    logging.getLogger("paper_lab").info("fetch summary %s", json.dumps(summary))
    print(json.dumps(summary, indent=2))
    failed = [f"{s.symbol} {s.interval}" for s in stats if s.error]
    if failed:
        print(f"FAILED series: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    cfg, info = _start(args)
    reports = []
    for inst in cfg.instruments:
        for interval in cfg.data.archive_intervals:
            df = load_archive(cfg.paths.data_archive, inst.file_stem, interval)
            reports.append(validate_series(df, inst, interval, cfg))
    day = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    meta: dict[str, object] = info.as_dict() | {"date": day}
    md, js = write_reports(reports, meta, cfg.paths.reports, day)
    status = overall_status(reports)
    print(f"{status}: {md} {js}")
    for r in reports:
        print(
            f"  {r.symbol:5} {r.interval:3} rows={r.rows:6d} days={r.trading_days_covered:3d} "
            f"{r.status}"
        )
    return 1 if status == "FAIL" else 0


def _cmd_calendar(args: argparse.Namespace) -> int:
    cfg, _ = _start(args)
    events = load_events(cfg.calendar.events_files)
    guard = NewsGuard(cfg.news_guard, cfg.session, events)
    counts = Counter((e.ts_et.year, e.event, e.status) for e in events)
    print("events by year / type / status:")
    for (y, ev, st), n in sorted(counts.items()):
        print(f"  {y} {ev:4} {st:10} {n}")
    today = pd.Timestamp.now(tz=cfg.session.timezone).date()
    upcoming = [e for e in events if e.day >= today][: args.upcoming]
    print(f"next {len(upcoming)} events:")
    for e in upcoming:
        w = session_window(cfg.session, e.day)
        note = (
            "no session"
            if w is None
            else (
                "open guard day"
                if guard.is_event_day(e.day)
                else "after session close"
                if e.ts_utc >= w.close_utc
                else "in session"
            )
        )
        print(f"  {e.ts_et:%Y-%m-%d %a %H:%M} ET  {e.event:4} {e.status:9} -> {note}")
    stem = cfg.instruments[0].file_stem
    bars = load_archive(cfg.paths.data_archive, stem, "5m")
    if len(bars):
        first = bars.index.min().tz_convert(cfg.session.timezone).date()
        last = bars.index.max().tz_convert(cfg.session.timezone).date()
        days = trading_days(cfg.session.exchange_calendar, first, last)
        ev_days = [d for d in days if guard.is_event_day(d)]
        print(
            f"{stem} 5m archive {first}..{last}: {len(days)} trading days, "
            f"{len(ev_days)} with a morning release: {', '.join(map(str, ev_days))}"
        )
    return 0


def _cmd_signals(args: argparse.Namespace) -> int:
    """Signals only. Trade outcomes are deliberately NOT computed here: results are produced
    only by a pre-registered experiment (Stage E)."""
    cfg, info = _start(args)
    log = logging.getLogger("paper_lab")
    if _frozen_refusal(cfg):
        return 2
    enabled = build_all(cfg)
    names = args.setups or sorted(enabled)
    for n in names:
        if n not in cfg.setups:
            print(f"unknown setup {n!r}; configured: {sorted(cfg.setups)}", file=sys.stderr)
            return 2
        if n not in enabled:
            print(
                f"setup {n!r} is disabled in config.yaml; it is enabled only through an "
                "approved experiment",
                file=sys.stderr,
            )
            return 2
    symbols = args.symbols or [i.symbol for i in cfg.instruments]
    guard = None
    if args.guard == "config" and cfg.news_guard.mode != "off":
        guard = NewsGuard(cfg.news_guard, cfg.session, load_events(cfg.calendar.events_files))
    out_dir = cfg.paths.reports / "signals"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    meta: dict[str, object] = info.as_dict() | {
        "guard": "config" if guard is not None else "off",
        "runs": [],
    }
    for n in names:
        setup = enabled[n]
        for sym in symbols:
            inst = cfg.instrument(sym)
            bars = load_archive(cfg.paths.data_archive, inst.file_stem, setup.interval)
            bars_1h = load_archive(cfg.paths.data_archive, inst.file_stem, "1h")
            days, incomplete = session_days(bars, cfg.session, setup.interval)
            results = run_setup(
                setup,
                instrument=inst,
                session=cfg.session,
                bars=bars,
                bars_1h=bars_1h,
                days=days,
                guard=guard,
            )
            sig, per_day = results_to_frames(results)
            tag = "" if guard is not None else "_noguard"
            base = out_dir / f"{n}_{inst.file_stem}{tag}_{stamp}"
            sig.to_csv(f"{base}_signals.csv", index=False)
            per_day.to_csv(f"{base}_days.csv", index=False)
            status = Counter(r.status for r in results)
            reasons = Counter(r.reason.split(":")[0] for r in results if r.status == "skipped")
            n_sig = sum(len(r.signals) for r in results)
            n_blk = sum(len(r.blocked) for r in results)
            n_rej = sum(len(r.rejected) for r in results)
            span = f"{days[0]}..{days[-1]}" if days else "no data"
            line = (
                f"{n} {sym} {setup.interval} {span}: days={len(days)} "
                f"scanned={status['scanned']} skipped={status['skipped']} "
                f"closed={status['closed']} signals={n_sig} guard_blocked={n_blk} "
                f"filter_rejected_bars={n_rej}"
            )
            print(line)
            if incomplete:
                print(
                    f"    left out, data ends before session close: {[str(d) for d in incomplete]}"
                )
            for reason, k in reasons.most_common():
                print(f"    skipped {k:3d}x  {reason}")
            print(f"    -> {base}_signals.csv, {base}_days.csv")
            log.info("signals %s", line)
            runs = meta["runs"]
            assert isinstance(runs, list)
            runs.append(
                {
                    "setup": n,
                    "symbol": sym,
                    "params": cfg.setups[n].params,
                    "days": len(days),
                    "signals": n_sig,
                    "files": [f"{base}_signals.csv", f"{base}_days.csv"],
                }
            )
    tag = "" if guard is not None else "_noguard"
    (out_dir / f"run{tag}_{stamp}.json").write_text(json.dumps(meta, indent=2, default=str))
    return 0


SYNTH_DAYS = ("2026-06-01", "2026-10-30")
SYNTH_BASE = {"GC=F": (4000.0, 11), "GLD": (370.0, 12)}


def _cmd_costs(args: argparse.Namespace) -> int:
    cfg, info = _start(args)
    log = logging.getLogger("paper_lab")
    setups = list(build_all(cfg).values())
    symbols = args.symbols or [i.symbol for i in cfg.instruments]
    events = load_events(cfg.calendar.events_files)
    news = NewsGuard(cfg.news_guard, cfg.session, events)
    guard = news if cfg.news_guard.mode != "off" else None
    label = "synthetic"  # on real data, cost sensitivity is part of `backtest --experiment`
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    out_dir = cfg.paths.reports / "cost_sensitivity" / f"{label}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for sym in symbols:
        inst = cfg.instrument(sym)
        intervals = sorted({s.interval for s in setups})
        bars, bars_1h = _synthetic_loader(cfg)(sym, intervals)
        run_days = _synthetic_days(cfg)
        for mult in (0.0, 1.0, 2.0):
            res = run_backtest(
                cfg, setups, inst, bars, bars_1h, run_days, guard, news, mult, info.run_id
            )
            res.trades.to_csv(out_dir / f"trades_{inst.file_stem}_x{mult:g}.csv", index=False)
            res.daily.to_csv(out_dir / f"daily_{inst.file_stem}_x{mult:g}.csv", index=False)
            rows.append({"symbol": sym, "mult": f"{mult:g}", **summarize(res.trades)})
        span = f"{run_days[0]}..{run_days[-1]}" if run_days else "no days"
        log.info("costs %s %s %s days=%d", label, sym, span, len(run_days))
    header = (
        "SYNTHETIC random-walk data: this demonstrates the pipeline and says NOTHING about gold."
    )
    body = table(rows)
    report = out_dir / "summary.md"
    report.write_text(
        f"# Cost sensitivity ({label}, {stamp})\n\n{header}\n\n"
        f"Setups: {', '.join(s.name for s in setups)}. Run {info.run_id}, commit "
        f"{info.git_commit}{' (dirty)' if info.git_dirty else ''}, config {info.config_hash}, "
        f"data {info.data_hash}.\n\n{body}\n\nNo confidence intervals here (Stage E).\n"
    )
    (out_dir / "run.json").write_text(json.dumps(info.as_dict() | {"label": label}, indent=2))
    print(header)
    print(body)
    print(f"-> {report}")
    return 0


def _date(s: str) -> date:
    return date.fromisoformat(s)


def _synthetic_days(cfg: Config) -> list[date]:
    days = trading_days(cfg.session.exchange_calendar, *map(_date, SYNTH_DAYS))
    return days[25:]  # the first weeks only build ATR / volume history


def _synthetic_loader(cfg: Config) -> Loader:
    tz = cfg.session.timezone

    def load(sym: str, intervals: list[str]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
        days = trading_days(cfg.session.exchange_calendar, *map(_date, SYNTH_DAYS))
        base, seed = SYNTH_BASE.get(sym, (100.0, 13))
        b5 = synthetic_bars(days, tz, base=base, seed=seed)
        bars = {
            iv: b5 if iv == "5m" else resample_bars(b5, iv, tz, cfg.session.start)
            for iv in intervals
        }
        return bars, resample_bars(b5, "1h", tz, cfg.session.start)

    return load


def _archive_loader(cfg: Config) -> Loader:
    def load(sym: str, intervals: list[str]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
        stem = cfg.instrument(sym).file_stem
        bars = {iv: load_archive(cfg.paths.data_archive, stem, iv) for iv in intervals}
        return bars, load_archive(cfg.paths.data_archive, stem, "1h")

    return load


def _demo_experiment(cfg: Config) -> Experiment:
    return Experiment(
        id="EXP-000",
        title="pipeline demo on synthetic data",
        status="approved",
        approved_by="demo",
        approved_on=date(2026, 11, 9),
        strategy_hash=cfg.strategy_hash(),
        setups=sorted(build_all(cfg)),
        symbols=[i.symbol for i in cfg.instruments],
        primary_symbol=next(i.symbol for i in cfg.instruments if i.role == "primary"),
        window="in_sample",
        news_guard_variants=["config", "off"],
        cost_mults=[0.0, 1.0, 2.0],
        grid={"orb.k": [1.5, 2.0, 3.0], "orb.min_range_atr": [0.05, 0.10, 0.15]},
        criteria=Criteria(
            min_trades=30,
            success_ci_low_above=0.0,
            success_mean_x2_above=0.0,
            failure_ci_high_below=0.0,
        ),
    )


def _cmd_backtest(args: argparse.Namespace) -> int:
    cfg, info = _start(args)
    log = logging.getLogger("paper_lab")
    events = load_events(cfg.calendar.events_files)
    news = NewsGuard(cfg.news_guard, cfg.session, events)
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    reports = cfg.paths.reports
    if args.synthetic:
        exp = _demo_experiment(cfg)
        days = _synthetic_days(cfg)
        name = f"backtest_synthetic_{stamp}"
        out_dir = reports / name
        res = run_experiment(
            cfg,
            exp,
            "synthetic",
            (days[0], days[-1]),
            lambda sym, bars: days,
            _synthetic_loader(cfg),
            news,
            info,
            out_dir,
            out_dir / "trial_log.csv",
        )
    else:
        if _frozen_refusal(cfg):
            return 2
        try:
            exp, _ = load_experiment(args.experiment)
            check_runnable(exp, cfg)
        except (ExperimentError, ValueError) as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        w = cfg.analysis.in_sample if exp.window == "in_sample" else cfg.analysis.out_of_sample
        exp_dir = cfg.paths.experiments
        if exp.window == "out_of_sample":
            try:
                open_oos(
                    exp_dir / "oos_access.csv",
                    exp.id,
                    {
                        "opened_utc": info.started_utc,
                        "run_id": info.run_id,
                        "git_commit": info.git_commit,
                        "window_start": w.start.isoformat(),
                        "window_end": w.end.isoformat(),
                    },
                )
            except OosLockedError as e:
                print(f"refused: {e}", file=sys.stderr)
                return 2
            log.warning("OOS window opened by %s (run %s)", exp.id, info.run_id)

        def window_days(sym: str, bars: dict[str, pd.DataFrame]) -> list[date]:
            iv = sorted(bars)[0]
            complete, _ = session_days(bars[iv], cfg.session, iv)
            return [d for d in complete if w.start <= d <= w.end]

        name = f"backtest_{stamp}_{exp.id}"
        out_dir = reports / name
        res = run_experiment(
            cfg,
            exp,
            exp.id,
            (w.start, w.end),
            window_days,
            _archive_loader(cfg),
            news,
            info,
            out_dir,
            exp_dir / "trial_log.csv",
        )
    report = write_report(res, cfg, info, reports / f"{name}.md")
    (out_dir / "run.json").write_text(
        json.dumps(info.as_dict() | {"experiment": exp.id, "decision": res.decision}, indent=2)
    )
    log.info("backtest %s decision=%s report=%s", exp.id, res.decision, report)
    print(report.read_text(encoding="utf-8"))
    print(f"-> {report}")
    return 0


def _cmd_forward(args: argparse.Namespace) -> int:
    cfg, info = _start(args)
    log = logging.getLogger("paper_lab")
    if _frozen_refusal(cfg):
        return 2
    repo = Path(".").resolve()
    as_of = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp.now(tz="UTC")
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")
    try:
        with forward_lock(cfg.paths.data_cache / "forward.lock"):
            fetch_errors: list[str] = []
            if not args.no_fetch:
                stats = run_update(cfg, _source_factory(), as_of)
                fetch_errors = [f"{s.symbol} {s.interval}" for s in stats if s.error]
                for e in fetch_errors:
                    log.error("forward: fetch failed for %s; using the archive as it is", e)
            # provenance must name the data after this run's fetch, not before it
            info = dataclasses.replace(info, data_hash=archive_hash(cfg.paths.data_archive))
            events = load_events(cfg.calendar.events_files)
            news = NewsGuard(cfg.news_guard, cfg.session, events)
            res = run_forward(cfg, info, news, _archive_loader(cfg), as_of)
            daily_path = cfg.paths.journal / "daily.csv"
            daily = pd.read_csv(daily_path) if daily_path.exists() else pd.DataFrame()
            trades_path = cfg.paths.journal / "trades.csv"
            trades = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame()
            weekly: list[Path] = []
            iv = min((s.interval for s in build_all(cfg).values()), default="5m")
            for week_end in finished_weeks(cfg, daily, cfg.paths.reports):
                if week_end > as_of.tz_convert(cfg.session.timezone).date():
                    continue
                monday = week_end - pd.Timedelta(days=week_end.weekday())
                days = trading_days(cfg.session.exchange_calendar, monday, week_end)
                counts = {
                    sym: session_bar_counts(cfg, bars, days, iv) for sym, bars in res.bars.items()
                }
                path = cfg.paths.reports / f"weekly_{week_end.isoformat()}.md"
                weekly.append(write_weekly(cfg, week_end, trades, daily, counts, info, path))
            row: dict[str, object] = {
                "run_id": info.run_id,
                "started_utc": info.started_utc,
                "as_of_utc": as_of.isoformat(),
                "git_commit": info.git_commit,
                "strategy_hash": cfg.strategy_hash(),
                "data_hash": info.data_hash,
                "fetch_errors": ";".join(fetch_errors),
                "days_processed": res.days_processed,
                "trades": res.trades,
                "waiting": ";".join(f"{s.symbol}:{d}" for s in res.symbols for d in s.waiting),
                "weekly_reports": ";".join(p.name for p in weekly),
            }
            log_run(cfg.paths.journal / "runs.csv", row)
            for s in res.symbols:
                print(
                    f"{s.symbol}: processed {[str(d) for d in s.processed] or 'nothing new'}, "
                    f"{s.trades} trade(s)"
                    + (f"; waiting for data: {[str(d) for d in s.waiting]}" if s.waiting else "")
                )
            for p in weekly:
                print(f"weekly report -> {p}")
            rc = 1 if fetch_errors else 0
            if args.commit:
                msg = (
                    f"forward {as_of.tz_convert(cfg.session.timezone).date()}: "
                    f"{res.days_processed} session(s), {res.trades} trade(s) (run {info.run_id})"
                )
                try:
                    commit = commit_outputs(repo, msg)
                except (CommitError, OSError) as e:
                    log.error("forward: commit failed: %s", e)
                    print(f"commit failed: {e}", file=sys.stderr)
                    return 1
                print(f"commit: {commit or 'nothing to commit'}")
                if commit and args.push:
                    ok, out = push(repo)
                    print("push: ok" if ok else f"push FAILED (retried next run): {out}")
                    if not ok:
                        log.error("forward: push failed: %s", out)
                        rc = 1
            log.info(
                "forward done: %d session(s), %d trade(s), weekly=%s",
                res.days_processed,
                res.trades,
                [p.name for p in weekly],
            )
            return rc
    except ForwardBusyError as e:
        print(str(e))
        return 0


def _cmd_freeze(args: argparse.Namespace) -> int:
    cfg, info = _start(args)
    today = pd.Timestamp.now(tz=cfg.session.timezone).date()
    try:
        path = freeze(
            args.config,
            cfg.paths.experiments,
            Path("."),
            args.setup,
            args.reference,
            args.reason,
            info.git_commit,
            today,
        )
    except FreezeError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    msg = f"Freeze {args.setup} ({args.reference}): {args.reason}"
    print(f"{args.setup} disabled in {args.config}; recorded in {path}. Commit with:")
    print(f'  git commit -m "{msg}" -- {args.config} {path}')
    return 0


def _cmd_agent_plist(args: argparse.Namespace) -> int:
    cfg, _ = load_config(args.config)
    uv = args.uv or shutil.which("uv")
    if not uv:
        print("uv not found on PATH; pass --uv /path/to/uv", file=sys.stderr)
        return 2
    sys.stdout.write(launch_agent_plist(Path("."), Path(uv), cfg.forward.schedule_local).decode())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paper-lab", description="Paper trading research lab")
    p.add_argument("--version", action="version", version=f"paper-lab {__version__}")
    p.add_argument("--config", type=Path, default=Path("config.yaml"))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="validate config.yaml and print run metadata")
    f = sub.add_parser("fetch", help="fetch bars into the archive (idempotent)")
    mode = f.add_mutually_exclusive_group(required=True)
    mode.add_argument("--update", action="store_true", help="from the archive tail onward")
    mode.add_argument("--backfill", action="store_true", help="full provider lookback")
    f.add_argument("--symbols", nargs="+", help="subset of configured symbols")
    f.add_argument("--intervals", nargs="+", help="subset of configured intervals")
    sub.add_parser("validate", help="write a data-quality report (exit 1 on errors)")
    c = sub.add_parser("calendar", help="validate the event calendar and show upcoming events")
    c.add_argument("--upcoming", type=int, default=8)
    s = sub.add_parser("signals", help="run enabled setups over the archive (signals only)")
    s.add_argument("--setups", nargs="+", help="subset of enabled setups")
    s.add_argument("--symbols", nargs="+", help="subset of configured symbols")
    k = sub.add_parser("costs", help="cost sensitivity x0/x1/x2")
    k.add_argument("--synthetic", action="store_true", required=True, help="synthetic data")
    k.add_argument("--symbols", nargs="+", help="subset of configured symbols")
    bt = sub.add_parser("backtest", help="one-page backtest report")
    bsrc = bt.add_mutually_exclusive_group(required=True)
    bsrc.add_argument("--synthetic", action="store_true", help="synthetic data (demo)")
    bsrc.add_argument("--experiment", type=Path, help="approved experiment file (real data)")
    fw = sub.add_parser("forward", help="daily forward paper loop (idempotent, catches up)")
    fw.add_argument("--no-fetch", action="store_true", help="use the archive as it is")
    fw.add_argument("--commit", action="store_true", help="commit archive/journal/weekly")
    fw.add_argument("--push", action="store_true", help="push after committing")
    fw.add_argument("--as-of", help="simulated 'now' (UTC ISO time), for replays and tests")
    fz = sub.add_parser("freeze", help="disable a setup that failed its criteria")
    fz.add_argument("--setup", required=True)
    fz.add_argument("--reference", required=True, help="EXP-NNN (FAILURE) or weekly report")
    fz.add_argument("--reason", required=True)
    ap = sub.add_parser("agent-plist", help="print the launchd LaunchAgent plist")
    ap.add_argument("--uv", help="path to uv (default: from PATH)")
    s.add_argument(
        "--guard",
        choices=["config", "off"],
        default="config",
        help="news guard as configured or off",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "check-config": _cmd_check_config,
        "forward": _cmd_forward,
        "freeze": _cmd_freeze,
        "agent-plist": _cmd_agent_plist,
        "fetch": _cmd_fetch,
        "validate": _cmd_validate,
        "calendar": _cmd_calendar,
        "signals": _cmd_signals,
        "costs": _cmd_costs,
        "backtest": _cmd_backtest,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
