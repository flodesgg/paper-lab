"""Reproducibility metadata attached to every run's output."""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    started_utc: str
    git_commit: str
    git_dirty: bool
    config_hash: str
    data_hash: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def new_run_info(config_hash: str, repo: Path = Path("."), data_hash: str | None = None) -> RunInfo:
    now = datetime.now(UTC)
    commit = _git(["rev-parse", "--short=12", "HEAD"], repo) or "no-git"
    status = _git(["status", "--porcelain"], repo)
    return RunInfo(
        run_id=f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}",
        started_utc=now.isoformat(timespec="seconds"),
        git_commit=commit,
        git_dirty=bool(status),
        config_hash=config_hash,
        data_hash=data_hash,
    )
