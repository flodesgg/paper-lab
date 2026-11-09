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
