#!/usr/bin/env python3
"""
Empyralis Self-Hosted Command Worker.

Drains the Main Agent's self-hosted command queue — the queue that holds
hardware tool calls (shell, file, screenshot) for user-owned machines
(Mac, Mac mini gateway, VPS) that have enrolled as self-hosted nodes.

Architecture (ADR: canonical-hardware-execution-dial-out-2026-06-21):
  The node polls OUT to the cloud.  The cloud never reaches IN over SSH.

Lifecycle per command:
  1. Claim  → POST /runtime/self-hosted-nodes/{profile_id}/commands/claim
  2. Govern → call the Rust kernel (local-worker-decision) to enforce
              kill switch, control-state, and lease validity
  3. Execute → shell_exec | file_read | file_write
  4. Complete → POST .../commands/{command_id}/result

The worker is SEPARATE from the Orion worker (scripts/orion_local_worker_runtime.py)
which drains the studio-agent task/run queue.  They can share a supervisor later.

Usage:
  EMPYRALIS_CLOUD_URL=https://empyralis.com \\
  EMPYRALIS_RUNTIME_PROFILE_ID=rp_xxxx \\
  EMPYRALIS_NODE_SESSION_TOKEN=ns_xxxx \\
  python scripts/empyralis_self_hosted_command_worker.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── Logging (JSON structured) ────────────────────────────────────────────────

LOG_FORMAT = os.getenv("EMPYRALIS_WORKER_LOG_FORMAT", "json").strip().lower()

if LOG_FORMAT == "json":
    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload: Dict[str, Any] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
                "level": record.levelname.lower(),
                "msg": record.getMessage(),
            }
            for key in ("command_id", "workspace_id", "profile_id", "operation"):
                if hasattr(record, key):
                    payload[key] = getattr(record, key)
            if record.exc_info and record.exc_info[1]:
                payload["error"] = str(record.exc_info[1])
            return json.dumps(payload, default=str)

    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(_JsonFormatter())
    logging.root.handlers.clear()
    logging.root.addHandler(_handler)
    logging.root.setLevel(logging.INFO)

_log = logging.getLogger("cmd_worker")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def _ensure_slashless(url: str) -> str:
    return str(url or "").rstrip("/")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class CommandWorkerError(RuntimeError):
    pass


# ── HTTP Client ───────────────────────────────────────────────────────────────

class HttpClient:
    """Minimal HTTP client with retry and auth header support."""

    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self.base_url = _ensure_slashless(base_url)
        self.token = token
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body else None
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            raise CommandWorkerError(f"HTTP {exc.code} {method} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CommandWorkerError(f"URL error {method} {path}: {exc}") from exc

    def retry(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
              attempts: int = 3, backoff: float = 1.0) -> Dict[str, Any]:
        last_err: Optional[Exception] = None
        for i in range(attempts):
            try:
                return self._request(method, path, body)
            except CommandWorkerError as exc:
                last_err = exc
                _log.warning("retry %d/%d %s %s: %s", i + 1, attempts, method, path, exc)
                if i < attempts - 1:
                    time.sleep(backoff * (2 ** i))
        raise CommandWorkerError(f"All {attempts} attempts failed for {method} {path}") from last_err


# ── Governance (Rust kernel, inlined client) ──────────────────────────────────

_KERNEL_REPO_ROOT = Path(__file__).resolve().parents[1]
_KERNEL_ENV_VAR = "EMPYRALIS_RUNTIME_KERNEL_BIN"
_KERNEL_DEFAULT_TIMEOUT = 5


def _runtime_kernel_binary() -> Optional[Path]:
    configured = os.environ.get(_KERNEL_ENV_VAR)
    candidates: List[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([
        _KERNEL_REPO_ROOT / "empyralis-runtime-kernel" / "target" / "release" / "empyralis-runtime-kernel",
        _KERNEL_REPO_ROOT / "empyralis-runtime-kernel" / "target" / "debug" / "empyralis-runtime-kernel",
    ])
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _kernel_available() -> bool:
    return _runtime_kernel_binary() is not None


def _run_runtime_kernel(command: str, payload: Dict[str, Any], *, timeout_seconds: int = _KERNEL_DEFAULT_TIMEOUT) -> Dict[str, Any]:
    binary = _runtime_kernel_binary()
    if binary is None:
        return {"ok": False, "decision": "block", "reason": "runtime_kernel_unavailable",
                "error": "runtime_kernel_unavailable", "operation": str(command), "next_action": "",
                "audit_visibility": "security", "approval_required": False, "cacheable": False}
    try:
        proc = subprocess.run(
            [str(binary), command],
            input=json.dumps(payload),
            text=True, capture_output=True, timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "decision": "block", "reason": "runtime_kernel_timeout",
                "error": "runtime_kernel_timeout", "operation": str(command), "next_action": "",
                "audit_visibility": "security", "approval_required": False, "cacheable": False}
    except OSError:
        return {"ok": False, "decision": "block", "reason": "runtime_kernel_exec_failed",
                "error": "runtime_kernel_exec_failed", "operation": str(command), "next_action": "",
                "audit_visibility": "security", "approval_required": False, "cacheable": False}

    stdout = proc.stdout.strip()
    if not stdout:
        return {"ok": False, "decision": "block", "reason": "runtime_kernel_empty_response",
                "error": "runtime_kernel_empty_response", "operation": str(command), "next_action": "",
                "audit_visibility": "security", "approval_required": False, "cacheable": False}
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "decision": "block", "reason": "runtime_kernel_invalid_json",
                "error": "runtime_kernel_invalid_json", "operation": str(command), "next_action": "",
                "audit_visibility": "security", "approval_required": False, "cacheable": False}
    if not isinstance(response, dict):
        return {"ok": False, "decision": "block", "reason": "runtime_kernel_invalid_response",
                "error": "runtime_kernel_invalid_response", "operation": str(command), "next_action": "",
                "audit_visibility": "security", "approval_required": False, "cacheable": False}
    if proc.returncode != 0:
        response.setdefault("ok", False)
        response.setdefault("decision", "block")
        response.setdefault("reason", "runtime_kernel_nonzero_exit")
        if response.get("ok") is not False or response.get("decision") != "block":
            return {"ok": False, "decision": "block", "reason": "runtime_kernel_invalid_response",
                    "error": "runtime_kernel_invalid_response", "operation": str(command), "next_action": "",
                    "audit_visibility": "security", "approval_required": False, "cacheable": False}
    if not isinstance(response.get("ok"), bool) or not isinstance(response.get("decision"), str):
        return {"ok": False, "decision": "block", "reason": "runtime_kernel_invalid_response",
                "error": "runtime_kernel_invalid_response", "operation": str(command), "next_action": "",
                "audit_visibility": "security", "approval_required": False, "cacheable": False}
    return response


def enforce_command_governance(
    *,
    operation: str = "execute_command",
    workspace_id: str = "",
    runtime_profile_id: str = "",
    command_id: str = "",
    capability_id: str = "",
    command_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the Rust kernel's local-worker-decision before executing a command.

    FAILS CLOSED: if the kernel is unavailable, refuse to execute.
    Returns the kernel decision dict on success.
    """
    if not _kernel_available():
        raise CommandWorkerError(
            f"Rust kernel unavailable — refusing to execute command {command_id}. "
            "The kernel must be installed and configured for node-side governance."
        )

    payload: Dict[str, Any] = {
        "operation": operation,
        "workspace_id": workspace_id,
        "runtime_profile_id": runtime_profile_id,
        "command_id": command_id,
        "capability_id": capability_id,
        "command_payload": command_payload or {},
        "local_companion_enabled": True,
        "kill_switch_active": _env("EMPYRALIS_KILL_SWITCH", "false").strip().lower() in ("1", "true", "yes", "on"),
    }

    decision = _run_runtime_kernel("local-worker-decision", payload)

    # Enforce: block if decision is not ok
    if decision.get("decision") == "block" or decision.get("ok") is False:
        reason = str(decision.get("reason") or "rust_kernel_blocked")
        raise CommandWorkerError(f"governance_blocked: {reason}")

    _log.info(
        "governance decision: %s for command %s",
        decision.get("next_action", decision.get("decision", "unknown")),
        command_id,
        extra={"command_id": command_id, "workspace_id": workspace_id, "operation": "governance"},
    )
    return decision


