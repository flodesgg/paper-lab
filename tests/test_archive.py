from __future__ import annotations

import gzip
import hashlib
from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest
from conftest import synth_bars

from paper_lab.data.archive import (
    archive_hash,
    bars_path,
    build_manifest,
    load_archive,
    merge_into_archive,
    read_bars_file,
    write_manifest,
)

NOW = pd.Timestamp("2026-11-09T18:00:00Z")


def _bars() -> pd.DataFrame:
    return synth_bars([date(2026, 11, 2), date(2026, 11, 3)], time(9, 30), time(12, 0), "5m")


def test_roundtrip_is_exact_for_float32_derived_prices(tmp_path: Path) -> None:
    df = _bars()
    df["close"] = [4058.300048828125] * len(df)  # typical yfinance float32->float64 value
    df["high"] = df[["high", "close"]].max(axis=1)
    df["low"] = df[["low", "close"]].min(axis=1)
    merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", df, NOW)
    back = load_archive(tmp_path, "GC_F", "5m")
    pd.testing.assert_frame_equal(back, df, check_freq=False)


def test_second_merge_is_noop_and_bytes_identical(tmp_path: Path) -> None:
    df = _bars()
    r1 = merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", df, NOW)
    path = bars_path(tmp_path, "2026-11", "GC_F", "5m")
    before = path.read_bytes()
    r2 = merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", df, NOW)
    assert r1.added == len(df) and r1.files_written
    assert r2.added == 0 and r2.unchanged == len(df) and r2.revised == 0
    assert r2.files_written == ()
    assert path.read_bytes() == before


def test_gzip_is_deterministic(tmp_path: Path) -> None:
    df = _bars()
    merge_into_archive(tmp_path / "a", "GC=F", "GC_F", "5m", df, NOW)
    merge_into_archive(tmp_path / "b", "GC=F", "GC_F", "5m", df, NOW)
    pa = bars_path(tmp_path / "a", "2026-11", "GC_F", "5m").read_bytes()
    pb = bars_path(tmp_path / "b", "2026-11", "GC_F", "5m").read_bytes()
    assert pa == pb
    assert pa[4:8] == b"\x00\x00\x00\x00"  # gzip header mtime = 0


def test_incremental_merge_appends_only_new_bars(tmp_path: Path) -> None:
    df = _bars()
    half = len(df) // 2
    merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", df.iloc[:half], NOW)
    r = merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", df.iloc[half - 5 :], NOW)
    assert r.added == len(df) - half
    assert r.unchanged == 5
    pd.testing.assert_frame_equal(load_archive(tmp_path, "GC_F", "5m"), df, check_freq=False)


def test_keep_first_and_revisions_logged_once(tmp_path: Path) -> None:
    df = _bars()
    merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", df, NOW)
    revised = df.copy()
    ts = revised.index[3]
    revised.loc[ts, "volume"] = revised.loc[ts, "volume"] + 7
    r = merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", revised, NOW)
    assert r.revised == 1 and r.added == 0
    # archived value is unchanged (keep-first)
    assert load_archive(tmp_path, "GC_F", "5m").loc[ts, "volume"] == df.loc[ts, "volume"]
    rev_path = tmp_path / "revisions" / "2026-11.csv.gz"
    with gzip.open(rev_path, "rt") as f:
        rev = pd.read_csv(f)
    assert len(rev) == 1 and rev.loc[0, "field"] == "volume"
    before = rev_path.read_bytes()
    merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", revised, NOW + pd.Timedelta(days=1))
    assert rev_path.read_bytes() == before  # same revision is not logged twice


def test_month_partition_is_by_utc_bar_start(tmp_path: Path) -> None:
    idx = pd.DatetimeIndex(["2026-10-31T23:59:00Z", "2026-11-01T00:00:00Z"], name="ts_utc")
    df = pd.DataFrame(
        {
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [1, 1],
        },
        index=idx,
    )
    merge_into_archive(tmp_path, "GLD", "GLD", "1m", df, NOW)
    assert len(read_bars_file(bars_path(tmp_path, "2026-10", "GLD", "1m"))) == 1
    assert len(read_bars_file(bars_path(tmp_path, "2026-11", "GLD", "1m"))) == 1
    assert len(load_archive(tmp_path, "GLD", "1m")) == 2


def test_duplicate_new_bars_rejected(tmp_path: Path) -> None:
    df = _bars()
    with pytest.raises(ValueError, match="duplicate"):
        merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", pd.concat([df, df.iloc[:1]]), NOW)


def test_manifest_rows_and_sha(tmp_path: Path) -> None:
    df = _bars()
    merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", df, NOW)
    path = write_manifest(tmp_path)
    m = pd.read_csv(path)
    assert list(m["path"]) == ["2026-11/GC_F_5m.csv.gz"]
    row = m.iloc[0]
    assert row["rows"] == len(df) and row["stem"] == "GC_F" and row["interval"] == "5m"
    assert row["first_ts_utc"] == "2026-11-02T14:30:00Z"  # 09:30 EST
    f = tmp_path / row["path"]
    assert row["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    h1 = archive_hash(tmp_path)
    write_manifest(tmp_path)
    assert archive_hash(tmp_path) == h1


def test_manifest_lists_revision_files(tmp_path: Path) -> None:
    df = _bars()
    merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", df, NOW)
    df2 = df.copy()
    df2.iloc[0, 0] += 1.0
    merge_into_archive(tmp_path, "GC=F", "GC_F", "5m", df2, NOW)
    m = build_manifest(tmp_path)
    assert set(m["kind"]) == {"bars", "revisions"}
