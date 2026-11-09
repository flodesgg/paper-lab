"""Fetch bars from a source, normalise them, and append them to the archive.

The network source is behind the small ``BarSource`` protocol so tests use recorded
fixtures and never touch the network.
"""

from __future__ import annotations

import fcntl
import logging
import time as _time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd

from paper_lab.config import Config, InstrumentConfig, Interval
from paper_lab.data.archive import MergeResult, load_archive, merge_into_archive, write_manifest
from paper_lab.data.bars import (
    COLUMNS,
    INDEX_NAME,
    PRICE_COLUMNS,
    empty_bars,
    interval_delta,
    interval_minutes,
)
from paper_lab.data.storage import write_cache

log = logging.getLogger("paper_lab.data.fetch")
_NO_DELAY = pd.Timedelta(0)


class HistoryLimitError(RuntimeError):
    """The provider refuses the window (outside its lookback limit). Not retried."""


class ProviderError(RuntimeError):
    """Unexpected provider response; retried like a network error."""


class ArchiveLockedError(RuntimeError):
    """Another fetch is writing the archive."""


class BarSource(Protocol):
    def history(
        self, symbol: str, interval: str, start: pd.Timestamp, end: pd.Timestamp, prepost: bool
    ) -> pd.DataFrame:
        """Raw provider frame for [start, end). May be empty."""
        ...


class YFinanceSource:
    """Thin wrapper around yfinance. Imported lazily so tests never need the network."""

    def history(
        self, symbol: str, interval: str, start: pd.Timestamp, end: pd.Timestamp, prepost: bool
    ) -> pd.DataFrame:
        import yfinance as yf
        from yfinance.exceptions import YFPricesMissingError

        # raise_errors=True: without it yfinance prints network errors and returns an
        # empty frame, which would look like "no bars" (a silent failure).
        try:
            df: pd.DataFrame = yf.Ticker(symbol).history(
                start=start.to_pydatetime(),
                end=end.to_pydatetime(),
                interval=interval,
                prepost=prepost,
                auto_adjust=False,
                actions=False,
                raise_errors=True,
            )
        except YFPricesMissingError as e:
            msg = str(e)
            if "must be within" in msg or "not available" in msg:
                raise HistoryLimitError(msg) from e
            if "no price data found" in msg:
                return pd.DataFrame()  # empty window (weekend / holiday); seen 2026-11-09
            raise ProviderError(msg) from e  # anything else is retried, then fails loudly
        return df


@dataclass
class FetchStats:
    symbol: str
    interval: str
    start: str = ""
    end: str = ""
    chunks: int = 0
    raw_rows: int = 0
    rejected_nan: int = 0
    rejected_misaligned: int = 0
    rejected_incomplete: int = 0
    dropped_first_row: int = 0
    first_row_not_anchor: int = 0
    dropped_last_row: int = 0
    duplicate_across_chunks: int = 0
    kept: int = 0
    added: int = 0
    unchanged: int = 0
    revised: int = 0
    files_written: list[str] = field(default_factory=list)
    error: str | None = None


def normalize(
    raw: pd.DataFrame,
    interval: str,
    as_of: pd.Timestamp,
    grace: pd.Timedelta,
    stats: FetchStats,
    delay: pd.Timedelta = _NO_DELAY,
) -> pd.DataFrame:
    """Provider frame -> canonical UTC bar frame, dropping (and counting) unusable bars.

    Dropped, never repaired:
      * rows with any NaN in OHLCV,
      * timestamps not aligned to the interval grid (e.g. Yahoo's live bar at 08:57:51),
      * bars that had not finished ``grace`` before ``as_of - delay`` (still forming;
        ``delay`` is the provider's data delay, ~10 min for COMEX on Yahoo).
    """
    if raw.empty:
        return empty_bars()
    if not isinstance(raw.index, pd.DatetimeIndex) or raw.index.tz is None:
        raise ValueError("provider returned a tz-naive or non-datetime index; refusing to guess")
    df = raw.rename(columns=str.lower)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"provider frame is missing columns {missing}")
    df = df[list(COLUMNS)].copy()
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
    df.index.name = INDEX_NAME
    stats.raw_rows += len(df)

    nan_mask = df.isna().any(axis=1)
    stats.rejected_nan += int(nan_mask.sum())
    df = df.loc[~nan_mask]

    minutes = interval_minutes(interval)
    idx = pd.DatetimeIndex(df.index)
    aligned = (idx.second == 0) & (idx.microsecond == 0)
    if minutes < 60:
        aligned &= (idx.minute % minutes) == 0
    stats.rejected_misaligned += int((~aligned).sum())
    df = df.loc[aligned]

    complete = (pd.DatetimeIndex(df.index) + (interval_delta(interval) + grace + delay)) <= as_of
    stats.rejected_incomplete += int((~complete).sum())
    df = df.loc[complete]

    out: pd.DataFrame = df.astype({c: "float64" for c in PRICE_COLUMNS} | {"volume": "int64"})
    return out


