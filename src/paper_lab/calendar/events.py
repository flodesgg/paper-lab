"""Macro-event calendar loaded from ``data/calendar/events_<year>.csv``.

Every row carries the official source URL it was taken from. BLS blocks scripted downloads
(HTTP 403 on 2026-11-09), so BLS dates were read from bls.gov pages in a browser and
transcribed; Fed dates were downloaded from federalreserve.gov. See docs/data_notes.md.

Status values:
  released   - the release happened; date/time read from the release itself
  scheduled  - from the agency's official schedule; can still change
  tentative  - the agency itself calls the date tentative (Fed: until the preceding meeting)
  unverified - could not be confirmed from an official source (none at present)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
COLUMNS = [
    "datetime_et",
    "event",
    "reference_period",
    "source_url",
    "retrieved_on",
    "status",
    "time_basis",
]
EVENTS = ("CPI", "NFP", "FOMC")
STATUSES = ("released", "scheduled", "tentative", "unverified")
OFFICIAL_HOSTS = ("www.bls.gov", "www.federalreserve.gov")


@dataclass(frozen=True)
class Event:
    ts_et: datetime
    event: str
    status: str
    source_url: str

    @property
    def ts_utc(self) -> pd.Timestamp:
        return pd.Timestamp(self.ts_et).tz_convert("UTC")

    @property
    def day(self) -> date:
        return self.ts_et.date()


def load_events(paths: list[Path]) -> list[Event]:
    """Read and validate event files. Raises ValueError on anything suspicious."""
    frames = []
    for p in paths:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        if list(df.columns) != COLUMNS:
            raise ValueError(f"{p}: columns {list(df.columns)} != {COLUMNS}")
        df["_file"] = str(p)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    events = []
    for rec in df.to_dict("records"):
        row = {str(k): str(v) for k, v in rec.items()}
        where = f"{row['_file']}: {row['datetime_et']} {row['event']}"
        if row["event"] not in EVENTS:
            raise ValueError(f"{where}: unknown event")
        if row["status"] not in STATUSES:
            raise ValueError(f"{where}: unknown status {row['status']!r}")
        url = urlparse(row["source_url"])
        if url.scheme != "https" or url.netloc not in OFFICIAL_HOSTS:
            raise ValueError(f"{where}: source is not an official https URL: {row['source_url']}")
        naive = datetime.strptime(row["datetime_et"], "%Y-%m-%d %H:%M")
        ts = naive.replace(tzinfo=ET)
        if ts.weekday() >= 5:
            raise ValueError(f"{where}: falls on a weekend")
        if not row["retrieved_on"]:
            raise ValueError(f"{where}: retrieved_on missing")
        events.append(Event(ts, row["event"], row["status"], row["source_url"]))
    keys = [(e.ts_et, e.event) for e in events]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate events across calendar files")
    return sorted(events, key=lambda e: (e.ts_et, e.event))


def events_on(events: list[Event], day: date) -> list[Event]:
    return [e for e in events if e.day == day]


def covered_years(events: list[Event]) -> set[int]:
    return {e.ts_et.year for e in events}
