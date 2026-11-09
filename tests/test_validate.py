from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

import pandas as pd
from conftest import load_yf_fixture, synth_bars

from paper_lab.config import Config
from paper_lab.data.fetch import FetchStats, normalize
from paper_lab.data.validate import SeriesReport, validate_series, write_reports

D1, D2, D3 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)


def _checks(rep: SeriesReport) -> dict[str, str]:
    return {i.check: i.severity for i in rep.issues}


def _gld(cfg: Config, df: pd.DataFrame, interval: str = "5m") -> SeriesReport:
    return validate_series(df, cfg.instrument("GLD"), interval, cfg)


def _gc(cfg: Config, df: pd.DataFrame, interval: str = "5m") -> SeriesReport:
    return validate_series(df, cfg.instrument("GC=F"), interval, cfg)


def _clean(days: list[date] | None = None) -> pd.DataFrame:
    return synth_bars(days or [D1, D2, D3], time(9, 30), time(16, 0), "5m")


def test_clean_synthetic_passes(cfg: Config) -> None:
    rep = _gld(cfg, _clean())
    assert rep.status == "PASS", rep.issues
    assert rep.trading_days_covered == 3


def test_recorded_gld_fixture_across_us_dst_change(cfg: Config) -> None:
    raw = load_yf_fixture("yf_GLD_5m_2026-10-30_2026-11-02.csv")
    df = normalize(
        raw, "5m", pd.Timestamp("2026-11-09T00:00Z"), pd.Timedelta(0), FetchStats("", "")
    )
    rep = _gld(cfg, df)
    errors = [i for i in rep.issues if i.severity == "error"]
    assert errors == []
    assert rep.trading_days_covered == 2  # Fri Oct 30 (EDT) and Mon Nov 2 (EST)


def test_empty_is_error(cfg: Config) -> None:
    assert _gld(cfg, _clean().iloc[:0]).status == "FAIL"


def test_duplicates_error(cfg: Config) -> None:
    df = _clean()
    rep = _gld(cfg, pd.concat([df, df.iloc[[5]]]).sort_index())
    assert _checks(rep)["duplicates"] == "error"


def test_non_monotonic_error(cfg: Config) -> None:
    df = _clean()
    rep = _gld(cfg, pd.concat([df.iloc[10:], df.iloc[:10]]))
    assert _checks(rep)["non_monotonic"] == "error"


def test_nan_error(cfg: Config) -> None:
    df = _clean()
    df.iloc[4, 3] = float("nan")
    assert _checks(_gld(cfg, df))["nan"] == "error"


def test_ohlc_inconsistent_error(cfg: Config) -> None:
    df = _clean()
    df.iloc[7, df.columns.get_loc("high")] = df.iloc[7]["low"] - 1
    assert _checks(_gld(cfg, df))["ohlc_inconsistent"] == "error"


def test_non_positive_price_error(cfg: Config) -> None:
    df = _clean()
    df.iloc[2, df.columns.get_loc("low")] = 0.0
    assert _checks(_gld(cfg, df))["non_positive"] == "error"


def test_misaligned_error(cfg: Config) -> None:
    df = _clean()
    idx = df.index.tolist()
    idx[3] = idx[3] + pd.Timedelta(seconds=51)
    df.index = pd.DatetimeIndex(idx, name="ts_utc")
    assert _checks(_gld(cfg, df))["misaligned"] == "error"


def test_zero_volume_warning(cfg: Config) -> None:
    df = _clean()
    df.iloc[1, df.columns.get_loc("volume")] = 0
    rep = _gld(cfg, df)
    assert _checks(rep)["zero_volume"] == "warning" and rep.status == "WARN"


def test_absurd_candle_warning(cfg: Config) -> None:
    df = _clean()
    i = 50
    df.iloc[i, df.columns.get_loc("high")] = df.iloc[i]["high"] + 500
    assert _checks(_gld(cfg, df))["absurd_candle"] == "warning"


