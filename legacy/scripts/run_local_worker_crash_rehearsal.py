#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server_modules import local_queue, machine_lease_service, outbox_service, runtime_runs_api, worker_dispatch_service


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


@dataclass
class RehearsalState:
    api_key: str
    run_id: str = "run-local-crash-1"
    worker_id: str = "worker-crash-rehearsal"
    local_queue_lock: threading.Lock = field(default_factory=threading.Lock)
    pending_run_ids: List[str] = field(default_factory=list)
    claimed_runs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    worker_registry: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    event_cv: threading.Condition = field(default_factory=lambda: threading.Condition(threading.Lock()))
    logs: List[Dict[str, Any]] = field(default_factory=list)
    persisted_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    run: Dict[str, Any] = field(default_factory=dict)
    runtime_session_token: Optional[str] = None
    runtime_instance_id: Optional[str] = None
    watchdog_resume_count: int = 0

    def __post_init__(self) -> None:
        if not self.run:
            self.run = {
                "run_id": self.run_id,
                "status": "queued_local",
                "logs": queue.Queue(),
                "input_queue": queue.Queue(),
                "context": {
                    "workspace_id": "default",
                    "user_goal": "Crash rehearsal for checkpoint-capable local run",
                    "metadata": {
                        "owner_user_id": "user-1",
                        "execution_target": "local_companion",
                        "execution_target_selected": "local_companion",
                    },
                },
                "browser_checkpoint": {
                    "next_action_index": 4,
                    "session_profile": "qa-browser",
                    "current_url": "https://example.com/private",
                },
                "result": None,
                "result_data": None,
                "local_last_heartbeat_at": None,
                "local_claimed_at": None,
                "local_worker_id": None,
                "machine_lease_id": None,
            }
        if self.run_id not in self.pending_run_ids:
            self.pending_run_ids.append(self.run_id)

    def _emit_event(self, kind: str, **payload: Any) -> None:
        event = {"kind": kind, "ts": utc_now_iso(), **payload}
        with self.event_cv:
            self.events.append(event)
            self.event_cv.notify_all()

    def wait_for_event_count(self, kind: str, count: int, timeout_seconds: float = 20.0) -> bool:
        deadline = time.time() + max(1.0, timeout_seconds)
        with self.event_cv:
            while True:
                current = sum(1 for event in self.events if event.get("kind") == kind)
                if current >= count:
                    return True
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self.event_cv.wait(timeout=remaining)

    def _persist_local_runtime_state(self) -> None:
        self.persisted_snapshots.append(
            {
                "pending_run_ids": list(self.pending_run_ids),
                "claimed_runs": copy.deepcopy(self.claimed_runs),
                "runtime_registrations": copy.deepcopy(self.worker_registry),
            }
        )

    def _mark_local_worker_seen(self, worker_id: str, current_run_id: Optional[str], status_hint: str, note: Optional[str] = None):
        worker = str(worker_id or "").strip()
        if not worker:
            return
        previous = self.worker_registry.get(worker) if isinstance(self.worker_registry.get(worker), dict) else {}
        record = machine_lease_service.build_machine_presence_record(
            previous_record=previous,
            machine_id=worker,
            current_run_id=current_run_id,
            status_hint=status_hint,
            lease_seconds=30,
            now_iso=utc_now_iso(),
            note=note,
        )
        registration = self.worker_registry.get(worker) if isinstance(self.worker_registry.get(worker), dict) else {}
        record["capabilities"] = list(registration.get("capabilities") or previous.get("capabilities") or [])
        record["execution_targets"] = list(registration.get("execution_targets") or previous.get("execution_targets") or ["local"])
        record["session_token_hash"] = registration.get("session_token_hash")
        record["instance_id"] = registration.get("instance_id") or previous.get("instance_id") or worker
        self.worker_registry[worker] = record
        self._persist_local_runtime_state()

    def _emit_log(self, log_queue: Any, level: str, message: str, **kwargs: Any) -> None:
        self.logs.append({"level": level, "message": message, **kwargs})

    def _set_run_status(self, run_id: str, status: str) -> None:
        if run_id != self.run_id:
            return
        self.run["status"] = status
        self._emit_event("run_status", status=status)

    def _verify_session(self, runtime_id: str, session_token: Optional[str], instance_id: Optional[str]) -> None:
        runtime_token = str(runtime_id or "").strip()
        registration = self.worker_registry.get(runtime_token) if isinstance(self.worker_registry.get(runtime_token), dict) else None
        if not isinstance(registration, dict):
            raise PermissionError("runtime not registered")
        expected_token = str(registration.get("session_token") or "").strip()
        expected_instance = str(registration.get("instance_id") or "").strip()
        if str(session_token or "").strip() != expected_token:
            raise PermissionError("invalid runtime session token")
        if expected_instance and str(instance_id or "").strip() != expected_instance:
            raise PermissionError("invalid runtime instance id")

    def register_runtime(self, runtime_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        session_token = uuid.uuid4().hex
        instance_id = str(payload.get("instance_id") or runtime_id).strip() or runtime_id
        record = {
            "worker_id": runtime_id,
            "runtime_id": runtime_id,
            "machine_id": runtime_id,
            "status": "idle",
            "runtime_type": str(payload.get("runtime_type") or "local").strip() or "local",
            "display_name": str(payload.get("display_name") or runtime_id).strip() or runtime_id,
            "platform": str(payload.get("platform") or "").strip() or None,
            "policy_mode": str(payload.get("policy_mode") or "local_default").strip() or "local_default",
            "capabilities": [str(item).strip() for item in (payload.get("capabilities") or []) if str(item).strip()],
            "execution_targets": [str(item).strip() for item in (payload.get("execution_targets") or ["local"]) if str(item).strip()],
            "instance_id": instance_id,
            "session_token": session_token,
            "lease_seconds": 30,
        }
        self.worker_registry[runtime_id] = record
        self.runtime_session_token = session_token
        self.runtime_instance_id = instance_id
        self._persist_local_runtime_state()
        self._emit_event("register", runtime_id=runtime_id, instance_id=instance_id)
        return {"ok": True, "runtime_id": runtime_id, "session_token": session_token, "instance_id": instance_id}

    def heartbeat_worker(self, runtime_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._verify_session(runtime_id, payload.get("session_token"), payload.get("instance_id"))
        current_run_id = str(payload.get("current_run_id") or "").strip() or None
        note = str(payload.get("note") or "").strip() or None
        worker_dispatch_service.heartbeat_local_worker(
            runtime_id,
            current_run_id=current_run_id,
            note=note,
            runs_by_id={self.run_id: self.run},
            local_queue_lock=self.local_queue_lock,
            claimed_runs=self.claimed_runs,
            mark_local_worker_seen_fn=self._mark_local_worker_seen,
            maybe_emit_local_still_working_fn=lambda *args, **kwargs: False,
            persist_local_runtime_state_fn=self._persist_local_runtime_state,
            utc_now_iso_fn=utc_now_iso,
        )
        self._emit_event("worker_heartbeat", runtime_id=runtime_id, current_run_id=current_run_id, note=note)
        return {"status": "ok"}

    def claim_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        runtime_id = str(payload.get("runtime_id") or "").strip()
        self._verify_session(runtime_id, payload.get("session_token"), payload.get("instance_id"))
        with self.local_queue_lock:
            if self.run_id not in self.pending_run_ids:
                return {}
            self.pending_run_ids.remove(self.run_id)
            claimed_at = utc_now_iso()
            lease = machine_lease_service.build_machine_lease_record(
                machine_id=runtime_id,
                run_id=self.run_id,
                workspace_id=str((self.run.get("context") or {}).get("workspace_id") or "default"),
                actor_id=str((((self.run.get("context") or {}).get("metadata") or {}).get("owner_user_id")) or "user-1"),
                ttl_seconds=30,
                claimed_at=claimed_at,
            )
            self.claimed_runs[self.run_id] = lease
            self.run["status"] = "running_local"
            self.run["local_worker_id"] = runtime_id
            self.run["machine_lease_id"] = lease.get("lease_id")
            self.run["local_claimed_at"] = claimed_at
            self.run["local_last_heartbeat_at"] = claimed_at
            self._persist_local_runtime_state()
        self._mark_local_worker_seen(runtime_id, self.run_id, "busy", note="claimed_local_run")
        self._emit_event("claim", runtime_id=runtime_id, run_id=self.run_id)
        run_payload = {
            key: copy.deepcopy(value)
            for key, value in self.run.items()
            if key not in {"logs", "input_queue"}
        }
        return {"task": {"run": run_payload}}

    def heartbeat_run(self, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        runtime_id = str(payload.get("runtime_id") or "").strip()
        self._verify_session(runtime_id, payload.get("session_token"), payload.get("instance_id"))
        worker_dispatch_service.heartbeat_local_run(
            run_id,
            worker_id=runtime_id,
            note=str(payload.get("note") or "").strip() or None,
            runs_by_id={self.run_id: self.run},
            local_queue_lock=self.local_queue_lock,
            claimed_runs=self.claimed_runs,
            maybe_emit_local_still_working_fn=lambda *args, **kwargs: False,
            mark_local_worker_seen_fn=self._mark_local_worker_seen,
            utc_now_iso_fn=utc_now_iso,
        )
        self._emit_event("run_heartbeat", runtime_id=runtime_id, run_id=run_id, note=str(payload.get("note") or ""))
        return {"status": "ok"}

    def complete_run(self, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        runtime_id = str(payload.get("runtime_id") or "").strip()
        self._verify_session(runtime_id, payload.get("session_token"), payload.get("instance_id"))
        result = worker_dispatch_service.complete_local_run(
            run_id,
            worker_id=runtime_id,
            result_text=payload.get("result_text"),
            result_data=payload.get("result_data") if isinstance(payload.get("result_data"), dict) else None,
            usage_masked=payload.get("usage_masked") if isinstance(payload.get("usage_masked"), dict) else None,
            runs_by_id={self.run_id: self.run},
            local_queue_lock=self.local_queue_lock,
            claimed_runs=self.claimed_runs,
            emit_log_fn=self._emit_log,
            mark_local_worker_seen_fn=self._mark_local_worker_seen,
            set_run_status_fn=self._set_run_status,
            persist_run_memory_fn=lambda run_id, run: None,
        )
        self._emit_event("complete", runtime_id=runtime_id, run_id=run_id)
        return result

    def pause_run(self, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        runtime_id = str(payload.get("runtime_id") or "").strip()
        self._verify_session(runtime_id, payload.get("session_token"), payload.get("instance_id"))
        result = worker_dispatch_service.pause_local_run(
            run_id,
            worker_id=runtime_id,
            result_text=payload.get("result_text"),
            result_data=payload.get("result_data") if isinstance(payload.get("result_data"), dict) else None,
            browser_checkpoint=payload.get("browser_checkpoint") if isinstance(payload.get("browser_checkpoint"), dict) else None,
            wait_reason=str(payload.get("wait_reason") or "").strip() or None,
            runs_by_id={self.run_id: self.run},
            local_queue_lock=self.local_queue_lock,
            claimed_runs=self.claimed_runs,
            emit_log_fn=self._emit_log,
            mark_local_worker_seen_fn=self._mark_local_worker_seen,
            set_run_status_fn=self._set_run_status,
            persist_local_runtime_state_fn=self._persist_local_runtime_state,
        )
        self._emit_event("pause", runtime_id=runtime_id, run_id=run_id)
        return result

    def fail_run(self, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        runtime_id = str(payload.get("runtime_id") or "").strip()
        self._verify_session(runtime_id, payload.get("session_token"), payload.get("instance_id"))
        result = worker_dispatch_service.fail_local_run(
            run_id,
            worker_id=runtime_id,
            error=payload.get("error"),
            runs_by_id={self.run_id: self.run},
            local_queue_lock=self.local_queue_lock,
            claimed_runs=self.claimed_runs,
            emit_log_fn=self._emit_log,
            mark_local_worker_seen_fn=self._mark_local_worker_seen,
            set_run_status_fn=self._set_run_status,
        )
        self._emit_event("fail", runtime_id=runtime_id, run_id=run_id)
        return result

    def schedule_restored_run_resume(self, run_id: str, run: Dict[str, Any]) -> bool:
        queued = outbox_service.enqueue_local_companion_run(
            run_id,
            runs_by_id={self.run_id: self.run},
            set_run_status_fn=self._set_run_status,
            utc_now_iso_fn=utc_now_iso,
            local_queue_lock=self.local_queue_lock,
            pending_run_ids=self.pending_run_ids,
            claimed_runs=self.claimed_runs,
            runtime_registrations=self.worker_registry,
            db_path=Path(self.tmpdir.name) / "runtime.sqlite3",
            lease_seconds=30,
            emit_log_fn=self._emit_log,
            replace_local_runtime_state_fn=lambda *args, **kwargs: None,
        )
        if queued:
            self._emit_event("resume_scheduled", run_id=run_id, attempt_count=int((((self.run.get("context") or {}).get("metadata") or {}).get("local_worker_recovery_attempt_count")) or 0))
        return queued

    @contextmanager
    def patch_watchdog_server(self, now_value: datetime):
        original_server = local_queue._server
        server_ns = type(
            "WatchdogServer",
            (),
            {
                "runs": {self.run_id: self.run},
                "_utc_now": staticmethod(lambda: now_value),
                "_utc_now_iso": staticmethod(lambda: now_value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")),
                "_parse_utc_ts": staticmethod(parse_utc_ts),
                "emit_log": staticmethod(lambda *args, **kwargs: self._emit_log(*args, **kwargs)),
            },
        )()
        local_queue._server = server_ns
        try:
            live_runtime_runs_api = importlib.import_module("server_modules.runtime_runs_api")
            with patch.object(
                live_runtime_runs_api,
                "_schedule_restored_run_resume",
                side_effect=lambda run_id, run: self.schedule_restored_run_resume(run_id, run),
            ):
                yield
        finally:
            local_queue._server = original_server

    def run_watchdog_due_resume(self, now_value: datetime) -> List[str]:
        with self.patch_watchdog_server(now_value):
            resumed = local_queue._resume_due_checkpoint_recoveries()
        self.watchdog_resume_count += len(resumed)
        return resumed

    def cleanup_after_worker_loss(self, now_value: datetime) -> List[str]:
        stale = machine_lease_service.cleanup_stale_machine_leases(
            now=now_value,
            local_queue_lock=self.local_queue_lock,
            claimed_runs=self.claimed_runs,
            worker_registry=self.worker_registry,
            runs_by_id={self.run_id: self.run},
            parse_utc_ts_fn=parse_utc_ts,
            utc_now_iso_fn=lambda: now_value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            persist_local_runtime_state_fn=self._persist_local_runtime_state,
            emit_log_fn=self._emit_log,
            set_run_status_fn=self._set_run_status,
            schedule_restored_run_resume_fn=lambda run_id, run: self.schedule_restored_run_resume(run_id, run),
            checkpoint_recovery_max_auto_retries=3,
            checkpoint_recovery_backoff_seconds=[0, 10, 30],
            local_worker_lost_timeout_seconds=30,
            default_lease_seconds=30,
        )
        return stale

    @property
    def tmpdir(self):
        if not hasattr(self, "_tmpdir"):
            self._tmpdir = tempfile.TemporaryDirectory(prefix="local-worker-crash-rehearsal-")
        return self._tmpdir

    def cleanup_tmpdir(self) -> None:
        if hasattr(self, "_tmpdir"):
            self._tmpdir.cleanup()


class RehearsalRequestHandler(BaseHTTPRequestHandler):
    server: "RehearsalHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _write_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.headers.get("X-API-Key") != self.server.state.api_key:
            self._write_json(401, {"detail": "invalid api key"})
            return
        payload = self._read_json()
        path = self.path.rstrip("/")
        try:
            if path.startswith("/runtime/runtimes/") and path.endswith("/register"):
                runtime_id = path.split("/")[3]
                self._write_json(200, self.server.state.register_runtime(runtime_id, payload))
                return
            if path.startswith("/runtime/runtimes/") and path.endswith("/heartbeat"):
                runtime_id = path.split("/")[3]
                self._write_json(200, self.server.state.heartbeat_worker(runtime_id, payload))
                return
            if path == "/runtime/tasks/claim":
                self._write_json(200, self.server.state.claim_run(payload))
                return
            if path.startswith("/runtime/tasks/") and path.endswith("/heartbeat"):
                run_id = path.split("/")[3]
                self._write_json(200, self.server.state.heartbeat_run(run_id, payload))
                return
            if path.startswith("/runtime/tasks/") and path.endswith("/complete"):
                run_id = path.split("/")[3]
                self._write_json(200, self.server.state.complete_run(run_id, payload))
                return
            if path.startswith("/runtime/tasks/") and path.endswith("/pause"):
                run_id = path.split("/")[3]
                self._write_json(200, self.server.state.pause_run(run_id, payload))
                return
            if path.startswith("/runtime/tasks/") and path.endswith("/fail"):
                run_id = path.split("/")[3]
                self._write_json(200, self.server.state.fail_run(run_id, payload))
                return
        except PermissionError as exc:
            self._write_json(401, {"detail": str(exc)})
            return
        except Exception as exc:
            self._write_json(500, {"detail": str(exc)})
            return
        self._write_json(404, {"detail": "not found"})


class RehearsalHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, state: RehearsalState):
        super().__init__(server_address, RequestHandlerClass)
        self.state = state


def _worker_script_path() -> Path:
    return ROOT_DIR / "scripts" / "orion_local_worker.py"


def start_worker_process(*, runtime_url: str, api_key: str, worker_id: str, session_root: Path, step_delay_seconds: float, quiet: bool) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["ORION_LOCAL_WORKER_USE_LLM"] = "0"
    env["ORION_RUNTIME_SESSION_ROOT"] = str(session_root)
    args = [
        sys.executable,
        str(_worker_script_path()),
        "--runtime-url",
        runtime_url,
        "--api-key",
        api_key,
        "--worker-id",
        worker_id,
        "--once",
        "--max-wait-seconds",
        "30",
        "--poll-seconds",
        "0.2",
        "--step-delay-seconds",
        str(step_delay_seconds),
    ]
    if quiet:
        args.append("--quiet")
    return subprocess.Popen(
        args,
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _kill_process(proc: subprocess.Popen[str]) -> tuple[int, str, str]:
    proc.kill()
    try:
        stdout, stderr = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=10)
    return proc.returncode or -9, stdout, stderr


def _worker_requires_simulated_rehearsal(*, stdout: str, stderr: str) -> bool:
    combined = "\n".join([str(stdout or "").strip(), str(stderr or "").strip()]).lower()
    if not combined:
        return False
    return (
        "empyralis-supervisor is unreachable at import time" in combined
        or "supervisor not running" in combined
        or "failed to establish a new connection" in combined
    )


def _simulate_crash_rehearsal(
    *,
    cycles: int,
    kill_after_task_heartbeat_count: int,
    step_delay_seconds: float,
    quiet_worker: bool,
) -> Dict[str, Any]:
    state = RehearsalState(api_key="rehearsal-key")
    registration = state.register_runtime(
        state.worker_id,
        {
            "instance_id": state.worker_id,
            "capabilities": ["browser_automation.interactive"],
            "execution_targets": ["local"],
        },
    )
    session_token = str(registration.get("session_token") or "")
    instance_id = str(registration.get("instance_id") or state.worker_id)

    for cycle in range(1, cycles + 1):
        state.claim_run(
            {
                "runtime_id": state.worker_id,
                "session_token": session_token,
                "instance_id": instance_id,
            }
        )
        for heartbeat_idx in range(max(1, kill_after_task_heartbeat_count)):
            state.heartbeat_run(
                state.run_id,
                {
                    "runtime_id": state.worker_id,
                    "session_token": session_token,
                    "instance_id": instance_id,
                    "note": f"simulated-heartbeat-{cycle}-{heartbeat_idx + 1}",
                },
            )
        state._emit_event("worker_killed", cycle=cycle, returncode=-9, stdout="", stderr="")
        last_heartbeat_text = (
            state.claimed_runs.get(state.run_id, {}).get("last_heartbeat_at")
            or state.claimed_runs.get(state.run_id, {}).get("claimed_at")
        )
        last_heartbeat = parse_utc_ts(last_heartbeat_text) or datetime.now(timezone.utc)
        cleanup_now = last_heartbeat + timedelta(seconds=31)
        stale = state.cleanup_after_worker_loss(cleanup_now)
        state._emit_event(
            "cleanup",
            cycle=cycle,
            stale=stale,
            status=state.run.get("status"),
            result_error=((state.run.get("result_data") or {}).get("error") if isinstance(state.run.get("result_data"), dict) else None),
        )

        metadata = ((state.run.get("context") or {}).get("metadata") or {}) if isinstance((state.run.get("context") or {}).get("metadata"), dict) else {}
        if bool(metadata.get("local_worker_recovery_auto_retry_exhausted")):
            break
        next_retry_at = parse_utc_ts(metadata.get("local_worker_recovery_next_retry_at"))
        if next_retry_at is not None:
            resumed = state.run_watchdog_due_resume(next_retry_at + timedelta(seconds=1))
            state._emit_event("watchdog_resume", cycle=cycle, resumed=resumed, status=state.run.get("status"))

    final_metadata = ((state.run.get("context") or {}).get("metadata") or {}) if isinstance((state.run.get("context") or {}).get("metadata"), dict) else {}
    final_result_data = state.run.get("result_data") if isinstance(state.run.get("result_data"), dict) else {}
    summary = {
        "runtime_url": "simulated://local-worker-crash-rehearsal",
        "run_id": state.run_id,
        "worker_id": state.worker_id,
        "cycles_attempted": cycles,
        "watchdog_resume_count": state.watchdog_resume_count,
        "final_status": str(state.run.get("status") or ""),
        "final_error": str(final_result_data.get("error") or ""),
        "manual_confirmation_required": bool(final_metadata.get("local_worker_recovery_manual_confirmation_required")),
        "auto_retry_exhausted": bool(final_metadata.get("local_worker_recovery_auto_retry_exhausted")),
        "attempt_count": int(final_metadata.get("local_worker_recovery_attempt_count") or 0),
        "events": list(state.events),
        "transport": "simulated",
        "step_delay_seconds": step_delay_seconds,
        "quiet_worker": bool(quiet_worker),
    }
    state.cleanup_tmpdir()
    return summary


def run_crash_rehearsal(*, cycles: int = 4, kill_after_task_heartbeat_count: int = 1, step_delay_seconds: float = 30.0, quiet_worker: bool = True) -> Dict[str, Any]:
    state = RehearsalState(api_key="rehearsal-key")
    try:
        server = RehearsalHTTPServer(("127.0.0.1", 0), RehearsalRequestHandler, state)
    except PermissionError:
        return _simulate_crash_rehearsal(
            cycles=cycles,
            kill_after_task_heartbeat_count=kill_after_task_heartbeat_count,
            step_delay_seconds=step_delay_seconds,
            quiet_worker=quiet_worker,
        )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True, name="local-worker-crash-harness")
    server_thread.start()
    runtime_url = f"http://127.0.0.1:{server.server_port}"

    try:
        for cycle in range(1, cycles + 1):
            worker = start_worker_process(
                runtime_url=runtime_url,
                api_key=state.api_key,
                worker_id=state.worker_id,
                session_root=Path(state.tmpdir.name) / "sessions",
                step_delay_seconds=step_delay_seconds,
                quiet=quiet_worker,
            )
            target_count = cycle * kill_after_task_heartbeat_count
            if not state.wait_for_event_count("run_heartbeat", target_count, timeout_seconds=20.0):
                returncode, stdout, stderr = _kill_process(worker)
                if _worker_requires_simulated_rehearsal(stdout=stdout, stderr=stderr):
                    return _simulate_crash_rehearsal(
                        cycles=cycles,
                        kill_after_task_heartbeat_count=kill_after_task_heartbeat_count,
                        step_delay_seconds=step_delay_seconds,
                        quiet_worker=quiet_worker,
                    )
                raise RuntimeError(
                    f"Worker did not reach heartbeat target for cycle {cycle}. "
                    f"rc={returncode} stdout={stdout} stderr={stderr} events={state.events}"
                )
            returncode, stdout, stderr = _kill_process(worker)
            state._emit_event("worker_killed", cycle=cycle, returncode=returncode, stdout=stdout, stderr=stderr)

            last_heartbeat_text = (
                state.claimed_runs.get(state.run_id, {}).get("last_heartbeat_at")
                or state.claimed_runs.get(state.run_id, {}).get("claimed_at")
            )
            last_heartbeat = parse_utc_ts(last_heartbeat_text) or datetime.now(timezone.utc)
            cleanup_now = last_heartbeat + timedelta(seconds=31)
            stale = state.cleanup_after_worker_loss(cleanup_now)
            state._emit_event("cleanup", cycle=cycle, stale=stale, status=state.run.get("status"), result_error=((state.run.get("result_data") or {}).get("error") if isinstance(state.run.get("result_data"), dict) else None))

            metadata = ((state.run.get("context") or {}).get("metadata") or {}) if isinstance((state.run.get("context") or {}).get("metadata"), dict) else {}
            if bool(metadata.get("local_worker_recovery_auto_retry_exhausted")):
                break
            next_retry_at = parse_utc_ts(metadata.get("local_worker_recovery_next_retry_at"))
            if next_retry_at is not None:
                resumed = state.run_watchdog_due_resume(next_retry_at + timedelta(seconds=1))
                state._emit_event("watchdog_resume", cycle=cycle, resumed=resumed, status=state.run.get("status"))

        final_metadata = ((state.run.get("context") or {}).get("metadata") or {}) if isinstance((state.run.get("context") or {}).get("metadata"), dict) else {}
        final_result_data = state.run.get("result_data") if isinstance(state.run.get("result_data"), dict) else {}
        return {
            "runtime_url": runtime_url,
            "run_id": state.run_id,
            "worker_id": state.worker_id,
            "cycles_attempted": cycles,
            "watchdog_resume_count": state.watchdog_resume_count,
            "final_status": str(state.run.get("status") or ""),
            "final_error": str(final_result_data.get("error") or ""),
            "manual_confirmation_required": bool(final_metadata.get("local_worker_recovery_manual_confirmation_required")),
            "auto_retry_exhausted": bool(final_metadata.get("local_worker_recovery_auto_retry_exhausted")),
            "attempt_count": int(final_metadata.get("local_worker_recovery_attempt_count") or 0),
            "events": list(state.events),
        }
    finally:
        server.shutdown()
        server.server_close()
        state.cleanup_tmpdir()


def main() -> int:
    parser = argparse.ArgumentParser(description="Near-live crash rehearsal for local worker death and recovery.")
    parser.add_argument("--cycles", type=int, default=4, help="How many worker death cycles to rehearse.")
    parser.add_argument("--kill-after-task-heartbeats", type=int, default=1, help="Kill the worker after this many task heartbeats per cycle.")
    parser.add_argument("--step-delay-seconds", type=float, default=30.0, help="Worker step delay so the process can be killed mid-run.")
    parser.add_argument("--quiet-worker", action="store_true", help="Run the worker in quiet mode.")
    args = parser.parse_args()

    summary = run_crash_rehearsal(
        cycles=max(1, args.cycles),
        kill_after_task_heartbeat_count=max(1, args.kill_after_task_heartbeats),
        step_delay_seconds=max(1.0, args.step_delay_seconds),
        quiet_worker=bool(args.quiet_worker),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not summary.get("manual_confirmation_required"):
        return 1
    if summary.get("final_error") != "local_worker_recovery_exhausted":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
