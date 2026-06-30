#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import time

from server_modules.connectors.discord_bot_runtime_service import DiscordBotRuntimeService


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Empyralis Discord bot runtime.")
    parser.add_argument("--check", action="store_true", help="Run preflight checks without starting Discord listeners.")
    args = parser.parse_args()

    service = DiscordBotRuntimeService()
    if args.check:
        result = service.preflight()
        print(result, flush=True)
        return 0 if bool(result.get("ok")) else 1

    result = service.start(block=False)
    print(result, flush=True)
    if int(result.get("started") or 0) <= 0:
        return 1

    stopping = False

    def _stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        print(service.stop(), flush=True)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while not stopping:
        time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
