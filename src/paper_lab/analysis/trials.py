"""Trial log and the out-of-sample lock (spec Stage E.3-4).

``experiments/trial_log.csv``: one row per evaluated variant (setup, parameters, symbol,
news-guard variant, cost multiplier, window). Its row count is the number of trials that
every reported result must be read against. Re-running an identical trial replaces its
row (same ``trial_id``) instead of inflating the count.

``experiments/oos_access.csv``: one row per experiment that opened the out-of-sample
window. A second opening is refused.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from paper_lab.journal.writer import upsert

TRIAL_COLUMNS = [
    "trial_id",
    "logged_utc",
    "experiment",
    "setup",
    "symbol",
    "window",
    "window_start",
    "window_end",
    "news_guard",
    "cost_mult",
    "params_hash",
    "params_json",
    "trades",
    "mean_r_net",
    "sharpe_per_trade",
    "run_id",
    "git_commit",
    "data_hash",
]
OOS_COLUMNS = ["experiment", "opened_utc", "run_id", "git_commit", "window_start", "window_end"]


class OosLockedError(RuntimeError):
    pass


def params_hash(params: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:12]


def trial_id(row: dict[str, object]) -> str:
    key = "|".join(
        str(row[k])
        for k in (
            "experiment",
            "setup",
            "symbol",
            "window",
            "window_start",
            "window_end",
            "news_guard",
            "cost_mult",
            "params_hash",
        )
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def log_trials(path: Path, rows: list[dict[str, object]]) -> pd.DataFrame:
    for r in rows:
        r["trial_id"] = trial_id(r)
    return upsert(path, pd.DataFrame(rows), "trial_id", TRIAL_COLUMNS)


def count_trials(path: Path) -> int:
    return len(pd.read_csv(path)) if path.exists() else 0


def trial_sharpes(path: Path) -> list[float]:
    if not path.exists():
        return []
    s = pd.read_csv(path)["sharpe_per_trade"].dropna()
    return [float(v) for v in s]


def open_oos(path: Path, experiment: str, row: dict[str, object]) -> None:
    """Record that ``experiment`` opens the OOS window; refuse if it already did."""
    if path.exists():
        seen = pd.read_csv(path, dtype=str)
        if experiment in set(seen["experiment"]):
            first = seen.loc[seen["experiment"] == experiment, "opened_utc"].iloc[0]
            raise OosLockedError(
                f"{experiment} already opened the out-of-sample window ({first}); "
                "it may be touched once per experiment"
            )
    upsert(path, pd.DataFrame([{"experiment": experiment, **row}]), "experiment", OOS_COLUMNS)
