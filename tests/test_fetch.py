from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from conftest import FakeSource, load_yf_fixture

from paper_lab.config import Config
from paper_lab.data.archive import MANIFEST_NAME, load_archive
from paper_lab.data.fetch import (
    FetchStats,
    HistoryLimitError,
    fetch_range,
    normalize,
    run_update,
    update_window,
)

GRACE = pd.Timedelta(seconds=120)
LATE = pd.Timestamp("2026-11-09T00:00:00Z")


def _stats() -> FetchStats:
    return FetchStats("X", "5m")


def test_normalize_recorded_frame_to_canonical_utc() -> None:
    raw = load_yf_fixture("yf_GLD_5m_2026-10-30_2026-11-02.csv")
    out = normalize(raw, "5m", LATE, GRACE, _stats())
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert str(out.index.tz) == "UTC" and out.index.name == "ts_utc"
    # 09:30 ET is 13:30 UTC under EDT (Oct 30) and 14:30 UTC under EST (Nov 2)
    assert pd.Timestamp("2026-10-30T13:30:00Z") in out.index
    assert pd.Timestamp("2026-11-02T14:30:00Z") in out.index
    assert out["volume"].dtype == "int64"
    assert len(out) == len(raw)


def test_normalize_drops_nan_misaligned_and_incomplete() -> None:
    idx = pd.DatetimeIndex(
        [
            "2026-11-09 08:50:00",
            "2026-11-09 08:55:00",
            "2026-11-09 08:57:51",
            "2026-11-09 09:00:00",
            "2026-11-09 09:05:00",
        ],
        tz="America/New_York",
    )
    raw = pd.DataFrame(
        {
            "Open": [1.0, None, 1, 1, 1],
            "High": [1.0, 1, 1, 1, 1],
            "Low": [1.0, 1, 1, 1, 1],
            "Close": [1.0, 1, 1, 1, 1],
            "Adj Close": [1.0] * 5,
            "Volume": [5, 5, 5, 5, 5],
        },
        index=idx,
    )
    as_of = pd.Timestamp("2026-11-09 09:06:00", tz="America/New_York").tz_convert("UTC")
    st = _stats()
    out = normalize(raw, "5m", as_of, GRACE, st)
    assert st.rejected_nan == 1
    assert st.rejected_misaligned == 1  # the live bar at 08:57:51
    # 09:00 bar ends 09:05 + 2 min grace = 09:07 > 09:06 -> incomplete; 09:05 too
    assert st.rejected_incomplete == 2
    assert list(out.index.tz_convert("America/New_York").strftime("%H:%M")) == ["08:50"]


def test_normalize_refuses_naive_index() -> None:
    raw = pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex(["2026-11-09 09:00"]))
    with pytest.raises(ValueError, match="tz-naive"):
        normalize(raw, "5m", LATE, GRACE, _stats())


def test_normalize_refuses_missing_columns() -> None:
    raw = pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex(["2026-11-09 09:00"], tz="UTC"))
    with pytest.raises(ValueError, match="missing columns"):
        normalize(raw, "5m", LATE, GRACE, _stats())


def _gc_like_raw(days: int = 6) -> pd.DataFrame:
    """Continuous 5m bars (like GC=F) in yfinance's raw shape, ET index."""
    idx = pd.date_range("2026-10-26T00:00Z", periods=days * 288, freq="5min").tz_convert(
        "America/New_York"
    )
    n = len(idx)
    return pd.DataFrame(
        {
            "Open": [100.0] * n,
            "High": [101.0] * n,
            "Low": [99.0] * n,
            "Close": [100.5] * n,
            "Adj Close": [100.5] * n,
            "Volume": list(range(1, n + 1)),
        },
        index=idx,
    )


def test_fetch_range_spans_limit_and_loses_no_bars_at_chunk_joins() -> None:
    raw = _gc_like_raw()
    src = FakeSource({("GC=F", "5m"): raw})
    start, end = pd.Timestamp("2026-10-26T00:00Z"), pd.Timestamp("2026-11-01T00:00Z")
    df, st = fetch_range(
        src, "GC=F", "5m", start, end, chunk_days=2, prepost=False, as_of=LATE, grace=GRACE
    )
    assert all(e - s <= pd.Timedelta(days=2) for _, _, s, e, _ in src.calls)
    assert st.chunks == len(src.calls) >= 3
    # every chunk after the first is anchored on the last bar already received
    for (_, _, s_next, _, _), prev_end in zip(
        src.calls[1:], [c[3] for c in src.calls[:-1]], strict=True
    ):
        assert s_next < prev_end
    assert st.first_row_not_anchor == 0
    # only the very first bar (no anchor) and the very last (maybe forming) are dropped
    assert len(df) == len(raw) - 2
    assert (df["volume"] > 0).all()  # no zero-volume artefacts at chunk joins
    expected = raw.iloc[1:-1]["Volume"].to_numpy()
    assert (df["volume"].to_numpy() == expected).all()


def test_first_row_zero_volume_is_dropped() -> None:
    raw = load_yf_fixture("yf_GCF_1m_2026-11-06.csv")
    assert raw["Volume"].iloc[0] == 0  # the recorded Yahoo artefact
    src = FakeSource({("GC=F", "1m"): raw})
    df, st = fetch_range(
        src,
        "GC=F",
        "1m",
        pd.Timestamp("2026-11-06T14:00Z"),
        pd.Timestamp("2026-11-06T17:30Z"),
        chunk_days=7,
        prepost=False,
        as_of=LATE,
        grace=GRACE,
    )
    assert st.dropped_first_row == 1 and st.dropped_last_row == 1
    assert df.index[0] == pd.Timestamp("2026-11-06T14:01:00Z")


