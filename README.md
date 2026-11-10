# Paper Trading Lab

A paper-only research lab that tests whether a simple New York–open intraday setup on gold
(`GC=F` futures prices, cross-checked on the `GLD` ETF) has a positive expectancy **after
realistic costs**. No real money and no live orders, ever. "No edge found" counts as a result.

## What it does
- **Data:** downloads 1m / 5m / 1h bars from Yahoo Finance, archives them locally every day
  with a SHA-256 manifest, and validates them (gaps, duplicates, off-grid bars, sessions).
- **Calendar:** NY session 09:30–12:00 ET with exchange holidays and early closes; official
  CPI / NFP / FOMC dates (BLS, Federal Reserve) for a news guard.
- **Setups:** opening-range breakout (enabled) and momentum continuation (disabled), each in
  one module behind a registry; a look-ahead test with a deliberately cheating negative control.
- **Execution:** pessimistic fills on bars (stop before target in the same bar, gaps fill
  beyond the stop, limits fill only through the price), sourced commissions, assumed spread
  and slippage, position sizing and a daily risk gate.
- **Analysis:** metrics in R, bootstrap confidence intervals, sample-size labels, in-sample /
  locked out-of-sample split, a log of every variant tried, deflated Sharpe and Bonferroni.
- **Discipline:** experiments are pre-registered (`experiments/EXP-NNN.md`); the code refuses
  to run one that is not approved or whose strategy hash differs from the config.
- **Forward test:** a macOS launchd job trades every new session on paper, writes a journal and
  a weekly report with an alarm and a freeze rule ([docs/forward_loop.md](docs/forward_loop.md)).

## Results
Every number below links to a file produced by the code.

- **EXP-001**, ORB baseline, GC=F, in-sample (41 sessions): 26 trades, −0.050 R per trade
  after costs, 95% CI [−0.406, +0.333] → **INCONCLUSIVE: no demonstrated edge**
  ([report](reports/backtest_2026-11-09_EXP-001.md), [experiment](experiments/EXP-001.md)).
- **Forward paper test** from 2026-11-09 (out-of-sample): journal in [journal/](journal/).
  Too few sessions and trades so far for any statement.

## Limitations (read before the results above)
- **Tiny sample.** Free 5-minute history covers ~60 calendar days, so the backtest has 26
  trades. The CI is wide; the result can neither confirm nor rule out an edge.
- **Free, unofficial data.** Yahoo Finance is delayed, sometimes revised, and `GC=F` is a
  continuous front-month series whose roll days are not detected
  ([docs/data_notes.md](docs/data_notes.md)).
- **Bar-level simulation.** The order of prices inside a bar is unknown; fills are made
  pessimistic on purpose. Spread and slippage are assumptions, not measured fills
  ([docs/cost_model.md](docs/cost_model.md)).
- **Hindsight in the idea.** The setup comes from popular practice, not from a search; no
  statistic corrects for how the idea was chosen.
- **One setup, one market, one time window.** Nothing here generalises beyond it.
- Two reports written before a fix put their caveats at the end instead of the start; their
  numbers are unchanged (see [docs/decisions.md](docs/decisions.md)).

## Data policy
Market data from Yahoo Finance is **not** in this repository. The raw archive lives in
`data_archive/` on the machine that runs the lab (ignored by git); only
`data_archive/MANIFEST.csv` (row counts, time ranges, SHA-256) is committed, so each result can
be tied to the exact data it used. Reproducing the real-data results requires downloading the
data yourself with `paper-lab fetch --update`; Yahoo keeps 5-minute bars for ~60 days only, so
the exact in-sample window cannot be re-downloaded later. Test fixtures in `tests/fixtures/`
are synthetic prices in Yahoo's CSV layout. The macro calendar in `data/calendar/` is built
from public U.S. government release schedules (sources listed per row).

## Run it
Requires macOS or Linux, [uv](https://docs.astral.sh/uv/) and Python 3.13 (uv installs it).

```bash
uv sync
make check                              # ruff, mypy --strict, 231 pytest cases, no network
uv run paper-lab check-config           # validated config and its strategy hash
uv run paper-lab backtest --synthetic   # full pipeline on synthetic data, no download needed
uv run paper-lab costs --synthetic      # cost sensitivity x0 / x1 / x2 (synthetic)
```

With real data (needs network):
```bash
uv run paper-lab fetch --update         # appends new bars to data_archive/
uv run paper-lab validate               # data-quality report in reports/
uv run paper-lab signals                # signal tables in reports/signals/
uv run paper-lab backtest --experiment experiments/EXP-NNN.md   # approved experiments only
uv run paper-lab forward                # forward paper loop (what the scheduler runs)
scripts/agent.sh install | status | uninstall   # launchd scheduler (docs/forward_loop.md)
```

## Layout
`src/paper_lab/` code (data, calendar, setups, execution, risk, journal, analysis, live,
reports) · `tests/` · `config.yaml` (validated) · `experiments/` · `journal/` · `reports/` ·
`docs/` (decisions, data notes, cost model, forward loop).

## License
[MIT](LICENSE).
