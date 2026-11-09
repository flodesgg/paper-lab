"""Command-line entry point: `paper-lab <command>`.

Commands
  check-config         validate config.yaml and print run metadata
  fetch --update       fetch new bars, append to data_archive/, rebuild manifest + cache
  fetch --backfill     same, but request the provider's full lookback window
  validate             data-quality report into reports/ (exit 1 if any error)
  calendar             validate the event calendar, show upcoming events
  signals              run enabled setups over the archive -> reports/signals/ (no P&L)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from paper_lab import __version__
from paper_lab.calendar.events import load_events
from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.calendar.sessions import session_window, trading_days
from paper_lab.config import Config, load_config
from paper_lab.data.archive import archive_hash, load_archive
from paper_lab.data.fetch import BarSource, YFinanceSource, run_update
from paper_lab.data.validate import overall_status, validate_series, write_reports
from paper_lab.logging_setup import setup_logging
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


def _cmd_check_config(args: argparse.Namespace) -> int:
    cfg, info = _start(args)
    build_all(cfg, enabled_only=False)  # every setup's params must validate too
    print(json.dumps(info.as_dict(), indent=2))
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
        "fetch": _cmd_fetch,
        "validate": _cmd_validate,
        "calendar": _cmd_calendar,
        "signals": _cmd_signals,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
