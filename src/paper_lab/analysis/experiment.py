"""Pre-registered experiments: ``experiments/EXP-NNN.md`` with a YAML front matter.

The front matter is the machine-readable part (what to run, which config, which decision
rule); the Markdown body is the human part (hypothesis, reasoning, result, decision).
A run is refused unless:
  * ``status: approved`` with ``approved_by`` and ``approved_on`` filled in by the user;
  * ``strategy_hash`` equals the current config's ``Config.strategy_hash()``: parameters
    cannot change between approval and run without a new experiment;
  * an OOS window has not been opened before for this experiment (trials.py);
  * a parameter grid is only requested on the in-sample window.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from paper_lab.config import Config


class ExperimentError(RuntimeError):
    pass


class Criteria(BaseModel):
    """Decision rule, evaluated on the primary symbol, news guard as configured, costs x1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_trades: int = Field(ge=1)
    success_ci_low_above: float  # bootstrap CI lower bound must exceed this (R)
    success_mean_x2_above: float  # mean R net with costs x2 must exceed this
    failure_ci_high_below: float  # CI upper bound below this -> failure


class Experiment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^EXP-\d{3}$")
    title: str
    status: Literal["draft", "approved", "done", "rejected"]
    approved_by: str | None = None
    approved_on: date | None = None
    strategy_hash: str
    setups: list[str] = Field(min_length=1)
    symbols: list[str] = Field(min_length=1)
    primary_symbol: str
    window: Literal["in_sample", "out_of_sample"]
    news_guard_variants: list[Literal["config", "off"]] = Field(min_length=1)
    cost_mults: list[float] = Field(min_length=1)
    grid: dict[str, list[float]] = Field(default_factory=dict)  # "setup.param": values
    criteria: Criteria

    @field_validator("news_guard_variants", mode="before")
    @classmethod
    def _no_yaml_bools(cls, v: object) -> object:
        if isinstance(v, list) and any(isinstance(x, bool) for x in v):
            raise ValueError('YAML read a bare off/on as a boolean: write "off" in quotes')
        return v

    @model_validator(mode="after")
    def _consistent(self) -> Experiment:
        if self.primary_symbol not in self.symbols:
            raise ValueError("primary_symbol must be one of symbols")
        if self.grid and self.window != "in_sample":
            raise ValueError("a parameter grid may only run on the in-sample window")
        if 1.0 not in self.cost_mults or 2.0 not in self.cost_mults:
            raise ValueError("cost_mults must include 1 and 2 (the decision rule uses both)")
        if "config" not in self.news_guard_variants:
            raise ValueError("news_guard_variants must include 'config'")
        for key in self.grid:
            if key.count(".") != 1:
                raise ValueError(f"grid key {key!r} must look like 'setup.param'")
        return self


def load_experiment(path: Path) -> tuple[Experiment, str]:
    """(front matter, Markdown body)."""
    if not path.is_file():
        raise ExperimentError(f"{path}: experiment file not found")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ExperimentError(f"{path}: missing YAML front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ExperimentError(f"{path}: unterminated YAML front matter")
    raw = yaml.safe_load(text[4:end])
    if not isinstance(raw, dict):
        raise ExperimentError(f"{path}: front matter must be a mapping")
    return Experiment.model_validate(raw), text[end + 5 :]


def check_runnable(exp: Experiment, cfg: Config) -> None:
    if exp.status != "approved":
        raise ExperimentError(f"{exp.id} has status {exp.status!r}; only 'approved' runs")
    if not exp.approved_by or exp.approved_on is None:
        raise ExperimentError(f"{exp.id}: approved_by and approved_on must be filled in")
    current = cfg.strategy_hash()
    if exp.strategy_hash != current:
        raise ExperimentError(
            f"{exp.id} was registered for strategy hash {exp.strategy_hash}, config is now "
            f"{current}: parameters changed after registration; write a new experiment"
        )
    for s in exp.setups:
        if s not in cfg.setups:
            raise ExperimentError(f"{exp.id}: unknown setup {s!r}")
    for sym in exp.symbols:
        cfg.instrument(sym)
    for key in exp.grid:
        setup, param = key.split(".")
        if setup not in exp.setups or param not in cfg.setups[setup].params:
            raise ExperimentError(f"{exp.id}: grid key {key!r} is not a configured parameter")