# ── Executors ─────────────────────────────────────────────────────────────────

# ── Hard-blocked command patterns (defense-in-depth) ──
# These mirror empyralis-runtime-kernel and empyralis-supervisor.
# They are checked HERE in addition to the kernel governance gate so that
# a direct call to execute_shell() (bypassing governance) is still blocked.

_HARD_BLOCKED_COMMAND_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "rm -fr /",
    "rm -fr /*",
    "rm -rf ~",
    "rm -fr ~",
    "rm -rf ~/",
    "rm -fr ~/",
    "rm -rf .",
    "mkfs.",
    "diskutil erasedisk",
    "dd if=/dev/",
    ":(){ :|:& };:",
    "> /dev/sda",
    "> /dev/nvme",
    "chmod -r 000 /",
    "chmod -r 777 /",
    "chown -r ",
    "shutdown -",
    "reboot",
    "halt",
    "poweroff",
]

_HARD_PROTECTED_PATH_MARKERS = [
    "/.empyralis/state/vault",
    "/.empyralis/state",
    "/.ssh",
    "/.gnupg",
    "/etc/empyralis",
    "/var/lib/empyralis/agent-computer",
    "/.orion-stack",
]


def _hard_blocked_command(command: str) -> bool:
    """Check if command matches a hard-blocked catastrophic pattern."""
    compact = " ".join(str(command or "").strip().lower().split())
    if not compact:
        return False
    for pattern in _HARD_BLOCKED_COMMAND_PATTERNS:
        if pattern in compact:
            # Path-root boundary check: "rm -rf /" must NOT match "rm -rf /tmp/scratch"
            # (agent CAN destroy workspace scope, canNOT destroy root/home).
            pos = compact.find(pattern)
            after = compact[pos + len(pattern):] if pos + len(pattern) < len(compact) else ""
            next_char = after[0] if after else ""
            if next_char and next_char != " ":
                if pattern.startswith("rm -") and (pattern.endswith(" /") or pattern.endswith(" ~") or pattern.endswith(" ~/") or pattern.endswith(" .")):
                    continue
            return True
    return False


