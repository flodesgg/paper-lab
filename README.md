# Paper Trading Lab

Paper-only research lab that tests whether simple New York–open intraday setups on gold
(`GC=F`, cross-checked with `GLD`) have a positive expectancy **after realistic costs**.
No real money, no live orders, ever.

**Status:** Stages A (data) and B (calendar) done. Data is archived daily; no backtests or results yet — see [STATUS.md](STATUS.md).

## Research process (planned)
- Data archived daily from day one (`data_archive/`, with SHA-256 manifest), because free intraday history is short.
- Pessimistic fill model (stop before target in the same bar, gaps fill beyond stops).
- Costs per instrument, each marked `sourced` or `assumption` (`docs/cost_model.md`).
- Pre-registered experiments (`experiments/EXP-NNN.md`) and a log of every variant tried.
- In-sample / locked out-of-sample split; bootstrap confidence intervals; "no edge found" is a valid result.

## Results
None yet. Every number in this repository must link to a file produced by its code.

## Reproduce
```bash
uv sync
make check
uv run paper-lab check-config
uv run paper-lab fetch --update   # needs network; appends to data_archive/
uv run paper-lab validate         # writes reports/data_quality_<date>.md
uv run paper-lab calendar         # checks the event calendar, shows upcoming releases
```

## Layout
`src/paper_lab/` code · `tests/` · `config.yaml` (validated) · `experiments/` · `data_archive/` ·
`journal/` · `reports/` · `docs/` (decisions, data notes, cost model, original spec).