def test_missing_trading_day_error(cfg: Config) -> None:
    df = _clean([D1, D3])  # Tue Nov 3 is an exchange trading day
    rep = _gld(cfg, df)
    issue = next(i for i in rep.issues if i.check == "missing_trading_days")
    assert issue.severity == "error" and issue.examples == ["2026-11-03"]


def test_weekend_and_holiday_are_not_missing(cfg: Config) -> None:
    # Wed Nov 25, (Thu Nov 26 Thanksgiving closed), Fri Nov 27 early close, Mon Nov 30
    df = _clean([date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 30)])
    df = df[
        ~(
            (df.index.tz_convert("America/New_York").date == date(2026, 11, 27))
            & (df.index.tz_convert("America/New_York").hour >= 13)
        )
    ]
    rep = _gld(cfg, df)
    assert "missing_trading_days" not in _checks(rep)
    assert rep.trading_days_covered == 3


def test_session_gap_warning_counts_bars(cfg: Config) -> None:
    df = _clean()
    t1 = pd.Timestamp("2026-11-03 10:00", tz="America/New_York").tz_convert("UTC")
    t2 = pd.Timestamp("2026-11-03 10:05", tz="America/New_York").tz_convert("UTC")
    t3 = pd.Timestamp("2026-11-03 13:00", tz="America/New_York").tz_convert("UTC")  # outside
    rep = _gld(cfg, df.drop([t1, t2, t3]))
    gap = next(i for i in rep.issues if i.check == "session_gaps")
    assert gap.count == 2 and gap.examples == ["2026-11-03: 2 missing"]


def test_timezone_mistake_detected(cfg: Config) -> None:
    df = _clean()
    df.index = df.index - pd.Timedelta(hours=5)  # ET wall-clock stored as if it were UTC
    c = _checks(_gld(cfg, df))
    assert c.get("outside_trading_hours") == "error" or c.get("timezone_shift") == "error"


def test_one_hour_shift_detected(cfg: Config) -> None:
    df = _clean()
    df.index = df.index + pd.Timedelta(hours=1)  # a DST-style one-hour mistake
    c = _checks(_gld(cfg, df))
    assert c.get("timezone_shift") == "error"


def test_gc_maintenance_break_bars_warn(cfg: Config) -> None:
    df = synth_bars([D1, D2], time(9, 30), time(12, 0), "5m")
    extra = synth_bars([D2], time(17, 15), time(17, 25), "5m", seed=3)
    rep = _gc(cfg, pd.concat([df, extra]).sort_index())
    assert _checks(rep)["outside_trading_hours"] == "warning"


def test_bars_on_exchange_holiday_warn(cfg: Config) -> None:
    df = synth_bars(
        [date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27)], time(9, 30), time(12, 0), "5m"
    )
    assert _checks(_gc(cfg, df))["bars_on_exchange_holiday"] == "warning"


def test_hourly_bars_skip_gap_check_but_find_missing_day(cfg: Config) -> None:
    df = synth_bars([D1, D3], time(9, 30), time(16, 0), "1h")
    c = _checks(_gld(cfg, df, "1h"))
    assert "session_gaps" not in c and c["missing_trading_days"] == "error"


def test_reports_written(cfg: Config, tmp_path: Path) -> None:
    reps = [_gld(cfg, _clean()), _gld(cfg, _clean([D1, D3]))]
    md, js = write_reports(reps, {"date": "2026-11-09", "run_id": "r"}, tmp_path, "2026-11-09")
    payload = json.loads(js.read_text())
    assert payload["status"] == "FAIL"
    assert [s["status"] for s in payload["series"]] == ["PASS", "FAIL"]
    assert "missing_trading_days" in md.read_text()


def test_gap_check_independent_of_index_resolution(cfg: Config) -> None:
    """Regression: comparing raw int64 (asi8) across s/ns indexes reported false gaps."""
    for unit in ("s", "ms", "us", "ns"):
        df = _clean()
        df.index = df.index.as_unit(unit)
        rep = _gld(cfg, df)
        assert "session_gaps" not in _checks(rep), unit