def _with_retries(
    fn: Callable[[], pd.DataFrame],
    retries: int,
    pause: float,
    what: str,
    sleep: Callable[[float], None],
) -> pd.DataFrame:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except HistoryLimitError:
            raise
        except Exception as e:  # provider/network errors are heterogeneous
            last = e
            log.warning("%s failed (attempt %d/%d): %s", what, attempt, retries, e)
            if attempt < retries:
                sleep(max(pause, 1.0) * 2 ** (attempt - 1))
    raise RuntimeError(f"{what} failed after {retries} attempts") from last


def fetch_range(
    source: BarSource,
    symbol: str,
    interval: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    chunk_days: int,
    prepost: bool,
    as_of: pd.Timestamp,
    grace: pd.Timedelta,
    delay: pd.Timedelta = _NO_DELAY,
    retries: int = 3,
    pause: float = 0.0,
    sleep: Callable[[float], None] = _time.sleep,
) -> tuple[pd.DataFrame, FetchStats]:
    """Fetch [start, end) in chunks, working around two Yahoo quirks (docs/data_notes.md).

    1. The **first row of every response has volume 0** (prices are correct). So the first
       row is always dropped, and each chunk after the first is requested from the last
       bar already received ("anchor"); the dropped row is then a duplicate, not lost data.
       If the first row is not the anchor, a bar is lost and counted in
       ``first_row_not_anchor``.
    2. The **newest bar of the fetch may still be forming** (data is delayed,
       e.g. ~10 min for COMEX). It is dropped; the next update's overlap re-fetches it.
    """
    stats = FetchStats(symbol, interval, start.isoformat(), end.isoformat())
    step = pd.Timedelta(days=chunk_days)
    parts: list[pd.DataFrame] = []
    anchor: pd.Timestamp | None = None
    s = start
    first_call = True
    while s < end:
        # Request from the anchor when it is recent enough; each request spans <= chunk_days.
        req_start = anchor if anchor is not None and s - anchor < step else s
        e = min(req_start + step, end)
        if not first_call and pause:
            sleep(pause)
        first_call = False

        def call(rs: pd.Timestamp = req_start, re_: pd.Timestamp = e) -> pd.DataFrame:
            return source.history(symbol, interval, rs, re_, prepost)

        raw = _with_retries(
            call, retries, pause, f"{symbol} {interval} {req_start:%Y-%m-%d}..{e:%Y-%m-%d}", sleep
        )
        stats.chunks += 1
        if len(raw):
            first_ts = pd.Timestamp(raw.index[0]).tz_convert("UTC")
            stats.dropped_first_row += 1
            if anchor is not None and req_start == anchor and first_ts != anchor:
                stats.first_row_not_anchor += 1
                log.warning(
                    "%s %s: first row %s is not the anchor %s; that bar is lost",
                    symbol,
                    interval,
                    first_ts,
                    anchor,
                )
            raw = raw.iloc[1:]
        part = normalize(raw, interval, as_of, grace, stats, delay)
        if len(part):
            parts.append(part)
            anchor = pd.Timestamp(part.index.max())
        s = e
    if not parts:
        return empty_bars(), stats
    df = pd.concat(parts).sort_index()
    dup = df.index.duplicated(keep="first")
    stats.duplicate_across_chunks = int(dup.sum())
    df = df.loc[~dup]
    # The newest bar of the whole fetch may still be forming (provider delay); never archive
    # it. The next update re-fetches it through update_overlap_days.
    df = df.iloc[:-1]
    stats.dropped_last_row = 1
    stats.kept = len(df)
    return df, stats


