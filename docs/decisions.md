# Design decisions

Running log. Newest at the bottom. Format: date — decision — reason.

- 2026-11-09 — Rebuild from scratch; no earlier result is reused. — The first version was lost
  with no backup; numbers "remembered" from it are unverifiable.
- 2026-11-09 — Python 3.13 via uv, lockfile committed. — Reproducible environment; avoid system Python.
- 2026-11-09 — Private GitHub repo `paper-lab`, push after every stage. — Backup from day one.
- 2026-11-09 — Original spec stored in `docs/PAPER_LAB_PROMPT_v2.md`. — The spec is part of the record.
- 2026-11-09 — Session 09:30–12:00 America/New_York (US equity open). — Chosen by Nick; matches how
  the setups are traded. 08:20 ET (COMEX gold open) is a possible future experiment, not a default.
- 2026-11-09 — Archive 1m, 5m and 1h bars for GC=F and GLD. — Chosen by Nick. yfinance keeps 1m bars
  for only ~7 days; once lost they cannot be re-downloaded free, so archive them daily.
- 2026-11-09 — Macro calendar covers 2026 and 2027, official sources only. — Chosen by Nick; the
  forward test will run into 2027. Dates not verifiable from BLS / Fed are marked `unverified`.
- 2026-11-09 — Risk defaults: paper capital $100k, 0.5% risk per trade, 1.5% daily loss limit,
  max 2 trades/day, 3 stops end the day. — Chosen by Nick; conservative; changed only by explicit decision.
- 2026-11-09 — Config validated with pydantic, unknown keys rejected, `paper_only` must be true. —
  A typo must fail loudly, not silently fall back to a default.
- 2026-11-09 — Storage: parquet with automatic CSV fallback (implemented in Stage A). — pyarrow broke
  once before; the pipeline must not depend on it.
- 2026-11-09 — Archive is keep-first; provider revisions are logged, not applied. — Backtests on
  archived data must not change after the fact; the revisions log measures how often Yahoo revises.
- 2026-11-09 — Drop the first row of every Yahoo response and anchor each request on the last
  received bar. — Yahoo reports volume 0 for the first row of any response (verified live).
- 2026-11-09 — Do not archive bars younger than 20 min (+2 min grace) and always drop the newest bar
  of a fetch. — COMEX data on Yahoo is ~10 min delayed and live GLD volume is re-attributed between
  bars for ~15 min (verified live). The daily job runs at 12:30 ET, 30 min after the session.
- 2026-11-09 — 1m history is backfilled ~29 days (Yahoo allows 30 days, 8 days per request), not 7.
- 2026-11-09 — The archive written by earlier code versions was deleted and rebuilt before the first
  commit. — It contained zero-volume artefacts and non-final bars that keep-first would have frozen.
- 2026-11-09 — A failing series does not stop the others; MANIFEST is always rebuilt; a file lock
  prevents concurrent fetches; `fetch` exits 1 if any series failed. — Found in code review.
- 2026-11-09 — Event calendar stored as committed CSVs under `data/calendar/` (`data/*` ignored
  except this folder). BLS dates read in a browser because bls.gov blocks scripts (403); every
  row keeps its official source URL, and the loader rejects non-official hosts. — Spec forbids
  guessing dates; scraping around the block would breach the site's terms.
- 2026-11-09 — Future FOMC statement time 14:00 ET is an assumption (all 2026 statements were at
  2:00 p.m.); 2027 FOMC dates are `tentative` as the Fed states. — Honest labelling.
- 2026-11-09 — Exchange calendar range fixed to 2000–2030. — The library's default ends one year
  after today, which made results depend on the run date (Dec 2027 events failed).
- 2026-11-09 — News guard asks about a year without an event file -> error, not "no news". —
  A missing calendar must not silently disable the filter.
- 2026-11-09 — ORB rules (chosen by Nick): breakout = 5m bar CLOSE strictly beyond the opening
  range, entry at the next bar's open; N = 15 min; volume vs the same ET bar time over the last 10
  sessions; min/max range as a fraction of daily ATR(14) from daily bars built from the 1h archive.
