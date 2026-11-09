from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from paper_lab.config import config_hash, load_config

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def _raw() -> dict[str, Any]:
    with REPO_CONFIG.open() as f:
        data: dict[str, Any] = yaml.safe_load(f)
    return data


def _write(tmp_path: Path, raw: dict[str, Any]) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


def test_repo_config_is_valid() -> None:
    cfg, h = load_config(REPO_CONFIG)
    assert cfg.project.paper_only is True
    assert cfg.session.timezone == "America/New_York"
    assert cfg.session.start == time(9, 30)
    assert cfg.session.end == time(12, 0)
    assert [i.symbol for i in cfg.instruments] == ["GC=F", "GLD"]
    assert len(h) == 12


def test_decided_risk_defaults() -> None:
    cfg, _ = load_config(REPO_CONFIG)
    assert cfg.risk.paper_capital_usd == 100_000
    assert cfg.risk.risk_per_trade_pct == 0.5
    assert cfg.risk.daily_loss_limit_pct == 1.5
    assert cfg.risk.max_trades_per_day == 2
    assert cfg.risk.max_stops_per_day == 3


def test_paper_only_false_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["project"]["paper_only"] = False
    with pytest.raises(ValidationError, match="paper_only"):
        load_config(_write(tmp_path, raw))


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["risk"]["risk_per_trade_pc"] = 1.0  # typo must not be silently ignored
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, raw))


def test_session_start_after_end_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["session"]["start"] = "12:30"
    with pytest.raises(ValidationError, match=r"start must be before"):
        load_config(_write(tmp_path, raw))


def test_bad_timezone_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["session"]["timezone"] = "Mars/Olympus"
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, raw))


def test_two_primaries_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["instruments"][1]["role"] = "primary"
    with pytest.raises(ValidationError, match="primary"):
        load_config(_write(tmp_path, raw))


def test_excessive_risk_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["risk"]["risk_per_trade_pct"] = 5.0
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, raw))


def test_config_hash_is_key_order_independent() -> None:
    assert config_hash({"a": 1, "b": 2}) == config_hash({"b": 2, "a": 1})
    assert config_hash({"a": 1}) != config_hash({"a": 2})
