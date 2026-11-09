"""Trade and day journals (spec Stage D.4): ``trades.csv`` and ``daily.csv``.

Rows are keyed (``trade_id`` / ``day_id``); writing the same key again replaces the row,
so re-running a day is idempotent. Files are written atomically.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

TRADE_COLUMNS = [
    "trade_id",
    "run_id",
    "day",
    "setup",
    "symbol",
    "trade_as",
    "status",  # filled | no_fill | skipped_risk | skipped_size | blocked_guard
    "side",
    "qty",
    "signal_bar_utc",
    "entry_utc",  # start of the entry bar (fill at its open)
    "entry",  # fill price, spread + slippage included
    "stop",
    "target",
    "exit_utc",  # start of the exit bar
    "exit",
    "exit_reason",
    "risk_usd",  # qty x multiplier x |entry - stop|
    "r_gross",  # cost-free path
    "r_net",  # all costs
    "pnl_gross_usd",
    "pnl_net_usd",
    "commission_usd",
    "spread_slippage_usd",
    "costs_usd",
    "cost_mult",
    "comment",
]

DAILY_COLUMNS = [
    "day_id",
    "run_id",
    "day",
    "symbol",
    "setups",
    "signals",
    "trades",
    "wins",
    "losses",
    "stops",
    "time_exits",
    "r_gross_sum",
    "r_net_sum",
    "pnl_net_usd",
    "halted",
    "comment",
]


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def upsert(path: Path, rows: pd.DataFrame, key: str, columns: list[str]) -> pd.DataFrame:
    """Merge ``rows`` into the CSV at ``path`` by ``key`` (new rows win) and return it."""
    missing = set(columns) - set(rows.columns)
    if missing:
        raise ValueError(f"journal rows miss columns {sorted(missing)}")
    rows = rows[columns]
    if path.exists():
        old = pd.read_csv(path, dtype={key: str})
        if list(old.columns) != columns:
            raise ValueError(f"{path}: columns differ from the journal schema")
        old = old[~old[key].isin(set(rows[key].astype(str)))]
        out = pd.concat([old, rows], ignore_index=True) if len(old) else rows.copy()
    else:
        out = rows.copy()
    out = out.sort_values(key, kind="stable").reset_index(drop=True)
    _write_atomic(out, path)
    return out
