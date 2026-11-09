"""Logging with file rotation into logs/."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from paper_lab.config import LoggingConfig

_FORMAT = "%(asctime)s %(levelname)s %(name)s [run=%(run_id)s] %(message)s"


class _RunIdFilter(logging.Filter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        return True


def setup_logging(cfg: LoggingConfig, log_dir: Path, run_id: str) -> logging.Logger:
    """Configure the 'paper_lab' logger: rotating file + stderr. Idempotent."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("paper_lab")
    logger.setLevel(cfg.level)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    run_filter = _RunIdFilter(run_id)
    fmt = logging.Formatter(_FORMAT)

    file_handler = RotatingFileHandler(
        log_dir / cfg.file, maxBytes=cfg.max_bytes, backupCount=cfg.backup_count
    )
    stream_handler = logging.StreamHandler()
    for h in (file_handler, stream_handler):
        h.setFormatter(fmt)
        h.addFilter(run_filter)
        logger.addHandler(h)
    logger.propagate = False
    return logger
