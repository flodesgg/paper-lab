"""Freeze rule (spec Stage F.4).

A setup that fails its pre-registered criteria (an experiment with ``decision: FAILURE``) or
that triggered the forward ALARM in a weekly report is disabled in ``config.yaml``
(``enabled: false``) and recorded in ``experiments/frozen.csv``. The commit message names the
reference. It is re-enabled only through a NEW experiment: ``frozen_violations`` reports an
enabled setup that is still frozen unless an experiment approved on or after the freeze date
lists it; the forward loop, ``signals`` and ``backtest`` refuse to run while there is one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from paper_lab.analysis.experiment import ExperimentError, load_experiment
from paper_lab.config import Config, load_config
from paper_lab.journal.writer import upsert

FROZEN_COLUMNS = ["setup", "frozen_on", "reference", "reason", "git_commit"]
ALARM_TEXT = "ALARM: forward result contradicts the backtest"


class FreezeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Frozen:
    setup: str
    frozen_on: date
    reference: str


def check_reference(reference: str, setup: str, experiments_dir: Path, repo: Path) -> None:
    """``reference`` must justify the freeze: EXP-NNN with decision FAILURE that tested the
    setup, or a weekly report whose ALARM line names the setup."""
    if re.fullmatch(r"EXP-\d{3}", reference):
        exp, _ = load_experiment(experiments_dir / f"{reference}.md")
        if exp.decision != "FAILURE":
            raise FreezeError(
                f"{reference} has decision {exp.decision}; only a FAILURE freezes a setup"
            )
        if setup not in exp.setups:
            raise FreezeError(f"{reference} did not test setup {setup!r}")
        return
    path = repo / reference
    if not path.is_file():
        raise FreezeError(f"reference {reference!r} is neither EXP-NNN nor an existing report")
    alarms = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ALARM_TEXT in ln]
    if not any(f"`{setup}`" in ln for ln in alarms):
        raise FreezeError(f"{reference} has no ALARM line for setup {setup!r}")


def disable_in_config_text(text: str, setup: str) -> str:
    """Flip ``enabled: true`` -> ``false`` in the ``setups.<setup>`` block, keeping every
    comment and all other lines unchanged."""
    lines = text.splitlines(keepends=True)
    in_setups = in_block = False
    for i, line in enumerate(lines):
        if re.match(r"^\S", line):
            in_setups = line.startswith("setups:")
            in_block = False
            continue
        if in_setups and re.match(r"^  \S", line):
            in_block = line.startswith(f"  {setup}:")
            continue
        if in_block and re.match(r"^    enabled:\s*true\b", line):
            lines[i] = re.sub(r"enabled:\s*true", "enabled: false", line, count=1)
            return "".join(lines)
    raise FreezeError(f"no 'enabled: true' line for setup {setup!r} in config.yaml")


def freeze(
    config_path: Path,
    experiments_dir: Path,
    repo: Path,
    setup: str,
    reference: str,
    reason: str,
    git_commit: str,
    today: date,
) -> Path:
    """Disable ``setup`` and record it. Returns the path of ``frozen.csv``."""
    cfg, _ = load_config(config_path)
    if setup not in cfg.setups:
        raise FreezeError(f"unknown setup {setup!r}")
    if not cfg.setups[setup].enabled:
        raise FreezeError(f"setup {setup!r} is already disabled")
    try:
        check_reference(reference, setup, experiments_dir, repo)
    except ExperimentError as e:
        raise FreezeError(str(e)) from e
    new_text = disable_in_config_text(config_path.read_text(encoding="utf-8"), setup)
    tmp = config_path.with_suffix(".yaml.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    after, _ = load_config(tmp)  # the edited file must still validate ...
    before = cfg.model_dump(mode="json")
    changed = after.model_dump(mode="json")
    before["setups"][setup]["enabled"] = False
    if changed != before:  # ... and differ only in that one flag
        tmp.unlink()
        raise FreezeError("editing config.yaml changed more than the enabled flag; aborted")
    tmp.replace(config_path)
    path = experiments_dir / "frozen.csv"
    row = {
        "setup": setup,
        "frozen_on": today.isoformat(),
        "reference": reference,
        "reason": reason,
        "git_commit": git_commit,
    }
    upsert(path, pd.DataFrame([row]), "setup", FROZEN_COLUMNS)
    return path


def frozen_setups(experiments_dir: Path) -> list[Frozen]:
    path = experiments_dir / "frozen.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype=str)
    return [
        Frozen(str(r["setup"]), date.fromisoformat(str(r["frozen_on"])), str(r["reference"]))
        for r in df.to_dict("records")
    ]


def frozen_violations(cfg: Config, experiments_dir: Path) -> list[str]:
    """Enabled setups that are frozen and not re-enabled by a later approved experiment."""
    out: list[str] = []
    for f in frozen_setups(experiments_dir):
        sc = cfg.setups.get(f.setup)
        if sc is None or not sc.enabled:
            continue
        ok = False
        for p in sorted(experiments_dir.glob("EXP-[0-9][0-9][0-9].md")):
            exp, _ = load_experiment(p)
            if (
                exp.id != f.reference
                and exp.status in ("approved", "done")
                and f.setup in exp.setups
                and exp.approved_on is not None
                and exp.approved_on >= f.frozen_on
            ):
                ok = True
                break
        if not ok:
            out.append(
                f"setup {f.setup!r} was frozen on {f.frozen_on} ({f.reference}) but is enabled "
                "in config.yaml without a new approved experiment"
            )
    return out
