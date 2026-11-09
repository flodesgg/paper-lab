"""Tests for defects found in the Stage A code review (2026-11-09)."""

from __future__ import annotations

import functools
import json
from datetime import date, time
from typing import Any

import pandas as pd
import pytest
from conftest import FakeSource, load_yf_fixture, synth_bars

from paper_lab import cli
from paper_lab.config import Config
from paper_lab.data.archive import MANIFEST_NAME, build_manifest, load_archive
from paper_lab.data.fetch import (
    ArchiveLockedError,
    HistoryLimitError,
    ProviderError,
    YFinanceSource,
    archive_lock,
    run_update,
)
from paper_lab.data.validate import validate_series

NOW = pd.Timestamp("2026-11-08T12:00:00Z")
_ORIGINAL_HISTORY = YFinanceSource.history  # captured before the autouse network guard


class _FailFor(FakeSource):
    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame], bad_symbol: str) -> None:
        super().__init__(frames)
        self.bad = bad_symbol

    def history(
        self, symbol: str, interval: str, start: pd.Timestamp, end: pd.Timestamp, prepost: bool
    ) -> pd.DataFrame:
        if symbol == self.bad:
            raise ConnectionError("simulated outage")
        return super().history(symbol, interval, start, end, prepost)


def _frames() -> dict[tuple[str, str], pd.DataFrame]:
    return {
        ("GLD", "5m"): load_yf_fixture("yf_GLD_5m_2026-10-30_2026-11-02.csv"),
        ("GC=F", "1m"): load_yf_fixture("yf_GCF_1m_2026-11-06.csv"),
    }


# ---------------------------------------------------------------- fetch robustness


def test_one_failing_series_does_not_block_others_and_manifest_stays_consistent(
    cfg: Config,
) -> None:
    stats = run_update(cfg, _FailFor(_frames(), "GLD"), NOW, sleep=lambda _: None)
    errors = {(s.symbol, s.interval) for s in stats if s.error}
    assert errors == {("GLD", "1m"), ("GLD", "5m"), ("GLD", "1h")}
    assert len(load_archive(cfg.paths.data_archive, "GC_F", "1m")) == 208
    on_disk = build_manifest(cfg.paths.data_archive)
    written = pd.read_csv(cfg.paths.data_archive / MANIFEST_NAME)
    assert list(written["sha256"]) == list(on_disk["sha256"])


def test_manifest_rebuilt_even_if_unexpected_exception(cfg: Config) -> None:
    class Boom(FakeSource):
        def history(self, *a: Any, **k: Any) -> pd.DataFrame:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_update(cfg, Boom({}), NOW, sleep=lambda _: None)
    assert (cfg.paths.data_archive / MANIFEST_NAME).exists()


def test_archive_lock_rejects_concurrent_run(cfg: Config) -> None:
    with archive_lock(cfg.paths.data_archive), pytest.raises(ArchiveLockedError):
        run_update(cfg, FakeSource(_frames()), NOW, sleep=lambda _: None)
    run_update(cfg, FakeSource(_frames()), NOW, sleep=lambda _: None)  # released afterwards


def test_run_update_applies_per_instrument_delay(cfg: Config) -> None:
    gc = cfg.instrument("GC=F").model_copy(update={"data_delay_min": 20})
    gld = cfg.instrument("GLD").model_copy(update={"data_delay_min": 0})
    c2 = cfg.model_copy(update={"instruments": [gc, gld]})
    as_of = pd.Timestamp("2026-11-06T16:00:00Z")  # inside the GC 1m fixture (14:00-17:29)
    run_update(c2, FakeSource(_frames()), as_of, intervals=["1m"], sleep=lambda _: None)
    last = load_archive(c2.paths.data_archive, "GC_F", "1m").index.max()
    # complete if ts + 1m + 2m grace + 20m delay <= 16:00 -> ts <= 15:37; newest dropped -> 15:36
    assert last == pd.Timestamp("2026-11-06T15:36:00Z")


