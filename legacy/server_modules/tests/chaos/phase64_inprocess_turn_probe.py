#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import sys
import threading
import time
import tracemalloc
import types
import uuid
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server_modules import runtime_runs_api


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method: str, path: str, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def get(self, path: str, **kwargs):
        return self._register("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self._register("POST", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self._register("DELETE", path, **kwargs)


class _FakeRequest:
    def __init__(self, payload=None, *, headers=None) -> None:
        self._payload = payload or {}
        self.headers = headers or {}

    async def json(self):
        return self._payload


def _current_user():
    return {
        "user_id": "service",
        "email": None,
        "auth_type": "api_key",
        "role": "owner",
        "is_admin": True,
        "workspace_ids": ["default"],
        "workspace_access": {
            "default": {
                "workspace_id": "default",
                "tenant_id": "default",
                "role": "owner",
                "tenant_role": "owner",
                "capabilities": {"allow": [], "deny": []},
                "tenant_capabilities": {"allow": [], "deny": []},
                "workspace_capabilities": {"allow": [], "deny": []},
                "dangerous_action_classes": {"allow": [], "deny": []},
                "tenant_dangerous_action_classes": {"allow": [], "deny": []},
                "workspace_dangerous_action_classes": {"allow": [], "deny": []},
                "connectors": {"allow": [], "deny": []},
                "tenant_connectors": {"allow": [], "deny": []},
                "workspace_connectors": {"allow": [], "deny": []},
                "machine_enrollment_scope": "workspace",
                "trusted_owner_machine_ids": [],
            }
        },
    }


async def _fake_agent_turn(*, turn_request, **kwargs):
    await asyncio.sleep(0.02)
    run_id = str(uuid.uuid4())
    return {
        "ok": True,
        "status": "accepted",
        "reply": "",
        "run_id": run_id,
        "thread_id": turn_request.thread_id,
        "session_id": turn_request.session_id,
        "artifacts": [],
        "approvals": [],
        "interventions": [],
        "metadata": {"kind": "durable_run"},
    }


def _install_fake_runtime():
    fake_server = types.ModuleType("server")
    fake_server.require_api_key = object()
    fake_server.require_admin_api_key = object()
    fake_server.ORION_SINGLE_AGENT_MODE = False
    fake_server.runs = {}
    fake_server.iter_logs_for_run = lambda run_id: []
    fake_server._get_replay_payload = lambda run_id: {}
    return fake_server


def _route_under_test():
    fake_server = _install_fake_runtime()
    previous_server = __import__("sys").modules.get("server")
    __import__("sys").modules["server"] = fake_server
    original_register = runtime_runs_api.runtime_route_registration_service.register_runtime_run_routes_from_api
    original_refresh = runtime_runs_api._refresh_server_exports
    original_turn = runtime_runs_api.execute_canonical_agent_turn
    original_run_services = runtime_runs_api._run_execution_services
    original_direct_chat_services = runtime_runs_api._direct_chat_execution_services
    original_stream_response = runtime_runs_api.build_agent_turn_stream_response
    original_stream_services = runtime_runs_api._direct_chat_stream_response_services
    try:
        runtime_runs_api.runtime_route_registration_service.register_runtime_run_routes_from_api = lambda *args, **kwargs: None
        runtime_runs_api._refresh_server_exports = lambda: fake_server
        runtime_runs_api.execute_canonical_agent_turn = _fake_agent_turn
        runtime_runs_api._run_execution_services = lambda: "run-services"
        runtime_runs_api._direct_chat_execution_services = lambda: "chat-services"
        runtime_runs_api.build_agent_turn_stream_response = lambda *args, **kwargs: {"kind": "stream"}
        runtime_runs_api._direct_chat_stream_response_services = lambda: "stream-services"
        app = _FakeApp()
        runtime_runs_api.register_run_routes(app)
        return app.routes[("POST", "/turn")], previous_server, (
            original_register,
            original_refresh,
            original_turn,
            original_run_services,
            original_direct_chat_services,
            original_stream_response,
            original_stream_services,
        )
    except Exception:
        if previous_server is None:
            __import__("sys").modules.pop("server", None)
        else:
            __import__("sys").modules["server"] = previous_server
        raise


def _restore_runtime(previous_server, originals):
    (
        original_register,
        original_refresh,
        original_turn,
        original_run_services,
        original_direct_chat_services,
        original_stream_response,
        original_stream_services,
    ) = originals
    runtime_runs_api.runtime_route_registration_service.register_runtime_run_routes_from_api = original_register
    runtime_runs_api._refresh_server_exports = original_refresh
    runtime_runs_api.execute_canonical_agent_turn = original_turn
    runtime_runs_api._run_execution_services = original_run_services
    runtime_runs_api._direct_chat_execution_services = original_direct_chat_services
    runtime_runs_api.build_agent_turn_stream_response = original_stream_response
    runtime_runs_api._direct_chat_stream_response_services = original_stream_services
    if previous_server is None:
        __import__("sys").modules.pop("server", None)
    else:
        __import__("sys").modules["server"] = previous_server


async def _burst_stage(route, size: int) -> dict[str, object]:
    async def _single(idx: int):
        payload = {
            "thread_id": f"phase64-burst-{size}-{idx}",
            "workspace_id": "default",
            "session_id": f"phase64-burst-{size}-{idx}",
            "channel": "web",
            "actor": {"type": "user", "id": "service"},
            "message": f"Research burst probe {size}-{idx} and summarize it.",
            "execution_mode": "durable",
            "response_mode": "artifact",
        }
        started = time.perf_counter()
        result = await route(_FakeRequest(payload), payload, current_user=_current_user())
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return {
            "status": str(getattr(result, "status", "")),
            "run_id": str(getattr(result, "run_id", "")),
            "elapsed_ms": elapsed_ms,
        }

    results = await asyncio.gather(*[_single(idx) for idx in range(size)])
    latencies = [item["elapsed_ms"] for item in results]
    statuses = {}
    for item in results:
        statuses[item["status"]] = statuses.get(item["status"], 0) + 1
    return {
        "stage_size": size,
        "status_counts": statuses,
        "latency_ms": {
            "min": min(latencies),
            "p50": median(latencies),
            "max": max(latencies),
        },
        "accepted_runs": len([item for item in results if item["run_id"]]),
        "failures": len([item for item in results if item["status"] != "accepted"]),
    }


async def _soak(route, turns: int) -> dict[str, object]:
    tracemalloc.start()
    gc.collect()
    before_current, before_peak = tracemalloc.get_traced_memory()
    started = time.perf_counter()
    for idx in range(turns):
        payload = {
            "thread_id": f"phase64-soak-{idx}",
            "workspace_id": "default",
            "session_id": f"phase64-soak-{idx}",
            "channel": "web",
            "actor": {"type": "user", "id": "service"},
            "message": f"Research soak probe {idx} and summarize it.",
            "execution_mode": "durable",
            "response_mode": "artifact",
        }
        result = await route(_FakeRequest(payload), payload, current_user=_current_user())
        if str(getattr(result, "status", "")) != "accepted":
            raise RuntimeError(f"unexpected soak status: {getattr(result, 'status', None)}")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    gc.collect()
    after_current, after_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "turns": turns,
        "elapsed_ms": elapsed_ms,
        "before_current_bytes": before_current,
        "before_peak_bytes": before_peak,
        "after_current_bytes": after_current,
        "after_peak_bytes": after_peak,
        "current_delta_bytes": after_current - before_current,
        "peak_delta_bytes": after_peak - before_peak,
        "thread_count": threading.active_count(),
    }


async def _main_async(stages: list[int], soak_turns: int) -> dict[str, object]:
    route, previous_server, originals = _route_under_test()
    try:
        burst = []
        for stage in stages:
            burst.append(await _burst_stage(route, stage))
        soak = await _soak(route, soak_turns)
        return {"burst": burst, "soak": soak}
    finally:
        _restore_runtime(previous_server, originals)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 64 in-process durable turn chaos probe.")
    parser.add_argument("--stages", default="25,50,100,250")
    parser.add_argument("--soak-turns", type=int, default=1000)
    args = parser.parse_args()
    stages = [max(1, int(item.strip())) for item in str(args.stages).split(",") if item.strip()]
    payload = asyncio.run(_main_async(stages, max(1, int(args.soak_turns))))
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
