from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest
from conftest import synth_bars

from paper_lab.data import storage


def _df() -> pd.DataFrame:
    return synth_bars([date(2026, 11, 2)], time(9, 30), time(10, 30), "5m")


def test_parquet_roundtrip(tmp_path: Path) -> None:
    if not storage.parquet_available():
        pytest.skip("pyarrow not installed")
    p = storage.write_cache(_df(), tmp_path, "GLD", "5m", "parquet")
    assert p.suffix == ".parquet"
    back = storage.read_cache(tmp_path, "GLD", "5m", "parquet")
    assert back is not None
    pd.testing.assert_frame_equal(back, _df(), check_freq=False)


def test_csv_fallback_when_pyarrow_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "parquet_available", lambda: False)
    p = storage.write_cache(_df(), tmp_path, "GLD", "5m", "parquet")
    assert p.suffix == ".csv"
    back = storage.read_cache(tmp_path, "GLD", "5m", "parquet")
    assert back is not None
    pd.testing.assert_frame_equal(back, _df(), check_freq=False, check_exact=False, rtol=1e-12)


def test_read_missing_cache_returns_none(tmp_path: Path) -> None:
    assert storage.read_cache(tmp_path, "GLD", "5m", "csv") is None


def test_broken_pyarrow_counts_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(name: str) -> None:
        raise OSError("broken wheel")

    monkeypatch.setattr(storage.importlib, "import_module", boom)
    assert storage.parquet_available() is False
