"""Working-cache storage: parquet when pyarrow works, CSV otherwise.

The cache in ``data/`` is disposable (rebuilt from ``data_archive/``). The fallback exists
because pyarrow failed to install/import once in the previous version of this lab.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Literal

import pandas as pd

from paper_lab.data.bars import INDEX_NAME, ensure_bar_frame

log = logging.getLogger("paper_lab.data.storage")

Format = Literal["parquet", "csv"]


def parquet_available() -> bool:
    try:
        importlib.import_module("pyarrow")
    except Exception:  # ImportError, or a broken binary wheel raising something else
        return False
    return True


def effective_format(preferred: Format) -> Format:
    if preferred == "parquet" and not parquet_available():
        log.warning("pyarrow unavailable: falling back to CSV for the working cache")
        return "csv"
    return preferred


def cache_path(cache_dir: Path, stem: str, interval: str, fmt: Format) -> Path:
    return cache_dir / f"{stem}_{interval}.{fmt}"


def write_cache(df: pd.DataFrame, cache_dir: Path, stem: str, interval: str, fmt: Format) -> Path:
    ensure_bar_frame(df)
    fmt = effective_format(fmt)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, stem, interval, fmt)
    if fmt == "parquet":
        df.to_parquet(path)
    else:
        df.to_csv(path, date_format="%Y-%m-%dT%H:%M:%SZ")
    return path


def read_cache(cache_dir: Path, stem: str, interval: str, fmt: Format) -> pd.DataFrame | None:
    fmt = effective_format(fmt)
    path = cache_path(cache_dir, stem, interval, fmt)
    if not path.exists():
        return None
    if fmt == "parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, index_col=INDEX_NAME)
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = INDEX_NAME
    return ensure_bar_frame(df)
