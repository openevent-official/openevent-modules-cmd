from __future__ import annotations

import argparse
import logging
import signal
import sys

from openevent.cmd_sdk.dependencies import require_sdk

from .config import ConfigError, load_config
from .worker import CmdWorker


class _StopRequested(Exception):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cmd-worker")
    parser.add_argument("--config", required=True, help="Path to cmd-worker YAML configuration")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        require_sdk()
        config = load_config(args.config)
    except (ConfigError, RuntimeError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    from openevent.sdk import OpenEventClient

    client = OpenEventClient(config.open_event.addr)
    worker = CmdWorker(config, client)
    previous_handlers = {value: signal.getsignal(value) for value in (signal.SIGTERM, signal.SIGINT)}
    stop_signal = None

    def request_stop(signum, frame):
        nonlocal stop_signal
        del frame
        if stop_signal is None:
            stop_signal = signum
        # stop() also marks stopping before cleanup after a program error.
        # Further signals must not interrupt cleanup or replace that error.
        if worker.stopping:
            return
        worker.stopping = True
        if not worker.starting_command:
            raise _StopRequested

    for value in previous_handlers:
        signal.signal(value, request_stop)
    try:
        worker.run()
    except KeyboardInterrupt:
        return 130
    except _StopRequested:
        pass
    finally:
        for value, handler in previous_handlers.items():
            signal.signal(value, handler)
        client.close()
    return 128 + stop_signal if stop_signal is not None else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
