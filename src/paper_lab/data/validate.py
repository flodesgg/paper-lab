"""Data-quality checks for archived bars. Reports problems; never modifies or fills data.

Severity:
  * ``error``   - the data cannot be trusted as-is (duplicates, broken OHLC, timezone
                  mistakes, missing trading days). ``paper-lab validate`` exits non-zero.
  * ``warning`` - usable but must be known (zero volume, gaps, absurd candles).
  * ``info``    - context (coverage, flat zero-volume bars).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from paper_lab.calendar.sessions import exchange_close_utc, trading_days, window_utc
from paper_lab.config import Config, InstrumentConfig
from paper_lab.data.bars import COLUMNS, PRICE_COLUMNS, interval_delta, interval_minutes

Severity = Literal["error", "warning", "info"]
ET = "America/New_York"
MAX_EXAMPLES = 5
TZ_MISTAKE_SHARE = 0.05  # >5% of bars outside trading hours => likely timezone mistake


@dataclass
class Issue:
    check: str
    severity: Severity
    count: int
    message: str
    examples: list[str] = field(default_factory=list)


@dataclass
class SeriesReport:
    symbol: str
    interval: str
    rows: int
    first_ts_utc: str
    last_ts_utc: str
    trading_days_covered: int
    issues: list[Issue] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(i.severity == "error" for i in self.issues):
            return "FAIL"
        if any(i.severity == "warning" for i in self.issues):
            return "WARN"
        return "PASS"


def _fmt(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _examples(idx: pd.Index) -> list[str]:
    return [_fmt(t) if isinstance(t, pd.Timestamp) else str(t) for t in list(idx[:MAX_EXAMPLES])]


def _in_hours(ts: pd.Timestamp, start: time, end: time) -> bool:
    """Is a bar starting at ``ts`` (ET) inside the instrument's weekly trading hours?

    Day sessions (start < end) trade Mon-Fri. Wrapping sessions (e.g. COMEX 18:00-17:00)
    open Sunday at ``start`` and close Friday at ``end``, with a daily break [end, start).
    """
    t, wd = ts.time(), ts.weekday()  # Mon=0 .. Sun=6
    if start < end:
        return wd < 5 and start <= t < end
    if wd == 5:
        return False
    if wd == 6:
        return t >= start
    if wd == 4:
        return t < end
    return t >= start or t < end


def _session_bounds(cfg: Config, day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    s, e = window_utc(day, cfg.session.start, cfg.session.end, cfg.session.timezone)
    return s, min(e, exchange_close_utc(cfg.session.exchange_calendar, day))


def validate_series(
    df: pd.DataFrame, inst: InstrumentConfig, interval: str, cfg: Config
) -> SeriesReport:
    rep = SeriesReport(
        symbol=inst.symbol,
        interval=interval,
        rows=len(df),
        first_ts_utc=_fmt(df.index.min()) if len(df) else "",
        last_ts_utc=_fmt(df.index.max()) if len(df) else "",
        trading_days_covered=0,
    )
    add = rep.issues.append
    if df.empty:
        add(Issue("empty", "error", 0, "no bars in archive"))
        return rep

    # ---- structural ------------------------------------------------------------
    if list(df.columns) != list(COLUMNS):
        add(Issue("columns", "error", 1, f"unexpected columns {list(df.columns)}"))
        return rep
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None or str(idx.tz) != "UTC":
        add(Issue("timezone", "error", 1, f"index must be tz-aware UTC, got {idx.dtype}"))
        return rep
    if not idx.is_monotonic_increasing:
        bad = idx[1:][np.diff(idx.asi8) < 0]
        add(Issue("non_monotonic", "error", len(bad), "timestamps go backwards", _examples(bad)))
    dups = idx[idx.duplicated()]
    if len(dups):
        add(Issue("duplicates", "error", len(dups), "duplicate timestamps", _examples(dups)))
    df = df[~idx.duplicated()].sort_index()
    idx = pd.DatetimeIndex(df.index)

    nan_rows = df.index[df.isna().any(axis=1)]
    if len(nan_rows):
        add(Issue("nan", "error", len(nan_rows), "NaN values in OHLCV", _examples(nan_rows)))
    o, h, lo, c = (df[k] for k in PRICE_COLUMNS)
    bad_ohlc = df.index[(h < np.maximum(o, c)) | (lo > np.minimum(o, c)) | (lo > h)]
    if len(bad_ohlc):
        add(
            Issue(
                "ohlc_inconsistent",
                "error",
                len(bad_ohlc),
                "high below open/close or low above open/close",
                _examples(bad_ohlc),
            )
        )
    nonpos = df.index[(df[list(PRICE_COLUMNS)] <= 0).any(axis=1) | (df["volume"] < 0)]
    if len(nonpos):
        add(
            Issue(
                "non_positive",
                "error",
                len(nonpos),
                "price <= 0 or negative volume",
                _examples(nonpos),
            )
        )

    # ---- timestamp grid ------------------------------------------------------------
    minutes = interval_minutes(interval)
    aligned = (idx.second == 0) & (idx.microsecond == 0)
    if minutes < 60:
        aligned &= (idx.minute % minutes) == 0
    if (~aligned).any():
        add(
            Issue(
                "misaligned",
                "error",
                int((~aligned).sum()),
                f"timestamps not on the {interval} grid",
                _examples(idx[~aligned]),
            )
        )
    elif minutes >= 60 and idx.minute.nunique() > 1:
        add(
            Issue(
                "mixed_offsets",
                "warning",
                int(idx.minute.nunique()),
                f"{interval} bars start at different minute offsets: {sorted(set(idx.minute))}",
            )
        )

    # ---- trading hours / timezone mistakes -----------------------------------------
    et = idx.tz_convert(ET)
    th = inst.trading_hours
    day_session = th.start < th.end
    outside_mask = np.array([not _in_hours(t, th.start, th.end) for t in et])
    n_out = int(outside_mask.sum())
    if n_out:
        share = n_out / len(df)
        # A day-session series requested without pre/post bars must never leave its hours;
        # a 23h series can have a few stray bars, so only a large share is an error there.
        sev: Severity = "error" if day_session or share > TZ_MISTAKE_SHARE else "warning"
        add(
            Issue(
                "outside_trading_hours",
                sev,
                n_out,
                f"{share:.1%} of bars start outside {th.start:%H:%M}-{th.end:%H:%M} ET "
                "(weekly hours)" + (" - possible timezone mistake" if sev == "error" else ""),
                _examples(idx[outside_mask]),
            )
        )
    if day_session:  # first bar of each day should be at the open
        first_bars = pd.Series(et.time, index=et.date).groupby(level=0).min()
        modal = first_bars.mode().iloc[0]
        if modal != th.start:
            add(
                Issue(
                    "timezone_shift",
                    "error",
                    int((first_bars != th.start).sum()),
                    f"typical first bar of the day is {modal} ET, expected {th.start} ET",
                )
            )
    else:  # 23h series: the daily break [end, start) must be empty on most days
        weekdays = pd.Series(et.day_of_week < 5, index=et)
        in_break = np.array([th.end <= t.time() < th.start for t in et]) & weekdays.to_numpy()
        days_all = set(et.date[weekdays.to_numpy()])
        days_break = set(et.date[in_break])
        if days_all and len(days_break) / len(days_all) > 0.5:
            add(
                Issue(
                    "timezone_shift",
                    "error",
                    len(days_break),
                    f"bars inside the {th.end:%H:%M}-{th.start:%H:%M} ET daily break on "
                    f"{len(days_break)}/{len(days_all)} weekdays - the series looks shifted",
                )
            )

    # ---- volume -------------------------------------------------------------------
    zero = df["volume"] == 0
    if zero.any():
        add(
            Issue(
                "zero_volume",
                "warning",
                int(zero.sum()),
                f"{zero.mean():.1%} of bars have zero volume",
                _examples(idx[zero.to_numpy()]),
            )
        )
    flat = zero & (h == lo) & (o == c) & (h == o)
    if flat.any():
        add(
            Issue(
                "flat_zero_volume",
                "info",
                int(flat.sum()),
                "bars with O=H=L=C and zero volume (likely provider-synthesised)",
            )
        )

    # ---- absurd candles --------------------------------------------------------------
    prev_close = c.shift(1)
    tr = pd.concat([h - lo, (h - prev_close).abs(), (lo - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(cfg.validation.atr_window).mean().shift(1)  # only past bars
    absurd = (h - lo) > cfg.validation.absurd_range_atr_mult * atr
    if absurd.any():
        add(
            Issue(
                "absurd_candle",
                "warning",
                int(absurd.sum()),
                f"range > {cfg.validation.absurd_range_atr_mult} x ATR"
                f"({cfg.validation.atr_window})",
                _examples(idx[absurd.to_numpy()]),
            )
        )

    # ---- price jumps between consecutive bars (bad prints with a normal range) ------
    contiguous = pd.Series(idx, index=idx).diff() == interval_delta(interval)
    jump = ((o - prev_close).abs() > cfg.validation.absurd_range_atr_mult * atr) & contiguous
    if jump.any():
        add(
            Issue(
                "price_jump",
                "warning",
                int(jump.sum()),
                f"|open - previous close| > {cfg.validation.absurd_range_atr_mult} x ATR "
                "between contiguous bars (bad print or contract roll?)",
                _examples(idx[jump.to_numpy()]),
            )
        )

    # ---- calendar: missing days, gaps, holiday bars ------------------------------------
    _calendar_checks(df, interval, cfg, rep)
    return rep


def _calendar_checks(df: pd.DataFrame, interval: str, cfg: Config, rep: SeriesReport) -> None:
    idx = pd.DatetimeIndex(df.index)
    ends = idx + interval_delta(interval)
    first, last = idx.min(), idx.max()
    step = interval_delta(interval)
    et_first = first.tz_convert(ET).date()
    et_last = last.tz_convert(ET).date()
    days = trading_days(cfg.session.exchange_calendar, et_first, et_last)
    missing_days: list[str] = []
    gap_days: dict[str, int] = {}
    covered = 0
    check_gaps = interval_minutes(interval) <= cfg.validation.gap_check_max_interval_min
    for d in days:
        s, e = _session_bounds(cfg, d)
        if e <= first or s > last:
            continue  # session entirely outside the archived span
        in_window = idx[(ends > s) & (idx < e)]
        if len(in_window) == 0:
            missing_days.append(d.isoformat())
            continue
        covered += 1
        if check_gaps:
            expected = pd.date_range(s, e, freq=step, inclusive="left")
            expected = expected[(expected >= first) & (expected <= last)]
            # isin compares instants, independent of the index resolution (s/us/ns)
            n_missing = int((~expected.isin(idx)).sum())
            if n_missing:
                gap_days[d.isoformat()] = n_missing
    rep.trading_days_covered = covered
    if missing_days:
        rep.issues.append(
            Issue(
                "missing_trading_days",
                "error",
                len(missing_days),
                "exchange trading days with no bars in the session window",
                missing_days[:MAX_EXAMPLES],
            )
        )
    if gap_days:
        total = sum(gap_days.values())
        worst = sorted(gap_days.items(), key=lambda kv: -kv[1])[:MAX_EXAMPLES]
        rep.issues.append(
            Issue(
                "session_gaps",
                "warning",
                total,
                f"{total} missing bars inside the session on {len(gap_days)} day(s)",
                [f"{k}: {v} missing" for k, v in worst],
            )
        )
    tdays = set(days)
    non_trading: list[str] = []
    for d in sorted(set(idx.tz_convert(ET).date)):
        if d in tdays or d.weekday() >= 5:
            continue
        s, e = window_utc(d, cfg.session.start, cfg.session.end, cfg.session.timezone)
        if ((ends > s) & (idx < e)).any():
            non_trading.append(d.isoformat())
    if non_trading:
        rep.issues.append(
            Issue(
                "bars_on_exchange_holiday",
                "warning",
                len(non_trading),
                f"bars inside the session window on days the "
                f"{cfg.session.exchange_calendar} calendar is closed",
                non_trading[:MAX_EXAMPLES],
            )
        )


# ---------------------------------------------------------------- reports


def overall_status(reports: list[SeriesReport]) -> str:
    states = {r.status for r in reports}
    for s in ("FAIL", "WARN"):
        if s in states:
            return s
    return "PASS"


def to_json(reports: list[SeriesReport], meta: dict[str, object]) -> str:
    payload = {
        "meta": meta,
        "status": overall_status(reports),
        "series": [asdict(r) | {"status": r.status} for r in reports],
    }
    return json.dumps(payload, indent=2, default=str) + "\n"


def to_markdown(reports: list[SeriesReport], meta: dict[str, object]) -> str:
    lines = [
        f"# Data quality report — {meta.get('date', '')}",
        "",
        f"Overall status: **{overall_status(reports)}**  ",
        f"Run `{meta.get('run_id', '')}`, commit `{meta.get('git_commit', '')}`, "
        f"config `{meta.get('config_hash', '')}`, data `{meta.get('data_hash', '')}`.",
        "",
        "Severity: `error` = do not trust the data as-is; `warning` = usable, but know it; "
        "`info` = context. The validator never modifies or fills data.",
        "",
        "| Symbol | Interval | Rows | First (UTC) | Last (UTC) | Trading days | Status |",
        "|---|---|---:|---|---|---:|---|",
    ]
    for r in reports:
        lines.append(
            f"| {r.symbol} | {r.interval} | {r.rows} | {r.first_ts_utc} | "
            f"{r.last_ts_utc} | {r.trading_days_covered} | {r.status} |"
        )
    for r in reports:
        lines += ["", f"## {r.symbol} {r.interval} — {r.status}", ""]
        if not r.issues:
            lines.append("No issues.")
            continue
        for i in r.issues:
            ex = f" Examples: {', '.join(i.examples)}" if i.examples else ""
            lines.append(f"- **{i.severity}** `{i.check}` ({i.count}): {i.message}.{ex}")
    return "\n".join(lines) + "\n"


def write_reports(
    reports: list[SeriesReport], meta: dict[str, object], out_dir: Path, day: str
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"data_quality_{day}.md"
    js = out_dir / f"data_quality_{day}.json"
    md.write_text(to_markdown(reports, meta))
    js.write_text(to_json(reports, meta))
    return md, js