- 2026-11-09 — ORB default numbers proposed WITHOUT looking at any result (only signal counts were
  run, never P&L): k = 2.0, stop_fraction = 1.0, range 0.10–0.60 ATR (inclusive), volume_mult = 1.2,
  at least 5 of 10 sessions in the volume profile, last entry bar closes by 11:30 ET, 1 signal/day.
  They are priors for EXP-001, not tuned values.
- 2026-11-09 — Momentum defaults (setup DISABLED until an experiment is approved): first window
  60 min, |move| >= 0.40 %, pullback = bar low touches session VWAP and closes back above it
  (mirrored for shorts), stop = pullback extreme, buffer 0 ATR, k = 2.0, last entry 11:30 ET.
- 2026-11-09 — Look-ahead protection is structural: a setup gets pre-open data in `start_day` and
  then one completed bar at a time; a signal at bar t is filled at the open of bar t+1. A test
  scrambles/deletes all data after a cutoff and requires identical earlier signals; a deliberately
  cheating setup is a negative control that the test must catch.
- 2026-11-09 — A signal blocked by the news guard does not use up the daily quota and does not
  disarm ORB: the next bar after the guard window that still closes beyond the range (with volume)
  can trigger. — The guard limits *when* entries happen, not which days are traded.
- 2026-11-09 — Gross trade resolution (stop before target in the same bar, gap through stop fills at
  the open, target needs a trade THROUGH the level and never fills better than it, time exit at the
  last session bar) lives in `execution/fills.py` from Stage C, because the Stage C scenario tests
  need known outcomes. Stage D adds costs and sizing on top without changing these rules.
- 2026-11-09 — `paper-lab signals` prints signal counts only, no P&L. — Outcomes are produced only by
  a pre-registered experiment (EXP-001), so the defaults above cannot be tuned on seen results.
- 2026-11-09 — Sessions whose data ends before the session close (e.g. today while it runs) are left
  out of signal runs. — Otherwise the same command gives a different answer tomorrow.
- 2026-11-09 — GC=F prices are traded as Micro Gold (MGC, 10 oz) for sizing and costs (chosen by
  Nick). — A full 100 oz GC contract does not fit a 0.5 % risk budget with typical ORB stops.
- 2026-11-09 — Commissions from Interactive Brokers' published US pricing (chosen by Nick), read
  in the browser because the pages block scripts; spread and slippage are assumptions
  (docs/cost_model.md).
- 2026-11-09 — Position value capped at 1x capital (`max_notional_multiple: 1.0`, no leverage). —
  Conservative default; it limits MGC to 2 contracts and GLD to ~260 shares, so the realised risk
  per trade is often below 0.5 %. R multiples do not depend on it.
- 2026-11-09 — Size is decided before the fill, from the signal-bar close plus the expected entry
  cost; the target is k x risk from the ACTUAL fill. A gap at the next open can push the actual
  risk above the budget; the journal records the actual `risk_usd`.
- 2026-11-09 — Risk gate per instrument account: one position at a time, max 2 trades/day, 3 stops
  end the day, no entry once the realised loss reaches 1.5 % or if a stop-out of the new trade
  would take it beyond 1.5 %.
- 2026-11-09 — Cost sensitivity on real data runs only inside an approved experiment (chosen by
  Nick: no peeking before EXP-001 is approved). `paper-lab costs --synthetic` demonstrates the
  command on random-walk data; since Stage E the real-data path is `paper-lab backtest
  --experiment` (cost multipliers x0/x1/x2 are part of every experiment).
- 2026-11-09 — Backtest trades go to `reports/cost_sensitivity/...`; `journal/trades.csv` and
  `journal/daily.csv` are reserved for the forward paper loop (Stage F), same schema and writer.
- 2026-11-09 — In-sample / out-of-sample split (chosen by Nick): IS = the archive
  2026-09-11..2026-11-06 (41 sessions); OOS = sessions from 2026-11-09 onward, collected by the
  daily archive / forward loop. — The archive is too short to split without leaving both halves
  tiny; future days cannot have been seen by anyone. Dates live in `config.yaml` (`analysis`).
