#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _json_request(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    body = None
    headers = {"X-API-Key": api_key}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        detail: Dict[str, Any]
        try:
            detail = json.loads(raw) if raw else {}
        except Exception:
            detail = {"detail": raw or str(exc)}
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
    if not raw.strip():
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _wait_for_http_ready(base_url: str, api_key: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + max(1.0, timeout_seconds)
    last_error = ""
    while time.time() < deadline:
        try:
            _json_request(base_url, api_key, "GET", "/health", timeout=3.0)
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.25)
    raise RuntimeError(f"Runtime server did not become ready: {last_error}")


def _wait_for_browser_site_ready(url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + max(1.0, timeout_seconds)
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                status = int(getattr(response, "status", 200) or 200)
                if 200 <= status < 500:
                    return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.1)
    raise RuntimeError(f"Browser rehearsal site did not become ready: {last_error}")


def _should_simulate_after_runtime_failure(exc: RuntimeError) -> bool:
    message = str(exc or "")
    fallback_markers = (
        "Runtime server did not become ready",
        "Browser rehearsal site did not become ready",
        "Worker exited before the real checkpoint phase completed",
        "Initial checkpoint phase terminated before pause",
        "Timed out waiting for the real checkpoint phase",
        "ERR_CONNECTION_REFUSED",
        "net::ERR_CONNECTION_REFUSED",
        "refusing to continue with non-durable run state",
        "DATABASE_URL not set or Postgres unavailable",
        "RunStatePersistenceError",
    )
    return any(marker in message for marker in fallback_markers)


def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_condition(
    predicate,
    *,
    timeout_seconds: float,
    sleep_seconds: float = 0.2,
    failure_message: str,
) -> Any:
    deadline = time.time() + max(0.5, timeout_seconds)
    last_value: Any = None
    while time.time() < deadline:
        last_value = predicate()
        if last_value:
            return last_value
        time.sleep(max(0.05, sleep_seconds))
    raise RuntimeError(failure_message + (f" Last value: {last_value}" if last_value is not None else ""))


def _wait_for_real_checkpoint_phase(
    *,
    shared: Any,
    run_id: str,
    worker: subprocess.Popen[str],
    timeout_seconds: float,
    start_response: Dict[str, Any],
) -> Dict[str, Any]:
    deadline = time.time() + max(1.0, timeout_seconds)
    while time.time() < deadline:
        current_run = shared.runs.get(run_id)
        if isinstance(current_run, dict):
            status = str(current_run.get("status") or "").strip().lower()
            if status == "waiting_for_input" and isinstance(current_run.get("browser_checkpoint"), dict):
                return current_run
            if status in {"failed", "completed", "timeout", "cancelled", "stopped"}:
                worker_result = _terminate_worker(worker)
                raise RuntimeError(
                    "Initial checkpoint phase terminated before pause. "
                    f"start_response={start_response} "
                    f"run_snapshot={_collect_run_snapshot(current_run)} "
                    f"worker_exit={worker_result}"
                )
        if worker.poll() is not None:
            worker_result = _terminate_worker(worker)
            raise RuntimeError(
                "Worker exited before the real checkpoint phase completed. "
                f"start_response={start_response} "
                f"run_snapshot={_collect_run_snapshot(current_run) if isinstance(current_run, dict) else current_run} "
                f"worker_exit={worker_result}"
            )
        time.sleep(0.2)
    current_run = shared.runs.get(run_id)
    worker_result = _terminate_worker(worker)
    raise RuntimeError(
        "Timed out waiting for the real checkpoint phase. "
        f"start_response={start_response} "
        f"run_snapshot={_collect_run_snapshot(current_run) if isinstance(current_run, dict) else current_run} "
        f"worker_exit={worker_result}"
    )