def _patch_yf(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    import yfinance

    class T:
        def __init__(self, symbol: str) -> None:
            pass

        def history(self, **kwargs: Any) -> pd.DataFrame:
            raise exc

    monkeypatch.setattr(yfinance, "Ticker", T)


def _real_history(self: YFinanceSource, *a: Any) -> pd.DataFrame:
    return _ORIGINAL_HISTORY(self, *a)


def test_yfinance_empty_window_is_no_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    from yfinance.exceptions import YFPricesMissingError

    monkeypatch.setattr(YFinanceSource, "history", _real_history)
    _patch_yf(monkeypatch, YFPricesMissingError("GLD", "(5m weekend)"))
    out = YFinanceSource().history("GLD", "5m", NOW, NOW + pd.Timedelta(hours=1), False)
    assert out.empty


def test_yfinance_limit_and_other_errors_are_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yfinance.exceptions import YFPricesMissingError

    monkeypatch.setattr(YFinanceSource, "history", _real_history)

    _patch_yf(
        monkeypatch,
        YFPricesMissingError(
            "GLD", "", "5m data not available. The requested range must be within the last 60 days"
        ),
    )
    with pytest.raises(HistoryLimitError):
        YFinanceSource().history("GLD", "5m", NOW, NOW + pd.Timedelta(hours=1), False)
    _patch_yf(monkeypatch, YFPricesMissingError("GLD", "", "Internal Server Error"))
    with pytest.raises(ProviderError):
        YFinanceSource().history("GLD", "5m", NOW, NOW + pd.Timedelta(hours=1), False)


# ---------------------------------------------------------------- CLI


def test_cli_fetch_exit_codes(
    cfg: Config, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml

    raw = yaml.safe_load((cli.Path(__file__).resolve().parents[1] / "config.yaml").read_text())
    raw["paths"] = {k: str(tmp_path / k) for k in raw["paths"]}
    raw["data"]["request_pause_sec"] = 0
    conf = tmp_path / "config.yaml"
    conf.write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(cli, "_source_factory", lambda: FakeSource(_frames()))
    monkeypatch.setattr(cli, "run_update", functools.partial(run_update, sleep=lambda _: None))
    assert cli.main(["--config", str(conf), "fetch", "--update"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["data_hash_after"]
    monkeypatch.setattr(cli, "_source_factory", lambda: _FailFor(_frames(), "GC=F"))
    assert cli.main(["--config", str(conf), "fetch", "--update"]) == 1
    log_text = (tmp_path / "logs" / "paper_lab.log").read_text()
    assert "run start command=fetch" in log_text and "config_hash" in log_text
    assert cli.main(["--config", str(conf), "validate"]) in (0, 1)
    assert list((tmp_path / "reports").glob("data_quality_*.json"))


# ---------------------------------------------------------------- validator


def _gc_continuous(days: list[date]) -> pd.DataFrame:
    """23h GC-like 5m bars: Sun-Thu 18:00 -> next day 17:00 ET."""
    parts = []
    for d in days:  # d = trading date; session opens 18:00 ET the evening before
        prev = d - pd.Timedelta(days=1)
        parts.append(synth_bars([prev], time(18, 0), time(23, 55), "5m", seed=d.day))
        parts.append(synth_bars([d], time(0, 0), time(17, 0), "5m", seed=d.day + 50))
    return pd.concat(parts).sort_index()


DAYS = [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)]


def test_gc_clean_has_no_timezone_error(cfg: Config) -> None:
    rep = validate_series(_gc_continuous(DAYS), cfg.instrument("GC=F"), "5m", cfg)
    assert not [i for i in rep.issues if i.severity == "error"], rep.issues


@pytest.mark.parametrize("hours", [1, -1, 4, -5])
def test_gc_timezone_shift_is_an_error(cfg: Config, hours: int) -> None:
    df = _gc_continuous(DAYS)
    df.index = df.index + pd.Timedelta(hours=hours)
    rep = validate_series(df, cfg.instrument("GC=F"), "5m", cfg)
    assert {"timezone_shift": "error"}.items() <= {i.check: i.severity for i in rep.issues}.items()


def test_gld_partial_one_hour_shift_is_an_error(cfg: Config) -> None:
    df = synth_bars(DAYS, time(9, 30), time(16, 0), "5m")
    et_date = df.index.tz_convert("America/New_York").date
    shifted = df[et_date == DAYS[1]].copy()
    shifted.index = shifted.index + pd.Timedelta(hours=1)  # one day with a DST-style mistake
    df = pd.concat([df[et_date != DAYS[1]], shifted]).sort_index()
    rep = validate_series(df, cfg.instrument("GLD"), "5m", cfg)
    sev = {i.check: i.severity for i in rep.issues}
    assert sev["outside_trading_hours"] == "error"


def test_gc_saturday_bars_are_outside_hours(cfg: Config) -> None:
    df = _gc_continuous(DAYS)
    sat = synth_bars([date(2026, 11, 7)], time(10, 0), time(10, 30), "5m", seed=9)
    rep = validate_series(pd.concat([df, sat]).sort_index(), cfg.instrument("GC=F"), "5m", cfg)
    issue = next(i for i in rep.issues if i.check == "outside_trading_hours")
    assert issue.count == len(sat)


def test_price_jump_with_normal_range_is_flagged(cfg: Config) -> None:
    df = synth_bars(DAYS, time(9, 30), time(16, 0), "5m")
    i = 40
    shift = 300.0  # a bad print: whole bar displaced, its own range is normal
    for col in ("open", "high", "low", "close"):
        df.iloc[i, df.columns.get_loc(col)] += shift
    rep = validate_series(df, cfg.instrument("GLD"), "5m", cfg)
    checks = {x.check for x in rep.issues}
    assert "price_jump" in checks and "absurd_candle" not in checks


def test_network_is_blocked_in_tests() -> None:
    """Regression: a CLI test once fetched real data because the source was not injected."""
    import socket

    with pytest.raises(RuntimeError, match="network access"):
        socket.create_connection(("example.com", 80), timeout=1)


def test_yfinance_source_is_blocked_in_tests() -> None:
    with pytest.raises(RuntimeError, match="network access"):
        YFinanceSource().history("GLD", "5m", NOW, NOW + pd.Timedelta(hours=1), False)
