---
# Machine-readable part. `paper-lab backtest --experiment experiments/EXP-NNN.md` refuses to
# run unless status is approved, approved_by/approved_on are filled in BY THE USER, and
# strategy_hash equals `uv run paper-lab check-config`'s strategy hash (config unchanged).
id: EXP-NNN
title: one line
status: draft                 # draft -> approved (user) -> done | rejected
approved_by:                  # filled in by the user only
approved_on:                  # YYYY-MM-DD, filled in by the user only
strategy_hash: "000000000000" # Config.strategy_hash() at registration
setups: [orb]
symbols: [GC=F, GLD]
primary_symbol: GC=F          # the decision rule is evaluated on this symbol
window: in_sample             # in_sample | out_of_sample (OOS opens once per experiment)
news_guard_variants: [config, "off"]  # quoted: YAML reads bare off as false
cost_mults: [0, 1, 2]         # must include 1 and 2
grid: {}                      # in-sample only, e.g. {orb.k: [1.5, 2.0, 3.0]}; every cell is a trial
criteria:                     # primary symbol, guard as configured, costs x1 (x2 where stated)
  min_trades: 30              # fewer -> INCONCLUSIVE, whatever the numbers
  success_ci_low_above: 0.0   # SUCCESS: 95% CI lower bound of mean R net above this ...
  success_mean_x2_above: 0.0  # ... AND mean R net with costs x2 above this
  failure_ci_high_below: 0.0  # FAILURE: 95% CI upper bound below this
---

# EXP-NNN — title

## Hypothesis
What should be true if the idea works, in one or two sentences, stated before any result.

## Setup
Which setup(s), on which instrument(s), with which filter variants.

## Exact parameters
Config strategy hash and the parameter values in plain text (copied from config.yaml).

## Data window
Window name and dates (config `analysis`), expected number of trading days and trades.

## Success and failure criteria (written before running)
The decision rule above in words, plus what each outcome leads to.

## Result
(Filled in after the run: link to `reports/backtest_<date>_EXP-NNN.md`, the key numbers,
number of trials logged.)

## Decision
(Filled in after the run, following the pre-registered rule. No new parameters here: a
change is a new experiment.)
