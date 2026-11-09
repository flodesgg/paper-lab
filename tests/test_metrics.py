"""Metrics in R, bootstrap CI, labels and overfitting statistics (hand-computed)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from paper_lab.analysis.metrics import (
    bonferroni_level,
    bootstrap_ci,
    compute_metrics,
    deflated_sharpe,
    expected_max_sharpe,
    sample_label,
    verdict,
)

R = [2.0, -1.0, -1.0, 0.5, -1.0, 2.0, -1.04]


def test_metrics_by_hand() -> None:
    """wins 2, 0.5, 2 (3/7); losses -1, -1, -1, -1.04 (avg -1.01)
    expectancy = 0.46 / 7 = 0.065714...; profit factor = 4.5 / 4.04 = 1.113861...
    cumulative R: 2, 1, 0, 0.5, -0.5, 1.5, 0.46 -> peak 2, trough -0.5 -> max DD 2.5
    losing streaks: 2 (trades 2-3), 1, 1 -> longest 2"""
    m = compute_metrics(
        R,
        exit_reasons=np.array(["target", "stop", "stop", "time", "stop", "target", "stop"]),
        hold_min=np.array([30] * 7),
    )
    assert m.trades == 7 and m.win_rate == pytest.approx(3 / 7)
    assert m.avg_win_r == pytest.approx(1.5) and m.avg_loss_r == pytest.approx(-1.01)
    assert m.expectancy_r == pytest.approx(0.46 / 7)
    assert m.profit_factor == pytest.approx(4.5 / 4.04)
    assert m.max_drawdown_r == pytest.approx(2.5)
    assert m.longest_losing_streak == 2
    assert m.time_exit_share == pytest.approx(1 / 7) and m.avg_hold_min == 30.0


def test_drawdown_counts_from_zero_and_no_losses() -> None:
    assert compute_metrics([-1.0, -1.0, 3.0]).max_drawdown_r == pytest.approx(2.0)
    m = compute_metrics([1.0, 2.0])
    assert m.profit_factor == math.inf and m.max_drawdown_r == 0.0
    assert compute_metrics([]).trades == 0


def test_bootstrap_ci_is_deterministic_and_sane() -> None:
    a = bootstrap_ci(R, 5000, seed=1, level=0.95)
    assert a == bootstrap_ci(R, 5000, seed=1, level=0.95)
    assert a[0] < np.mean(R) < a[1]
    assert bootstrap_ci([0.3] * 10, 1000, 1, 0.95) == pytest.approx((0.3, 0.3))
    assert all(math.isnan(v) for v in bootstrap_ci([1.0], 100, 1, 0.95))


def test_sample_labels() -> None:
    assert sample_label(29) == "insufficient sample"
    assert sample_label(30) == "weak evidence" and sample_label(100) == "weak evidence"
    assert sample_label(101) == "usable"


def test_verdict_never_calls_a_zero_crossing_ci_an_edge() -> None:
    assert verdict((-0.1, 0.4), 120) == "no demonstrated edge"
    assert verdict((0.05, 0.4), 120) == "expectancy above zero (CI excludes zero)"
    assert verdict((-0.4, -0.05), 120) == "negative expectancy (CI below zero)"
    assert verdict((0.05, 0.4), 20).endswith("insufficient sample (20 < 30 trades)")


def test_deflated_sharpe_penalises_many_trials() -> None:
    """E[max SR] for 1 trial is 0 and grows with the number of trials; the deflated
    Sharpe probability of the same returns therefore falls as trials are added."""
    rng = np.random.default_rng(3)
    r = rng.normal(0.15, 1.0, 200)
    assert expected_max_sharpe(1, 0.01) == 0.0
    assert expected_max_sharpe(10, 0.01) < expected_max_sharpe(100, 0.01)
    one, many = deflated_sharpe(r, 1, 0.01), deflated_sharpe(r, 100, 0.01)
    assert 0 <= many < one <= 1


def test_bonferroni_level() -> None:
    assert bonferroni_level(1) == pytest.approx(0.95)
    assert bonferroni_level(10) == pytest.approx(0.995)
