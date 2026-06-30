#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_api_key(cli_value: str) -> str:
    value = (
        str(cli_value or "").strip()
        or str(os.getenv("RUNTIME_KEY") or "").strip()
        or str(os.getenv("ORION_API_KEY") or "").strip()
    )
    if not value:
        raise SystemExit("Missing runtime API key. Pass --api-key or set RUNTIME_KEY / ORION_API_KEY.")
    return value


def flush_event(event_name: str, data_lines: list[str], timeout_seconds: int) -> Optional[int]:
    payload = "\n".join(data_lines).strip()
    if not payload:
        return None
    if event_name == "inbox_event":
        print(payload)
        return 0
    if event_name == "done":
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = {}
        reason = str(parsed.get("reason") or "timeout").strip() or "timeout"
        print(f"TIMEOUT: no inbound Telegram message received in {timeout_seconds} seconds. reason={reason}", file=sys.stderr)
        return 124
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Listen for one fresh inbound Telegram event from the Empyralis runtime.")
    parser.add_argument("--runtime-url", default=os.getenv("RUNTIME_URL", "http://127.0.0.1:8001"))
    parser.add_argument("--api-key", default="")
    parser.add_argument("--workspace-id", default=os.getenv("WORKSPACE_ID", "default"))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--heartbeat-seconds", type=float, default=5.0)
    args = parser.parse_args()

    api_key = resolve_api_key(args.api_key)
    params = urlencode(
        {
            "workspace_id": args.workspace_id,
            "channel": "telegram",
            "direction": "inbound",
            "include_backlog": "false",
            "since_ts": utc_now_iso(),
            "timeout_seconds": str(max(1, int(args.timeout))),
            "poll_seconds": str(max(0.1, float(args.poll_seconds))),
            "heartbeat_seconds": str(max(1.0, float(args.heartbeat_seconds))),
        }
    )
    url = f"{args.runtime_url.rstrip('/')}/events/inbox/stream?{params}"
    req = Request(url, headers={"X-API-Key": api_key, "Accept": "text/event-stream"})

    try:
        with urlopen(req, timeout=max(5, int(args.timeout) + 10)) as response:
            current_event = ""
            current_data: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    code = flush_event(current_event, current_data, int(args.timeout))
                    if code is not None:
                        return code
                    current_event = ""
                    current_data = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("data:"):
                    current_data.append(line.split(":", 1)[1].lstrip())
                    continue
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"TIMEOUT: no inbound Telegram message received in {int(args.timeout)} seconds.", file=sys.stderr)
    return 124


if __name__ == "__main__":
    raise SystemExit(main())
