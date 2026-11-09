"""Load and validate config.yaml.

All parameters live in config.yaml; code reads them only through `load_config`.
Unknown keys are rejected so a typo cannot silently fall back to a default.
"""

from __future__ import annotations

import hashlib
import json
from datetime import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path("config.yaml")

Interval = Literal["1m", "2m", "5m", "15m", "30m", "1h", "1d"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectConfig(_Strict):
    name: str
    paper_only: bool

    @field_validator("paper_only")
    @classmethod
    def _must_be_paper(cls, v: bool) -> bool:
        if not v:
            raise ValueError("paper_only must be true: this lab never trades real money")
        return v


class PathsConfig(_Strict):
    data_cache: Path
    data_archive: Path
    journal: Path
    reports: Path
    experiments: Path
    logs: Path


class LoggingConfig(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    file: str = "paper_lab.log"
    max_bytes: int = Field(gt=0)
    backup_count: int = Field(ge=0)


class InstrumentConfig(_Strict):
    symbol: str
    role: Literal["primary", "cross_check"]
    description: str
    contract_multiplier: float = Field(gt=0)
    price_unit: str


class DataConfig(_Strict):
    source: Literal["yfinance"]
    archive_intervals: list[Interval] = Field(min_length=1)
    storage_format: Literal["parquet", "csv"]


class SessionConfig(_Strict):
    timezone: str
    start: time
    end: time
    exchange_calendar: str

    @model_validator(mode="after")
    def _start_before_end(self) -> SessionConfig:
        if self.start >= self.end:
            raise ValueError("session.start must be before session.end")
        return self

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            # ZoneInfoNotFoundError is a KeyError, which pydantic does not convert
            raise ValueError(f"unknown timezone: {v!r}") from e
        return v


class RiskConfig(_Strict):
    paper_capital_usd: float = Field(gt=0)
    risk_per_trade_pct: float = Field(gt=0, le=2.0)
    daily_loss_limit_pct: float = Field(gt=0, le=5.0)
    max_trades_per_day: int = Field(ge=1, le=10)
    max_stops_per_day: int = Field(ge=1, le=10)


class Config(_Strict):
    project: ProjectConfig
    paths: PathsConfig
    logging: LoggingConfig
    instruments: list[InstrumentConfig] = Field(min_length=1)
    data: DataConfig
    session: SessionConfig
    risk: RiskConfig

    @model_validator(mode="after")
    def _one_primary(self) -> Config:
        primaries = [i for i in self.instruments if i.role == "primary"]
        if len(primaries) != 1:
            raise ValueError("exactly one instrument must have role 'primary'")
        symbols = [i.symbol for i in self.instruments]
        if len(set(symbols)) != len(symbols):
            raise ValueError("instrument symbols must be unique")
        return self


def config_hash(raw: dict[str, object]) -> str:
    """Stable short hash of the config content (key order independent)."""
    canonical = json.dumps(raw, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).hexdigest()[:12]


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> tuple[Config, str]:
    """Read, validate and hash the config. Raises on any invalid value."""
    p = Path(path)
    with p.open() as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: top level must be a mapping")
    return Config.model_validate(raw), config_hash(raw)
