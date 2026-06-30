#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib import error, request


def fetch_json(base_url: str, path: str, api_key: str) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    req = request.Request(url, headers={"X-API-Key": api_key})
    with request.urlopen(req, timeout=5) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw)


def print_block(title: str, payload: Any) -> None:
    print(f"[{title}]")
    print(json.dumps(payload, indent=2, sort_keys=True))
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Print local runtime, worker, and queue status.")
    parser.add_argument("--base-url", required=True, help="Runtime base URL, for example http://127.0.0.1:8001")
    parser.add_argument("--api-key", required=True, help="Runtime API key")
    parser.add_argument(
        "--workspace-id",
        default="default",
        help="Workspace id for queue status checks. Defaults to default.",
    )
    args = parser.parse_args()

    checks = [
        ("health", "/health"),
        ("workers", "/local/workers/status"),
        ("queue", f"/runs/queue/local?workspace_id={args.workspace_id}"),
    ]

    for title, path in checks:
        try:
            payload = fetch_json(args.base_url, path, args.api_key)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            print_block(title, {"status": exc.code, "error": detail or exc.reason})
            continue
        except Exception as exc:  # pragma: no cover - CLI fallback
            print_block(title, {"error": str(exc)})
            continue
        print_block(title, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
