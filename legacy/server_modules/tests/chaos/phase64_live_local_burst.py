#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _request_json(method: str, url: str, *, headers: dict[str, str], payload: dict | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    data = None
    request_headers = dict(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp is not None else ""
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"raw": body}
        return exc.code, parsed


def _burst_once(base_url: str, api_key: str, workspace_id: str, stage: int, idx: int) -> dict[str, object]:
    started = time.perf_counter()
    status, payload = _request_json(
        "POST",
        f"{base_url.rstrip('/')}/files/write",
        headers={"X-API-Key": api_key},
        payload={
            "workspace_id": workspace_id,
            "path": f"tmp/phase64-chaos/stage-{stage}/file-{idx}.txt",
            "content": f"phase64 burst stage={stage} idx={idx}",
            "overwrite": True,
        },
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "status": status,
        "elapsed_ms": elapsed_ms,
        "run_id": str(payload.get("run_id") or ""),
        "detail": payload.get("detail"),
    }


def _queue_snapshot(base_url: str, api_key: str, workspace_id: str) -> dict:
    query = urllib.parse.urlencode({"workspace_id": workspace_id, "limit": 200})
    _status, payload = _request_json(
        "GET",
        f"{base_url.rstrip('/')}/runs/queue/local?{query}",
        headers={"X-API-Key": api_key},
    )
    return payload


def _health_snapshot(base_url: str, api_key: str) -> dict:
    _status, payload = _request_json(
        "GET",
        f"{base_url.rstrip('/')}/health",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return payload


def _run_stage(base_url: str, api_key: str, workspace_id: str, stage: int, workers: int) -> dict[str, object]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(stage, workers)) as executor:
        results = list(executor.map(lambda idx: _burst_once(base_url, api_key, workspace_id, stage, idx), range(stage)))
    latencies = [float(item["elapsed_ms"]) for item in results]
    status_counts: dict[str, int] = {}
    for item in results:
        status_counts[str(item["status"])] = status_counts.get(str(item["status"]), 0) + 1
    queue_payload = _queue_snapshot(base_url, api_key, workspace_id)
    health_payload = _health_snapshot(base_url, api_key)
    local_queue = ((health_payload.get("scale_safety_baseline") or {}).get("local_queue") or {})
    return {
        "stage_size": stage,
        "status_counts": status_counts,
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "p50": median(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "accepted_runs": len([item for item in results if item.get("run_id")]),
        "error_details": [item.get("detail") for item in results if int(item["status"]) >= 400][:5],
        "queue": {
            "queued": len(queue_payload.get("queued") or []),
            "claimed": len(queue_payload.get("claimed") or []),
            "pressure": queue_payload.get("pressure"),
        },
        "health_local_queue": {
            "state": local_queue.get("state"),
            "queued_count": local_queue.get("queued_count"),
            "online_workers": local_queue.get("online_workers"),
            "retry_after_seconds": local_queue.get("retry_after_seconds"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 64 live local queue burst probe.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--workspace-id", default="phase64-burst")
    parser.add_argument("--stages", default="25,50")
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()

    stages = [max(1, int(item.strip())) for item in str(args.stages).split(",") if item.strip()]
    results = [
        _run_stage(
            base_url=args.base_url,
            api_key=args.api_key,
            workspace_id=args.workspace_id,
            stage=stage,
            workers=max(1, int(args.workers)),
        )
        for stage in stages
    ]
    print(json.dumps({"workspace_id": args.workspace_id, "stages": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
