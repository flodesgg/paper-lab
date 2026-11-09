from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from paper_lab.cli import main
from paper_lab.config import LoggingConfig
from paper_lab.logging_setup import setup_logging
from paper_lab.runinfo import new_run_info

REPO = Path(__file__).resolve().parents[1]


def test_logging_writes_run_id_and_rotates(tmp_path: Path) -> None:
    cfg = LoggingConfig(level="INFO", file="t.log", max_bytes=200, backup_count=2)
    log = setup_logging(cfg, tmp_path, run_id="RUN123")
    for i in range(20):
        log.info("line %d with some padding text", i)
    for h in log.handlers:
        h.flush()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert "t.log" in files
    assert "t.log.1" in files  # rotation happened
    assert len(files) <= 3  # main + backup_count
    assert "[run=RUN123]" in (tmp_path / "t.log").read_text()
    # idempotent: second setup does not duplicate handlers
    log2 = setup_logging(cfg, tmp_path, run_id="RUN456")
    assert len(log2.handlers) == 2
    for h in log2.handlers:
        h.close()
    logging.getLogger("paper_lab").handlers.clear()


def test_run_info_fields() -> None:
    info = new_run_info("abc123", repo=REPO)
    d = info.as_dict()
    assert d["config_hash"] == "abc123"
    assert isinstance(d["git_commit"], str) and d["git_commit"]
    assert str(d["run_id"]).endswith(info.run_id[-6:])


def test_cli_check_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # logs/ is created in tmp, not in the repo
    rc = main(["--config", str(REPO / "config.yaml"), "check-config"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert {"run_id", "git_commit", "config_hash"} <= out.keys()
    assert (tmp_path / "logs" / "paper_lab.log").exists()
    logging.getLogger("paper_lab").handlers.clear()


def test_secrets_and_cache_are_gitignored() -> None:
    ignored = (REPO / ".gitignore").read_text().splitlines()
    for entry in (".env", "data/", "logs/"):
        assert entry in ignored
