# Data quality report — 2026-11-09

Overall status: **WARN**  
Run `20261109T162616Z-c4d27c`, commit `696277cbb674`, config `dcb40a160b4b`, data `1d24e66fd368`.

Severity: `error` = do not trust the data as-is; `warning` = usable, but know it; `info` = context. The validator never modifies or fills data.

| Symbol | Interval | Rows | First (UTC) | Last (UTC) | Trading days | Status |
|---|---|---:|---|---|---:|---|
| GC=F | 1m | 28419 | 2026-10-11T22:11:00Z | 2026-11-09T16:02:00Z | 21 | WARN |
| GC=F | 5m | 11259 | 2026-09-11T16:30:00Z | 2026-11-09T15:50:00Z | 41 | WARN |
| GC=F | 1h | 11456 | 2024-11-11T00:00:00Z | 2026-11-09T14:00:00Z | 499 | WARN |
| GLD | 1m | 7791 | 2026-10-12T13:31:00Z | 2026-11-09T16:02:00Z | 21 | WARN |
| GLD | 5m | 3160 | 2026-09-11T16:30:00Z | 2026-11-09T15:50:00Z | 41 | WARN |
| GLD | 1h | 3471 | 2024-11-11T15:30:00Z | 2026-11-06T20:30:00Z | 499 | WARN |

## GC=F 1m — WARN

- **warning** `outside_trading_hours` (1): 0.0% of bars start outside 18:00-17:00 ET (weekly hours). Examples: 2026-10-13T21:00:00Z
- **warning** `zero_volume` (20): 0.1% of bars have zero volume. Examples: 2026-10-12T22:00:00Z, 2026-10-13T21:00:00Z, 2026-10-14T22:00:00Z, 2026-10-15T22:00:00Z, 2026-10-18T22:10:00Z
- **info** `flat_zero_volume` (1): bars with O=H=L=C and zero volume (likely provider-synthesised).
- **warning** `absurd_candle` (12): range > 8.0 x ATR(20). Examples: 2026-10-12T22:00:00Z, 2026-10-14T12:30:00Z, 2026-10-15T07:36:00Z, 2026-10-15T12:30:00Z, 2026-10-16T14:20:00Z
- **warning** `session_gaps` (10): 10 missing bars inside the session on 1 day(s). Examples: 2026-10-16: 10 missing

## GC=F 5m — WARN

- **warning** `outside_trading_hours` (3): 0.0% of bars start outside 18:00-17:00 ET (weekly hours). Examples: 2026-09-23T21:00:00Z, 2026-10-05T21:00:00Z, 2026-10-13T21:00:00Z
- **warning** `zero_volume` (32): 0.3% of bars have zero volume. Examples: 2026-09-14T22:00:00Z, 2026-09-15T22:00:00Z, 2026-09-16T22:00:00Z, 2026-09-17T22:00:00Z, 2026-09-21T22:00:00Z
- **info** `flat_zero_volume` (1): bars with O=H=L=C and zero volume (likely provider-synthesised).
- **warning** `absurd_candle` (6): range > 8.0 x ATR(20). Examples: 2026-09-16T18:00:00Z, 2026-10-02T12:30:00Z, 2026-10-07T12:25:00Z, 2026-10-14T12:30:00Z, 2026-10-15T07:35:00Z
- **warning** `price_jump` (1): |open - previous close| > 8.0 x ATR between contiguous bars (bad print or contract roll?). Examples: 2026-10-15T07:35:00Z
- **warning** `session_gaps` (2): 2 missing bars inside the session on 1 day(s). Examples: 2026-10-16: 2 missing

## GC=F 1h — WARN

- **warning** `mixed_offsets` (2): 1h bars start at different minute offsets: [0, 30].
- **warning** `outside_trading_hours` (7): 0.1% of bars start outside 18:00-17:00 ET (weekly hours). Examples: 2025-05-14T21:00:00Z, 2025-08-18T21:00:00Z, 2026-08-14T21:00:00Z, 2026-09-03T21:00:00Z, 2026-09-23T21:00:00Z
- **warning** `zero_volume` (410): 3.6% of bars have zero volume. Examples: 2024-11-11T23:00:00Z, 2024-11-12T23:00:00Z, 2024-11-13T23:00:00Z, 2024-11-14T23:00:00Z, 2024-11-18T23:00:00Z
- **info** `flat_zero_volume` (1): bars with O=H=L=C and zero volume (likely provider-synthesised).
- **warning** `absurd_candle` (4): range > 8.0 x ATR(20). Examples: 2025-07-30T07:00:00Z, 2025-08-22T14:00:00Z, 2026-02-12T16:00:00Z, 2026-09-04T12:00:00Z
- **warning** `bars_on_exchange_holiday` (4): bars inside the session window on days the XNYS calendar is closed. Examples: 2025-01-09, 2025-05-26, 2025-06-19, 2025-07-04

## GLD 1m — WARN

- **warning** `zero_volume` (4): 0.1% of bars have zero volume. Examples: 2026-10-20T14:41:00Z, 2026-10-21T14:32:00Z, 2026-10-29T18:49:00Z, 2026-11-03T16:05:00Z
- **warning** `absurd_candle` (5): range > 8.0 x ATR(20). Examples: 2026-10-20T13:30:00Z, 2026-10-21T16:39:00Z, 2026-10-28T16:54:00Z, 2026-10-28T18:00:00Z, 2026-10-28T18:33:00Z
- **warning** `session_gaps` (4): 4 missing bars inside the session on 1 day(s). Examples: 2026-11-05: 4 missing

## GLD 5m — WARN

- **warning** `zero_volume` (7): 0.2% of bars have zero volume. Examples: 2026-09-11T18:40:00Z, 2026-09-15T17:30:00Z, 2026-09-15T17:45:00Z, 2026-09-15T17:50:00Z, 2026-09-17T16:25:00Z
- **warning** `absurd_candle` (1): range > 8.0 x ATR(20). Examples: 2026-09-16T18:00:00Z

## GLD 1h — WARN

- **warning** `zero_volume` (3): 0.1% of bars have zero volume. Examples: 2025-11-17T17:30:00Z, 2026-06-22T15:30:00Z, 2026-09-11T18:30:00Z
- **warning** `absurd_candle` (1): range > 8.0 x ATR(20). Examples: 2026-01-29T14:30:00Z
