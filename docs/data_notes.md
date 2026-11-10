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

- Since 2026-11-10 the raw archive is **not committed** (third-party data); it stays in
  `data_archive/` on this Mac, ignored by git. Only `MANIFEST.csv` is committed. Back the folder
  up locally (Time Machine): bars older than Yahoo's history window cannot be re-downloaded.

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


## Macro-event calendar (2026–2027)

Files: `data/calendar/events_2026.csv`, `data/calendar/events_2027.csv` (retrieved 2026-11-09).
Check with `uv run paper-lab calendar`.

How the dates were obtained:
- **BLS (CPI, Employment Situation / NFP):** bls.gov returns HTTP 403 to scripts, so the pages
  were read in a browser. Past 2026 releases: the date comes from the archived release itself
  and the time from its header ("embargoed until 8:30 a.m. (ET)"). Future releases: BLS schedule
  pages, which state 08:30 AM. Status `released` or `scheduled`.
- **Fed (FOMC):** downloaded from federalreserve.gov. The statement is on the second day of the
  meeting. The time (2:00 p.m.) comes from each 2026 press release that has already been
  published. For future meetings the calendar page gives no time, so 14:00 is an **assumption**
  (`time_basis` says so). The Fed calls 2027 dates tentative until the preceding meeting, so
  they are `tentative`.
- No event is `unverified`. Other releases (PPI, retail sales, GDP, Fed speeches, etc.) are not
  included. They can be added on request.

Timing facts used by the news guard (tested): CPI and NFP are released at 08:30 ET, before the
09:30 open. FOMC statements come at 14:00 ET, after the 12:00 session end. A ±15 min window
around any of these never overlaps the session, so the guard instead blocks the first
`open_guard_min` (30) minutes on morning-release days. `news_guard.mode: off` is the
no-filter variant.

**Sample warning:** the 5m archive (2026-09-11..2026-11-09) contains only **4** morning-release
days (2026-09-11, 2026-10-02, 2026-10-14, 2026-11-06). That is far too few to judge whether the
filter helps; any backtest must say so.

