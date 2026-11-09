"""Stage B: sessions, DST, holidays, early closes, event calendar, news guard."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from paper_lab.calendar.events import Event, load_events
from paper_lab.calendar.news_guard import CalendarCoverageError, NewsGuard
from paper_lab.calendar.sessions import (
    early_closes,
    holidays,
    is_trading_day,
    session_window,
    trading_days,
)
from paper_lab.config import Config, NewsGuardConfig, load_config

REPO = Path(__file__).resolve().parents[1]
CPH = ZoneInfo("Europe/Copenhagen")
ET = ZoneInfo("America/New_York")


@pytest.fixture(scope="module")
def conf() -> Config:
    c, _ = load_config(REPO / "config.yaml")
    return c


@pytest.fixture(scope="module")
def events(conf: Config) -> list[Event]:
    return load_events([REPO / p for p in conf.calendar.events_files])


def _cph_open(conf: Config, d: date) -> str:
    w = session_window(conf.session, d)
    assert w is not None
    return w.open_utc.tz_convert(CPH).strftime("%H:%M")


# ---------------------------------------------------------------- DST


@pytest.mark.parametrize(
    ("day", "cph", "utc"),
    [
        # 2026: US switches Mar 8 / Nov 1, EU switches Mar 29 / Oct 25
        (date(2026, 3, 6), "15:30", "14:30"),  # both on winter time
        (date(2026, 3, 9), "14:30", "13:30"),  # US summer, EU winter: mismatch weeks
        (date(2026, 3, 27), "14:30", "13:30"),
        (date(2026, 3, 30), "15:30", "13:30"),  # both on summer time
        (date(2026, 10, 23), "15:30", "13:30"),
        (date(2026, 10, 26), "14:30", "13:30"),  # EU winter, US still summer
        (date(2026, 10, 30), "14:30", "13:30"),
        (date(2026, 11, 2), "15:30", "14:30"),  # both on winter time
        # 2027: US Mar 14 / Nov 7, EU Mar 28 / Oct 31
        (date(2027, 3, 15), "14:30", "13:30"),
        (date(2027, 3, 25), "14:30", "13:30"),  # (Mar 26 is Good Friday)
        (date(2027, 3, 29), "15:30", "13:30"),
        (date(2027, 11, 1), "14:30", "13:30"),
        (date(2027, 11, 5), "14:30", "13:30"),
        (date(2027, 11, 8), "15:30", "14:30"),
    ],
)
def test_ny_open_in_copenhagen_across_dst_mismatch_weeks(
    conf: Config, day: date, cph: str, utc: str
) -> None:
    w = session_window(conf.session, day)
    assert w is not None
    assert w.open_utc.strftime("%H:%M") == utc
    assert _cph_open(conf, day) == cph
    assert w.open_utc.tz_convert(ET).strftime("%H:%M") == "09:30"
    assert w.close_utc - w.open_utc == pd.Timedelta(hours=2, minutes=30)


# ---------------------------------------------------------------- holidays / early closes


def test_holidays_2026(conf: Config) -> None:
    h = set(holidays(conf.session.exchange_calendar, 2026))
    for d in [
        date(2026, 1, 1),
        date(2026, 4, 3),
        date(2026, 7, 3),
        date(2026, 11, 26),
        date(2026, 12, 25),
    ]:  # New Year, Good Friday, July 4 observed, Thanksgiving, Xmas
        assert d in h
        assert session_window(conf.session, d) is None
    assert date(2026, 10, 12) not in h  # Columbus Day: NYSE open


def test_early_closes_2026_do_not_cut_a_noon_session(conf: Config) -> None:
    ec = early_closes(conf.session.exchange_calendar, 2026)
    assert set(ec) == {date(2026, 11, 27), date(2026, 12, 24)}
    for d in ec:
        assert ec[d].tz_convert(ET).time() == time(13, 0)
        w = session_window(conf.session, d)
        assert w is not None and not w.early_close
        assert w.close_utc.tz_convert(ET).time() == time(12, 0)


def test_early_close_cuts_a_longer_session(conf: Config) -> None:
    long = conf.session.model_copy(update={"end": time(15, 0)})
    w = session_window(long, date(2026, 11, 27))
    assert w is not None and w.early_close
    assert w.close_utc.tz_convert(ET).time() == time(13, 0)
    assert w.first_minutes(600)[1] == w.close_utc  # never beyond the close


def test_calendar_range_does_not_depend_on_today(conf: Config) -> None:
    assert is_trading_day(conf.session.exchange_calendar, date(2027, 12, 31))
    assert not is_trading_day(conf.session.exchange_calendar, date(2027, 12, 24))  # Xmas obs.
    assert (
        len(trading_days(conf.session.exchange_calendar, date(2027, 1, 1), date(2027, 12, 31)))
        > 240
    )


def test_first_minutes(conf: Config) -> None:
    w = session_window(conf.session, date(2026, 11, 10))
    assert w is not None
    s, e = w.first_minutes(30)
    assert s.tz_convert(ET).time() == time(9, 30) and e.tz_convert(ET).time() == time(10, 0)


# ---------------------------------------------------------------- event calendar


def test_calendar_files_cover_both_years_with_official_sources(events: list[Event]) -> None:
    for year in (2026, 2027):
        ys = [e for e in events if e.ts_et.year == year]
        for name, n in (("CPI", 12), ("NFP", 12), ("FOMC", 8)):
            assert sum(e.event == name for e in ys) == n, (year, name)
    assert all(e.source_url.startswith("https://www.") for e in events)
    assert not [e for e in events if e.status == "unverified"]


def test_known_timing_facts(events: list[Event]) -> None:
    """CPI/NFP at 08:30 ET (before the open); FOMC statement at 14:00 ET (after 12:00)."""
    for e in events:
        expected = time(14, 0) if e.event == "FOMC" else time(8, 30)
        assert e.ts_et.time() == expected, e


def test_plain_15_min_window_never_touches_the_session(conf: Config, events: list[Event]) -> None:
    """Why open_guard_min exists: +/-15 min around any event misses 09:30-12:00 entirely."""
    for e in events:
        w = session_window(conf.session, e.day)
        if w is None:
            continue
        lo, hi = e.ts_utc - pd.Timedelta(minutes=15), e.ts_utc + pd.Timedelta(minutes=15)
        assert hi <= w.open_utc or lo >= w.close_utc, e


def test_known_2026_dates(events: list[Event]) -> None:
    keys = {(e.ts_et.strftime("%Y-%m-%d %H:%M"), e.event) for e in events}
    assert ("2026-11-10 08:30", "CPI") in keys  # first scheduled CPI after today
    assert ("2026-11-06 08:30", "NFP") in keys
    assert ("2026-10-28 14:00", "FOMC") in keys
    assert ("2026-02-11 08:30", "NFP") in keys  # a Wednesday release, as published


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    header = "datetime_et,event,reference_period,source_url,retrieved_on,status,time_basis"
    p = tmp_path / "events.csv"
    p.write_text("\n".join([header, *rows]) + "\n")
    return p


@pytest.mark.parametrize(
    ("row", "msg"),
    [
        ("2026-11-10 08:30,GDP,,https://www.bls.gov/x,2026-11-09,scheduled,x", "unknown event"),
        ("2026-11-10 08:30,CPI,,https://www.bls.gov/x,2026-11-09,guess,x", "unknown status"),
        ("2026-11-10 08:30,CPI,,https://example.com/x,2026-11-09,scheduled,x", "official"),
        ("2026-11-14 08:30,CPI,,https://www.bls.gov/x,2026-11-09,scheduled,x", "weekend"),
        ("2026-11-10 08:30,CPI,,https://www.bls.gov/x,,scheduled,x", "retrieved_on"),
    ],
)
def test_bad_calendar_rows_rejected(tmp_path: Path, row: str, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        load_events([_write_csv(tmp_path, [row])])


def test_duplicate_events_rejected(tmp_path: Path) -> None:
    row = "2026-11-10 08:30,CPI,,https://www.bls.gov/x,2026-11-09,scheduled,x"
    with pytest.raises(ValueError, match="duplicate"):
        load_events([_write_csv(tmp_path, [row, row])])


# ---------------------------------------------------------------- news guard


def _ts(d: date, hh: int, mm: int) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(d, time(hh, mm), tzinfo=ET)).tz_convert("UTC")


def _guard(conf: Config, events: list[Event], **kw: object) -> NewsGuard:
    return NewsGuard(conf.news_guard.model_copy(update=kw), conf.session, events)


CPI_DAY = date(2026, 11, 10)
NFP_DAY = date(2026, 11, 6)
FOMC_DAY = date(2026, 10, 28)
QUIET = date(2026, 11, 9)


def test_event_day_detection(conf: Config, events: list[Event]) -> None:
    g = _guard(conf, events)
    assert g.is_event_day(CPI_DAY) and g.is_event_day(NFP_DAY)
    assert not g.is_event_day(FOMC_DAY)  # 14:00 release is after the session
    assert not g.is_event_day(QUIET)
    assert not g.is_event_day(date(2026, 11, 26))  # holiday


def test_open_guard_blocks_first_minutes_only(conf: Config, events: list[Event]) -> None:
    g = _guard(conf, events, open_guard_min=30)
    assert not g.check_entry(_ts(CPI_DAY, 9, 30)).allowed
    assert "CPI" in g.check_entry(_ts(CPI_DAY, 9, 59)).reason
    assert g.check_entry(_ts(CPI_DAY, 10, 0)).allowed
    assert g.check_entry(_ts(QUIET, 9, 30)).allowed
    assert g.check_entry(_ts(FOMC_DAY, 9, 30)).allowed


def test_open_guard_length_is_configurable(conf: Config, events: list[Event]) -> None:
    g = _guard(conf, events, open_guard_min=60)
    assert not g.check_entry(_ts(NFP_DAY, 10, 25)).allowed
    assert g.check_entry(_ts(NFP_DAY, 10, 30)).allowed


def test_off_mode_is_the_no_filter_variant(conf: Config, events: list[Event]) -> None:
    g = _guard(conf, events, mode="off")
    assert g.check_entry(_ts(CPI_DAY, 9, 30)).allowed


def test_entries_outside_session_rejected(conf: Config, events: list[Event]) -> None:
    g = _guard(conf, events)
    assert not g.check_entry(_ts(QUIET, 12, 0)).allowed
    assert not g.check_entry(_ts(QUIET, 9, 29)).allowed
    assert not g.check_entry(_ts(date(2026, 11, 26), 10, 0)).allowed  # Thanksgiving


def test_in_session_event_window(conf: Config) -> None:
    ev = [
        Event(datetime(2026, 11, 12, 10, 0, tzinfo=ET), "CPI", "scheduled", "https://www.bls.gov/x")
    ]
    g = NewsGuard(
        NewsGuardConfig(
            mode="open_guard", open_guard_min=30, morning_events=["CPI"], in_session_window_min=15
        ),
        conf.session,
        ev,
    )
    d = date(2026, 11, 12)
    assert not g.is_event_day(d)  # not a *morning* release
    assert not g.check_entry(_ts(d, 9, 45)).allowed
    assert not g.check_entry(_ts(d, 10, 15)).allowed
    assert g.check_entry(_ts(d, 10, 16)).allowed
    assert g.check_entry(_ts(d, 9, 30)).allowed


def test_guard_dst_mismatch_week(conf: Config) -> None:
    """A morning release in the US/EU mismatch week still blocks 09:30-10:00 ET."""
    d = date(2026, 10, 28)
    ev = [
        Event(datetime(2026, 10, 28, 8, 30, tzinfo=ET), "CPI", "released", "https://www.bls.gov/x")
    ]
    g = NewsGuard(conf.news_guard, conf.session, ev)
    assert not g.check_entry(pd.Timestamp("2026-10-28T13:30Z")).allowed  # 14:30 Copenhagen
    assert g.check_entry(pd.Timestamp("2026-10-28T14:00Z")).allowed
    assert g.is_event_day(d)


def test_uncovered_year_fails_loudly(conf: Config, events: list[Event]) -> None:
    g = _guard(conf, events)
    with pytest.raises(CalendarCoverageError):
        g.check_entry(_ts(date(2028, 1, 4), 10, 0))
