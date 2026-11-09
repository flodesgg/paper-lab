"""Cost sensitivity: the same trades with costs x0, x1, x2 (spec Stage D acceptance).

Only plain descriptive numbers here; confidence intervals and sample-size labels belong to
the analysis stage (Stage E). The experiment gate below keeps real-data results behind a
pre-registered, approved experiment file (decision 2026-11-09).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

APPROVED = re.compile(r"^\s*status\s*:\s*approved\b", re.IGNORECASE | re.MULTILINE)


class ExperimentNotApprovedError(RuntimeError):
    pass


def require_approved(path: Path) -> None:
    """Real-data results only for an existing experiment file containing `Status: approved`."""
    if not path.is_file():
        raise ExperimentNotApprovedError(f"{path}: experiment file not found")
    if not APPROVED.search(path.read_text(encoding="utf-8")):
        raise ExperimentNotApprovedError(
            f"{path}: no 'Status: approved' line; the user must approve the criteria first"
        )


def summarize(trades: pd.DataFrame) -> dict[str, float]:
    f = trades[trades["status"] == "filled"]
    n = len(f)
    if n == 0:
        return {"trades": 0}
    r_net = f["r_net"].astype(float)
    r_gross = f["r_gross"].astype(float)
    return {
        "trades": n,
        "win_rate": float((r_net > 0).mean()),
        "mean_r_gross": float(r_gross.mean()),
        "mean_r_net": float(r_net.mean()),
        "sum_r_net": float(r_net.sum()),
        "mean_cost_r": float((r_gross - r_net).mean()),
        "net_usd": float(f["pnl_net_usd"].astype(float).sum()),
        "costs_usd": float(f["costs_usd"].astype(float).sum()),
    }


def table(rows: list[dict[str, object]]) -> str:
    cols = [
        ("symbol", "symbol"),
        ("mult", "costs x"),
        ("trades", "trades"),
        ("win_rate", "win rate"),
        ("mean_r_gross", "mean R gross"),
        ("mean_r_net", "mean R net"),
        ("mean_cost_r", "cost/trade R"),
        ("net_usd", "net USD"),
    ]
    out = ["| " + " | ".join(h for _, h in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        cells = []
        for k, _ in cols:
            v = r.get(k, "")
            if isinstance(v, float):
                cells.append(
                    f"{v:.1%}"
                    if k == "win_rate"
                    else f"{v:.3f}"
                    if k.startswith("mean_")
                    else f"{v:.2f}"
                )
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)
