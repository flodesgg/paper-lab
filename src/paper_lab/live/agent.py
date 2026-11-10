"""Unattended side of the forward loop: git commit/push of its own outputs and the launchd
LaunchAgent definition (installed only by ``scripts/agent.sh install``, run by the user).

The loop commits ONLY its own paths (``COMMIT_PATHS``) with a pathspec commit, so whatever
else is staged or modified in the working tree is left alone. Files above 20 MB are never
committed (repository rule).
"""

from __future__ import annotations

import plistlib
import subprocess
from datetime import time
from pathlib import Path

LABEL = "com.paperlab.forward"
# raw bars are third-party data and stay local (.gitignore); only their manifest is committed
COMMIT_PATHS = ["data_archive/MANIFEST.csv", "journal", "reports/weekly_*.md"]
MAX_FILE_BYTES = 20 * 1024 * 1024


class CommitError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=check, timeout=300
    )


def commit_outputs(repo: Path, message: str, paths: list[str] = COMMIT_PATHS) -> str | None:
    """Stage and commit ``paths`` only. Returns the new commit (short) or None if nothing
    changed."""
    # expand globs here: `git add` fails on a pattern that matches nothing yet
    paths = sorted({str(p.relative_to(repo)) for pat in paths for p in repo.glob(pat)})
    if not paths:
        return None
    _git(repo, "add", "-A", "--", *paths)
    staged = _git(repo, "diff", "--cached", "--name-only", "--", *paths).stdout.split()
    if not staged:
        return None
    big = [f for f in staged if (repo / f).is_file() and (repo / f).stat().st_size > MAX_FILE_BYTES]
    if big:
        _git(repo, "reset", "-q", "--", *paths)
        raise CommitError(f"refusing to commit files above 20 MB: {big}")
    _git(repo, "commit", "-q", "-m", message, "--", *paths)
    return _git(repo, "rev-parse", "--short=12", "HEAD").stdout.strip()


def push(repo: Path) -> tuple[bool, str]:
    r = _git(repo, "push", "-q", check=False)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def launch_agent_plist(repo: Path, uv: Path, schedule: list[time]) -> bytes:
    """LaunchAgent: run the forward loop at the scheduled local times; a time missed while
    the Mac sleeps runs on wake (launchd coalesces it), and RunAtLoad catches up at login."""
    repo = repo.resolve()
    cmd = f"cd '{repo}' && exec '{uv}' run --frozen paper-lab forward --commit --push"
    plist = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/zsh", "-lc", cmd],
        "WorkingDirectory": str(repo),
        "StartCalendarInterval": [{"Hour": t.hour, "Minute": t.minute} for t in schedule],
        "RunAtLoad": True,
        "StandardOutPath": str(repo / "logs" / "launchd.out.log"),
        "StandardErrorPath": str(repo / "logs" / "launchd.err.log"),
        "ProcessType": "Background",
    }
    return plistlib.dumps(plist)
