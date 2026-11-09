# Paper Trading Lab

Paper-only research lab that tests whether simple New York–open intraday setups on gold
(`GC=F`, cross-checked with `GLD`) have a positive expectancy **after realistic costs**.
No real money, no live orders, ever.

**Status:** Stages A–F done (data, calendar, setups, execution/costs/risk, analysis, forward loop). EXP-001 (in-sample ORB baseline) ran once: INCONCLUSIVE; the forward paper journal grows daily from 2026-11-09 — see [STATUS.md](STATUS.md).

## Research process
- Data archived daily from day one (`data_archive/`, with SHA-256 manifest), because free intraday history is short.
- Pessimistic fill model (stop before target in the same bar, gaps fill beyond stops).
- Costs per instrument, each marked `sourced` or `assumption` (`docs/cost_model.md`).
- Pre-registered experiments (`experiments/EXP-NNN.md`) and a log of every variant tried.
- In-sample / locked out-of-sample split; bootstrap confidence intervals; "no edge found" is a valid result.
- Daily forward paper loop on unseen sessions (`journal/`), weekly report with an alarm and a
  freeze rule ([docs/forward_loop.md](docs/forward_loop.md)).

## Results
Every number in this repository must link to a file produced by its code.

- EXP-001, ORB baseline, GC=F in-sample (41 sessions): 26 trades, -0.050 R per trade after
  costs, 95 % CI [-0.406, 0.333] → **INCONCLUSIVE, no demonstrated edge**
  ([report](reports/backtest_2026-11-09_EXP-001.md), [experiment](experiments/EXP-001.md)).
- Forward paper test from 2026-11-09: journal in [journal/](journal/), weekly reports
  `reports/weekly_<date>.md`. Too few trades yet for any statement.

## Reproduce
```bash
uv sync
make check
uv run paper-lab check-config
uv run paper-lab fetch --update   # needs network; appends to data_archive/
uv run paper-lab validate         # writes reports/data_quality_<date>.md
uv run paper-lab calendar         # checks the event calendar, shows upcoming releases
uv run paper-lab signals          # enabled setups over the archive -> reports/signals/ (no P&L)
uv run paper-lab signals --guard off   # same without the news guard, for comparison
uv run paper-lab costs --synthetic     # cost sensitivity x0/x1/x2 on synthetic data (demo)
uv run paper-lab backtest --synthetic  # one-page report on synthetic data (demo)
uv run paper-lab backtest --experiment experiments/EXP-NNN.md   # real data; approved experiments only
uv run paper-lab forward --commit --push   # forward paper loop (what the scheduler runs)
scripts/agent.sh install | status | uninstall   # launchd scheduler (docs/forward_loop.md)
```

An experiment runs only with `status: approved`, `approved_by`, `approved_on` and a
`strategy_hash` equal to `uv run paper-lab check-config`'s current value; see
[experiments/README.md](experiments/README.md).

## Adding a setup
One new module in `src/paper_lab/setups/` (subclass `Setup`, decorate with `@register`, define a
pydantic `params_model`) plus a `setups.<name>` block in `config.yaml`. The registry discovers it;
the driver handles the bar-by-bar loop, the news guard and the signal tables.

## Layout
`src/paper_lab/` code · `tests/` · `config.yaml` (validated) · `experiments/` · `data_archive/` ·
`journal/` · `reports/` · `docs/` (decisions, data notes, cost model, original spec).
