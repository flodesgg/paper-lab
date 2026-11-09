"""Stage F: forward paper loop, weekly report, alarm and freeze rules, git and launchd."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest
from conftest import FIXTURES, REPO

from paper_lab.analysis.freeze import (
    ALARM_TEXT,
    FreezeError,
    disable_in_config_text,
    freeze,
    frozen_violations,
)
from paper_lab.analysis.metrics import compute_metrics, suspicious
from paper_lab.config import load_config
from paper_lab.live.agent import CommitError, commit_outputs, launch_agent_plist
from paper_lab.live.forward import ForwardBusyError, forward_lock
from paper_lab.reports.weekly import write_weekly
from paper_lab.runinfo import new_run_info

CFG, _ = load_config(REPO / "config.yaml")
CFGP = str(REPO / "config.yaml")


def _synthetic_archive(root: Path) -> None:
    """Synthetic 5m + 1h archive for both instruments, 2026-07-01 .. 2026-11-20."""
    from paper_lab.calendar.sessions import trading_days
    from paper_lab.data.archive import merge_into_archive
    from paper_lab.data.synthetic import resample_bars, synthetic_bars

    shutil.copytree(REPO / "data" / "calendar", root / "data" / "calendar")
    days = trading_days("XNYS", date(2026, 7, 1), date(2026, 11, 20))
    seen = pd.Timestamp("2026-11-21", tz="UTC")
    for sym, stem, base, seed in (("GC=F", "GC_F", 4000.0, 7), ("GLD", "GLD", 370.0, 8)):
        b5 = synthetic_bars(days, "America/New_York", base=base, seed=seed)
        b1 = resample_bars(b5, "1h", "America/New_York", time(9, 30))
        merge_into_archive(root / "data_archive", sym, stem, "5m", b5, seen)
        merge_into_archive(root / "data_archive", sym, stem, "1h", b1, seen)


def _forward(as_of: str, *extra: str) -> int:
    from paper_lab.cli import main

    return main(["--config", CFGP, "forward", "--no-fetch", "--as-of", as_of, *extra])


def _daily(root: Path) -> pd.DataFrame:
    return pd.read_csv(root / "journal" / "daily.csv")


def test_three_simulated_days_catch_up_and_idempotence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Acceptance (spec F): the loop runs for simulated days and the journal grows; a day
    missed while the Mac was off is caught up; re-running changes nothing."""
    monkeypatch.chdir(tmp_path)
    _synthetic_archive(tmp_path)

    assert _forward("2026-11-09T16:00:00Z") == 0  # 11:00 ET: session still running
    assert "waiting for data" in capsys.readouterr().out
    assert not (tmp_path / "journal" / "daily.csv").exists()

    assert _forward("2026-11-09T17:30:00Z") == 0  # day 1, after the 12:00 ET close
    assert sorted(_daily(tmp_path)["day_id"]) == ["2026-11-09|GC=F", "2026-11-09|GLD"]
    assert _forward("2026-11-10T17:30:00Z") == 0  # day 2
    assert len(_daily(tmp_path)) == 4
    # day 3 (11-11) missed: Mac off. Next run on 11-12 catches up both sessions.
    assert _forward("2026-11-12T17:30:00Z") == 0
    days = sorted(set(_daily(tmp_path)["day"]))
    assert days == ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]

    before = {p.name: p.read_bytes() for p in (tmp_path / "journal").glob("[dt]*.csv")}
    capsys.readouterr()
    assert _forward("2026-11-12T21:00:00Z") == 0  # second run the same day
    assert "nothing new" in capsys.readouterr().out
    after = {p.name: p.read_bytes() for p in (tmp_path / "journal").glob("[dt]*.csv")}
    assert before == after
    runs = pd.read_csv(tmp_path / "journal" / "runs.csv")
    assert list(runs["days_processed"])[-1] == 0 and len(runs) == 5
    assert not list((tmp_path / "reports").glob("weekly_*.md"))  # week not finished yet

    assert _forward("2026-11-14T12:00:00Z") == 0  # Saturday: Friday done -> weekly report
    report = tmp_path / "reports" / "weekly_2026-11-13.md"
    text = report.read_text()
    assert text.index("## Limitations (read first)") < text.index("## Forward results")
    assert "not evaluated yet" in text and "5/5 sessions in the journal" in text
    assert "baseline" in text.lower()
    assert _forward("2026-11-14T13:00:00Z") == 0
    assert report.read_text() == text  # not rewritten


