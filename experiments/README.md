# Experiments

Pre-registered experiments `EXP-NNN.md` (copy `EXP-000_template.md`). The YAML front matter is
what the code runs; the text is the reasoning. Criteria are written and approved by Nick
**before** a run, and `paper-lab backtest --experiment` refuses to run an experiment that is not
approved or whose `strategy_hash` differs from the current config.

- `trial_log.csv` — every variant ever evaluated on real data (created by the first run).
- `oos_access.csv` — which experiment opened the out-of-sample window, and when (once each).
- `results/` — reserved.
