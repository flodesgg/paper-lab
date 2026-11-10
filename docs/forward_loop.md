# Forward paper loop (Stage F)

The forward loop is the part that builds real evidence: every session after the in-sample
archive (from 2026-11-09, the first out-of-sample day) is traded on paper with the exact
rules pinned by EXP-001, once, right after it completes. Nothing is sent to any broker.

## What one run does (`uv run paper-lab forward`)
1. Fetches new bars into `data_archive/` (local only, ignored by git) (same code as `paper-lab fetch --update`). A failed
   series is logged; the run continues with what is archived and exits 1.
2. For each instrument, finds the complete sessions since `forward.start` that are not yet in
   `journal/daily.csv`: today after the close, plus any day missed while the Mac was asleep or
   off (catch-up). A session still running or not yet in Yahoo's data is "waiting" and is
   picked up by the next run.
3. Runs the enabled setups on those sessions with the backtest code (costs x1, news guard as
   configured, daily risk gate) and upserts `journal/trades.csv` and `journal/daily.csv`.
   A session already in the journal is never recomputed.
4. Writes `reports/weekly_<last session>.md` for every finished week without a report.
5. Appends one line to `journal/runs.csv` (run id, commit, strategy/data hash, what happened).
6. With `--commit --push`: commits only `data_archive/MANIFEST.csv`, `journal/` and `reports/weekly_*.md`
   (pathspec commit; anything else you have staged is left alone; files > 20 MB are refused)
   and pushes. A failed push is logged and retried by the next run's push.

Only one run at a time (`data/forward.lock`). Re-running is always safe.

## Schedule (launchd)
`scripts/agent.sh install` writes `~/Library/LaunchAgents/com.paperlab.forward.plist` and
loads it. It runs at the local times in `config.yaml` → `forward.schedule_local`
(18:45 and 21:30 Copenhagen time; the ET session ends 12:00 = 18:00 local, 17:00 in the
US/EU daylight-saving mismatch weeks):
- Mac asleep at that time → launchd runs the job once on wake;
- Mac off → `RunAtLoad` runs it at login; catch-up processes every missed session;
- the 5-minute history on Yahoo is ~60 days, so a gap longer than that loses sessions
  (they would show as "not processed" in the weekly report).

Logs: `logs/paper_lab.log` (rotating), `logs/launchd.out.log`, `logs/launchd.err.log`.
`git push` from launchd uses the same credential helper as your shell (`zsh -l`).

```bash
scripts/agent.sh install     # install + load (runs once immediately)
scripts/agent.sh status      # loaded? last exit code, last journal/runs.csv lines
scripts/agent.sh run-now     # trigger a run now
```

## Uninstall
```bash
scripts/agent.sh uninstall
```
This unloads the job (`launchctl bootout gui/$UID/com.paperlab.forward`) and deletes the
plist; it then checks that the job is no longer loaded. Nothing else on the system is touched.
Manual equivalent:
```bash
launchctl bootout gui/$(id -u)/com.paperlab.forward
rm ~/Library/LaunchAgents/com.paperlab.forward.plist
```

## Weekly report, alarm and freeze
- The weekly report puts limitations first, then the alarm check, cumulative forward results
  with a bootstrap CI next to the EXP-001 baseline, the week's signals, and data quality.
- **Alarm:** forward expectancy below −0.10 R after at least 20 forward trades (per setup and
  symbol) → `ALARM: forward result contradicts the backtest` and a recommendation to freeze.
- **Freeze:** `uv run paper-lab freeze --setup orb --reference <EXP-NNN with decision FAILURE |
  reports/weekly_<date>.md with an ALARM for that setup> --reason "..."` sets
  `enabled: false` in `config.yaml` (no other change), records `experiments/frozen.csv` and
  prints the commit command; the commit message names the reference. A frozen setup that is
  enabled again without a NEW experiment approved after the freeze makes `forward`, `signals`,
  `backtest` and `check-config` refuse to run.
- Results that look too good (profit factor > 2 on fewer than 100 trades, annualised Sharpe
  > 3; checked from 10 trades) are flagged `SUSPICIOUS` as a likely bug.
