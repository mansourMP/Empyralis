#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


ROOT_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT_DIR / ".orion-stack"
LOG_DIR = STATE_DIR / "logs"
KEY_FILE = STATE_DIR / "ops_daemon_key"
PID_DIR = STATE_DIR / "pids"
START_META_FILE = STATE_DIR / "start.meta.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_RUNTIME_URL = "http://127.0.0.1:8001"
DEFAULT_RUNTIME_KEY = "replace-with-strong-key"
DEFAULT_TELEGRAM_WORKSPACE_ID = "ws-1"

BOT_TOKEN_RE = r"^\d{6,}:[A-Za-z0-9_-]{20,}$"
MAX_REQUEST_BYTES = 65536
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SENSITIVE_KEYS = {
    "api_key",
    "access_token",
    "oauth_token",
    "auth_token",
    "bot_token",
    "token",
    "password",
    "secret",
}
ALLOWED_ACTIONS = {
    "start_services",
    "restart_services",
    "readiness",
    "release_status",
    "telegram_rebind",
    "ops_daemon_status",
}

AUTO_RECOVER_ENABLED = os.getenv("ORION_OPS_DAEMON_AUTORECOVER", "1") == "1"
AUTO_RECOVER_POLL_SECONDS = max(
    2.0,
    min(float(os.getenv("ORION_OPS_DAEMON_HEALTH_POLL_SECONDS", "8")), 120.0),
)
AUTO_RECOVER_FAILURE_THRESHOLD = max(
    1,
    min(int(os.getenv("ORION_OPS_DAEMON_FAILURE_THRESHOLD", "2")), 20),
)
AUTO_RECOVER_COOLDOWN_SECONDS = max(
    5,
    min(int(os.getenv("ORION_OPS_DAEMON_RECOVERY_COOLDOWN_SECONDS", "45")), 3600),
)
AUTO_RECOVER_MAX_PER_HOUR = max(
    1,
    min(int(os.getenv("ORION_OPS_DAEMON_MAX_RECOVERIES_PER_HOUR", "8")), 100),
)
AUTO_RECOVER_REQUIRE_WORKER = os.getenv("ORION_OPS_DAEMON_REQUIRE_WORKER", "1") == "1"
AUTO_RECOVER_REQUIRE_TELEGRAM = os.getenv("ORION_OPS_DAEMON_REQUIRE_TELEGRAM", "1") == "1"
AUTO_RECOVER_STARTUP_GRACE_SECONDS = max(
    5,
    min(int(os.getenv("ORION_OPS_DAEMON_STARTUP_GRACE_SECONDS", "45")), 600),
)

WATCHDOG_LOCK = threading.Lock()
WATCHDOG_STOP = threading.Event()
WATCHDOG_STATE: Dict[str, Any] = {
    "enabled": AUTO_RECOVER_ENABLED,
    "poll_seconds": AUTO_RECOVER_POLL_SECONDS,
    "failure_threshold": AUTO_RECOVER_FAILURE_THRESHOLD,
    "cooldown_seconds": AUTO_RECOVER_COOLDOWN_SECONDS,
    "max_recoveries_per_hour": AUTO_RECOVER_MAX_PER_HOUR,
    "require_worker": AUTO_RECOVER_REQUIRE_WORKER,
    "require_telegram": AUTO_RECOVER_REQUIRE_TELEGRAM,
    "startup_grace_seconds": AUTO_RECOVER_STARTUP_GRACE_SECONDS,
    "last_probe_at": None,
    "last_probe": {},
    "healthy": None,
    "consecutive_failures": 0,
    "last_unhealthy_reason": None,
    "last_recovery_at": None,
    "last_recovery_ok": None,
    "last_recovery_summary": None,
    "next_recovery_at": None,
    "recovery_attempts_total": 0,
    "recovery_attempts_last_hour": 0,
    "recovery_timestamps": [],
}


def _ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)