def test_forward_regression_against_stored_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §6: the full pipeline on a small fixture must reproduce a stored result."""
    monkeypatch.chdir(tmp_path)
    _synthetic_archive(tmp_path)
    assert _forward("2026-11-21T00:00:00Z") == 0
    got = pd.read_csv(tmp_path / "journal" / "trades.csv").drop(columns=["run_id"])
    expected_path = FIXTURES / "expected_forward_trades.csv"
    if os.environ.get("PAPER_LAB_REGEN"):
        got.to_csv(expected_path, index=False)
    expected = pd.read_csv(expected_path)
    assert len(expected) >= 5, "the fixture should contain real trades"
    pd.testing.assert_frame_equal(got, expected, check_dtype=False)


def test_forward_lock_refuses_a_second_run(tmp_path: Path) -> None:
    lock = tmp_path / "forward.lock"
    with forward_lock(lock), pytest.raises(ForwardBusyError), forward_lock(lock):
        pass


def _trades(r: list[float], day: str = "2026-12-01") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "day": [day] * len(r),
            "setup": "orb",
            "symbol": "GC=F",
            "status": "filled",
            "side": "long",
            "qty": 1,
            "entry": 4000.0,
            "exit": 4001.0,
            "exit_reason": "time",
            "r_net": r,
            "hold_min": 60.0,
        }
    )


def _weekly(tmp_path: Path, trades: pd.DataFrame) -> str:
    daily = pd.DataFrame(columns=["day_id", "day", "symbol", "signals", "setups"])
    info = new_run_info("test")
    p = write_weekly(CFG, date(2026, 12, 4), trades, daily, {}, info, tmp_path / "w.md")
    return p.read_text()


def test_alarm_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)  # the EXP-001 baseline files are read from the repo
    alarm = _weekly(tmp_path, _trades([-0.2] * 20))
    assert f"**{ALARM_TEXT}** — `orb` GC=F" in alarm and "paper-lab freeze --setup orb" in alarm
    assert "| 26 | -0.050 |" in alarm  # EXP-001 baseline next to the forward numbers
    few = _weekly(tmp_path, _trades([-0.2] * 19))
    assert ALARM_TEXT not in few and "not evaluated yet (19 < 20" in few
    fine = _weekly(tmp_path, _trades([0.1, -0.05] * 10))
    assert ALARM_TEXT not in fine and "no alarm" in fine
    edge = _weekly(tmp_path, _trades([-0.1] * 20))  # exactly -0.10 is not below -0.10
    assert ALARM_TEXT not in edge


def test_suspicious_results_are_flagged() -> None:
    good = compute_metrics(pd.Series([1.0, 1.0, 1.0, -0.2] * 10))
    notes = suspicious(good, sessions=40)
    assert any("profit factor" in n for n in notes) and any("Sharpe" in n for n in notes)
    plain = compute_metrics(pd.Series([1.0, -1.0, 0.5, -0.6] * 10))
    assert suspicious(plain, sessions=40) == []
    tiny = compute_metrics(pd.Series([1.0, 1.0, 2.0, -0.2]))  # too few trades to judge
    assert suspicious(tiny, sessions=4) == []


def test_backtest_report_puts_limitations_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from paper_lab.cli import main

    monkeypatch.chdir(tmp_path)
    shutil.copytree(REPO / "data" / "calendar", tmp_path / "data" / "calendar")
    assert main(["--config", CFGP, "backtest", "--synthetic"]) == 0
    out = capsys.readouterr().out
    assert out.index("## Limitations (read first)") < out.index("**Verdict:**")
    assert out.index("**Verdict:**") < out.index("## GC=F, news guard")


# --- freeze rule -------------------------------------------------------------------------


def _freeze_repo(tmp_path: Path, decision: str) -> Path:
    shutil.copy(REPO / "config.yaml", tmp_path / "config.yaml")
    exp_dir = tmp_path / "experiments"
    exp_dir.mkdir()
    text = (REPO / "experiments" / "EXP-001.md").read_text()
    text = text.replace("id: EXP-001", "id: EXP-002").replace(
        "decision: INCONCLUSIVE", f"decision: {decision}"
    )
    (exp_dir / "EXP-002.md").write_text(text)
    shutil.copy(REPO / "experiments" / "EXP-001.md", exp_dir / "EXP-001.md")
    return exp_dir


def test_disable_edits_only_the_enabled_flag() -> None:
    text = (REPO / "config.yaml").read_text()
    new = disable_in_config_text(text, "orb")
    diff = [(a, b) for a, b in zip(text.splitlines(), new.splitlines(), strict=True) if a != b]
    assert len(diff) == 1 and "enabled: true" in diff[0][0] and "enabled: false" in diff[0][1]
    with pytest.raises(FreezeError, match="no 'enabled: true'"):
        disable_in_config_text(text, "momentum")  # already disabled


def test_freeze_needs_a_failure_or_an_alarm(tmp_path: Path) -> None:
    exp_dir = _freeze_repo(tmp_path, "FAILURE")
    cfgp = tmp_path / "config.yaml"
    today = date(2026, 12, 1)
    with pytest.raises(FreezeError, match="only a FAILURE"):
        freeze(cfgp, exp_dir, tmp_path, "orb", "EXP-001", "x", "c", today)
    weekly = tmp_path / "reports" / "weekly_2026-11-27.md"
    weekly.parent.mkdir()
    weekly.write_text("no alarm here\n")
    with pytest.raises(FreezeError, match="no ALARM line"):
        freeze(cfgp, exp_dir, tmp_path, "orb", "reports/weekly_2026-11-27.md", "x", "c", today)
    weekly.write_text(f"- **{ALARM_TEXT}** — `orb` GC=F: -0.3 R\n")
    freeze(cfgp, exp_dir, tmp_path, "orb", "reports/weekly_2026-11-27.md", "alarm", "c", today)
    cfg, _ = load_config(cfgp)
    assert not cfg.setups["orb"].enabled
    assert cfg.strategy_hash() != CFG.strategy_hash()  # behaviour changed: new hash
    with pytest.raises(FreezeError, match="already disabled"):
        freeze(cfgp, exp_dir, tmp_path, "orb", "EXP-002", "again", "c", today)


def test_frozen_setup_stays_off_until_a_new_experiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exp_dir = _freeze_repo(tmp_path, "FAILURE")
    cfgp = tmp_path / "config.yaml"
    freeze(cfgp, exp_dir, tmp_path, "orb", "EXP-002", "failed", "c", date(2026, 12, 1))
    # someone flips it back by hand, without an experiment
    cfgp.write_text(cfgp.read_text().replace("enabled: false", "enabled: true", 1))
    cfg, _ = load_config(cfgp)
    assert cfg.setups["orb"].enabled
    assert frozen_violations(cfg, exp_dir)

    monkeypatch.chdir(tmp_path)
    shutil.copytree(REPO / "data" / "calendar", tmp_path / "data" / "calendar")
    from paper_lab.cli import main

    assert main(["--config", str(cfgp), "forward", "--no-fetch"]) == 2
    assert "frozen" in capsys.readouterr().err

    # a new experiment approved after the freeze that tests orb re-enables it
    text = (exp_dir / "EXP-002.md").read_text()
    text = text.replace("id: EXP-002", "id: EXP-003").replace(
        "approved_on: 2026-11-09", "approved_on: 2026-12-02"
    )
    (exp_dir / "EXP-003.md").write_text(text)
    assert frozen_violations(cfg, exp_dir) == []


# --- git and launchd ---------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def test_commit_outputs_commits_only_its_own_paths(tmp_path: Path) -> None:
    from paper_lab.live.agent import push

    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "test")
    _git(repo, "remote", "add", "origin", str(remote))
    (repo / "README").write_text("x")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "push", "-q", "-u", "origin", "main")

    assert commit_outputs(repo, "nothing") is None
    (repo / "journal").mkdir()
    (repo / "journal" / "daily.csv").write_text("a\n")
    (repo / "reports").mkdir()
    (repo / "reports" / "weekly_2026-11-13.md").write_text("w\n")
    (repo / "reports" / "other.md").write_text("not mine\n")
    (repo / "notes.txt").write_text("user work in progress\n")
    _git(repo, "add", "notes.txt")  # staged by the user: must not be committed by the loop

    commit = commit_outputs(repo, "forward test")
    assert commit
    files = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(files) == ["journal/daily.csv", "reports/weekly_2026-11-13.md"]
    assert "notes.txt" in _git(repo, "diff", "--cached", "--name-only")
    ok, _ = push(repo)
    assert ok and _git(remote, "log", "--oneline", "-1", "main").startswith(commit[:7])

    big = repo / "journal" / "big.csv"
    with big.open("wb") as fh:
        fh.truncate(21 * 1024 * 1024)
    with pytest.raises(CommitError, match="20 MB"):
        commit_outputs(repo, "too big")
    assert "big.csv" not in _git(repo, "diff", "--cached", "--name-only")


def test_launch_agent_plist() -> None:
    raw = launch_agent_plist(REPO, Path("/opt/homebrew/bin/uv"), CFG.forward.schedule_local)
    p = plistlib.loads(raw)
    assert p["Label"] == "com.paperlab.forward" and p["RunAtLoad"] is True
    assert p["StartCalendarInterval"] == [{"Hour": 18, "Minute": 45}, {"Hour": 21, "Minute": 30}]
    assert p["ProgramArguments"][-1].endswith("paper-lab forward --commit --push")
    assert "live" not in p["ProgramArguments"][-1]


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not available")
def test_agent_script_syntax_and_usage() -> None:
    script = REPO / "scripts" / "agent.sh"
    subprocess.run(["zsh", "-n", str(script)], check=True)
    r = subprocess.run(["zsh", str(script)], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr
