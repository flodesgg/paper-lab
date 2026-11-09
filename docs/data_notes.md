# Data notes

Everything here was observed by running code on **2026-11-09** (yfinance 1.8.0, Yahoo Finance
backend). Free data sources change without notice; re-check these facts when something breaks.

## Source and instruments

| Symbol | What it is | Hours on Yahoo (ET) | Price unit |
|---|---|---|---|
| `GC=F` | COMEX gold futures, continuous front month | Sun–Fri 18:00–17:00, daily break 17:00–18:00 | USD per troy oz (contract = 100 oz) |
| `GLD` | SPDR Gold Shares ETF | 09:30–16:00 (requested with `prepost=False`) | USD per share |

Source: `yfinance` (unofficial Yahoo Finance API). No account, no key. Not an official or
licensed data feed: Yahoo can change limits, revise bars, or break the API at any time.

## History available today (hard limits returned by Yahoo)

| Interval | Lookback limit | Per-request limit | First bar available on 2026-11-09 |
|---|---|---|---|
| 1m | last **30 days** | **8 days** per request | ≈ 2026-10-10 |
| 5m | last **60 days** | — | ≈ 2026-09-10 |
| 1h | last **730 days** | — | ≈ 2024-11-10 |
| 1d | many years (GC=F from 2000-08-30, GLD from 2004-11-18) | — | — |

Error messages seen beyond the limits, e.g. *"5m data not available … The requested range must
be within the last 60 days"*, *"Only 8 days worth of 1m granularity data are allowed to be fetched
per request"*. The fetcher stays one day inside each limit (`config.yaml: data.limits`).

**Consequence:** 5-minute history for a backtest is about 40 US trading days. 1-minute bars
disappear after 30 days — they exist for this project only because `data_archive/` stores them
daily. Missing a month of daily runs loses 1-minute data permanently.

## Yahoo quirks found today (and how the code handles them)

1. **First row of every response has volume 0.** Prices are correct; only volume is zeroed.
   For `GC=F` this happens for any request start; for `GLD` when the start falls inside or at a
   bar. Verified by requesting the same bar as first row vs. mid-response (e.g. GC=F 5m
   2026-11-05 15:00 UTC: volume 0 vs. 2882). *Handling:* the first row of every response is
   dropped and every follow-up request starts at the last bar already received, so the dropped
   row is a duplicate. `FetchStats.first_row_not_anchor` counts any bar lost this way (0 so far).
   The very first bar of a full backfill is lost by design.
2. **Recent bars are not final.** COMEX data on Yahoo is delayed ~10 minutes and the newest bar is
   still forming. Live `GLD` volume is re-attributed between recent 1m/5m bars for ~15 minutes
   (seen: 15:15 UTC 5m bar 78 899 → 107 537 = +28 638 moved from the 15:19 1m bar).
   *Handling:* bars that ended less than `data_delay_min` (20 min) + `incomplete_bar_grace_sec`
   before the fetch are not archived, and the newest bar of every fetch is dropped. The next
   update re-fetches them (overlap of `update_overlap_days`).
3. **A live bar with an off-grid timestamp** (e.g. `08:57:51`) can appear at the end of a
   response. *Handling:* dropped as misaligned (counted).
4. **Empty windows** (weekend/holiday) raise `YFPricesMissingError("possibly delisted…")` when
   `raise_errors=True`. They are treated as "no bars"; limit errors are raised; network errors are
   retried. Without `raise_errors=True`, yfinance prints errors and returns an empty frame, which
   would hide failures — so it is always on.
5. **GLD pre/post-market** bars are mostly zero-volume; `prepost=False` keeps regular hours only.

## Archive policy

- `data_archive/YYYY-MM/<symbol>_<interval>.csv.gz`, partitioned by UTC month of bar start,
  written byte-deterministically (gzip mtime 0); unchanged content is never rewritten.
- **Keep-first:** an archived bar is never overwritten. If Yahoo later reports a different value,
  it is written to `data_archive/revisions/YYYY-MM.csv.gz` (once per distinct new value). This
  keeps backtests reproducible and makes provider revisions measurable.
- `MANIFEST.csv`: rows, first/last timestamp and SHA-256 for every file; its hash is the
  `data_hash` recorded by every run.

## GC=F continuous series and rolls

`GC=F` splices successive front-month contracts. At a roll the series jumps from one contract to
the next (the next contract usually trades at a premium of carry, a few USD/oz), producing a gap
that is not a market move, and volume shifts between contracts around the roll. Yahoo does not
say when it rolls. Intraday setups that enter and exit within 09:30–12:00 ET are mostly unaffected,
but: (a) indicators that span a roll (multi-day ATR, overnight gaps) are distorted; (b) volume
averages around roll days are unreliable. The validator's absurd-candle and gap checks may flag
roll days; such flags must be read with this in mind. Roll dates are not yet detected
automatically (to do when daily data is analysed).

## Coverage archived on 2026-11-09

Source: [reports/data_quality_2026-11-09.md](../reports/data_quality_2026-11-09.md) (run
`20261109T162616Z-c4d27c`, data hash `1d24e66fd368`).

| Series | First bar (UTC) | US trading days with bars in 09:30–12:00 ET |
|---|---|---:|
| GC=F / GLD 1m | 2026-10-11 / 2026-10-12 | 21 |
| GC=F / GLD 5m | 2026-09-11 | 41 |
| GC=F / GLD 1h | 2024-11-11 | 499 |

So a 5-minute backtest today covers **41 trading days**. Everything after this date is only
available because the archive grows daily.

## How to read the validator warnings (2026-11-09 report: 0 errors, all series WARN)

- `absurd_candle` at 12:30 UTC (08:30 ET): CPI / payrolls-type release minutes. Real moves, not
  bad data — exactly the bars the news guard (Stage B) is for.
- `bars_on_exchange_holiday` for GC=F (2025-01-09, Memorial Day, Juneteenth, July 4): COMEX
  trades (abbreviated) when NYSE is closed. Expected; GLD has none.
- `zero_volume` for GC=F around 22:00–23:00 UTC: the first bar after the daily 17:00–18:00 ET
  break often reports 0 volume. Outside the analysis session, so harmless for 09:30–12:00 setups.
- `outside_trading_hours` (a handful of GC=F bars at 17:00 ET): stray bars in the daily break.
- `session_gaps`: real holes in Yahoo data (e.g. GC=F 1m on 2026-10-16: 10 bars). Never filled.
- `mixed_offsets` for GC=F 1h: a few hourly bars start at :30. Not investigated yet; 1h data is
  not used for intraday entries.

## Validation report

`uv run paper-lab validate` writes `reports/data_quality_<date>.md` and `.json` and exits 1 if
any series has an error.
