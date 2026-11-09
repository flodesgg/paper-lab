"""Cost sensitivity: the same trades with costs x0, x1, x2 (spec Stage D acceptance).

Synthetic data only: on the real archive, cost sensitivity is part of a pre-registered
experiment (`paper-lab backtest --experiment`, analysis/run.py).
"""

from __future__ import annotations

import pandas as pd


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