def _trim_output(raw: str, max_chars: int = 4000) -> str:
    text = str(raw or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...truncated..."


def _normalize_runtime_key(raw: Optional[str]) -> str:
    cleaned = "".join((raw or "").split())
    return cleaned or DEFAULT_RUNTIME_KEY


def _resolve_runtime_key() -> str:
    env_key = _normalize_runtime_key(os.getenv("RUNTIME_KEY") or os.getenv("ORION_API_KEY"))
    if env_key != DEFAULT_RUNTIME_KEY:
        return env_key
    key_file = STATE_DIR / "runtime_key"
    if key_file.exists():
        try:
            file_key = _normalize_runtime_key(key_file.read_text(encoding="utf-8"))
            if file_key:
                return file_key
        except Exception:
            pass
    return env_key


def _read_or_create_ops_key() -> str:
    _ensure_dirs()
    if KEY_FILE.exists():
        value = KEY_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    KEY_FILE.write_text(value, encoding="utf-8")
    os.chmod(KEY_FILE, 0o600)
    return value


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in SENSITIVE_KEYS:
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


def _is_loopback_host(host: str) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = info[4][0]
        if ip.startswith("127.") or ip == "::1":
            continue
        return False
    return True


def _allow_remote_ops() -> bool:
    return os.getenv("ORION_OPS_DAEMON_ALLOW_REMOTE", "0") == "1"


def _run_script(script: str, runtime_key: str, extra_env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    env = dict(os.environ)
    env["RUNTIME_KEY"] = runtime_key
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        ["bash", script],
        cwd=str(ROOT_DIR),
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
        check=False,
    )
    return proc.returncode, _trim_output(proc.stdout), _trim_output(proc.stderr)


def _safe_status(payload: Any, key: str, default: Any = None) -> Any:
    if isinstance(payload, dict):
        return payload.get(key, default)
    return default


def _watchdog_snapshot() -> Dict[str, Any]:
    with WATCHDOG_LOCK:
        state = dict(WATCHDOG_STATE)
        timestamps = state.get("recovery_timestamps") or []
        state["recovery_timestamps"] = list(timestamps)[-AUTO_RECOVER_MAX_PER_HOUR:]
        return _redact_payload(state)


def _runtime_request(
    runtime_key: str,
    path: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    runtime_url: str = DEFAULT_RUNTIME_URL,
) -> Tuple[int, Any]:
    url = f"{runtime_url}{path}"
    data = None
    headers = {"X-API-Key": runtime_key}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return int(resp.status), json.loads(body or "{}")
            except Exception:
                return int(resp.status), body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            parsed = raw
        return int(exc.code), parsed
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}


