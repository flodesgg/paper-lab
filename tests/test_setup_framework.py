"""Setup contract, registry, daily bars / ATR and the signal tables."""

from __future__ import annotations

from datetime import date, time

import pandas as pd
import pytest
from pydantic import ValidationError
from scenario import CFG, DAY, bars, flat, run, ts

from paper_lab.config import HoursConfig, SetupConfig
from paper_lab.data.daily import atr_before, daily_from_intraday, trading_dates
from paper_lab.setups.base import DayContext, DayState, Setup, SetupParams, Signal
from paper_lab.setups.registry import build_all, build_setup, register, registry
from paper_lab.setups.runner import results_to_frames


def test_registry_discovers_setup_modules() -> None:
    assert {"orb", "momentum"} <= set(registry())


def test_every_configured_setup_validates() -> None:
    built = build_all(CFG, enabled_only=False)
    assert set(built) == set(CFG.setups)
    assert set(build_all(CFG)) == {n for n, s in CFG.setups.items() if s.enabled}


def test_unknown_or_misspelled_param_is_rejected() -> None:
    params = dict(CFG.setups["orb"].params) | {"volume_mutl": 2.0}
    with pytest.raises(ValidationError, match="volume_mutl"):
        build_setup("orb", SetupConfig(enabled=True, interval="5m", params=params))


def test_inconsistent_params_are_rejected() -> None:
    p = dict(CFG.setups["orb"].params)
    with pytest.raises(ValidationError, match="min_range_atr"):
        build_setup(
            "orb", SetupConfig(enabled=True, interval="5m", params=p | {"min_range_atr": 0.9})
        )
    with pytest.raises(ValueError, match="not a multiple"):
        build_setup("orb", SetupConfig(enabled=True, interval="5m", params=p | {"or_minutes": 12}))
    with pytest.raises(KeyError, match="unknown setup"):
        build_setup("nope", CFG.setups["orb"])


def test_adding_a_setup_is_one_class() -> None:
    """A new setup = subclass + @register; the driver, guard and tables come for free."""

    @register
    class FirstBarUp(Setup[DayState]):
        name = "test_first_bar_up"
        params_model = SetupParams

        def start_day(self, ctx: DayContext) -> DayState:
            return DayState()

        def on_bar(self, ctx: DayContext, state: DayState, seen: pd.DataFrame) -> Signal | None:
            if len(seen) == 1 and seen["close"].iloc[0] > seen["open"].iloc[0]:
                return self.make_signal(ctx, seen, "long", float(seen["low"].iloc[0]), "up")
            return None

    try:
        s = build_setup(
            "test_first_bar_up",
            SetupConfig(
                enabled=True,
                interval="5m",
                params={"k": 1.5, "last_entry": "11:30", "max_signals_per_day": 1},
            ),
        )
        r = run(s, bars(DAY, [("09:30", 100.0, 101.0, 99.5, 100.8, 10)]))
        assert [(x.side, x.stop, x.target_r) for x in r.signals] == [("long", 99.5, 1.5)]
        dup = type("Dup", (FirstBarUp,), {})  # same `name` as FirstBarUp
        with pytest.raises(ValueError, match="registered twice"):
            register(dup)
    finally:
        from paper_lab.setups import registry as reg

        reg._REGISTRY.pop("test_first_bar_up", None)


def test_wrapping_session_trading_date() -> None:
    """GC=F: the trading day D runs 18:00 ET on D-1 to 17:00 ET on D."""
    hours = HoursConfig(start=time(18, 0), end=time(17, 0))
    idx = pd.DatetimeIndex(
        [ts(date(2026, 10, 14), "17:55"), ts(date(2026, 10, 14), "18:00"), ts(DAY, "09:30")]
    )
    got = trading_dates(idx, hours, "America/New_York")
    assert [d.date() for d in got] == [date(2026, 10, 14), DAY, DAY]