def test_provider_delay_marks_recent_bars_incomplete() -> None:
    raw = _gc_like_raw(1)
    src = FakeSource({("GC=F", "5m"): raw})
    as_of = pd.Timestamp("2026-10-26T12:00Z")
    df, st = fetch_range(
        src,
        "GC=F",
        "5m",
        pd.Timestamp("2026-10-26T00:00Z"),
        as_of,
        chunk_days=1,
        prepost=False,
        as_of=as_of,
        grace=GRACE,
        delay=pd.Timedelta(minutes=15),
    )
    # bars must end by 12:00 - 15 min - 2 min = 11:43: 11:40..11:55 (4 bars) are forming;
    # then the newest remaining bar (11:35) is dropped as well
    assert st.rejected_incomplete == 4
    assert df.index.max() == pd.Timestamp("2026-10-26T11:30Z")


def test_retries_then_succeeds() -> None:
    raw = load_yf_fixture("yf_GLD_5m_2026-10-30_2026-11-02.csv")
    src = FakeSource({("GLD", "5m"): raw}, fail_times=2)
    sleeps: list[float] = []
    df, _ = fetch_range(
        src,
        "GLD",
        "5m",
        pd.Timestamp("2026-10-29T00:00Z"),
        pd.Timestamp("2026-11-04T00:00Z"),
        chunk_days=10,
        prepost=False,
        as_of=LATE,
        grace=GRACE,
        retries=3,
        sleep=sleeps.append,
    )
    assert len(df) == len(raw) - 2 and len(sleeps) == 2


def test_retries_exhausted_raises() -> None:
    src = FakeSource({}, fail_times=5)
    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        fetch_range(
            src,
            "GLD",
            "5m",
            pd.Timestamp("2026-10-29T00:00Z"),
            pd.Timestamp("2026-10-30T00:00Z"),
            chunk_days=10,
            prepost=False,
            as_of=LATE,
            grace=GRACE,
            retries=3,
            sleep=lambda _: None,
        )


def test_history_limit_error_is_not_retried() -> None:
    src = FakeSource({}, fail_times=5, exc=HistoryLimitError("must be within the last 60 days"))
    with pytest.raises(HistoryLimitError):
        fetch_range(
            src,
            "GLD",
            "5m",
            pd.Timestamp("2026-10-29T00:00Z"),
            pd.Timestamp("2026-10-30T00:00Z"),
            chunk_days=10,
            prepost=False,
            as_of=LATE,
            grace=GRACE,
            retries=3,
            sleep=lambda _: None,
        )
    assert len(src.calls) == 1


def _src() -> FakeSource:
    return FakeSource(
        {
            ("GLD", "5m"): load_yf_fixture("yf_GLD_5m_2026-10-30_2026-11-02.csv"),
            ("GC=F", "1m"): load_yf_fixture("yf_GCF_1m_2026-11-06.csv"),
        }
    )


def test_run_update_twice_is_idempotent(cfg: Config) -> None:
    as_of = pd.Timestamp("2026-11-08T12:00:00Z")
    s1 = run_update(cfg, _src(), as_of, sleep=lambda _: None)
    snap = {p: p.read_bytes() for p in cfg.paths.data_archive.rglob("*") if p.is_file()}
    s2 = run_update(cfg, _src(), as_of + pd.Timedelta(hours=1), sleep=lambda _: None)
    snap2 = {p: p.read_bytes() for p in cfg.paths.data_archive.rglob("*") if p.is_file()}
    assert sum(s.added for s in s1) == (156 - 2) + (210 - 2)  # first+last row dropped
    assert sum(s.added for s in s2) == 0
    assert sum(s.revised for s in s2) == 0
    assert snap == snap2  # archive and manifest byte-identical
    assert (cfg.paths.data_archive / MANIFEST_NAME).exists()
    assert len(load_archive(cfg.paths.data_archive, "GLD", "5m")) == 156 - 2
    assert any(cfg.paths.data_cache.iterdir())  # working cache written


def test_update_window_incremental_vs_full(cfg: Config) -> None:
    as_of = pd.Timestamp("2026-11-08T12:00:00Z")
    run_update(cfg, _src(), as_of, symbols=["GLD"], intervals=["5m"], sleep=lambda _: None)
    last = load_archive(cfg.paths.data_archive, "GLD", "5m").index.max()
    s, e = update_window(cfg.paths.data_archive, "GLD", "5m", as_of, 59, 3, full=False)
    assert s == (last - pd.Timedelta(days=3)).floor("min") and e == as_of.floor("min")
    s_full, _ = update_window(cfg.paths.data_archive, "GLD", "5m", as_of, 59, 3, full=True)
    assert s_full == (as_of - pd.Timedelta(days=59)).floor("min")


def test_run_update_requests_within_lookback(cfg: Config) -> None:
    as_of = pd.Timestamp("2026-11-08T12:00:00Z")
    src = _src()
    run_update(cfg, src, as_of, sleep=lambda _: None)
    for _, interval, s, e, _ in src.calls:
        lim = cfg.data.limits[interval]
        assert s >= as_of - pd.Timedelta(days=lim.lookback_days) - pd.Timedelta(minutes=1)
        assert e - s <= pd.Timedelta(days=lim.chunk_days)


def test_prepost_flag_passed_per_instrument(cfg: Config, tmp_path: Path) -> None:
    src = _src()
    run_update(
        cfg, src, pd.Timestamp("2026-11-08T12:00:00Z"), intervals=["1h"], sleep=lambda _: None
    )
    assert {(sym, pp) for sym, _, _, _, pp in src.calls} == {("GC=F", False), ("GLD", False)}
