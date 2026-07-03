#!/usr/bin/env python3
"""Minimal Empyralis Supervisor for headless Linux.

Handles shell.execute and filesystem.read_write capabilities.
Listens on port 7788 with HMAC-signed request verification.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from wsgiref.simple_server import make_server

SIGNATURE_VERSION = "v2"
MAX_REQUEST_FUTURE_SECONDS = 60
MAX_REQUEST_PAST_GRACE_SECONDS = 30
VERSION = "0.1.0-minimal"

# ── Hard blocks (mirror Rust supervisor) ──
HARD_BLOCKED_COMMAND_PATTERNS = [
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

HARD_PROTECTED_PATH_MARKERS = [
    "/.empyralis/state/vault",
    "/.empyralis/state",
    "/.ssh",
    "/.gnupg",
    "/etc/empyralis",
    "/var/lib/empyralis/agent-computer",
    "/.orion-stack",
]

BLOCKED_COMMANDS = {
    "rm", "mv", "cp", "chmod", "chown", "mkdir", "touch", "tee", "echo",
    "python", "python3", "node", "bash", "zsh", "sh", "kill", "xargs",
    "perl", "ruby", "git", "curl", "wget", "scp", "rsync",
}

ALLOWED_READ_COMMANDS = {
    "ls", "pwd", "find", "head", "tail", "cat", "wc", "stat", "file",
    "du", "tree", "rg", "grep", "readlink", "dirname", "basename",
    "which", "whoami", "uname", "hostname", "df", "free", "ps", "top",
    "uptime", "date", "env", "printenv", "id", "groups", "ulimit",
    "lscpu", "lsblk", "lspci", "lsusb", "ip", "ifconfig", "netstat",
    "ss", "ping", "nslookup", "dig", "nmap", "nc", "telnet",
    "systemctl", "journalctl", "pm2", "docker", "cargo", "rustc",
    "npm", "npx", "yarn", "pip", "pip3", "apt", "apt-get", "dpkg",
    "snap", "flatpak",
}


def canonical_json(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        parts = [canonical_json(v) for v in value]
        return "[" + ",".join(parts) + "]"
    if isinstance(value, dict):
        keys = sorted(value.keys())
        parts = [f"{json.dumps(k)}:{canonical_json(value[k])}" for k in keys]
        return "{" + ",".join(parts) + "}"
    return json.dumps(value)


def sign_payload(secret: bytes, payload_str: str) -> str:
    mac = hmac.new(secret, payload_str.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def verify_signature(secret: bytes, sign_str: str, signature: str) -> bool:
    expected = sign_payload(secret, sign_str)
    provided = bytes.fromhex(signature) if all(c in "0123456789abcdefABCDEF" for c in signature) else b""
    return hmac.compare_digest(expected.encode() if isinstance(expected, str) else expected, provided if isinstance(provided, bytes) else provided.encode())


class NonceRegistry:
    def __init__(self):
        self._nonces: dict[str, datetime] = {}

    def check_and_consume(self, namespace: str, nonce: str, expires_at: datetime) -> bool:
        key = f"{namespace}:{nonce}"
        now = datetime.now(timezone.utc)
        # Clean expired
        expired = [k for k, v in self._nonces.items() if now - v > timedelta(seconds=MAX_REQUEST_PAST_GRACE_SECONDS)]
        for k in expired:
            del self._nonces[k]
        if key in self._nonces:
            return False
        self._nonces[key] = expires_at
        return True


nonce_registry = NonceRegistry()


def hard_blocked_command(command: str) -> bool:
    compact = " ".join(command.split()).lower()
    if not compact:
        return False
    normalized = " ".join(compact.split())
    for pattern in HARD_BLOCKED_COMMAND_PATTERNS:
        if pattern in normalized:
            return True
    return False


def command_touches_protected_path(command: str) -> bool:
    for token in shlex.split(command):
        cleaned = token.strip('"\'').lower()
        for marker in HARD_PROTECTED_PATH_MARKERS:
            if marker in cleaned:
                return True
    return False


def execute_shell(arguments: dict, trusted: bool = False, allowed_roots: list[str] | None = None) -> dict:
    command = str(arguments.get("command", "")).strip()
    if not command:
        return {"success": False, "error": "command is required"}

    if hard_blocked_command(command):
        return {"success": False, "error": "shell.execute command is permanently blocked"}

    if command_touches_protected_path(command):
        return {"success": False, "error": "shell.execute path is permanently protected"}

    if not trusted:
        if not safe_shell_command(command):
            return {"success": False, "error": "shell.execute command is not allowed"}

    try:
        result = subprocess.run(
            ["/bin/bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=os.getcwd(),
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            return {
                "success": False,
                "error": stderr or f"command failed with exit code {result.returncode}",
            }
        return {
            "success": True,
            "result": {
                "command": command,
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
            },
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "shell command timed out after 120s"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def safe_shell_command(command: str) -> bool:
    compact = " ".join(command.split()).lower()
    if not compact:
        return False
    # Block dangerous operators
    for blocked in ["&&", "||", ";", "|", ">", "<", "$(", "`"]:
        if blocked in compact:
            return False
    tokens = compact.split()
    first = tokens[0] if tokens else ""
    if first in BLOCKED_COMMANDS:
        return False
    if first in ALLOWED_READ_COMMANDS:
        return True
    # Allow sed with -n flag
    if first == "sed" and len(tokens) > 1 and tokens[1] == "-n":
        return True
    return False


def execute_filesystem_read_write(arguments: dict, trusted: bool = False, allowed_paths: list[str] | None = None) -> dict:
    """Handle filesystem.read_write capability."""
    action = str(arguments.get("action", "")).strip()
    path_str = str(arguments.get("path", "")).strip()

    if not action:
        return {"success": False, "error": "action is required (read, write, list, exists, delete, mkdir)"}

    if not path_str and action not in ("list",):
        return {"success": False, "error": "path is required"}

    p = Path(os.path.expanduser(path_str)).resolve() if path_str else Path.cwd()

    try:
        if action == "read":
            if not p.is_file():
                return {"success": False, "error": f"not a file: {path_str}"}
            content = p.read_text(encoding="utf-8")
            return {"success": True, "result": {"path": str(p), "content": content, "size": len(content)}}

        elif action == "write":
            content = str(arguments.get("content", ""))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"success": True, "result": {"path": str(p), "size": len(content), "written": True}}

        elif action == "list":
            directory = p if p.is_dir() else p.parent
            if not directory.is_dir():
                return {"success": False, "error": f"not a directory: {directory}"}
            entries = []
            for entry in sorted(directory.iterdir()):
                stat = entry.stat()
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "is_dir": entry.is_dir(),
                    "is_file": entry.is_file(),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
            return {"success": True, "result": {"path": str(directory), "entries": entries, "count": len(entries)}}

        elif action == "exists":
            exists = p.exists()
            return {"success": True, "result": {"path": str(p), "exists": exists, "is_file": p.is_file(), "is_dir": p.is_dir()}}

        elif action == "delete":
            if not p.exists():
                return {"success": False, "error": f"path does not exist: {path_str}"}
            if p.is_dir():
                import shutil
                shutil.rmtree(p)
            else:
                p.unlink()
            return {"success": True, "result": {"path": str(p), "deleted": True}}

        elif action == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
            return {"success": True, "result": {"path": str(p), "created": True}}

        else:
            return {"success": False, "error": f"unsupported filesystem action: {action}"}

    except PermissionError as e:
        return {"success": False, "error": f"permission denied: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_execute(body: dict) -> dict:
    secret = os.environ.get("EMPYRALIS_SUPERVISOR_SECRET", "")
    if not secret:
        return {"success": False, "error": "EMPYRALIS_SUPERVISOR_SECRET is not configured on supervisor"}

    # Verify request
    request_id = str(body.get("request_id", ""))
    expires_at_str = str(body.get("expires_at", ""))
    nonce = str(body.get("nonce", ""))
    signature = str(body.get("signature", ""))

    try:
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
    except ValueError:
        return {"success": False, "error": "invalid expires_at"}

    # Check expiry
    now = datetime.now(timezone.utc)
    if now - expires_at > timedelta(seconds=MAX_REQUEST_PAST_GRACE_SECONDS):
        return {"success": False, "error": "request expired"}
    if expires_at - now > timedelta(seconds=MAX_REQUEST_FUTURE_SECONDS):
        return {"success": False, "error": "request expires_at is too far in the future"}

    # Verify nonce
    if not nonce_registry.check_and_consume("execute", nonce, expires_at):
        return {"success": False, "error": "request nonce already used"}

    # Build canonical payload for signature verification
    canonical_body = {
        "type": "execute",
        "version": SIGNATURE_VERSION,
        "request_id": body.get("request_id", ""),
        "capability_id": body.get("capability_id", ""),
        "run_id": body.get("run_id", ""),
        "trace_id": body.get("trace_id", ""),
        "workspace_id": body.get("workspace_id", ""),
        "arguments": body.get("arguments", {}),
        "runtime_access_mode": str(body.get("runtime_access_mode") or ""),
        "empyralis_approved": bool(body.get("empyralis_approved", False)),
        "agent_scope": str(body.get("agent_scope") or ""),
        "policy": body.get("policy"),
        "nonce": nonce,
        "expires_at": expires_at_str,
    }

    sign_str = canonical_json(canonical_body)
    if not verify_signature(secret.encode("utf-8"), sign_str, signature):
        return {"success": False, "error": "signature mismatch"}

    # Execute capability
    capability_id = str(body.get("capability_id", ""))
    arguments = body.get("arguments", {})

    # Resolve policy
    policy = body.get("policy") or {}
    mode = str(policy.get("mode") or body.get("runtime_access_mode") or "").strip().lower()
    agent_scope = str(policy.get("agent_scope") or body.get("agent_scope") or "").strip().lower()
    full_access = mode == "full_access" and agent_scope == "sage"
    if mode == "full_access" and not full_access:
        return {"success": False, "error": "full_access is available only to Sage Agent Computer requests"}
    if full_access and not policy.get("full_access_warning_acknowledged", False):
        return {"success": False, "error": "full_access requires the Sage setup warning acknowledgement"}

    allowed_paths = []
    if policy:
        allowed_paths = [p for p in (policy.get("allowed_paths") or []) + (policy.get("filesystem_scope") or []) if p and p != "*"]

    if capability_id == "shell.execute":
        result = execute_shell(arguments, trusted=full_access, allowed_roots=allowed_paths)
    elif capability_id == "filesystem.read_write":
        result = execute_filesystem_read_write(arguments, trusted=full_access, allowed_paths=allowed_paths)
    elif capability_id in (
        "screenshot.capture", "computer_control.ocr",
        "computer_control.move", "computer_control.click",
        "computer_control.type", "computer_control.key",
        "computer_control.clipboard_read", "computer_control.clipboard_write",
        "computer_control.list_windows", "computer_control.list_apps",
        "computer_control.launch", "computer_control.launch_app",
        "computer_control.notify", "computer_control.applescript",
        "computer_control.speak",
    ):
        return {"success": False, "error": f"{capability_id} is not available on headless Linux"}
    else:
        return {"success": False, "error": f"unsupported capability_id: {capability_id}"}

    return result


def handle_interrupt(body: dict) -> dict:
    """Handle interrupt requests (minimal — just validates signature)."""
    secret = os.environ.get("EMPYRALIS_SUPERVISOR_SECRET", "")
    if not secret:
        return {"success": False, "error": "EMPYRALIS_SUPERVISOR_SECRET is not configured"}

    nonce = str(body.get("nonce", ""))
    signature = str(body.get("signature", ""))
    expires_at_str = str(body.get("expires_at", ""))

    try:
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
    except ValueError:
        return {"success": False, "error": "invalid expires_at"}

    sign_str = (
        f"interrupt:{body.get('request_id', '')}:{body.get('run_id', '')}:"
        f"{body.get('target_request_id') or ''}:{body.get('workspace_id', '')}:"
        f"{nonce}:{expires_at_str}"
    )

    if not verify_signature(secret.encode("utf-8"), sign_str, signature):
        return {"success": False, "error": "signature mismatch"}

    if not nonce_registry.check_and_consume("interrupt", nonce, expires_at):
        return {"success": False, "error": "request nonce already used"}

    return {
        "success": True,
        "interrupted": False,
        "interrupt_count": 0,
        "run_id": str(body.get("run_id", "")),
        "target_request_id": body.get("target_request_id"),
    }


def application(environ, start_response):
    method = environ["REQUEST_METHOD"]
    path = environ["PATH_INFO"]

    # CORS
    headers = [
        ("Content-Type", "application/json"),
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "content-type"),
    ]

    if method == "OPTIONS":
        start_response("204 No Content", headers)
        return [b""]

    # Read body
    content_length = int(environ.get("CONTENT_LENGTH") or 0)
    body_raw = environ["wsgi.input"].read(content_length) if content_length else b""
    body = json.loads(body_raw) if body_raw else {}

    if path == "/health" and method == "GET":
        response = {"status": "ok", "version": VERSION}
        start_response("200 OK", headers)
        return [json.dumps(response).encode("utf-8")]

    if path == "/execute" and method == "POST":
        result = handle_execute(body)
        status = "401 Unauthorized" if result.get("error") in ("signature mismatch", "request expired", "request nonce already used") else "200 OK"
        response = {
            "success": result.get("success", False),
            "result": result.get("result"),
            "error": result.get("error"),
        }
        start_response(status, headers)
        return [json.dumps(response).encode("utf-8")]

    if path == "/interrupt" and method == "POST":
        result = handle_interrupt(body)
        status = "200 OK"
        start_response(status, headers)
        return [json.dumps(result).encode("utf-8")]

    start_response("404 Not Found", headers)
    return [json.dumps({"error": "not found"}).encode("utf-8")]


def main():
    port = int(os.environ.get("EMPYRALIS_SUPERVISOR_PORT", "7788"))
    host = os.environ.get("EMPYRALIS_SUPERVISOR_HOST", "127.0.0.1")

    secret = os.environ.get("EMPYRALIS_SUPERVISOR_SECRET", "")
    if not secret:
        print("WARNING: EMPYRALIS_SUPERVISOR_SECRET not set. Using random secret.", file=sys.stderr)
        os.environ["EMPYRALIS_SUPERVISOR_SECRET"] = uuid.uuid4().hex

    print(f"Empyralis Minimal Supervisor v{VERSION}", file=sys.stderr)
    print(f"Listening on {host}:{port}", file=sys.stderr)
    print(f"Capabilities: shell.execute, filesystem.read_write", file=sys.stderr)

    server = make_server(host, port, application)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)


if __name__ == "__main__":
    main()
