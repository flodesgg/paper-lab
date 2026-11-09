# Backtest EXP-000: pipeline demo on synthetic data

**SYNTHETIC random-walk data — this page demonstrates the report and says nothing about gold.**

**Verdict:** no demonstrated edge. **Pre-registered decision:** INCONCLUSIVE — no demonstrated edge: the CI does not meet the success or failure rule.

**Data:** synthetic window, 82 trading days (2026-07-08 .. 2026-10-30), 5-minute bars. **Sample:** 78 trades on GC=F → `weak evidence`.

## GC=F, news guard as configured, costs x1

| trades | win rate | avg win R | avg loss R | expectancy R | 95% CI | profit factor | max DD R | longest losing streak | time exits | avg hold min |
|---|---|---|---|---|---|---|---|---|---|---|
| 78 | 41.0% | 1.092 | -0.723 | 0.022 | [-0.209, 0.256] | 1.05 | 9.42 | 5 | 50% | 78 |

## Variants (expectancy R net per trade)

| symbol | news guard | costs x | trades | expectancy R | CI |
|---|---|---|---|---|---|
| GC=F | config | 0 | 78 | 0.057 | [-0.172, 0.290] |
| GC=F | config | 1 | 78 | 0.022 | [-0.209, 0.256] |
| GC=F | config | 2 | 78 | -0.024 | [-0.250, 0.209] |
| GC=F | off | 0 | 78 | 0.069 | [-0.160, 0.304] |
| GC=F | off | 1 | 78 | 0.032 | [-0.197, 0.269] |
| GC=F | off | 2 | 78 | -0.013 | [-0.241, 0.219] |
| GLD | config | 0 | 80 | -0.002 | [-0.242, 0.243] |
| GLD | config | 1 | 80 | -0.071 | [-0.304, 0.169] |
| GLD | config | 2 | 80 | -0.124 | [-0.355, 0.112] |
| GLD | off | 0 | 80 | -0.006 | [-0.244, 0.238] |
| GLD | off | 1 | 80 | -0.075 | [-0.307, 0.165] |
| GLD | off | 2 | 80 | -0.128 | [-0.357, 0.107] |

**News filter on vs off:** 7 morning-release day(s) in the window. GC=F, costs x1: guard as configured 0.022 R over 78 trades, guard off 0.032 R over 78 trades (difference -0.011 R per trade).

## Parameter grid (in-sample, GC=F, costs x1)

| orb.k | orb.min_range_atr | trades | expectancy R | CI |
|---|---|---|---|---|
| 1.5 | 0.05 | 80 | 0.017 | [-0.198, 0.238] |
| 1.5 | 0.1 | 78 | -0.021 | [-0.230, 0.194] |
| 1.5 | 0.15 | 57 | -0.016 | [-0.250, 0.224] |
| 2 | 0.05 | 80 | 0.061 | [-0.172, 0.301] |
| 2 | 0.1 | 78 | 0.022 | [-0.209, 0.256] |
| 2 | 0.15 | 57 | 0.024 | [-0.233, 0.290] |
| 3 | 0.05 | 80 | 0.073 | [-0.171, 0.329] |
| 3 | 0.1 | 78 | 0.063 | [-0.184, 0.320] |
| 3 | 0.15 | 57 | 0.071 | [-0.208, 0.371] |

## Multiple testing

Trials logged so far: **21** (`trial_log.csv in this folder`). Deflated Sharpe probability: 0.219. Bonferroni 99.76% CI: [-0.316, 0.389] — includes zero.

## Caveats

- Data: Yahoo Finance (unofficial, delayed, revisable); GC=F is a continuous front-month series and roll days are not detected.
- Costs: commissions are sourced, spread and slippage are assumptions (docs/cost_model.md); GC=F prices are traded as Micro Gold, which can trade slightly differently.
- Fills are simulated on 5-minute bars with pessimistic rules; the order of prices inside a bar is unknown.
- Universe chosen with hindsight risk: gold, the NY open and the ORB idea come from popular trading practice, not from a search, but that preference itself cannot be corrected statistically.
- One market regime: a few weeks of data in one period cannot show how the rules behave in other volatility regimes.

Run `20261109T173447Z-2e5728`, commit `a325684afb18`, strategy hash `219b8dfb9257`, data hash `1d24e66fd368`. Trade files: `reports/backtest_synthetic_2026-11-09/`.
