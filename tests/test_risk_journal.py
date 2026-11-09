"""Risk gate, backtest wiring and journal files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from scenario import CFG, DAY, REPO, SESSION, bars, history, ts

from paper_lab.calendar.events import load_events
from paper_lab.calendar.news_guard import NewsGuard
from paper_lab.config import RiskConfig
from paper_lab.execution.backtest import run_backtest
from paper_lab.journal.writer import DAILY_COLUMNS, TRADE_COLUMNS, upsert
from paper_lab.risk.manager import DayRisk

RISK = RiskConfig(
    paper_capital_usd=100_000,
    risk_per_trade_pct=0.5,
    daily_loss_limit_pct=1.5,
    max_trades_per_day=2,
    max_stops_per_day=3,
    max_notional_multiple=1.0,
)
T0 = ts(DAY, "10:00")


def test_one_position_at_a_time() -> None:
    r = DayRisk(RISK)
    r.record(-100.0, True, flat_after=ts(DAY, "10:30"))
    assert r.block_reason(ts(DAY, "10:25"), 500) == "position already open"
    assert r.block_reason(ts(DAY, "10:30"), 500) is None


def test_max_trades_per_day() -> None:
    r = DayRisk(RISK)
    r.record(50.0, False, T0)
    r.record(50.0, False, T0)
    assert r.block_reason(T0, 100) == "max 2 trades per day" and r.halted


def test_three_stops_end_the_day() -> None:
    r = DayRisk(RISK.model_copy(update={"max_trades_per_day": 10}))
    for _ in range(3):
        r.record(-10.0, True, T0)
    assert r.block_reason(T0, 1.0) == "3 stops: day is over"


def test_daily_loss_limit_reached() -> None:
    """Limit 1.5 % x 100 000 = 1500 USD; realized -1500 -> no more entries."""
    r = DayRisk(RISK.model_copy(update={"max_trades_per_day": 10, "max_stops_per_day": 10}))
    r.record(-1000.0, True, T0)
    r.record(-500.0, False, T0)
    reason = r.block_reason(T0, 1.0)
    assert reason is not None and reason.startswith("daily loss limit reached")


def test_trade_that_could_breach_the_limit_is_refused() -> None:
    """Realized -1100; a new trade risking 500 could end at -1600 > -1500 -> refused,
    a trade risking 400 (worst case -1500) is allowed. Not a halt: the day goes on."""
    r = DayRisk(RISK.model_copy(update={"max_trades_per_day": 10}))
    r.record(-1100.0, True, T0)
    assert "would breach" in (r.block_reason(T0, 500.0) or "")
    assert r.block_reason(T0, 400.0) is None and r.halted == ""


def _guard() -> NewsGuard:
    return NewsGuard(
        CFG.news_guard, SESSION, load_events([REPO / p for p in CFG.calendar.events_files])
    )


def _orb_day() -> tuple[pd.DataFrame, pd.DataFrame]:
    """GLD-like day: OR 99-102 (0.3 ATR), long break 09:45 (2x volume), entry 09:50 open
    102.6, then a stop-out. History: 10 flat sessions + 20 days of 1h bars with TR 10."""
    rows = [
        ("09:30", 100.0, 101.0, 99.5, 100.5, 100),
        ("09:35", 100.5, 102.0, 100.0, 101.0, 100),
        ("09:40", 101.0, 101.5, 99.0, 100.0, 100),
        ("09:45", 100.0, 102.6, 99.8, 102.5, 200),
        ("09:50", 102.6, 103.0, 102.4, 102.9, 100),
        ("09:55", 102.9, 103.0, 98.0, 98.5, 100),
    ]
    b5 = pd.concat([history(DAY), bars(DAY, rows)])
    days_1h = sorted(
        {d.date() for d in history(DAY, n_sessions=20).index.tz_convert(SESSION.timezone)}
    )
    b1 = pd.concat([bars(d, [("09:30", 100.0, 105.0, 95.0, 100.0, 1000)]) for d in days_1h])
    return b5, b1


def test_backtest_rows_and_journal_roundtrip(tmp_path: Path) -> None:
    from paper_lab.setups.registry import build_setup

    b5, b1 = _orb_day()
    setup = build_setup("orb", CFG.setups["orb"])
    res = run_backtest(
        CFG,
        [setup],
        CFG.instrument("GLD"),
        {"5m": b5},
        b1,
        [DAY],
        _guard(),
        _guard(),
        cost_mult=1.0,
        run_id="RUN1",
    )
    t = res.trades
    assert list(t.columns) == TRADE_COLUMNS and len(t) == 1
    row = t.iloc[0]
    # size is decided BEFORE the fill, from the signal close: 102.5 + 0.015 = 102.515;
    # 500 / (102.515 - 99.0) = 142.2 -> 142 by risk; 100 000 / 102.515 = 975 by value -> 142
    # fill: 09:50 open 102.6 + 0.015 = 102.615; 09:55 low 98.0 <= 99 -> exit 98.975
    assert (row.status, row.qty, row.exit_reason) == ("filled", 142, "stop")
    assert row.entry == pytest.approx(102.615) and row.exit == pytest.approx(98.975)
    assert row.r_gross == pytest.approx(-1.0) and row.r_net < -1.0
    d = res.daily.iloc[0]
    assert (d.trades, d.stops, d.losses) == (1, 1, 1)

    path = tmp_path / "journal" / "trades.csv"
    upsert(path, t, "trade_id", TRADE_COLUMNS)
    again = upsert(path, t, "trade_id", TRADE_COLUMNS)  # re-running a day is idempotent
    assert len(again) == 1
    daily = upsert(tmp_path / "journal" / "daily.csv", res.daily, "day_id", DAILY_COLUMNS)
    assert daily["day_id"].tolist() == [f"{DAY.isoformat()}|GLD"]


def test_backtest_cost_multiplier_zero_matches_gross() -> None:
    from paper_lab.setups.registry import build_setup

    b5, b1 = _orb_day()
    res = run_backtest(
        CFG,
        [build_setup("orb", CFG.setups["orb"])],
        CFG.instrument("GLD"),
        {"5m": b5},
        b1,
        [DAY],
        _guard(),
        _guard(),
        cost_mult=0.0,
        run_id="RUN0",
    )
    row = res.trades.iloc[0]
    assert row.r_net == pytest.approx(row.r_gross) and row.costs_usd == 0.0


def test_journal_rejects_foreign_schema(tmp_path: Path) -> None:
    p = tmp_path / "trades.csv"
    pd.DataFrame({"trade_id": ["x"], "other": [1]}).to_csv(p, index=False)
    rows = pd.DataFrame([{c: None for c in TRADE_COLUMNS} | {"trade_id": "y"}])
    with pytest.raises(ValueError, match="columns differ"):
        upsert(p, rows, "trade_id", TRADE_COLUMNS)


def test_cli_costs_synthetic_and_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    from paper_lab.cli import main

    monkeypatch.chdir(tmp_path)
    shutil.copytree(REPO / "data" / "calendar", tmp_path / "data" / "calendar")
    cfgp = str(REPO / "config.yaml")
    rc = main(["--config", cfgp, "costs", "--synthetic", "--symbols", "GLD"])
    out = capsys.readouterr().out
    assert rc == 0 and "SYNTHETIC" in out
    lines = [ln for ln in out.splitlines() if ln.startswith("| GLD")]
    assert [ln.split("|")[2].strip() for ln in lines] == ["0", "1", "2"]
    trades = [int(ln.split("|")[3]) for ln in lines]
    assert trades[0] > 10 and len(set(trades)) == 1  # same trades, only costs differ
    r_net = [float(ln.split("|")[6]) for ln in lines]
    assert r_net[0] > r_net[1] > r_net[2]  # more costs, less R
    # real data only runs through an approved experiment: `costs` has no real-data mode
    with pytest.raises(SystemExit) as e:
        main(["--config", cfgp, "costs"])
    assert e.value.code == 2 and "--synthetic" in capsys.readouterr().err