def update_window(
    archive_dir: Path,
    stem: str,
    interval: str,
    as_of: pd.Timestamp,
    lookback_days: int,
    overlap_days: int,
    full: bool,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Window to request: from the archive tail (minus overlap) or the full lookback."""
    earliest = as_of - pd.Timedelta(days=lookback_days)
    start = earliest
    if not full:
        existing = load_archive(archive_dir, stem, interval)
        if len(existing):
            start = max(earliest, existing.index.max() - pd.Timedelta(days=overlap_days))
    return start.floor("min"), as_of.floor("min")


@contextmanager
def archive_lock(archive_dir: Path) -> Iterator[None]:
    """Exclusive, non-blocking lock so a scheduled and a manual run never merge concurrently."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    with (archive_dir / ".lock").open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise ArchiveLockedError(f"another fetch holds {archive_dir / '.lock'}") from e
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _update_series(
    cfg: Config,
    source: BarSource,
    inst: InstrumentConfig,
    interval: Interval,
    as_of: pd.Timestamp,
    full: bool,
    sleep: Callable[[float], None],
) -> FetchStats:
    archive_dir = cfg.paths.data_archive
    lim = cfg.data.limits[interval]
    start, end = update_window(
        archive_dir,
        inst.file_stem,
        interval,
        as_of,
        lim.lookback_days,
        cfg.data.update_overlap_days,
        full,
    )
    df, stats = fetch_range(
        source,
        inst.symbol,
        interval,
        start,
        end,
        chunk_days=lim.chunk_days,
        prepost=inst.prepost,
        as_of=as_of,
        grace=pd.Timedelta(seconds=cfg.data.incomplete_bar_grace_sec),
        delay=pd.Timedelta(minutes=inst.data_delay_min),
        retries=cfg.data.max_retries,
        pause=cfg.data.request_pause_sec,
        sleep=sleep,
    )
    merged: MergeResult = merge_into_archive(
        archive_dir, inst.symbol, inst.file_stem, interval, df, as_of
    )
    stats.added, stats.unchanged, stats.revised = merged.added, merged.unchanged, merged.revised
    stats.files_written = list(merged.files_written)
    full_df = load_archive(archive_dir, inst.file_stem, interval)
    if len(full_df):
        write_cache(
            full_df, cfg.paths.data_cache, inst.file_stem, interval, cfg.data.storage_format
        )
    log.info(
        "%s %s: window %s..%s chunks=%d raw=%d kept=%d added=%d unchanged=%d "
        "revised=%d dropped(nan=%d misaligned=%d incomplete=%d first=%d last=%d) "
        "lost_first_rows=%d",
        inst.symbol,
        interval,
        start,
        end,
        stats.chunks,
        stats.raw_rows,
        stats.kept,
        stats.added,
        stats.unchanged,
        stats.revised,
        stats.rejected_nan,
        stats.rejected_misaligned,
        stats.rejected_incomplete,
        stats.dropped_first_row,
        stats.dropped_last_row,
        stats.first_row_not_anchor,
    )
    return stats


def run_update(
    cfg: Config,
    source: BarSource,
    as_of: pd.Timestamp,
    *,
    symbols: list[str] | None = None,
    intervals: list[str] | None = None,
    full: bool = False,
    sleep: Callable[[float], None] = _time.sleep,
) -> list[FetchStats]:
    """Fetch, archive, rebuild manifest and working cache. Idempotent.

    A failing series is logged and recorded in its ``FetchStats.error``; the other series
    still run, and the manifest is always rebuilt from what is on disk.
    """
    archive_dir = cfg.paths.data_archive
    results: list[FetchStats] = []
    with archive_lock(archive_dir):
        try:
            for inst in cfg.instruments:
                if symbols and inst.symbol not in symbols:
                    continue
                for interval in cfg.data.archive_intervals:
                    if intervals and interval not in intervals:
                        continue
                    try:
                        stats = _update_series(cfg, source, inst, interval, as_of, full, sleep)
                    except (RuntimeError, ValueError, OSError) as e:
                        log.error("%s %s: update failed: %s", inst.symbol, interval, e)
                        stats = FetchStats(inst.symbol, interval, error=f"{type(e).__name__}: {e}")
                    results.append(stats)
                    if cfg.data.request_pause_sec:
                        sleep(cfg.data.request_pause_sec)
        finally:
            write_manifest(archive_dir)
    return results
