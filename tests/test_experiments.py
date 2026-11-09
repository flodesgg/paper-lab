"""Pre-registration: experiment files, decision rule, trial log, OOS lock, backtest command."""

from __future__ import annotations

import re
import shutil
from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest
from conftest import REPO

from paper_lab.analysis.experiment import (
    Criteria,
    ExperimentError,
    check_runnable,
    load_experiment,
)
from paper_lab.analysis.run import decide
from paper_lab.analysis.trials import OosLockedError, count_trials, log_trials, open_oos
from paper_lab.config import load_config

CFG, _ = load_config(REPO / "config.yaml")
EXP_DIR = REPO / "experiments"
C = Criteria(
    min_trades=30, success_ci_low_above=0.0, success_mean_x2_above=0.0, failure_ci_high_below=0.0
)


def test_repo_experiments_parse_and_pin_current_config() -> None:
    """An experiment that is still draft or approved must match the current config: changing
    a parameter without a new experiment makes this test fail."""
    files = sorted(p for p in EXP_DIR.glob("EXP-[0-9][0-9][0-9].md") if p.stem != "EXP-000")
    assert files, "EXP-001 must exist"
    for p in files:
        exp, body = load_experiment(p)
        assert exp.id == p.stem and body.strip()
        if exp.status in ("draft", "approved"):
            assert exp.strategy_hash == CFG.strategy_hash(), f"{p.name} pins an old config"


def test_approved_experiments_name_who_and_when() -> None:
    for p in EXP_DIR.glob("EXP-[0-9][0-9][0-9].md"):
        exp, _ = load_experiment(p)
        if exp.status in ("approved", "done"):
            assert exp.approved_by and exp.approved_on, p.name


def test_draft_is_refused() -> None:
    exp, _ = load_experiment(EXP_DIR / "EXP-001.md")
    draft = exp.model_copy(update={"status": "draft"})
    with pytest.raises(ExperimentError, match="only 'approved' runs"):
        check_runnable(draft, CFG)


def _write(tmp: Path, **changes: object) -> Path:
    text = (EXP_DIR / "EXP-001.md").read_text()
    for k, v in changes.items():
        text = re.sub(rf"^{k}:.*$", f"{k}: {v}", text, count=1, flags=re.M)
    p = tmp / "EXP-001.md"
    p.write_text(text)
    return p


def test_approval_needs_name_and_date_and_unchanged_config(tmp_path: Path) -> None:
    exp, _ = load_experiment(_write(tmp_path, status="approved"))
    with pytest.raises(ExperimentError, match="approved_by and approved_on"):
        check_runnable(exp, CFG)
    exp, _ = load_experiment(
        _write(
            tmp_path,
            status="approved",
            approved_by="Nick",
            approved_on="2026-11-10",
            strategy_hash='"deadbeef0000"',
        )
    )
    with pytest.raises(ExperimentError, match="parameters changed after registration"):
        check_runnable(exp, CFG)
    exp, _ = load_experiment(
        _write(tmp_path, status="approved", approved_by="Nick", approved_on="2026-11-10")
    )
    check_runnable(exp, CFG)


def test_invalid_experiment_definitions(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="in-sample"):
        load_experiment(_write(tmp_path, window="out_of_sample", grid="{orb.k: [1.5, 2.0]}"))
    with pytest.raises(ValueError, match="cost_mults"):
        load_experiment(_write(tmp_path, cost_mults="[0, 1]"))
    with pytest.raises(ValueError, match="extra"):
        load_experiment(_write(tmp_path, title="x\nsurprise: 1"))
    with pytest.raises(ExperimentError, match="not found"):
        load_experiment(tmp_path / "EXP-999.md")


def test_decision_rule_order() -> None:
    assert decide(C, 29, (0.5, 0.9), 0.5)[0] == "INCONCLUSIVE"  # sample size first
    assert decide(C, 40, (0.05, 0.4), 0.1)[0] == "SUCCESS"
    assert decide(C, 40, (0.05, 0.4), -0.01)[0] == "INCONCLUSIVE"  # fails at costs x2
    assert decide(C, 40, (-0.4, -0.05), -0.3)[0] == "FAILURE"
    assert decide(C, 40, (-0.1, 0.3), 0.0)[0] == "INCONCLUSIVE"