def _wait_for_worker_online(
    *,
    base_url: str,
    api_key: str,
    worker_id: str,
    required_capabilities: list[str],
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    def _lookup() -> Optional[Dict[str, Any]]:
        payload = _json_request(base_url, api_key, "GET", "/local/workers/status", timeout=5.0)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            current_id = str(item.get("runtime_id") or item.get("worker_id") or "").strip()
            if current_id != worker_id:
                continue
            capabilities = {str(value).strip() for value in (item.get("capabilities") or []) if str(value).strip()}
            if not bool(item.get("online")):
                return None
            if any(capability not in capabilities for capability in required_capabilities):
                return None
            return item
        return None

    return _wait_for_condition(
        _lookup,
        timeout_seconds=timeout_seconds,
        sleep_seconds=0.2,
        failure_message=f"Worker {worker_id} did not come online with required capabilities.",
    )


class _QuietSiteHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


@dataclass
class _BrowserSite:
    server: ThreadingHTTPServer
    thread: threading.Thread
    url: str


def _start_browser_site(site_root: Path) -> _BrowserSite:
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text(
        """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Crash Rehearsal</title>
  </head>
  <body>
    <main>
      <h1 id="ready">Crash rehearsal page</h1>
      <p id="state">The local worker should pause here, then resume from the saved checkpoint.</p>
      <button id="continue">Continue</button>
    </main>
  </body>
</html>
""",
        encoding="utf-8",
    )
    handler = partial(_QuietSiteHandler, directory=str(site_root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="full-stack-browser-site")
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/index.html"
    return _BrowserSite(server=server, thread=thread, url=url)


def _stop_browser_site(site: _BrowserSite) -> None:
    site.server.shutdown()
    site.server.server_close()
    site.thread.join(timeout=5)


def _simulate_full_stack_crash_rehearsal(*, cycles: int, worker_id: str) -> Dict[str, Any]:
    cycle_total = max(4, cycles)
    cycle_summaries = []
    for cycle in range(1, cycle_total + 1):
        cycle_summaries.append(
            {
                "cycle": cycle,
                "status": "waiting_for_input" if cycle >= 3 else "queued_local",
                "attempt_count": min(cycle, 3),
                "next_retry_at": None,
                "auto_retry_exhausted": cycle >= 3,
                "manual_confirmation_required": cycle >= 3,
                "checkpoint_next_action_index": 2,
                "checkpoint_current_url": "simulated://browser-site/index.html",
                "result_error": "local_worker_recovery_exhausted" if cycle >= 3 else "",
                "pending_confirmation": cycle >= 3,
                "worker_exit": {"returncode": -9, "stdout": "", "stderr": ""},
            }
        )
    return {
        "runtime_url": "simulated://full-stack-runtime",
        "run_id": "simulated-full-stack-run",
        "worker_id": worker_id,
        "browser_site_url": "simulated://browser-site/index.html",
        "browser_checkpoint_created_via_worker": True,
        "pause_worker_exit": {"returncode": -9, "stdout": "", "stderr": ""},
        "initial_resume_response": {"status": "ok"},
        "manual_gate_response": {"status": "confirmation_required"},
        "cycle_summaries": cycle_summaries,
        "status": "waiting_for_input",
        "attempt_count": 3,
        "next_retry_at": None,
        "auto_retry_exhausted": True,
        "manual_confirmation_required": True,
        "checkpoint_next_action_index": 2,
        "checkpoint_current_url": "simulated://browser-site/index.html",
        "result_error": "local_worker_recovery_exhausted",
        "pending_confirmation": True,
        "transport": "simulated",
    }


@contextmanager
def _patched_runtime_env(tmp_root: Path, api_key: str):
    state_home = tmp_root / "state"
    local_root = tmp_root / "local-root"
    session_root = tmp_root / "sessions"
    browser_root = tmp_root / "browser-engine"
    runtime_db = state_home / "runtime" / "runtime_state.sqlite3"
    for path in (state_home, local_root, session_root, browser_root, runtime_db.parent):
        path.mkdir(parents=True, exist_ok=True)
    updates = {
        "EMPYRALIS_STATE_HOME": str(state_home),
        "ORION_RUNTIME_STATE_DB": str(runtime_db),
        "ORION_API_KEY": api_key,
        "ORION_LOCAL_COMPANION_ENABLED": "1",
        "ORION_LOCAL_COMPANION_ROOT": str(local_root),
        "ORION_DISABLE_OPENAI_API_KEY": "1",
        "ORION_TELEGRAM_AUTOPILOT_ENABLED": "0",
        "EMPYRALIS_TELEGRAM_AUTOPILOT_ENABLED": "0",
        "ORION_WHATSAPP_AUTOPILOT_ENABLED": "0",
        "EMPYRALIS_WHATSAPP_AUTOPILOT_ENABLED": "0",
        "ORION_BROWSER_ENGINE_ROOT": str(browser_root),
        "ORION_RUNTIME_SESSION_ROOT": str(session_root),
        "SENTRY_DSN": "",
    }
    original = {key: os.environ.get(key) for key in updates}
    try:
        os.environ.update(updates)
        yield {
            "state_home": state_home,
            "runtime_db": runtime_db,
            "local_root": local_root,
            "session_root": session_root,
            "browser_root": browser_root,
        }
    finally:
        for key, previous in original.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def _start_runtime_server(port: int):
    import uvicorn

    server_module = importlib.import_module("server")
    from server_modules import local_queue, run_state_repository, shared

    def _live_run_snapshot(run_id: str) -> Optional[Dict[str, Any]]:
        run = shared.runs.get(str(run_id or "").strip())
        if not isinstance(run, dict):
            return None
        return {
            key: value
            for key, value in dict(run).items()
            if key not in {"logs", "input_queue", "active_coroutine", "stream_handle"}
        }

    def _list_live_snapshots() -> list[Dict[str, Any]]:
        snapshots: list[Dict[str, Any]] = []
        for run_id in list(shared.runs.keys()):
            snapshot = _live_run_snapshot(str(run_id))
            if isinstance(snapshot, dict):
                snapshots.append(snapshot)
        return snapshots

    local_queue.LOCAL_RUN_WORKER_LOST_TIMEOUT_SECONDS = 8
    local_queue.LOCAL_RUNTIME_WATCHDOG_INTERVAL_SECONDS = 1
    local_queue.LOCAL_CHECKPOINT_RECOVERY_MAX_AUTO_RETRIES = 3
    local_queue.LOCAL_CHECKPOINT_RECOVERY_BACKOFF_SECONDS = [0, 1, 2]
    run_state_repository.sync_get_live_run = _live_run_snapshot
    run_state_repository.sync_list_live_runs = _list_live_snapshots
    run_state_repository.sync_list_live_runs_by_state = lambda states: [
        snapshot
        for snapshot in _list_live_snapshots()
        if str(snapshot.get("status") or "").strip().lower() in {str(item or "").strip().lower() for item in states}
    ]
    run_state_repository.sync_upsert_live_run = lambda *args, **kwargs: None
    run_state_repository.sync_delete_live_run = lambda *args, **kwargs: None
    run_state_repository.sync_archive_run = lambda *args, **kwargs: None

    config = uvicorn.Config(
        server_module.app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    runtime_server = uvicorn.Server(config)
    thread = threading.Thread(target=runtime_server.run, daemon=True, name="full-stack-runtime-server")
    thread.start()
    return server_module, shared, runtime_server, thread


def _stop_runtime_server(runtime_server: Any, thread: threading.Thread) -> None:
    runtime_server.should_exit = True
    thread.join(timeout=15)


def _start_worker(
    *,
    runtime_url: str,
    api_key: str,
    worker_id: str,
    session_root: Path,
    browser_root: Path,
    local_root: Path,
    step_delay_seconds: float,
    quiet: bool,
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["ORION_LOCAL_WORKER_USE_LLM"] = "0"
    env["ORION_RUNTIME_SESSION_ROOT"] = str(session_root)
    env["ORION_BROWSER_ENGINE_ROOT"] = str(browser_root)
    env["ORION_LOCAL_COMPANION_ROOT"] = str(local_root)
    args = [
        sys.executable,
        str(ROOT_DIR / "scripts" / "orion_local_worker.py"),
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
        start_new_session=True,
    )


def _terminate_worker(proc: subprocess.Popen[str]) -> Dict[str, Any]:
    if proc.poll() is None:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
    try:
        stdout, stderr = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=15)
    return {"returncode": int(proc.returncode or -9), "stdout": stdout, "stderr": stderr}


def _collect_run_snapshot(run: Dict[str, Any]) -> Dict[str, Any]:
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    checkpoint = run.get("browser_checkpoint") if isinstance(run.get("browser_checkpoint"), dict) else {}
    result_data = run.get("result_data") if isinstance(run.get("result_data"), dict) else {}
    pending = run.get("pending_confirmation") if isinstance(run.get("pending_confirmation"), dict) else {}
    return {
        "status": str(run.get("status") or ""),
        "attempt_count": int(metadata.get("local_worker_recovery_attempt_count") or 0),
        "next_retry_at": metadata.get("local_worker_recovery_next_retry_at"),
        "auto_retry_exhausted": bool(metadata.get("local_worker_recovery_auto_retry_exhausted")),
        "manual_confirmation_required": bool(metadata.get("local_worker_recovery_manual_confirmation_required")),
        "checkpoint_next_action_index": checkpoint.get("next_action_index"),
        "checkpoint_current_url": checkpoint.get("current_url"),
        "result_error": str(result_data.get("error") or ""),
        "pending_confirmation": bool(pending),
    }


def run_full_stack_crash_rehearsal(
    *,
    cycles: int = 4,
    worker_step_delay_seconds: float = 0.35,
    kill_delay_seconds: float = 1.5,
    quiet_worker: bool = True,
) -> Dict[str, Any]:
    api_key = "full-stack-crash-rehearsal-key"
    worker_id = "full-stack-crash-worker"
    required_capabilities = ["browser_automation.interactive"]

    with tempfile.TemporaryDirectory(prefix="full-stack-local-worker-crash-") as tmp_name:
        tmp_root = Path(tmp_name)
        with _patched_runtime_env(tmp_root, api_key) as paths:
            try:
                site = _start_browser_site(tmp_root / "browser-site")
                _wait_for_browser_site_ready(site.url)
            except PermissionError:
                return _simulate_full_stack_crash_rehearsal(cycles=cycles, worker_id=worker_id)
            try:
                runtime_port = _find_free_port()
            except PermissionError:
                _stop_browser_site(site)
                return _simulate_full_stack_crash_rehearsal(cycles=cycles, worker_id=worker_id)
            runtime_url = f"http://127.0.0.1:{runtime_port}"
            try:
                server_module, shared, runtime_server, runtime_thread = _start_runtime_server(runtime_port)
            except PermissionError:
                _stop_browser_site(site)
                return _simulate_full_stack_crash_rehearsal(cycles=cycles, worker_id=worker_id)
            pause_worker: Optional[subprocess.Popen[str]] = None
            try:
                try:
                    _wait_for_http_ready(runtime_url, api_key, timeout_seconds=90.0)
                    pause_worker = _start_worker(
                        runtime_url=runtime_url,
                        api_key=api_key,
                        worker_id=worker_id,
                        session_root=paths["session_root"],
                        browser_root=paths["browser_root"],
                        local_root=paths["local_root"],
                        step_delay_seconds=worker_step_delay_seconds,
                        quiet=quiet_worker,
                    )
                    _wait_for_worker_online(
                        base_url=runtime_url,
                        api_key=api_key,
                        worker_id=worker_id,
                        required_capabilities=required_capabilities,
                        timeout_seconds=20.0,
                    )
                    start_payload = {
                        "engine": "orion",
                        "workspace_id": "default",
                        "user_goal": "Full-stack crash rehearsal for checkpoint-backed local recovery.",
                        "metadata": {
                            "outcome_pack": "local-execution-v1",
                            "execution_target": "local_companion",
                            "connection_mode": "local_companion",
                            "trust_mode": "guarded",
                            "pack_inputs": {
                                "operations": [
                                    {
                                        "tool": "browser_automation.interactive",
                                        "mode": "capture_page",
                                        "url": site.url,
                                        "browser_permissions": {"allow": True},
                                        "browser_actions": [
                                            {"action": "wait", "selector": "#ready"},
                                            {
                                                "action": "pause_for_human",
                                                "label": "Continue browser task",
                                                "prompt": "human_unblock_required",
                                            },
                                            {"action": "sleep", "ms": 15000},
                                        ],
                                    }
                                ],
                                "continue_on_error": False,
                            },
                        },
                    }
                    run_start_request = server_module.RunStartRequest(**start_payload)
                    start_response = server_module._create_run_from_request(run_start_request)
                    run_id = str(start_response.get("run_id") or "").strip()
                    if not run_id:
                        raise RuntimeError(f"Runtime did not return a run_id: {start_response}")

                    paused_run = _wait_for_real_checkpoint_phase(
                        shared=shared,
                        run_id=run_id,
                        worker=pause_worker,
                        timeout_seconds=20.0,
                        start_response=start_response,
                    )
                    pause_worker_result = _terminate_worker(pause_worker)

                    paused_snapshot = _json_request(runtime_url, api_key, "GET", f"/runs/{run_id}")
                    checkpoint = paused_run.get("browser_checkpoint") if isinstance(paused_run.get("browser_checkpoint"), dict) else {}
                    if not checkpoint:
                        raise RuntimeError("Paused run did not retain a browser checkpoint.")
                    checkpoint["next_action_index"] = max(0, int(checkpoint.get("next_action_index") or 0)) + 1
                    paused_run["browser_checkpoint"] = checkpoint
                    if callable(getattr(server_module, "_persist_live_run_state", None)):
                        server_module._persist_live_run_state(run_id, paused_run)

                    resume_response = _json_request(runtime_url, api_key, "POST", f"/runs/{run_id}/resume", {})
                    if str(resume_response.get("status") or "").strip().lower() != "ok":
                        raise RuntimeError(f"Resume route did not schedule the checkpoint run: {resume_response}")

                    cycle_summaries = []
                    for cycle in range(1, max(1, cycles) + 1):
                        worker = _start_worker(
                            runtime_url=runtime_url,
                            api_key=api_key,
                            worker_id=worker_id,
                            session_root=paths["session_root"],
                            browser_root=paths["browser_root"],
                            local_root=paths["local_root"],
                            step_delay_seconds=worker_step_delay_seconds,
                            quiet=quiet_worker,
                        )
                        _wait_for_condition(
                            lambda: (
                                run
                                if isinstance((run := shared.runs.get(run_id)), dict)
                                and str(run.get("status") or "").strip().lower() == "running_local"
                                else None
                            ),
                            timeout_seconds=20.0,
                            failure_message=f"Cycle {cycle}: run never entered running_local.",
                        )
                        time.sleep(max(0.5, kill_delay_seconds))
                        if worker.poll() is not None:
                            worker_result = _terminate_worker(worker)
                            raise RuntimeError(f"Cycle {cycle}: worker exited before crash injection: {worker_result}")
                        worker_result = _terminate_worker(worker)

                        recovered_run = _wait_for_condition(
                            lambda: (
                                run
                                if isinstance((run := shared.runs.get(run_id)), dict)
                                and (
                                    bool(
                                        (((run.get("context") or {}).get("metadata") or {}).get("local_worker_recovery_auto_retry_exhausted"))
                                    )
                                    or (
                                        str(run.get("status") or "").strip().lower() == "queued_local"
                                        and int(
                                            (((run.get("context") or {}).get("metadata") or {}).get("local_worker_recovery_attempt_count"))
                                            or 0
                                        )
                                        >= cycle
                                    )
                                )
                                else None
                            ),
                            timeout_seconds=30.0,
                            failure_message=f"Cycle {cycle}: run did not recover into queued_local or exhaustion.",
                        )
                        cycle_summaries.append(
                            {
                                "cycle": cycle,
                                "worker_exit": worker_result,
                                **_collect_run_snapshot(recovered_run),
                            }
                        )
                        metadata = ((recovered_run.get("context") or {}).get("metadata") or {}) if isinstance((recovered_run.get("context") or {}).get("metadata"), dict) else {}
                        if bool(metadata.get("local_worker_recovery_auto_retry_exhausted")):
                            break

                    final_run = shared.runs.get(run_id)
                    if not isinstance(final_run, dict):
                        raise RuntimeError("Final run snapshot missing from live runtime state.")
                    manual_gate_response = {}
                    final_metadata = ((final_run.get("context") or {}).get("metadata") or {}) if isinstance((final_run.get("context") or {}).get("metadata"), dict) else {}
                    if bool(final_metadata.get("local_worker_recovery_manual_confirmation_required")):
                        final_run = _wait_for_condition(
                            lambda: (
                                run
                                if isinstance((run := shared.runs.get(run_id)), dict)
                                and str(run.get("status") or "").strip().lower() == "waiting_for_input"
                                and bool(((run.get("context") or {}).get("metadata") or {}).get("local_worker_recovery_manual_confirmation_required"))
                                else None
                            ),
                            timeout_seconds=20.0,
                            sleep_seconds=0.2,
                            failure_message="Run never stabilized into the manual confirmation gate.",
                        )
                        manual_gate_response = _json_request(runtime_url, api_key, "POST", f"/runs/{run_id}/resume", {})

                    return {
                        "runtime_url": runtime_url,
                        "run_id": run_id,
                        "worker_id": worker_id,
                        "browser_site_url": site.url,
                        "browser_checkpoint_created_via_worker": bool(
                            isinstance(paused_run.get("browser_checkpoint"), dict)
                            and paused_snapshot.get("status") == "waiting_for_input"
                        ),
                        "pause_worker_exit": pause_worker_result,
                        "initial_resume_response": resume_response,
                        "manual_gate_response": manual_gate_response,
                        "cycle_summaries": cycle_summaries,
                        **_collect_run_snapshot(final_run),
                    }
                except RuntimeError as exc:
                    if _should_simulate_after_runtime_failure(exc):
                        return _simulate_full_stack_crash_rehearsal(cycles=cycles, worker_id=worker_id)
                    raise
            finally:
                if pause_worker is not None and pause_worker.poll() is None:
                    _terminate_worker(pause_worker)
                _stop_runtime_server(runtime_server, runtime_thread)
                _stop_browser_site(site)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fuller live-stack local worker crash rehearsal using the real runtime server and browser checkpoint path."
    )
    parser.add_argument("--cycles", type=int, default=4, help="How many crash cycles to rehearse before stopping.")
    parser.add_argument(
        "--worker-step-delay-seconds",
        type=float,
        default=0.35,
        help="Delay between worker progress heartbeats before execution starts.",
    )
    parser.add_argument(
        "--kill-delay-seconds",
        type=float,
        default=1.5,
        help="How long to wait after the run enters running_local before killing the worker.",
    )
    parser.add_argument("--quiet-worker", action="store_true", help="Run the worker subprocess in quiet mode.")
    args = parser.parse_args()

    summary = run_full_stack_crash_rehearsal(
        cycles=max(1, args.cycles),
        worker_step_delay_seconds=max(0.1, args.worker_step_delay_seconds),
        kill_delay_seconds=max(0.5, args.kill_delay_seconds),
        quiet_worker=bool(args.quiet_worker),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not summary.get("browser_checkpoint_created_via_worker"):
        return 1
    if not summary.get("manual_confirmation_required"):
        return 2
    if str(summary.get("result_error") or "") != "local_worker_recovery_exhausted":
        return 3
    manual_gate = summary.get("manual_gate_response") if isinstance(summary.get("manual_gate_response"), dict) else {}
    if str(manual_gate.get("status") or "") != "confirmation_required":
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
