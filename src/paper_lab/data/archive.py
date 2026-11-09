"""Committed raw-data archive: ``data_archive/YYYY-MM/<stem>_<interval>.csv.gz``.

Rules
-----
* Partitioned by the UTC month of the bar start.
* **Keep-first:** a bar already in the archive is never overwritten. If Yahoo later returns
  a different value for the same timestamp, the difference is recorded in
  ``data_archive/revisions/YYYY-MM.csv.gz`` and the archived value stays. A backtest on
  archived data therefore cannot change after the fact.
* Files are written byte-deterministically (gzip mtime=0, fixed CSV format) and only when
  content changes, so an update with no new bars produces no git diff.
* ``MANIFEST.csv`` is rebuilt from the files on every write: rows, first/last timestamp,
  SHA-256 of the file bytes.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from paper_lab.data.bars import COLUMNS, INDEX_NAME, PRICE_COLUMNS, empty_bars, ensure_bar_frame

MANIFEST_NAME = "MANIFEST.csv"
REVISIONS_DIR = "revisions"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_REVISION_COLUMNS = ["symbol", "interval", "ts_utc", "field", "archived", "new", "first_seen_utc"]
_MANIFEST_COLUMNS = [
    "path",
    "kind",
    "stem",
    "interval",
    "rows",
    "first_ts_utc",
    "last_ts_utc",
    "sha256",
]


@dataclass(frozen=True)
class MergeResult:
    added: int
    unchanged: int
    revised: int
    files_written: tuple[str, ...]


# ---------------------------------------------------------------- serialisation


def _gzip_bytes(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0)


def bars_to_csv_text(df: pd.DataFrame) -> str:
    ensure_bar_frame(df)
    out = df.sort_index().copy()
    out.index = pd.DatetimeIndex(out.index).strftime(_TS_FORMAT)
    out.index.name = INDEX_NAME
    buf = io.StringIO()
    out.to_csv(buf, lineterminator="\n")
    return buf.getvalue()


def read_bars_file(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        df = pd.read_csv(
            f,
            index_col=INDEX_NAME,
            dtype={c: "float64" for c in PRICE_COLUMNS} | {"volume": "int64"},
        )
    df.index = pd.to_datetime(df.index, format=_TS_FORMAT, utc=True)
    df.index.name = INDEX_NAME
    return ensure_bar_frame(df[list(COLUMNS)])


def _write_if_changed(path: Path, payload: bytes) -> bool:
    if path.exists() and path.read_bytes() == payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)  # atomic on the same filesystem
    return True


# ---------------------------------------------------------------- paths


def month_key(ts: pd.Timestamp) -> str:
    return f"{ts.year:04d}-{ts.month:02d}"


def bars_path(archive_dir: Path, month: str, stem: str, interval: str) -> Path:
    return archive_dir / month / f"{stem}_{interval}.csv.gz"


# ---------------------------------------------------------------- merge


def _values_equal(a: float, b: float, field: str) -> bool:
    if field == "volume":
        return int(a) == int(b)
    if math.isnan(a) and math.isnan(b):
        return True
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def merge_into_archive(
    archive_dir: Path,
    symbol: str,
    stem: str,
    interval: str,
    new: pd.DataFrame,
    seen_utc: pd.Timestamp,
) -> MergeResult:
    """Append bars not yet archived; record (but do not apply) revisions."""
    ensure_bar_frame(new)
    if new.index.has_duplicates:
        raise ValueError("new bars contain duplicate timestamps; refusing to archive")
    added = unchanged = revised = 0
    written: list[str] = []
    revisions: list[dict[str, object]] = []

    for month, chunk in new.groupby(new.index.map(month_key)):
        path = bars_path(archive_dir, str(month), stem, interval)
        existing = read_bars_file(path) if path.exists() else empty_bars()
        overlap = chunk.index.intersection(existing.index)
        fresh = chunk.loc[~chunk.index.isin(existing.index)]
        for ts in overlap:
            row_changed = False
            for field in COLUMNS:
                old_v = float(existing[field].to_numpy()[existing.index.get_loc(ts)])
                new_v = float(chunk[field].to_numpy()[chunk.index.get_loc(ts)])
                if not _values_equal(old_v, new_v, field):
                    row_changed = True
                    revisions.append(
                        {
                            "symbol": symbol,
                            "interval": interval,
                            "ts_utc": ts.strftime(_TS_FORMAT),
                            "field": field,
                            "archived": repr(old_v),
                            "new": repr(new_v),
                            "first_seen_utc": seen_utc.strftime(_TS_FORMAT),
                        }
                    )
            revised += int(row_changed)
            unchanged += int(not row_changed)
        added += len(fresh)
        if len(fresh):
            combined = pd.concat([existing, fresh]).sort_index() if len(existing) else fresh
            if _write_if_changed(path, _gzip_bytes(bars_to_csv_text(combined))):
                written.append(str(path.relative_to(archive_dir)))

    if revisions:
        written.extend(_append_revisions(archive_dir, revisions))
    return MergeResult(added, unchanged, revised, tuple(written))


def _append_revisions(archive_dir: Path, rows: list[dict[str, object]]) -> list[str]:
    """Idempotent: the same revision (same new value) is stored once."""
    df_new = pd.DataFrame(rows, columns=_REVISION_COLUMNS)
    written = []
    key = ["symbol", "interval", "ts_utc", "field", "new"]
    for month, chunk in df_new.groupby(df_new["ts_utc"].str.slice(0, 7)):
        path = archive_dir / REVISIONS_DIR / f"{month}.csv.gz"
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as f:
                old = pd.read_csv(f, dtype=str)
            merged = pd.concat([old, chunk.astype(str)])
        else:
            merged = chunk.astype(str)
        merged = merged.drop_duplicates(subset=key, keep="first").sort_values(key)
        text = merged.to_csv(index=False, lineterminator="\n")
        if _write_if_changed(path, _gzip_bytes(text)):
            written.append(str(path.relative_to(archive_dir)))
    return written


# ---------------------------------------------------------------- load / manifest


def load_archive(archive_dir: Path, stem: str, interval: str) -> pd.DataFrame:
    files = sorted(archive_dir.glob(f"[0-9][0-9][0-9][0-9]-[0-9][0-9]/{stem}_{interval}.csv.gz"))
    if not files:
        return empty_bars()
    df = pd.concat([read_bars_file(p) for p in files]).sort_index()
    if df.index.has_duplicates:
        raise ValueError(f"archive for {stem} {interval} has duplicate timestamps across files")
    return df


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(archive_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for path in sorted(archive_dir.rglob("*.csv.gz")):
        rel = path.relative_to(archive_dir).as_posix()
        if rel.startswith(REVISIONS_DIR + "/"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                rev = pd.read_csv(f, dtype=str)
            rows.append(
                {
                    "path": rel,
                    "kind": "revisions",
                    "stem": "",
                    "interval": "",
                    "rows": len(rev),
                    "first_ts_utc": rev["ts_utc"].min() if len(rev) else "",
                    "last_ts_utc": rev["ts_utc"].max() if len(rev) else "",
                    "sha256": _sha256(path),
                }
            )
            continue
        stem, interval = path.name.removesuffix(".csv.gz").rsplit("_", 1)
        df = read_bars_file(path)
        rows.append(
            {
                "path": rel,
                "kind": "bars",
                "stem": stem,
                "interval": interval,
                "rows": len(df),
                "first_ts_utc": df.index.min().strftime(_TS_FORMAT) if len(df) else "",
                "last_ts_utc": df.index.max().strftime(_TS_FORMAT) if len(df) else "",
                "sha256": _sha256(path),
            }
        )
    return pd.DataFrame(rows, columns=_MANIFEST_COLUMNS)


def write_manifest(archive_dir: Path) -> Path:
    manifest = build_manifest(archive_dir)
    path = archive_dir / MANIFEST_NAME
    _write_if_changed(path, manifest.to_csv(index=False, lineterminator="\n").encode())
    return path


def archive_hash(archive_dir: Path) -> str | None:
    """Short hash identifying the archive state (hash of MANIFEST.csv)."""
    path = archive_dir / MANIFEST_NAME
    if not path.exists():
        return None
    return _sha256(path)[:12]
