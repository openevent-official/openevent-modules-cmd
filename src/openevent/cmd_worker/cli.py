from __future__ import annotations

import argparse
import logging
import sys

from openevent.sdk import OpenEventClient

from .config import ConfigError, load_config
from .worker import CmdWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cmd-worker")
    parser.add_argument("--config", required=True, help="Path to cmd-worker YAML configuration")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    client = OpenEventClient(config.open_event.addr)
    worker = CmdWorker(config, client)
    try:
        worker.run()
    except KeyboardInterrupt:
        worker.stop()
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