def test_daily_bars_and_atr_use_only_prior_days() -> None:
    hours = HoursConfig(start=time(9, 30), end=time(16, 0))
    rows_a = [("09:30", 100.0, 104.0, 99.0, 103.0, 10), ("10:30", 103.0, 103.5, 101.0, 102.0, 5)]
    rows_b = [("09:30", 106.0, 108.0, 105.0, 107.0, 10)]  # gap up: TR = 108 - 102 = 6
    rows_c = [("09:30", 50.0, 500.0, 5.0, 60.0, 10)]  # "today": must not enter its own ATR
    d1, d2, d3 = date(2026, 10, 13), date(2026, 10, 14), DAY
    b = pd.concat([bars(d1, rows_a), bars(d2, rows_b), bars(d3, rows_c)])
    daily = daily_from_intraday(b, hours, "America/New_York")
    assert daily.loc[
        pd.Timestamp(d1), ["open", "high", "low", "close", "volume", "n_bars"]
    ].tolist() == [
        100.0,
        104.0,
        99.0,
        102.0,
        15,
        2,
    ]
    # TRs before DAY: d1 = 104 - 99 = 5 (no previous close), d2 = max(3, 6, 3) = 6
    assert atr_before(daily, d3, 2) == pytest.approx(5.5)
    assert atr_before(daily, d3, 3) is None  # only 2 prior days: no guess
    assert atr_before(daily, d2, 1) == pytest.approx(5.0)


def test_results_tables() -> None:
    from scenario import ctx as make_ctx

    p = dict(CFG.setups["orb"].params) | {"volume_mult": 0.0}
    s = build_setup("orb", SetupConfig(enabled=True, interval="5m", params=p))
    rows = [
        ("09:30", 100.0, 101.0, 99.5, 100.5, 100),
        ("09:35", 100.5, 102.0, 100.0, 101.0, 100),
        ("09:40", 101.0, 101.5, 99.0, 100.0, 100),
        ("09:45", 100.0, 102.6, 99.8, 102.5, 200),
    ] + flat("09:50", "10:00", 102.5)
    r = s.run_day(make_ctx(), bars(DAY, rows), None)
    sig, days = results_to_frames([r])
    assert sig[["status", "side", "stop"]].values.tolist() == [["accepted", "long", 99.0]]
    assert "range_atr=0.3" in sig["info"].iloc[0]
    assert days[["status", "signals", "blocked"]].values.tolist() == [["scanned", 1, 0]]


def test_cli_signals_end_to_end(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """`paper-lab signals` on a synthetic archive: files written, run metadata recorded,
    the unfinished last session left out, disabled setups refused."""
    import json
    import shutil
    from datetime import time as dtime

    from conftest import REPO, synth_bars

    from paper_lab.calendar.sessions import trading_days
    from paper_lab.cli import main
    from paper_lab.data.archive import merge_into_archive

    monkeypatch.chdir(tmp_path)
    shutil.copytree(REPO / "data" / "calendar", tmp_path / "data" / "calendar")
    days = trading_days("XNYS", date(2026, 9, 1), date(2026, 10, 30))
    b5 = synth_bars(days, dtime(9, 30), dtime(16, 0), "5m", seed=3, base=300.0)
    b5 = b5[b5.index < ts(days[-1], "11:00")]  # last session still "running"
    b1 = synth_bars(days, dtime(9, 30), dtime(16, 0), "1h", seed=4, base=300.0)
    seen = pd.Timestamp("2026-11-01", tz="UTC")
    merge_into_archive(tmp_path / "data_archive", "GLD", "GLD", "5m", b5, seen)
    merge_into_archive(tmp_path / "data_archive", "GLD", "GLD", "1h", b1, seen)

    rc = main(["--config", str(REPO / "config.yaml"), "signals", "--symbols", "GLD"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"left out, data ends before session close: ['{days[-1]}']" in out
    files = sorted(p.name for p in (tmp_path / "reports" / "signals").iterdir())
    assert any(f.startswith("orb_GLD_") and f.endswith("_signals.csv") for f in files)
    meta = json.loads(next((tmp_path / "reports" / "signals").glob("run_*.json")).read_text())
    assert meta["guard"] == "config" and meta["runs"][0]["days"] == len(days) - 1
    assert {"git_commit", "config_hash", "data_hash"} <= meta.keys()

    rc = main(["--config", str(REPO / "config.yaml"), "signals", "--setups", "momentum"])
    assert rc == 2 and "disabled" in capsys.readouterr().err
