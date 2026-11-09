"""Trade metrics in R, bootstrap confidence interval, sample-size label, overfitting stats.

All functions take R multiples in trade order (the order matters for drawdown and streaks).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


@dataclass(frozen=True)
class Metrics:
    trades: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float
    profit_factor: float  # inf when there is no losing trade; nan with no trades
    max_drawdown_r: float  # largest peak-to-trough fall of cumulative R (>= 0)
    longest_losing_streak: int
    time_exit_share: float
    avg_hold_min: float
    sharpe_per_trade: float  # mean / std of R per trade (not annualised)

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _nan() -> float:
    return float("nan")


def compute_metrics(
    r: pd.Series | np.ndarray,
    exit_reasons: pd.Series | np.ndarray | None = None,
    hold_min: pd.Series | np.ndarray | None = None,
) -> Metrics:
    x = np.asarray(r, dtype="float64")
    n = len(x)
    if n == 0:
        return Metrics(0, _nan(), _nan(), _nan(), _nan(), _nan(), 0.0, 0, _nan(), _nan(), _nan())
    wins, losses = x[x > 0], x[x <= 0]
    gross_loss = -losses.sum()
    pf = float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf")
    cum = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    max_dd = float(np.max(peak - cum)) if n else 0.0
    streak = best = 0
    for v in x:
        streak = streak + 1 if v <= 0 else 0
        best = max(best, streak)
    sd = float(x.std(ddof=1)) if n > 1 else _nan()
    return Metrics(
        trades=n,
        win_rate=float(len(wins) / n),
        avg_win_r=float(wins.mean()) if len(wins) else _nan(),
        avg_loss_r=float(losses.mean()) if len(losses) else _nan(),
        expectancy_r=float(x.mean()),
        profit_factor=pf,
        max_drawdown_r=max(0.0, max_dd),
        longest_losing_streak=best,
        time_exit_share=(
            float((np.asarray(exit_reasons) == "time").mean())
            if exit_reasons is not None
            else _nan()
        ),
        avg_hold_min=float(np.mean(np.asarray(hold_min, dtype="float64")))
        if hold_min is not None
        else _nan(),
        sharpe_per_trade=float(x.mean() / sd) if sd and sd > 0 else _nan(),
    )


def bootstrap_ci(
    r: pd.Series | np.ndarray, n_boot: int, seed: int, level: float
) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean (trades resampled with replacement)."""
    x = np.asarray(r, dtype="float64")
    if len(x) < 2:
        return _nan(), _nan()
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), size=(n_boot, len(x)))].mean(axis=1)
    a = (1.0 - level) / 2.0
    lo, hi = np.quantile(means, [a, 1.0 - a])
    return float(lo), float(hi)


def sample_label(n: int, insufficient_below: int = 30, usable_above: int = 100) -> str:
    if n < insufficient_below:
        return "insufficient sample"
    if n <= usable_above:
        return "weak evidence"
    return "usable"


def verdict(ci: tuple[float, float], n: int, insufficient_below: int = 30) -> str:
    """Plain-language verdict. A CI containing zero is never called an edge."""
    lo, hi = ci
    if n == 0:
        return "no trades"
    if math.isnan(lo) or lo <= 0 <= hi:
        base = "no demonstrated edge"
    elif lo > 0:
        base = "expectancy above zero (CI excludes zero)"
    else:
        base = "negative expectancy (CI below zero)"
    if n < insufficient_below:
        base += f"; insufficient sample ({n} < {insufficient_below} trades)"
    return base


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum Sharpe of ``n_trials`` zero-skill trials (Bailey & Lopez de Prado,
    2014, 'The Deflated Sharpe Ratio'); 0 for a single trial."""
    if n_trials <= 1 or sr_variance <= 0:
        return 0.0
    z1 = _N.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe(r: pd.Series | np.ndarray, n_trials: int, sr_variance: float) -> float:
    """Probability that the true per-trade Sharpe exceeds what the best of ``n_trials``
    zero-skill variants would show by luck. NaN when it cannot be computed."""
    x = np.asarray(r, dtype="float64")
    t = len(x)
    if t < 3 or x.std(ddof=1) == 0:
        return _nan()
    sr = x.mean() / x.std(ddof=1)
    z = (x - x.mean()) / x.std(ddof=0)
    skew = float(np.mean(z**3))
    kurt = float(np.mean(z**4))
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    if denom <= 0:
        return _nan()
    return float(_N.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom)))


def bonferroni_level(n_trials: int, alpha: float = 0.05) -> float:
    """Two-sided confidence level that keeps the family-wise error at ``alpha``."""
    return 1.0 - alpha / max(1, n_trials)