- 2026-11-09 — Experiments are Markdown files with a YAML front matter (`experiments/EXP-NNN.md`):
  setups, symbols, window, news-guard and cost variants, optional grid, decision rule. A run is
  refused unless `status: approved` with `approved_by`/`approved_on` filled in, and the file's
  `strategy_hash` equals the config's (a hash of every strategy-relevant section). A test fails
  while any draft/approved experiment pins an older config. — Parameters cannot drift between
  approval and run.
- 2026-11-09 — The OOS window opens once per experiment (`experiments/oos_access.csv`); a second
  run is refused. Parameter grids are allowed only in-sample.
- 2026-11-09 — Every evaluated variant (setup, parameters, symbol, guard, cost multiplier, window)
  is a row in `experiments/trial_log.csv`; re-running an identical trial replaces its row. The
  report reads results against that count: deflated Sharpe ratio (Bailey & López de Prado, per
  trade) and a Bonferroni-adjusted CI.
- 2026-11-09 — Metrics in R; 95 % percentile bootstrap CI of mean R (10 000 resamples, fixed
  seed). Sample label: < 30 trades insufficient, 30–100 weak, > 100 usable. Verdict "no
  demonstrated edge" whenever the CI includes zero. The pre-registered decision (SUCCESS / FAILURE /
  INCONCLUSIVE) is evaluated on the primary symbol, guard as configured, costs x1 (x2 for the
  robustness condition).
- 2026-11-09 — Experiment files quote "off": YAML 1.1 reads a bare `off` as false. The loader
  rejects booleans in `news_guard_variants` with an explicit message (bug found by a test).
- 2026-11-09 — EXP-001 approved by Nick and run once on the in-sample archive: INCONCLUSIVE
  (`experiments/EXP-001.md`, `reports/backtest_2026-11-09_EXP-001.md`). As pre-registered, ORB
  parameters stay unchanged and the same rules go into the forward paper loop.
- 2026-11-09 — `make check` must not be piped into `tail` when its exit code gates a commit:
  4f8a491 was pushed with one failing test because the pipe hid the failure (fixed in cb57296).
- 2026-11-09 — Forward loop (Stage F): each complete session from 2026-11-09 is computed once,
  right after it completes, with the backtest code at costs x1 and the guard as configured;
  later data revisions never change a journal row. The forward journal is out-of-sample data:
  it is monitored weekly but never used to tune parameters. (docs/forward_loop.md)
- 2026-11-09 — Schedule 18:45 and 21:30 Copenhagen time plus RunAtLoad: one fixed local time
  after the latest possible ET close (+Yahoo delay) in both DST regimes, a second try the same
  evening, and catch-up at login. The `forward` config section is operational and is NOT part
  of the strategy hash.
- 2026-11-09 — The loop commits only `data_archive/`, `journal/`, `reports/weekly_*.md` with a
  pathspec commit, never other work in the tree, and refuses files > 20 MB.
- 2026-11-09 — Alarm: expectancy < −0.10 R after >= 20 forward trades (spec F.3), compared at
  the journal's 6-decimal precision (20 x −0.1 is not an alarm; float noise was).
- 2026-11-09 — Freeze needs a reference: an experiment with `decision: FAILURE` (new front-matter
  field, set when status becomes done) or a weekly report with an ALARM for that setup. The edit
  flips only the `enabled` flag (verified by reloading the config). Re-enabling needs a new
  experiment approved after the freeze; otherwise forward/signals/backtest/check-config refuse.
- 2026-11-09 — Spec §7 fix (my omission in Stage E): reports put limitations BEFORE results and
  flag too-good results (PF > 2 on < 100 trades, annualised Sharpe > 3) as suspicious, from 10
  trades on. The two reports committed earlier (`reports/backtest_synthetic_2026-11-09.md`,
  `reports/backtest_2026-11-09_EXP-001.md`) still have the caveats at the end; their numbers
  are unchanged and EXP-001 is not re-run for this.
- 2026-11-09 — Regression test (spec §6): a fixed synthetic archive replayed through the
  forward loop must reproduce `tests/fixtures/expected_forward_trades.csv` (18 trades;
  regenerate deliberately with `PAPER_LAB_REGEN=1` and review the diff).

