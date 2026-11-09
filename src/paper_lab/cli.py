"""Command-line entry point: `paper-lab <command>`.

Stage 0 provides only `check-config`. Later stages add fetch, validate, backtest, etc.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from paper_lab import __version__
from paper_lab.config import load_config
from paper_lab.logging_setup import setup_logging
from paper_lab.runinfo import new_run_info


def _cmd_check_config(args: argparse.Namespace) -> int:
    cfg, chash = load_config(args.config)
    info = new_run_info(chash)
    log = setup_logging(cfg.logging, cfg.paths.logs, info.run_id)
    log.info("config ok: %s", args.config)
    print(json.dumps(info.as_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paper-lab", description="Paper trading research lab")
    p.add_argument("--version", action="version", version=f"paper-lab {__version__}")
    p.add_argument("--config", type=Path, default=Path("config.yaml"))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="validate config.yaml and print run metadata")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check-config":
        return _cmd_check_config(args)
    return 2  # pragma: no cover - argparse rejects unknown commands


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