| Date/time (ET) | Event | Ref. period | Status | Source |
|---|---|---|---|---|
| 2026-01-09 08:30 | NFP | 2025-12 | released | [www.bls.gov/news.release/archives/empsit_01092026.htm](https://www.bls.gov/news.release/archives/empsit_01092026.htm) |
| 2026-01-13 08:30 | CPI | 2025-12 | released | [www.bls.gov/news.release/archives/cpi_01132026.htm](https://www.bls.gov/news.release/archives/cpi_01132026.htm) |
| 2026-01-28 14:00 | FOMC | – | released | [www.federalreserve.gov/newsevents/pressreleases/monetary20260128a.htm](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260128a.htm) |
| 2026-02-11 08:30 | NFP | 2026-01 | released | [www.bls.gov/news.release/archives/empsit_02112026.htm](https://www.bls.gov/news.release/archives/empsit_02112026.htm) |
| 2026-02-13 08:30 | CPI | 2026-01 | released | [www.bls.gov/news.release/archives/cpi_02132026.htm](https://www.bls.gov/news.release/archives/cpi_02132026.htm) |
| 2026-03-06 08:30 | NFP | 2026-02 | released | [www.bls.gov/news.release/archives/empsit_03062026.htm](https://www.bls.gov/news.release/archives/empsit_03062026.htm) |
| 2026-03-11 08:30 | CPI | 2026-02 | released | [www.bls.gov/news.release/archives/cpi_03112026.htm](https://www.bls.gov/news.release/archives/cpi_03112026.htm) |
| 2026-03-18 14:00 | FOMC | – | released | [www.federalreserve.gov/newsevents/pressreleases/monetary20260318a.htm](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260318a.htm) |
| 2026-04-03 08:30 | NFP | 2026-03 | released | [www.bls.gov/news.release/archives/empsit_04032026.htm](https://www.bls.gov/news.release/archives/empsit_04032026.htm) |
| 2026-04-10 08:30 | CPI | 2026-03 | released | [www.bls.gov/news.release/archives/cpi_04102026.htm](https://www.bls.gov/news.release/archives/cpi_04102026.htm) |
| 2026-04-29 14:00 | FOMC | – | released | [www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm) |
| 2026-05-08 08:30 | NFP | 2026-04 | released | [www.bls.gov/news.release/archives/empsit_05082026.htm](https://www.bls.gov/news.release/archives/empsit_05082026.htm) |
| 2026-05-12 08:30 | CPI | 2026-04 | released | [www.bls.gov/news.release/archives/cpi_05122026.htm](https://www.bls.gov/news.release/archives/cpi_05122026.htm) |
| 2026-06-05 08:30 | NFP | 2026-05 | released | [www.bls.gov/news.release/archives/empsit_06052026.htm](https://www.bls.gov/news.release/archives/empsit_06052026.htm) |
| 2026-06-10 08:30 | CPI | 2026-05 | released | [www.bls.gov/news.release/archives/cpi_06102026.htm](https://www.bls.gov/news.release/archives/cpi_06102026.htm) |
| 2026-06-17 14:00 | FOMC | – | released | [www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm) |
| 2026-07-02 08:30 | NFP | 2026-06 | released | [www.bls.gov/news.release/archives/empsit_07022026.htm](https://www.bls.gov/news.release/archives/empsit_07022026.htm) |
| 2026-07-14 08:30 | CPI | 2026-06 | released | [www.bls.gov/news.release/archives/cpi_07142026.htm](https://www.bls.gov/news.release/archives/cpi_07142026.htm) |
| 2026-07-29 14:00 | FOMC | – | released | [www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm) |
| 2026-08-07 08:30 | NFP | 2026-07 | released | [www.bls.gov/news.release/archives/empsit_08072026.htm](https://www.bls.gov/news.release/archives/empsit_08072026.htm) |
| 2026-08-12 08:30 | CPI | 2026-07 | released | [www.bls.gov/news.release/archives/cpi_08122026.htm](https://www.bls.gov/news.release/archives/cpi_08122026.htm) |
| 2026-09-04 08:30 | NFP | 2026-08 | released | [www.bls.gov/news.release/archives/empsit_09042026.htm](https://www.bls.gov/news.release/archives/empsit_09042026.htm) |
| 2026-09-11 08:30 | CPI | 2026-08 | released | [www.bls.gov/news.release/archives/cpi_09112026.htm](https://www.bls.gov/news.release/archives/cpi_09112026.htm) |
| 2026-09-16 14:00 | FOMC | – | released | [www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm) |
| 2026-10-02 08:30 | NFP | 2026-09 | released | [www.bls.gov/news.release/archives/empsit_10022026.htm](https://www.bls.gov/news.release/archives/empsit_10022026.htm) |
| 2026-10-14 08:30 | CPI | 2026-09 | released | [www.bls.gov/news.release/archives/cpi_10142026.htm](https://www.bls.gov/news.release/archives/cpi_10142026.htm) |
| 2026-10-28 14:00 | FOMC | – | released | [www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm](https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm) |
| 2026-11-06 08:30 | NFP | 2026-10 | released | [www.bls.gov/news.release/archives/empsit_11062026.htm](https://www.bls.gov/news.release/archives/empsit_11062026.htm) |
| 2026-11-10 08:30 | CPI | 2026-10 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2026-12-04 08:30 | NFP | 2026-11 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2026-12-09 14:00 | FOMC | – | scheduled | [www.federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| 2026-12-10 08:30 | CPI | 2026-11 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-01-08 08:30 | NFP | 2026-12 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-01-13 08:30 | CPI | 2026-12 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-01-27 14:00 | FOMC | – | tentative | [www.federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| 2027-02-05 08:30 | NFP | 2027-01 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-02-11 08:30 | CPI | 2027-01 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-03-05 08:30 | NFP | 2027-02 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-03-10 08:30 | CPI | 2027-02 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-03-17 14:00 | FOMC | – | tentative | [www.federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| 2027-04-02 08:30 | NFP | 2027-03 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-04-13 08:30 | CPI | 2027-03 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-04-28 14:00 | FOMC | – | tentative | [www.federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| 2027-05-07 08:30 | NFP | 2027-04 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-05-12 08:30 | CPI | 2027-04 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-06-04 08:30 | NFP | 2027-05 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-06-09 14:00 | FOMC | – | tentative | [www.federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| 2027-06-10 08:30 | CPI | 2027-05 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-07-02 08:30 | NFP | 2027-06 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-07-14 08:30 | CPI | 2027-06 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-07-28 14:00 | FOMC | – | tentative | [www.federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| 2027-08-06 08:30 | NFP | 2027-07 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-08-11 08:30 | CPI | 2027-07 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-09-03 08:30 | NFP | 2027-08 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-09-14 08:30 | CPI | 2027-08 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-09-15 14:00 | FOMC | – | tentative | [www.federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| 2027-10-08 08:30 | NFP | 2027-09 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-10-14 08:30 | CPI | 2027-09 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-10-27 14:00 | FOMC | – | tentative | [www.federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| 2027-11-05 08:30 | NFP | 2027-10 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-11-10 08:30 | CPI | 2027-10 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |
| 2027-12-03 08:30 | NFP | 2027-11 | scheduled | [www.bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm) |
| 2027-12-08 14:00 | FOMC | – | tentative | [www.federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| 2027-12-10 08:30 | CPI | 2027-11 | scheduled | [www.bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm) |

## Validation report

`uv run paper-lab validate` writes `reports/data_quality_<date>.md` and `.json` and exits 1 if
any series has an error.