def _command_touches_protected_path(command: str) -> bool:
    """Check if any token in command references a hard-protected path."""
    for token in str(command or "").split():
        cleaned = token.strip('"').strip("'").lower()
        for marker in _HARD_PROTECTED_PATH_MARKERS:
            if marker in cleaned:
                return True
    return False


def _path_touches_protected(file_path: str) -> bool:
    """Check if a filesystem path targets a hard-protected directory."""
    import os as _os
    try:
        resolved = str(Path(file_path).expanduser().resolve())
    except Exception:
        resolved = str(file_path or "")
    lower = resolved.lower()
    for marker in _HARD_PROTECTED_PATH_MARKERS:
        if marker in lower:
            return True
    # Also check the vault key file by name
    name = _os.path.basename(resolved).lower()
    if name == "key" and ("/.empyralis/state/vault" in lower or "/.orion-stack" in lower):
        return True
    return False


def execute_shell(command: str, timeout_seconds: int = 30, cwd: Optional[str] = None) -> Dict[str, Any]:
    """Execute a shell command and return structured result."""
    cmd = str(command or "").strip()
    if not cmd:
        return {"stdout": "", "stderr": "No command provided", "exit_code": -1, "status": "error"}

    # ── Hard-blocks: defense-in-depth (kernel governance also checks these) ──
    if _hard_blocked_command(cmd):
        return {
            "stdout": "",
            "stderr": "shell.execute command is permanently blocked — it could cause irreversible destruction of the runtime environment",
            "exit_code": -1,
            "status": "blocked",
        }
    if _command_touches_protected_path(cmd):
        return {
            "stdout": "",
            "stderr": "shell.execute path is permanently protected — it contains credentials, pairings, or runtime state",
            "exit_code": -1,
            "status": "blocked",
        }

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=cwd,
        )
        return {
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "exit_code": proc.returncode,
            "status": "completed" if proc.returncode == 0 else "error",
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Command timed out after {timeout_seconds}s", "exit_code": -1, "status": "timeout"}
    except Exception as exc:
        return {"stdout": "", "stderr": str(exc), "exit_code": -1, "status": "error"}


def execute_file_read(path: str, max_bytes: int = 1_000_000) -> Dict[str, Any]:
    """Read a file from the node filesystem."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {"content": "", "path": str(p), "size_bytes": 0, "status": "not_found", "error": f"File not found: {path}"}
    # ── Hard-protected path check: read is allowed for visibility, but blocked
    #     for credential/key/vault files specifically ──
    if _path_touches_protected(str(p)):
        return {
            "content": "",
            "path": str(p),
            "size_bytes": 0,
            "status": "blocked",
            "error": "filesystem.read path is permanently protected — it contains credentials, pairings, or runtime state that must not be exposed to agent actions",
        }
    try:
        content = p.read_text()[:max_bytes]
        size = p.stat().st_size
        return {"content": content, "path": str(p), "size_bytes": size, "status": "completed"}
    except Exception as exc:
        return {"content": "", "path": str(p), "size_bytes": 0, "status": "error", "error": str(exc)}


def execute_file_write(path: str, content: str) -> Dict[str, Any]:
    """Write content to a file on the node filesystem."""
    p = Path(path).expanduser().resolve()
    # ── Hard-protected path check ──
    if _path_touches_protected(str(p)):
        return {
            "path": str(p), "size_bytes": 0, "status": "blocked",
            "error": "filesystem.write path is permanently protected — it contains credentials, pairings, or runtime state that must survive agent actions",
        }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        size = p.stat().st_size
        return {"path": str(p), "size_bytes": size, "status": "completed"}
    except Exception as exc:
        return {"path": str(p), "size_bytes": 0, "status": "error", "error": str(exc)}


# ── Command Dispatcher ────────────────────────────────────────────────────────

def execute_command(capability_id: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch to the correct executor based on capability_id."""
    cap = str(capability_id or "").strip().lower().replace("-", "_").replace(".", "_").replace(" ", "_")

    if cap in {"shell_execute", "shell_exec", "shell_exec", "shell__exec"}:
        cmd = str(arguments.get("command") or arguments.get("cmd") or "").strip()
        if not cmd:
            return {"status": "error", "error": "No command provided for shell execution"}
        timeout = int(arguments.get("timeout_seconds") or arguments.get("timeout") or 30)
        cwd = str(arguments.get("cwd") or arguments.get("working_directory") or "") or None
        return execute_shell(cmd, timeout_seconds=timeout, cwd=cwd)

    if cap in {"filesystem_read", "file_read", "file__read", "filesystem_read"}:
        path = str(arguments.get("path") or arguments.get("file_path") or "").strip()
        if not path:
            return {"status": "error", "error": "No path provided for file read"}
        return execute_file_read(path)

    if cap in {"filesystem_write", "file_write", "file__write", "filesystem_write"}:
        path = str(arguments.get("path") or arguments.get("file_path") or "").strip()
        content = str(arguments.get("content") or arguments.get("data") or "")
        if not path:
            return {"status": "error", "error": "No path provided for file write"}
        return execute_file_write(path, content)

    return {"status": "error", "error": f"Unsupported capability: {capability_id}"}


# ── Command Worker ────────────────────────────────────────────────────────────

class SelfHostedCommandWorker:
    """Polls the cloud for Main Agent hardware commands, executes them, and reports results."""

    def __init__(
        self,
        cloud_url: str,
        runtime_profile_id: str,
        node_session_token: str,
        *,
        poll_interval: float = 2.0,
        max_commands_per_poll: int = 5,
        lease_seconds: int = 120,
        request_timeout: int = 30,
    ):
        self.profile_id = runtime_profile_id
        self.http = HttpClient(cloud_url, node_session_token, timeout=request_timeout)
        self.poll_interval = poll_interval
        self.max_commands = max_commands_per_poll
        self.lease_seconds = lease_seconds
        self._running = False
        self._commands_executed = 0
        self._commands_failed = 0

    # ── API calls ────────────────────────────────────────────────────────────

    def claim_commands(self) -> List[Dict[str, Any]]:
        """Claim pending commands from the self-hosted command queue."""
        resp = self.http.retry(
            "POST",
            f"/runtime/self-hosted-nodes/{self.profile_id}/commands/claim",
            {
                "node_session_token": self.http.token,
                "max_commands": self.max_commands,
                "lease_seconds": self.lease_seconds,
            },
        )
        commands = resp.get("commands") or resp.get("claimed") or []
        return [dict(c) for c in commands if isinstance(c, dict)]

    def complete_command(
        self,
        command_id: str,
        *,
        status: str,
        result_payload: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Report a command result back to the cloud."""
        return self.http.retry(
            "POST",
            f"/runtime/self-hosted-nodes/{self.profile_id}/commands/{command_id}/result",
            {
                "node_session_token": self.http.token,
                "status": status,
                "result_payload": result_payload or {},
                "artifacts": [],
                "error": error,
            },
        )

    def heartbeat(self) -> Dict[str, Any]:
        """Send a heartbeat to renew the node session."""
        try:
            return self.http._request(
                "POST",
                f"/runtime/self-hosted-nodes/{self.profile_id}/heartbeat",
                {"node_session_token": self.http.token},
            )
        except Exception as exc:
            _log.warning("heartbeat failed (non-fatal): %s", exc)
            return {"ok": False, "error": str(exc)}

    # ── Main loop ────────────────────────────────────────────────────────────

    def _process_command(self, cmd: Dict[str, Any]) -> bool:
        """Execute one command: govern → execute → complete. Returns True on success."""
        command_id = str(cmd.get("id") or cmd.get("command_id") or "").strip()
        payload = cmd.get("command_payload") if isinstance(cmd.get("command_payload"), dict) else {}
        capability_id = str(payload.get("capability_id") or "").strip()
        workspace_id = str(payload.get("workspace_id") or "default").strip()
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}

        if not command_id or not capability_id:
            _log.warning("skipping malformed command: id=%s cap=%s", command_id, capability_id,
                         extra={"command_id": command_id, "workspace_id": workspace_id})
            return False

        log_ctx: Dict[str, Any] = {"command_id": command_id, "workspace_id": workspace_id, "profile_id": self.profile_id}

        # ── Step 1: Governance gate (fail closed) ──
        try:
            enforce_command_governance(
                operation="execute_command",
                workspace_id=workspace_id,
                runtime_profile_id=self.profile_id,
                command_id=command_id,
                capability_id=capability_id,
                command_payload=payload,
            )
        except CommandWorkerError as exc:
            _log.error("governance blocked command %s: %s", command_id, exc, extra=log_ctx)
            self.complete_command(command_id, status="failed", error=f"governance_blocked: {exc}")
            self._commands_failed += 1
            return False

        # ── Step 2: Execute (with progress heartbeat for long-running commands) ──
        _log.info("executing %s (%s)", capability_id, command_id, extra={**log_ctx, "operation": "execute"})
        start = time.monotonic()
        result: Optional[Dict[str, Any]] = None
        execution_error: Optional[str] = None

        def _run_execution() -> None:
            nonlocal result, execution_error
            try:
                result = execute_command(capability_id, arguments)
            except Exception as exc:
                execution_error = str(exc)

        exec_thread = threading.Thread(target=_run_execution, daemon=True)
        exec_thread.start()
        heartbeat_interval_s = 10.0

        while exec_thread.is_alive():
            exec_thread.join(timeout=heartbeat_interval_s)
            if exec_thread.is_alive():
                elapsed_so_far = round(time.monotonic() - start, 1)
                _log.info(
                    "still running %s (%s) — %.1fs elapsed",
                    capability_id, command_id, elapsed_so_far,
                    extra={**log_ctx, "operation": "heartbeat", "elapsed": elapsed_so_far},
                )
                try:
                    self.heartbeat()
                except Exception:
                    pass

        elapsed = round(time.monotonic() - start, 3)
        if execution_error:
            result = {"status": "error", "error": execution_error}
        elif result is None:
            result = {"status": "error", "error": "execution returned no result"}
        status = str(result.get("status") or "error")

        _log.info(
            "executed %s → %s in %.3fs",
            capability_id, status, elapsed,
            extra={**log_ctx, "operation": "complete", "elapsed": elapsed, "status": status},
        )

        # ── Step 3: Complete ──
        self.complete_command(
            command_id,
            status="completed" if status == "completed" else "failed",
            result_payload=result,
            error=result.get("error") if status != "completed" else None,
        )

        if status == "completed":
            self._commands_executed += 1
        else:
            self._commands_failed += 1
        return status == "completed"

    def _poll_cycle(self) -> int:
        """One poll-claim-execute-complete cycle. Returns number of commands processed."""
        try:
            commands = self.claim_commands()
        except CommandWorkerError as exc:
            _log.warning("claim failed: %s", exc)
            return 0

        if not commands:
            return 0

        _log.info("claimed %d command(s)", len(commands))
        processed = 0
        for cmd in commands:
            try:
                if self._process_command(cmd):
                    processed += 1
            except Exception as exc:
                command_id = str(cmd.get("id") or "unknown")
                _log.error("unhandled error processing command %s: %s", command_id, exc,
                           extra={"command_id": command_id})
                try:
                    self.complete_command(command_id, status="failed", error=str(exc))
                except Exception:
                    pass
                self._commands_failed += 1
        return processed

    def run(self) -> None:
        """Main poll loop with graceful shutdown."""
        self._running = True
        _log.info(
            "command-worker started profile=%s poll=%.1fs max_cmds=%d lease=%ds",
            self.profile_id, self.poll_interval, self.max_commands, self.lease_seconds,
            extra={"profile_id": self.profile_id, "operation": "startup"},
        )

        last_heartbeat = time.monotonic()
        heartbeat_interval = max(30, self.lease_seconds / 3)

        while self._running:
            try:
                self._poll_cycle()
            except Exception as exc:
                _log.error("poll cycle error: %s", exc)
                time.sleep(self.poll_interval)
                continue

            # Heartbeat
            now = time.monotonic()
            if now - last_heartbeat > heartbeat_interval:
                self.heartbeat()
                last_heartbeat = now

            time.sleep(self.poll_interval)

        _log.info(
            "command-worker shutting down — executed=%d failed=%d",
            self._commands_executed, self._commands_failed,
            extra={"profile_id": self.profile_id, "operation": "shutdown",
                   "executed": self._commands_executed, "failed": self._commands_failed},
        )

    def shutdown(self, _signum: Optional[int] = None, _frame: Any = None) -> None:
        """Signal handler — gracefully stop the poll loop."""
        _log.info("received shutdown signal", extra={"profile_id": self.profile_id, "operation": "shutdown"})
        self._running = False


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Empyralis Self-Hosted Command Worker — drains the Main Agent command queue",
    )
    parser.add_argument("--cloud-url", default=_env("EMPYRALIS_CLOUD_URL"),
                        help="Empyralis cloud API base URL (env: EMPYRALIS_CLOUD_URL)")
    parser.add_argument("--profile-id", default=_env("EMPYRALIS_RUNTIME_PROFILE_ID"),
                        help="Self-hosted runtime profile ID (env: EMPYRALIS_RUNTIME_PROFILE_ID)")
    parser.add_argument("--session-token", default=_env("EMPYRALIS_NODE_SESSION_TOKEN"),
                        help="Node session token from enrollment (env: EMPYRALIS_NODE_SESSION_TOKEN)")
    parser.add_argument("--poll-interval", type=float, default=float(_env("EMPYRALIS_POLL_INTERVAL") or 2.0),
                        help="Seconds between poll cycles (default: 2.0)")
    parser.add_argument("--max-commands", type=int, default=int(_env("EMPYRALIS_MAX_COMMANDS_PER_POLL") or 5),
                        help="Max commands per claim (default: 5)")
    parser.add_argument("--lease-seconds", type=int, default=int(_env("EMPYRALIS_LEASE_SECONDS") or 120),
                        help="Command lease duration in seconds (default: 120)")

    args = parser.parse_args()

    if not args.cloud_url:
        _log.error("EMPYRALIS_CLOUD_URL is required")
        sys.exit(1)
    if not args.profile_id:
        _log.error("EMPYRALIS_RUNTIME_PROFILE_ID is required")
        sys.exit(1)
    if not args.session_token:
        _log.error("EMPYRALIS_NODE_SESSION_TOKEN is required")
        sys.exit(1)

    worker = SelfHostedCommandWorker(
        cloud_url=args.cloud_url,
        runtime_profile_id=args.profile_id,
        node_session_token=args.session_token,
        poll_interval=args.poll_interval,
        max_commands_per_poll=args.max_commands,
        lease_seconds=args.lease_seconds,
    )

    # Graceful shutdown
    signal.signal(signal.SIGTERM, worker.shutdown)
    signal.signal(signal.SIGINT, worker.shutdown)

    worker.run()


if __name__ == "__main__":
    main()