def _load_start_meta() -> Dict[str, Any]:
    try:
        payload = json.loads(START_META_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _runtime_start_age_seconds(now_ts: int) -> Optional[int]:
    meta = _load_start_meta()
    started_at = str(meta.get("started_at") or "").strip()
    if not started_at:
        return None
    try:
        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    started_ts = int(dt.timestamp())
    age = now_ts - started_ts
    return age if age >= 0 else 0


def _telegram_get(bot_token: str, method: str, query: str = "") -> Tuple[int, Any]:
    url = f"https://api.telegram.org/bot{bot_token}/{method}{query}"
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), json.loads(body or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            parsed = raw
        return int(exc.code), parsed


def _kill_worker_processes() -> None:
    for pattern in (
        f"{ROOT_DIR}/scripts/orion_local_worker.py",
        f"{ROOT_DIR}/scripts/run_local_worker.sh",
    ):
        subprocess.run(
            ["bash", "-lc", f'pkill -f "{pattern}" || true'],
            cwd=str(ROOT_DIR),
            text=True,
            capture_output=True,
            check=False,
        )


def _action_start_services(runtime_key: str) -> Dict[str, Any]:
    _kill_worker_processes()
    code, out, err = _run_script(
        "scripts/start_orion_local_stack.sh",
        runtime_key=runtime_key,
        extra_env={
            "START_FRONTEND": "0",
            "START_OPS_DAEMON": "0",
            "ORION_OPENCLAW_GATEWAY_POLICY": "ignore",
        },
    )
    return {"ok": code == 0, "action": "start_services", "code": code, "stdout": out, "stderr": err}


def _action_restart_services(runtime_key: str) -> Dict[str, Any]:
    return _action_start_services(runtime_key)


def _action_readiness(runtime_key: str) -> Dict[str, Any]:
    code, out, err = _run_script("scripts/orion_environment_readiness.sh", runtime_key=runtime_key)
    return {"ok": code == 0, "action": "readiness", "code": code, "stdout": out, "stderr": err}


def _action_release_status(runtime_key: str) -> Dict[str, Any]:
    code, out, err = _run_script("scripts/orion_release_status.sh", runtime_key=runtime_key)
    return {"ok": code == 0, "action": "release_status", "code": code, "stdout": out, "stderr": err}


def _action_ops_daemon_status(runtime_url: str) -> Dict[str, Any]:
    snapshot = _watchdog_snapshot()
    return {
        "ok": True,
        "action": "ops_daemon_status",
        "running": True,
        "runtime_url": runtime_url,
        "ts": int(time.time()),
        "watchdog": snapshot,
    }


def _action_telegram_rebind(runtime_key: str, payload: Dict[str, Any], runtime_url: str) -> Dict[str, Any]:
    bot_token = str(payload.get("botToken") or "").strip()
    chat_id = str(payload.get("chatId") or "").strip()
    allow_any_chat = bool(payload.get("allowAnyChat"))
    workspace_id = str(
        payload.get("workspaceId")
        or os.getenv("ORION_TELEGRAM_AUTOPILOT_WORKSPACE_ID")
        or os.getenv("WORKSPACE_ID")
        or DEFAULT_TELEGRAM_WORKSPACE_ID
    ).strip() or DEFAULT_TELEGRAM_WORKSPACE_ID

    import re

    if not re.match(BOT_TOKEN_RE, bot_token):
        return {"ok": False, "error": "Invalid Telegram bot token format."}
    if not chat_id:
        return {"ok": False, "error": "chat_id is required."}

    me_status, me_payload = _telegram_get(bot_token, "getMe")
    if me_status != 200 or not isinstance(me_payload, dict) or not me_payload.get("ok"):
        return {"ok": False, "error": "Telegram getMe failed.", "status": me_status, "details": me_payload}

    connector_payload: Dict[str, Any] = {
        "label": "Telegram Bot LIVE",
        "connector": "telegram_bot",
        "workspace_id": workspace_id,
        "credentials": {"bot_token": bot_token, "chat_id": chat_id},
        "metadata": {"allow_any_chat": True} if allow_any_chat else {},
    }
    create_status, create_payload = _runtime_request(
        runtime_key,
        "/connectors/vault",
        method="POST",
        payload=connector_payload,
        runtime_url=runtime_url,
    )
    if create_status >= 400:
        return {
            "ok": False,
            "error": "Failed to create Telegram connector.",
            "status": create_status,
            "details": create_payload,
        }

    _, probe_payload = _runtime_request(
        runtime_key,
        "/channels/telegram/send",
        method="POST",
        payload={
            "text": "[Empyralis probe] Daemon rebind complete. Reply here with: LIVE-OK",
            "workspace_id": workspace_id,
            "chat_id": chat_id,
        },
        runtime_url=runtime_url,
    )
    _, status_payload = _runtime_request(
        runtime_key,
        "/channels/telegram/autopilot/status",
        method="GET",
        runtime_url=runtime_url,
    )
    return {
        "ok": True,
        "action": "telegram_rebind",
        "bot": me_payload.get("result") if isinstance(me_payload, dict) else None,
        "connector": create_payload,
        "probe": probe_payload,
        "autopilot": status_payload,
    }


def _watchdog_probe(runtime_key: str, runtime_url: str) -> Dict[str, Any]:
    checked_at = int(time.time())
    runtime_start_age_seconds = _runtime_start_age_seconds(checked_at)
    startup_grace_active = (
        isinstance(runtime_start_age_seconds, int)
        and runtime_start_age_seconds < AUTO_RECOVER_STARTUP_GRACE_SECONDS
    )
    health_status, health_payload = _runtime_request(runtime_key, "/health", runtime_url=runtime_url)
    runtime_ok = bool(health_status == 200 and _safe_status(health_payload, "ok", False))
    issues = []
    worker_online = 0
    pending_runs = 0
    claimed_runs = 0
    active_local_work = False
    telegram_enabled = False
    telegram_active = False
    telegram_thread_alive = False
    telegram_error = None
    if not runtime_ok:
        issues.append("runtime_unreachable_or_unhealthy")
    else:
        worker_status, worker_payload = _runtime_request(
            runtime_key,
            "/runtime/runtimes/status",
            runtime_url=runtime_url,
        )
        worker_summary = _safe_status(worker_payload, "summary", {})
        worker_online = int(_safe_status(worker_summary, "online", 0) or 0)
        pending_runs = int(_safe_status(worker_summary, "pending_runs", 0) or 0)
        claimed_runs = int(_safe_status(worker_summary, "claimed_runs", 0) or 0)
        active_local_work = pending_runs > 0 or claimed_runs > 0
        worker_ok = worker_status == 200 and worker_online > 0
        if AUTO_RECOVER_REQUIRE_WORKER and not worker_ok and not startup_grace_active and not active_local_work:
            issues.append("local_worker_offline")

        telegram_enabled = bool(_safe_status(health_payload, "telegram_autopilot_enabled", False))
        if AUTO_RECOVER_REQUIRE_TELEGRAM and telegram_enabled:
            tg_status, tg_payload = _runtime_request(
                runtime_key,
                "/channels/telegram/autopilot/status",
                runtime_url=runtime_url,
            )
            tg_data = _safe_status(tg_payload, "autopilot", {})
            telegram_active = bool(_safe_status(tg_data, "active", False))
            telegram_thread_alive = bool(_safe_status(tg_data, "thread_alive", False))
            telegram_error = _safe_status(tg_data, "last_error", None)
            if tg_status != 200 and not startup_grace_active and not active_local_work:
                issues.append("telegram_autopilot_status_unavailable")
            elif not telegram_active and not startup_grace_active and not active_local_work:
                issues.append("telegram_autopilot_inactive")
            elif not telegram_thread_alive and not startup_grace_active and not active_local_work:
                issues.append("telegram_autopilot_thread_dead")

    return {
        "checked_at": checked_at,
        "runtime_start_age_seconds": runtime_start_age_seconds,
        "startup_grace_active": startup_grace_active,
        "runtime_ok": runtime_ok,
        "worker_online": worker_online,
        "pending_runs": pending_runs,
        "claimed_runs": claimed_runs,
        "active_local_work": active_local_work,
        "telegram_enabled": telegram_enabled,
        "telegram_active": telegram_active,
        "telegram_thread_alive": telegram_thread_alive,
        "telegram_error": telegram_error,
        "issues": issues,
        "healthy": len(issues) == 0,
    }


def _watchdog_prune_recovery_timestamps(now_ts: int) -> None:
    with WATCHDOG_LOCK:
        timestamps = WATCHDOG_STATE.get("recovery_timestamps") or []
        if not isinstance(timestamps, list):
            timestamps = []
        cutoff = now_ts - 3600
        timestamps = [int(ts) for ts in timestamps if isinstance(ts, (int, float)) and int(ts) >= cutoff]
        WATCHDOG_STATE["recovery_timestamps"] = timestamps
        WATCHDOG_STATE["recovery_attempts_last_hour"] = len(timestamps)


def _watchdog_record_recovery(now_ts: int, ok: bool, summary: str) -> None:
    with WATCHDOG_LOCK:
        timestamps = WATCHDOG_STATE.get("recovery_timestamps") or []
        if not isinstance(timestamps, list):
            timestamps = []
        timestamps.append(int(now_ts))
        WATCHDOG_STATE["recovery_timestamps"] = timestamps
        WATCHDOG_STATE["recovery_attempts_total"] = int(WATCHDOG_STATE.get("recovery_attempts_total") or 0) + 1
        WATCHDOG_STATE["recovery_attempts_last_hour"] = len(timestamps)
        WATCHDOG_STATE["last_recovery_at"] = now_ts
        WATCHDOG_STATE["last_recovery_ok"] = bool(ok)
        WATCHDOG_STATE["last_recovery_summary"] = summary
        WATCHDOG_STATE["next_recovery_at"] = now_ts + AUTO_RECOVER_COOLDOWN_SECONDS


def _watchdog_loop(runtime_url: str) -> None:
    while not WATCHDOG_STOP.is_set():
        runtime_key = _resolve_runtime_key()
        probe = _watchdog_probe(runtime_key, runtime_url)
        now_ts = int(time.time())
        with WATCHDOG_LOCK:
            WATCHDOG_STATE["last_probe_at"] = now_ts
            WATCHDOG_STATE["last_probe"] = probe
            WATCHDOG_STATE["healthy"] = bool(probe.get("healthy"))
            if probe.get("healthy"):
                WATCHDOG_STATE["consecutive_failures"] = 0
                WATCHDOG_STATE["last_unhealthy_reason"] = None
            else:
                WATCHDOG_STATE["consecutive_failures"] = int(WATCHDOG_STATE.get("consecutive_failures") or 0) + 1
                WATCHDOG_STATE["last_unhealthy_reason"] = ",".join(probe.get("issues") or []) or "unknown"

        _watchdog_prune_recovery_timestamps(now_ts)

        if AUTO_RECOVER_ENABLED and not probe.get("healthy"):
            with WATCHDOG_LOCK:
                consecutive = int(WATCHDOG_STATE.get("consecutive_failures") or 0)
                next_recovery_at = int(WATCHDOG_STATE.get("next_recovery_at") or 0)
                recovery_attempts_last_hour = int(WATCHDOG_STATE.get("recovery_attempts_last_hour") or 0)
            should_recover = consecutive >= AUTO_RECOVER_FAILURE_THRESHOLD
            if should_recover and now_ts >= next_recovery_at and recovery_attempts_last_hour < AUTO_RECOVER_MAX_PER_HOUR:
                result = _action_start_services(runtime_key)
                ok = bool(result.get("ok"))
                summary = f"auto_recover:{'ok' if ok else 'failed'} code={result.get('code')}"
                _watchdog_record_recovery(now_ts, ok, summary)
                if ok:
                    with WATCHDOG_LOCK:
                        WATCHDOG_STATE["consecutive_failures"] = 0
                        WATCHDOG_STATE["last_unhealthy_reason"] = None

        WATCHDOG_STOP.wait(AUTO_RECOVER_POLL_SECONDS)


def _dispatch_action(action: str, payload: Dict[str, Any], runtime_url: str) -> Dict[str, Any]:
    runtime_key = _normalize_runtime_key(str(payload.get("runtimeKey") or ""))
    if action == "start_services":
        return _action_start_services(runtime_key)
    if action == "restart_services":
        return _action_restart_services(runtime_key)
    if action == "readiness":
        return _action_readiness(runtime_key)
    if action == "release_status":
        return _action_release_status(runtime_key)
    if action == "telegram_rebind":
        return _action_telegram_rebind(runtime_key, payload, runtime_url=runtime_url)
    if action == "ops_daemon_status":
        return _action_ops_daemon_status(runtime_url=runtime_url)
    return {"ok": False, "error": f"Unsupported action '{action}'."}


class _Handler(BaseHTTPRequestHandler):
    server_version = "orion-ops-daemon/1.0"

    def _write_json(self, status: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        daemon_key = getattr(self.server, "daemon_key", "")
        if not daemon_key:
            return False
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1].strip()
            return token == daemon_key
        return False

    def _remote_allowed(self) -> bool:
        client_ip = self.client_address[0] if self.client_address else ""
        if _allow_remote_ops():
            return True
        return client_ip.startswith("127.") or client_ip == "::1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            snapshot = _watchdog_snapshot()
            return self._write_json(
                200,
                {
                    "ok": True,
                    "service": "orion_ops_daemon",
                    "root": str(ROOT_DIR),
                    "runtime_url": getattr(self.server, "runtime_url", DEFAULT_RUNTIME_URL),
                    "ts": int(time.time()),
                    "watchdog": snapshot,
                },
            )
        self._write_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            return self._write_json(404, {"ok": False, "error": "not_found"})
        if not self._remote_allowed():
            return self._write_json(403, {"ok": False, "error": "forbidden_remote"})
        if not self._authorized():
            return self._write_json(401, {"ok": False, "error": "unauthorized"})
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type.lower():
            return self._write_json(415, {"ok": False, "error": "unsupported_content_type"})
        try:
            raw_len = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raw_len = 0
        if raw_len <= 0:
            return self._write_json(400, {"ok": False, "error": "empty_body"})
        if raw_len > MAX_REQUEST_BYTES:
            return self._write_json(413, {"ok": False, "error": "payload_too_large"})
        raw = self.rfile.read(max(raw_len, 0)).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw or "{}")
        except Exception:
            return self._write_json(400, {"ok": False, "error": "invalid_json"})
        if not isinstance(body, dict):
            return self._write_json(400, {"ok": False, "error": "invalid_payload"})
        action = str(body.get("action") or "").strip()
        if not action:
            return self._write_json(400, {"ok": False, "error": "missing_action"})
        if action not in ALLOWED_ACTIONS:
            return self._write_json(400, {"ok": False, "error": f"unsupported_action:{action}"})
        try:
            result = _dispatch_action(action, body, runtime_url=getattr(self.server, "runtime_url", DEFAULT_RUNTIME_URL))
            result = _redact_payload(result) if isinstance(result, dict) else {"ok": False, "error": "invalid_result"}
            status = 200 if result.get("ok") else 400
            self._write_json(status, result)
        except subprocess.TimeoutExpired:
            self._write_json(504, {"ok": False, "error": "timeout"})
        except Exception as exc:  # pragma: no cover
            self._write_json(500, {"ok": False, "error": f"internal_error: {exc}"})

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        log_path = LOG_DIR / "ops-daemon.log"
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{self.log_date_time_string()} {fmt % args}\n")
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Empyralis local ops daemon")
    parser.add_argument("--host", default=os.getenv("ORION_OPS_DAEMON_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("ORION_OPS_DAEMON_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--runtime-url", default=os.getenv("ORION_API_URL", DEFAULT_RUNTIME_URL))
    args = parser.parse_args()
    if not _allow_remote_ops() and not _is_loopback_host(args.host):
        print(
            f"refusing non-loopback host '{args.host}'. "
            "Set ORION_OPS_DAEMON_ALLOW_REMOTE=1 to override.",
            file=sys.stderr,
        )
        return 2

    _ensure_dirs()
    daemon_key = _read_or_create_ops_key()
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    server.daemon_key = daemon_key  # type: ignore[attr-defined]
    server.runtime_url = args.runtime_url  # type: ignore[attr-defined]

    pid_file = PID_DIR / "ops-daemon.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        os.chmod(pid_file, 0o600)
    except Exception:
        pass

    WATCHDOG_STOP.clear()
    watchdog_thread = threading.Thread(
        target=_watchdog_loop,
        kwargs={"runtime_url": args.runtime_url},
        daemon=True,
        name="orion-ops-watchdog",
    )
    watchdog_thread.start()

    print(f"Empyralis ops daemon listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        WATCHDOG_STOP.set()
        try:
            watchdog_thread.join(timeout=2.0)
        except Exception:
            pass
        try:
            if pid_file.exists():
                pid_file.unlink()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