def test_trial_log_counts_unique_trials(tmp_path: Path) -> None:
    row = {
        "logged_utc": "t",
        "experiment": "EXP-001",
        "setup": "orb",
        "symbol": "GC=F",
        "window": "in_sample",
        "window_start": "2026-09-11",
        "window_end": "2026-11-06",
        "news_guard": "config",
        "cost_mult": 1.0,
        "params_hash": "abc",
        "params_json": "{}",
        "trades": 10,
        "mean_r_net": 0.1,
        "sharpe_per_trade": 0.1,
        "run_id": "r",
        "git_commit": "c",
        "data_hash": "d",
    }
    p = tmp_path / "trial_log.csv"
    log_trials(p, [dict(row), dict(row, cost_mult=2.0)])
    log_trials(p, [dict(row, run_id="r2")])  # identical trial re-run: replaced, not added
    assert count_trials(p) == 2


def test_oos_opens_once_per_experiment(tmp_path: Path) -> None:
    p = tmp_path / "oos_access.csv"
    row = {
        "opened_utc": "t1",
        "run_id": "r",
        "git_commit": "c",
        "window_start": "a",
        "window_end": "b",
    }
    open_oos(p, "EXP-002", row)
    with pytest.raises(OosLockedError, match="once per experiment"):
        open_oos(p, "EXP-002", row)
    open_oos(p, "EXP-003", row)
    assert len(pd.read_csv(p)) == 2


def test_cli_backtest_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Approved copy of EXP-001 on a synthetic archive: report written, 12 trials logged,
    draft refused; an OOS experiment opens its window once."""
    from paper_lab.calendar.sessions import trading_days
    from paper_lab.cli import main
    from paper_lab.data.archive import merge_into_archive
    from paper_lab.data.synthetic import resample_bars, synthetic_bars

    monkeypatch.chdir(tmp_path)
    shutil.copytree(REPO / "data" / "calendar", tmp_path / "data" / "calendar")
    (tmp_path / "experiments").mkdir()
    days = trading_days("XNYS", date(2026, 7, 1), date(2026, 11, 20))
    seen = pd.Timestamp("2026-11-21", tz="UTC")
    for sym, stem, base in (("GC=F", "GC_F", 4000.0), ("GLD", "GLD", 370.0)):
        b5 = synthetic_bars(days, "America/New_York", base=base, seed=5)
        b1 = resample_bars(b5, "1h", "America/New_York", time(9, 30))
        merge_into_archive(tmp_path / "data_archive", sym, stem, "5m", b5, seen)
        merge_into_archive(tmp_path / "data_archive", sym, stem, "1h", b1, seen)
    cfgp = str(REPO / "config.yaml")

    _write(tmp_path / "experiments", status="draft")
    rc = main(["--config", cfgp, "backtest", "--experiment", "experiments/EXP-001.md"])
    assert rc == 2 and "only 'approved' runs" in capsys.readouterr().err

    _write(
        tmp_path / "experiments", status="approved", approved_by="Nick", approved_on="2026-11-10"
    )
    rc = main(["--config", cfgp, "backtest", "--experiment", "experiments/EXP-001.md"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "**Data:** in-sample window, 41 trading days (2026-09-11 .. 2026-11-06)" in out
    assert "Pre-registered decision:" in out
    assert count_trials(tmp_path / "experiments" / "trial_log.csv") == 12
    assert not (tmp_path / "experiments" / "oos_access.csv").exists()

    oos = _write(
        tmp_path / "experiments",
        status="approved",
        approved_by="Nick",
        approved_on="2026-11-10",
        window="out_of_sample",
        id="EXP-002",
    )
    oos.rename(tmp_path / "experiments" / "EXP-002.md")
    args = ["--config", cfgp, "backtest", "--experiment", "experiments/EXP-002.md"]
    assert main(args) == 0
    capsys.readouterr()
    assert main(args) == 2 and "once per experiment" in capsys.readouterr().err


def test_bare_yaml_off_is_reported_clearly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='write "off" in quotes'):
        load_experiment(_write(tmp_path, news_guard_variants="[config, off]"))
