#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.cookiejar import MozillaCookieJar
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid
from typing import Any, Callable, Iterable
from urllib import error, parse, request


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server_modules.internal_tool_markup_service import detect_internal_tool_markup

VALID_SUITES = {"automated", "agent-computer", "gateway", "self-hosted", "cloud-computer", "full"}

SURFACE_MARKUP_BLOCKED_TOKENS = (
    "DSML",
    "tool_calls",
    "invoke name",
    "<||",
    "< | |",
    "<｜｜",
    "<|｜",
    "<｜|",
)

LAUNCH_GATE_REQUIRED_STEPS = (
    "backend_runtime_transparency_tests",
    "frontend_typecheck",
    "frontend_chat_transparency_e2e",
    "agent_computer_backend_tests",
    "agent_computer_gateway_typecheck",
    "agent_computer_gateway_phase_tests",
    "agent_computer_supervisor_tests",
    "cloud_computer_screenshot_artifact_replay",
    "gateway_doctor",
    "gateway_sage_chat_local_gateway_smoke",
    "gateway_default_safe_actions",
    "self_hosted_claim_complete_artifact_replay",
)

BACKEND_TESTS = [
    "server_modules/tests/test_agent_computer_permission_secret_model.py",
    "server_modules/tests/test_agent_computer_service_script.py",
    "server_modules/tests/test_transcript_events_service.py",
    "server_modules/tests/test_agent_turn.py",
    "server_modules/tests/test_gateway_browser_attach_policy.py",
    "server_modules/tests/test_gateway_execution_service.py",
    "server_modules/tests/test_gateway_health_service.py",
    "server_modules/tests/test_hardware_action_broker_service.py",
    "server_modules/tests/test_hardware_cloud_computer_adapter.py",
    "server_modules/tests/test_hardware_gateway_adapter.py",
    "server_modules/tests/test_hardware_self_hosted_node_adapter.py",
    "server_modules/tests/test_hardware_transcript_completion_certification.py",
    "server_modules/tests/test_hardware_transparency_certification_runner.py",
    "server_modules/tests/test_runtime_runs_api_chat_stream.py",
    "server_modules/tests/test_thread_transcript_event_append.py",
    "server_modules/tests/test_virtual_computer_ephemeral_session.py",
]

AGENT_COMPUTER_BACKEND_TESTS = [
    "server_modules/tests/test_agent_computer_permission_secret_model.py",
    "server_modules/tests/test_agent_computer_service_script.py",
    "server_modules/tests/test_gateway_browser_attach_policy.py",
    "server_modules/tests/test_gateway_execution_service.py",
    "server_modules/tests/test_gateway_health_service.py",
    "server_modules/tests/test_gateway_routes.py",
    "server_modules/tests/test_gateway_phase5_routes.py",
    "server_modules/tests/test_gateway_phase7_routes.py",
    "server_modules/tests/test_hardware_gateway_adapter.py",
    "server_modules/tests/test_runtime_attachment_service.py",
    "server_modules/tests/test_agent_computer_approval_decision_service.py",
]

AGENT_COMPUTER_GATEWAY_TESTS = [
    "dist/__tests__/service-inventory.test.js",
    "dist/__tests__/heartbeat-service-inventory.test.js",
    "dist/__tests__/system-service-mode.test.js",
]


@dataclass
class StepResult:
    name: str
    status: str
    command: list[str]
    duration_seconds: float
    summary: str
    log_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class CertificationError(RuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class CertClient:
    def __init__(
        self,
        *,
        base_url: str,
        runtime_key: str | None = None,
        bearer_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookie_jar = MozillaCookieJar()
        self.runtime_key = str(runtime_key or "").strip()
        self.bearer_token = str(bearer_token or "").strip()

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        opener = request.build_opener(request.HTTPCookieProcessor(self.cookie_jar))
        body = None
        request_headers = {"Accept": "application/json", **dict(headers or {})}
        if self.runtime_key:
            request_headers["X-API-Key"] = self.runtime_key
        if self.bearer_token:
            request_headers["Authorization"] = f"Bearer {self.bearer_token}"
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with opener.open(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CertificationError(
                f"HTTP {exc.code} for {method} {path}: {detail}",
                {"status_code": exc.code, "path": path, "detail": detail},
            ) from exc
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {"items": parsed}

    def stream_turn(self, payload: dict[str, Any], *, timeout: int) -> list[dict[str, Any]]:
        opener = request.build_opener(request.HTTPCookieProcessor(self.cookie_jar))
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if self.runtime_key:
            headers["X-API-Key"] = self.runtime_key
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        req = request.Request(
            f"{self.base_url}/api/turn",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        events: list[dict[str, Any]] = []
        with opener.open(req, timeout=timeout) as response:
            current_event = "message"
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    current_event = "message"
                    continue
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = line.split(":", 1)[1].strip()
                try:
                    payload_data = json.loads(data)
                except json.JSONDecodeError:
                    payload_data = {"raw": data}
                events.append({"event": current_event, "payload": payload_data})
                if current_event == "final":
                    break
        return events


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _command_text(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def _tail(text: str, *, line_count: int = 16) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[-line_count:])


def _write_log(log_dir: Path, name: str, content: str) -> str:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{name}.log"
    path.write_text(content, encoding="utf-8")
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _write_json_log(log_dir: Path, name: str, payload: dict[str, Any]) -> str:
    return _write_log(log_dir, name, json.dumps(payload, indent=2, sort_keys=True))


def _run_step(
    *,
    name: str,
    command: list[str],
    cwd: Path = ROOT_DIR,
    timeout: int | None = None,
    log_dir: Path,
) -> StepResult:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        duration = time.monotonic() - start
        output = completed.stdout or ""
        log_path = _write_log(log_dir, name, output)
        if completed.returncode == 0:
            return StepResult(name, "PASS", command, duration, "ok", log_path)
        return StepResult(name, "FAIL", command, duration, _tail(output) or f"exit code {completed.returncode}", log_path)
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        log_path = _write_log(log_dir, name, output)
        return StepResult(name, "FAIL", command, duration, f"timed out after {timeout}s", log_path)


def _live_step(
    *,
    name: str,
    command: list[str],
    log_dir: Path,
    fn: Callable[[], dict[str, Any]],
) -> StepResult:
    start = time.monotonic()
    try:
        details = fn()
        duration = time.monotonic() - start
        log_path = _write_json_log(log_dir, name, details)
        return StepResult(name, "PASS", command, duration, "ok", log_path, details)
    except CertificationError as exc:
        duration = time.monotonic() - start
        details = {"error": str(exc), **exc.details}
        log_path = _write_json_log(log_dir, name, details)
        return StepResult(name, "FAIL", command, duration, str(exc), log_path, details)
    except Exception as exc:
        duration = time.monotonic() - start
        details = {"error": str(exc)}
        log_path = _write_json_log(log_dir, name, details)
        return StepResult(name, "FAIL", command, duration, str(exc), log_path, details)


def _backend_url_healthy(backend_url: str, timeout: int) -> bool:
    try:
        with request.urlopen(f"{backend_url.rstrip('/')}/health", timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except Exception:
        return False


def _text(value: Any) -> str:
    return str(value or "").strip()


def _capture_command(command: list[str], *, cwd: Path = ROOT_DIR, timeout: int = 10) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout or ""
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return 124, output


def _git_metadata() -> dict[str, Any]:
    sha_code, sha = _capture_command(["git", "rev-parse", "HEAD"])
    branch_code, branch = _capture_command(["git", "branch", "--show-current"])
    status_code, status = _capture_command(["git", "status", "--porcelain"])
    return {
        "sha": sha.strip() if sha_code == 0 else "",
        "branch": branch.strip() if branch_code == 0 else "",
        "dirty": bool(status.strip()) if status_code == 0 else True,
        "status_porcelain": status.strip().splitlines() if status_code == 0 else ["git status failed"],
    }


def _redacted_launch_inputs(args: argparse.Namespace) -> dict[str, Any]:
    gateway_id = args.gateway_id or os.getenv("EMPYRALIS_CERT_GATEWAY_ID") or ""
    return {
        "backend_url": args.backend_url,
        "workspace_id": args.workspace_id or os.getenv("EMPYRALIS_CERT_WORKSPACE_ID") or "",
        "tenant_id": args.tenant_id or os.getenv("EMPYRALIS_CERT_TENANT_ID") or "",
        "gateway_id": gateway_id,
        "self_hosted_profile_id_present": bool(args.self_hosted_profile_id or os.getenv("EMPYRALIS_CERT_SELF_HOSTED_PROFILE_ID")),
        "self_hosted_node_token_present": bool(args.self_hosted_node_token or os.getenv("EMPYRALIS_CERT_SELF_HOSTED_NODE_TOKEN")),
        "self_hosted_node_id_present": bool(args.self_hosted_node_id or os.getenv("EMPYRALIS_CERT_SELF_HOSTED_NODE_ID")),
        "auth_method": "email_password" if (args.email or os.getenv("EMPYRALIS_CERT_EMAIL")) and (args.password or os.getenv("EMPYRALIS_CERT_PASSWORD")) else ("bearer_token" if (args.bearer_token or os.getenv("EMPYRALIS_CERT_BEARER_TOKEN")) else ("runtime_key" if (args.runtime_key or os.getenv("EMPYRALIS_CERT_RUNTIME_KEY")) else "")),
    }


def _assert_no_internal_markup_surface(value: Any, *, label: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    if detect_internal_tool_markup(serialized):
        raise CertificationError(f"{label} leaked internal tool markup")
    for token in SURFACE_MARKUP_BLOCKED_TOKENS:
        if token in serialized:
            raise CertificationError(f"{label} leaked blocked token: {token}")


def _read_first(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        token = _text(payload.get(key))
        if token:
            return token
    return ""


def _assistant_turns(thread_payload: dict[str, Any]) -> list[dict[str, Any]]:
    direct_turns = [item for item in list(thread_payload.get("turns") or []) if isinstance(item, dict)]
    nested_turns: list[dict[str, Any]] = []
    for item in list(thread_payload.get("items") or []):
        if not isinstance(item, dict):
            continue
        nested_turns.extend([turn for turn in list(item.get("turns") or []) if isinstance(turn, dict)])
    return [turn for turn in [*direct_turns, *nested_turns] if _text(turn.get("role")) == "assistant"]


def _transcript_payloads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item.get("payload") for item in events if isinstance(item.get("payload"), dict)]


def _assert_safe_transcript_events(events: list[dict[str, Any]]) -> None:
    if not events:
        raise CertificationError("assistant turn did not persist metadata.transcript_events")
    _assert_no_internal_markup_surface(events, label="transcript_events")
    serialized = json.dumps(events, sort_keys=True)
    for token in ("args_preview", "arguments", "raw_output", "prompt", "trace_id", "policy", "provider_id"):
        if token in serialized:
            raise CertificationError(f"transcript_events leaked blocked token: {token}")
    for item in events:
        if _text(item.get("event")) not in {"trace", "step"}:
            raise CertificationError(f"unexpected transcript event name: {_text(item.get('event'))}")
        if item.get("schema_version") != 1:
            raise CertificationError("transcript event schema_version must be 1")


def _assert_transcript_contains(
    events: list[dict[str, Any]],
    *,
    event_type: str,
    runtime_target: str | None = None,
    status: str | None = None,
) -> None:
    payloads = _transcript_payloads(events)
    for payload in payloads:
        if _text(payload.get("event_type")) != event_type:
            continue
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if runtime_target and _text(data.get("runtime_target")) != runtime_target:
            continue
        if status and _text(data.get("status")) != status:
            continue
        return
    raise CertificationError(
        "expected transcript proof event was not found",
        {"event_type": event_type, "runtime_target": runtime_target, "status": status, "events": events},
    )


def _authenticate(args: argparse.Namespace, *, require_existing_workspace: bool) -> tuple[CertClient, str, str]:
    client = CertClient(
        base_url=args.backend_url,
        runtime_key=args.runtime_key or os.getenv("EMPYRALIS_CERT_RUNTIME_KEY") or "",
        bearer_token=args.bearer_token or os.getenv("EMPYRALIS_CERT_BEARER_TOKEN") or "",
    )
    email = args.email or os.getenv("EMPYRALIS_CERT_EMAIL") or ""
    password = args.password or os.getenv("EMPYRALIS_CERT_PASSWORD") or ""
    workspace_id = args.workspace_id or os.getenv("EMPYRALIS_CERT_WORKSPACE_ID") or ""
    tenant_id = args.tenant_id or os.getenv("EMPYRALIS_CERT_TENANT_ID") or ""
    if email and password:
        login = client.request(
            "/api/v1/auth/login",
            method="POST",
            payload={
                "email": email,
                "password": password,
                "workspace_id": workspace_id or None,
                "channel": "web",
            },
            timeout=args.timeout,
        )
        workspace_id = workspace_id or _read_first(login, "current_workspace_id", "default_workspace_id", "workspace_id")
        tenant_id = tenant_id or _read_first(login, "current_tenant_id", "default_tenant_id", "tenant_id")
    elif client.bearer_token:
        profile = client.request("/api/v1/auth/me", timeout=args.timeout)
        workspace_id = workspace_id or _read_first(profile, "current_workspace_id", "default_workspace_id", "workspace_id")
        tenant_id = tenant_id or _read_first(profile, "current_tenant_id", "default_tenant_id", "tenant_id")
    elif require_existing_workspace:
        raise CertificationError(
            "live hardware suites require owner authentication for the workspace; pass --email/--password or --bearer-token",
            {"workspace_id": workspace_id or None},
        )
    else:
        signup = client.request(
            "/api/v1/auth/signup",
            method="POST",
            payload={
                "email": f"codex.cert.{int(time.time())}.{uuid.uuid4().hex[:8]}@local.test",
                "password": f"CodexCert{uuid.uuid4().hex[:8]}!",
                "name": "Hardware Transparency Certification",
            },
            timeout=args.timeout,
        )
        workspace_id = workspace_id or _read_first(signup, "default_workspace_id", "current_workspace_id", "workspace_id")
        tenant_id = tenant_id or _read_first(signup, "default_tenant_id", "current_tenant_id", "tenant_id")
    if not workspace_id:
        raise CertificationError("workspace_id could not be resolved; pass --workspace-id")
    if not tenant_id:
        tenant_id = workspace_id
    return client, workspace_id, tenant_id


def _seed_chat_turn(
    client: CertClient,
    *,
    workspace_id: str,
    tenant_id: str,
    message: str,
    timeout: int,
    poll_attempts: int,
    poll_interval: float,
) -> dict[str, Any]:
    request_id = f"cert-chat-{uuid.uuid4().hex[:12]}"
    stream_events = client.stream_turn(
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "session_id": "certification",
            "thread_id": "certification",
            "channel": "web",
            "actor": {"type": "user", "id": "certification", "display_name": "Certification"},
            "message": message,
            "execution_mode": "sync",
            "response_mode": "stream",
            "context_hints": {"metadata": {"request_id": request_id}},
            "client_request_id": request_id,
        },
        timeout=timeout,
    )
    if "final" not in {_text(item.get("event")) for item in stream_events}:
        raise CertificationError("seed chat turn did not emit final", {"request_id": request_id})
    for _ in range(poll_attempts):
        turn, transcript_events = _transcript_events_for_request(
            client,
            workspace_id=workspace_id,
            request_id=request_id,
            timeout=timeout,
        )
        if turn:
            thread_id = _text(turn.get("thread_id"))
            if not thread_id:
                raise CertificationError("assistant turn did not include thread_id", {"request_id": request_id})
            if transcript_events:
                _assert_safe_transcript_events(transcript_events)
            return {
                "request_id": request_id,
                "thread_id": thread_id,
                "stream_events": stream_events,
                "transcript_events": transcript_events,
            }
        time.sleep(poll_interval)
    raise CertificationError("seed assistant turn was not replayable from thread history", {"request_id": request_id})


def _transcript_events_for_request(
    client: CertClient,
    *,
    workspace_id: str,
    request_id: str,
    timeout: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    query = parse.urlencode({"workspace_id": workspace_id, "include_turns": "true"})
    threads = client.request(f"/api/threads?{query}", timeout=timeout)
    for turn in _assistant_turns(threads):
        metadata = turn.get("metadata") if isinstance(turn.get("metadata"), dict) else {}
        if _text(metadata.get("request_id") or turn.get("request_id")) == request_id:
            events = [item for item in list(metadata.get("transcript_events") or []) if isinstance(item, dict)]
            return turn, events
    return None, []


def _poll_transcript(
    client: CertClient,
    *,
    workspace_id: str,
    request_id: str,
    timeout: int,
    poll_attempts: int,
    poll_interval: float,
) -> list[dict[str, Any]]:
    for _ in range(poll_attempts):
        _, events = _transcript_events_for_request(
            client,
            workspace_id=workspace_id,
            request_id=request_id,
            timeout=timeout,
        )
        if events:
            return events
        time.sleep(poll_interval)
    return []


def _hardware_execute(
    client: CertClient,
    *,
    workspace_id: str,
    thread_id: str,
    request_id: str,
    runtime_target: str,
    action_id: str,
    runtime_access_mode: str,
    timeout: int,
    arguments: dict[str, Any] | None = None,
    capability_id: str | None = None,
    gateway_id: str | None = None,
    node_id: str | None = None,
    session_id: str | None = None,
    cost_metadata: dict[str, Any] | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    return client.request(
        "/api/runtime/hardware/actions/execute",
        method="POST",
        payload={
            "workspace_id": workspace_id,
            "action_id": action_id,
            "capability_id": capability_id,
            "arguments": dict(arguments or {}),
            "runtime_target": runtime_target,
            "runtime_access_mode": runtime_access_mode,
            "gateway_id": gateway_id,
            "node_id": node_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "run_id": run_id or f"cert-run-{uuid.uuid4().hex[:12]}",
            "trace_id": trace_id or f"trace-cert-{uuid.uuid4().hex[:12]}",
            "session_id": session_id or f"hrs-cert-{uuid.uuid4().hex[:12]}",
            "cost_metadata": dict(cost_metadata or {}),
        },
        timeout=timeout,
    )


def _hardware_stop(
    client: CertClient,
    *,
    workspace_id: str,
    thread_id: str,
    request_id: str,
    runtime_target: str,
    run_id: str,
    session_id: str,
    timeout: int,
    gateway_id: str | None = None,
    node_id: str | None = None,
    target_request_id: str | None = None,
) -> dict[str, Any]:
    return client.request(
        "/api/runtime/hardware/actions/stop",
        method="POST",
        payload={
            "workspace_id": workspace_id,
            "runtime_target": runtime_target,
            "gateway_id": gateway_id,
            "node_id": node_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "target_request_id": target_request_id,
            "run_id": run_id,
            "trace_id": f"trace-stop-{uuid.uuid4().hex[:12]}",
            "session_id": session_id,
            "reason": "certification_stop",
        },
        timeout=timeout,
    )


def _assert_status(payload: dict[str, Any], expected: set[str], *, label: str) -> None:
    status = _text(payload.get("status"))
    if status not in expected:
        raise CertificationError(f"{label} returned unexpected status: {status or payload}", payload)


def _assert_runtime_session(
    payload: dict[str, Any],
    *,
    runtime_target: str,
    request_id: str | None = None,
    access_mode: str | None = None,
    states: set[str] | None = None,
    device_or_node_id: str | None = None,
) -> dict[str, Any]:
    session = payload.get("runtime_session") if isinstance(payload.get("runtime_session"), dict) else {}
    if not session:
        raise CertificationError("hardware action did not return runtime_session", payload)
    target = _text(session.get("canonical_runtime_target") or session.get("runtime_target"))
    if target != runtime_target:
        raise CertificationError(
            f"runtime_session target mismatch: expected {runtime_target}, got {target}",
            {"runtime_session": session, "payload_status": payload.get("status")},
        )
    if request_id and _text(session.get("request_id")) != request_id:
        raise CertificationError(
            "runtime_session request_id did not correlate to the assistant turn",
            {"expected_request_id": request_id, "runtime_session": session},
        )
    if access_mode and _text(session.get("runtime_access_mode")) != access_mode:
        raise CertificationError(
            "runtime_session access mode mismatch",
            {"expected_access_mode": access_mode, "runtime_session": session},
        )
    if states and _text(session.get("state")) not in states:
        raise CertificationError(
            f"runtime_session state mismatch: expected one of {sorted(states)}, got {_text(session.get('state'))}",
            {"runtime_session": session},
        )
    if device_or_node_id:
        ids = {
            _text(session.get("gateway_id")),
            _text(session.get("device_id")),
            _text(session.get("runtime_node_id")),
            _text(session.get("runtime_profile_id")),
            _text(session.get("cloud_computer_session_id")),
            _text(session.get("session_id")),
        }
        if device_or_node_id not in ids:
            raise CertificationError(
                "runtime_session did not include expected device/node/session id",
                {"expected_id": device_or_node_id, "runtime_session": session},
            )
    return session


def _cert_ids(*payloads: dict[str, Any]) -> dict[str, Any]:
    ids: dict[str, Any] = {}
    artifact_ids: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        session = payload.get("runtime_session") if isinstance(payload.get("runtime_session"), dict) else {}
        execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
        for key in ("trace_id", "request_id", "run_id", "approval_id"):
            token = _text(payload.get(key) or session.get(key) or execution.get(key))
            if token and key not in ids:
                ids[key] = token
        session_id = _text(session.get("session_id") or payload.get("session_id"))
        if session_id and "runtime_session_id" not in ids:
            ids["runtime_session_id"] = session_id
        for key in ("gateway_id", "device_id", "runtime_node_id", "runtime_profile_id", "cloud_computer_session_id", "self_hosted_command_id"):
            token = _text(session.get(key) or execution.get(key))
            if token and key not in ids:
                ids[key] = token
        for artifact_id in list(payload.get("artifacts") or session.get("artifacts") or []):
            token = _text(artifact_id)
            if token and token not in artifact_ids:
                artifact_ids.append(token)
    if artifact_ids:
        ids["artifact_ids"] = artifact_ids
    return ids


def _expected_echo_stdout(command: str) -> str:
    stripped = str(command or "").strip()
    if stripped.startswith("echo "):
        return stripped.removeprefix("echo ").strip().strip("\"'")
    return ""


def _missing_input_step(*, suite: str, name: str, missing: list[str], env: dict[str, str]) -> StepResult:
    return StepResult(
        name,
        "FAIL",
        ["scripts/empyralis_hardware_transparency_certification.py", "--suite", suite],
        0.0,
        f"{suite} suite requires {', '.join('--' + item for item in missing)}",
        details={
            "missing_flags": [f"--{item}" for item in missing],
            "env_alternatives": env,
            "setup": "Provide real enrolled hardware identifiers. Missing live hardware inputs are certification failures, not passes.",
        },
    )


def launch_gate_preflight_steps(args: argparse.Namespace, log_dir: Path) -> list[StepResult]:
    if not args.launch_gate:
        return []
    command = ["scripts/empyralis_hardware_transparency_certification.py", "--suite", args.suite, "--launch-gate"]

    def require_full_suite() -> dict[str, Any]:
        if args.suite != "full":
            raise CertificationError("--launch-gate requires --suite full", {"suite": args.suite})
        return {"suite": args.suite}

    def require_live_smoke() -> dict[str, Any]:
        if not args.require_live_smoke:
            raise CertificationError("--launch-gate requires --require-live-smoke")
        return {"require_live_smoke": True}

    def require_clean_git() -> dict[str, Any]:
        metadata = _git_metadata()
        if metadata["dirty"]:
            raise CertificationError("launch gate requires a clean git worktree", metadata)
        return metadata

    def require_live_inputs() -> dict[str, Any]:
        inputs = _redacted_launch_inputs(args)
        missing: list[str] = []
        if not inputs["auth_method"]:
            missing.append("owner authentication (--email/--password or --bearer-token)")
        if not inputs["gateway_id"]:
            missing.append("--gateway-id")
        if not inputs["self_hosted_profile_id_present"]:
            missing.append("--self-hosted-profile-id")
        if not inputs["self_hosted_node_token_present"]:
            missing.append("--self-hosted-node-token")
        if missing:
            raise CertificationError("launch gate is missing required live inputs", {"missing": missing, "inputs": inputs})
        return inputs

    return [
        _live_step(name="launch_gate_requires_full_suite", command=command, log_dir=log_dir, fn=require_full_suite),
        _live_step(name="launch_gate_requires_live_smoke", command=command, log_dir=log_dir, fn=require_live_smoke),
        _live_step(name="launch_gate_git_clean", command=command, log_dir=log_dir, fn=require_clean_git),
        _live_step(name="launch_gate_required_live_inputs", command=command, log_dir=log_dir, fn=require_live_inputs),
        _run_step(
            name="launch_gate_backend_health",
            command=["bash", "-lc", f"curl -fsS {shlex.quote(args.backend_url.rstrip('/') + '/health')}"],
            timeout=args.health_timeout,
            log_dir=log_dir,
        ),
        _run_step(
            name="launch_gate_agent_computer_status",
            command=["bash", "-lc", "./scripts/agent_computer.sh status"],
            timeout=30,
            log_dir=log_dir,
        ),
    ]


def automated_steps(args: argparse.Namespace, log_dir: Path) -> list[StepResult]:
    python = sys.executable or "python3"
    results = [
        _run_step(
            name="backend_runtime_transparency_tests",
            command=[python, "-m", "pytest", *BACKEND_TESTS, "-q"],
            timeout=args.backend_timeout,
            log_dir=log_dir,
        ),
        _run_step(
            name="frontend_typecheck",
            command=["npm", "run", "typecheck", "--prefix", "frontend"],
            timeout=args.frontend_timeout,
            log_dir=log_dir,
        ),
        _run_step(
            name="frontend_chat_transparency_e2e",
            command=["npm", "run", "test:e2e", "--prefix", "frontend", "--", "tests/e2e/chat-transparency-timeline.spec.ts"],
            timeout=args.e2e_timeout,
            log_dir=log_dir,
        ),
        _run_step(
            name="mobile_typecheck",
            command=["./node_modules/.bin/tsc", "--noEmit", "-p", "tsconfig.json"],
            cwd=ROOT_DIR / "mobile",
            timeout=args.mobile_timeout,
            log_dir=log_dir,
        ),
        _run_step(
            name="mobile_transcript_lint",
            command=[
                "npm",
                "run",
                "lint",
                "--",
                "--max-warnings=0",
                "src/lib/transcriptEvents.ts",
                "src/screens/ChatScreen.tsx",
                "app/(tabs)/_layout.tsx",
            ],
            cwd=ROOT_DIR / "mobile",
            timeout=args.mobile_timeout,
            log_dir=log_dir,
        ),
        _run_step(
            name="smoke_script_py_compile",
            command=[python, "-m", "py_compile", "scripts/empyralis_chat_transparency_live_smoke.py"],
            timeout=30,
            log_dir=log_dir,
        ),
        _run_step(
            name="hardware_certification_runner_py_compile",
            command=[python, "-m", "py_compile", "scripts/empyralis_hardware_transparency_certification.py"],
            timeout=30,
            log_dir=log_dir,
        ),
    ]
    if args.include_live_smoke or args.require_live_smoke:
        if _backend_url_healthy(args.backend_url, args.health_timeout):
            results.append(
                _run_step(
                    name="live_chat_hardware_transparency_smoke",
                    command=["scripts/empyralis_chat_transparency_live_smoke.py", "--backend-url", args.backend_url],
                    timeout=args.live_timeout,
                    log_dir=log_dir,
                )
            )
        else:
            results.append(
                StepResult(
                    "live_chat_hardware_transparency_smoke",
                    "FAIL" if args.require_live_smoke else "SKIP",
                    ["scripts/empyralis_chat_transparency_live_smoke.py", "--backend-url", args.backend_url],
                    0.0,
                    f"backend health check failed at {args.backend_url}/health",
                )
            )
    return results


def agent_computer_steps(args: argparse.Namespace, log_dir: Path) -> list[StepResult]:
    python = sys.executable or "python3"
    return [
        _run_step(
            name="agent_computer_script_syntax",
            command=["bash", "-n", "scripts/agent_computer.sh"],
            timeout=30,
            log_dir=log_dir,
        ),
        _run_step(
            name="agent_computer_linux_systemd_dry_run",
            command=[
                "bash",
                "-lc",
                "EMPYRALIS_AGENT_COMPUTER_TEST_UNAME=Linux "
                "EMPYRALIS_AGENT_COMPUTER_SERVICE_USER=agentuser "
                "bash scripts/agent_computer.sh service-install --system --dry-run",
            ],
            timeout=30,
            log_dir=log_dir,
        ),
        _run_step(
            name="agent_computer_backend_tests",
            command=[python, "-m", "pytest", *AGENT_COMPUTER_BACKEND_TESTS, "-q"],
            timeout=args.backend_timeout,
            log_dir=log_dir,
        ),
        _run_step(
            name="agent_computer_gateway_typecheck",
            command=["npm", "run", "typecheck", "--silent"],
            cwd=ROOT_DIR / "empyralis-gateway",
            timeout=args.frontend_timeout,
            log_dir=log_dir,
        ),
        _run_step(
            name="agent_computer_gateway_phase_tests",
            command=[
                "bash",
                "-lc",
                "npm run build --silent && node --test "
                + " ".join(shlex.quote(path) for path in AGENT_COMPUTER_GATEWAY_TESTS),
            ],
            cwd=ROOT_DIR / "empyralis-gateway",
            timeout=args.e2e_timeout,
            log_dir=log_dir,
        ),
        _run_step(
            name="agent_computer_supervisor_tests",
            command=["cargo", "test", "--manifest-path", "empyralis-supervisor/Cargo.toml"],
            timeout=args.backend_timeout,
            log_dir=log_dir,
        ),
    ]


def cloud_computer_steps(args: argparse.Namespace, log_dir: Path) -> list[StepResult]:
    client, workspace_id, tenant_id = _authenticate(args, require_existing_workspace=False)
    command = ["scripts/empyralis_hardware_transparency_certification.py", "--suite", "cloud-computer"]
    seed: dict[str, Any] = {}

    def seed_step() -> dict[str, Any]:
        nonlocal seed
        seed = _seed_chat_turn(
            client,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            message="Certification seed turn for cloud-computer hardware transparency. Reply with OK.",
            timeout=args.turn_timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        return {"workspace_id": workspace_id, "tenant_id": tenant_id, **seed}

    def screenshot_step() -> dict[str, Any]:
        result = _hardware_execute(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=seed["request_id"],
            runtime_target="empyralis_cloud_computer",
            action_id="screenshot.capture",
            runtime_access_mode="full_access",
            timeout=args.hardware_timeout,
            cost_metadata={"source": "cloud_computer_certification"},
        )
        _assert_status(result, {"completed"}, label="cloud computer screenshot")
        _assert_runtime_session(
            result,
            runtime_target="empyralis_cloud_computer",
            request_id=seed["request_id"],
            access_mode="full_access",
            states={"ready"},
        )
        events = _poll_transcript(
            client,
            workspace_id=workspace_id,
            request_id=seed["request_id"],
            timeout=args.timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        _assert_safe_transcript_events(events)
        _assert_transcript_contains(events, event_type="artifact.created")
        _assert_transcript_contains(events, event_type="tool.result", runtime_target="empyralis_cloud_computer", status="completed")
        return {"ids": _cert_ids(result), "result": result, "transcript_events": events}

    def stop_step() -> dict[str, Any]:
        session_id = f"hrs-cloud-stop-{uuid.uuid4().hex[:12]}"
        run_id = f"cert-cloud-stop-{uuid.uuid4().hex[:12]}"
        request_id = seed["request_id"]
        created = _hardware_execute(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=request_id,
            runtime_target="empyralis_cloud_computer",
            action_id="browser.open",
            runtime_access_mode="full_access",
            timeout=args.hardware_timeout,
            arguments={"url": "https://example.com"},
            session_id=session_id,
            run_id=run_id,
            cost_metadata={
                "source": "cloud_computer_stop_certification",
                "session_persistence": {"session_mode": "resumable", "auto_terminate_on_task_complete": False},
            },
        )
        _assert_status(created, {"completed"}, label="cloud computer resumable action")
        _assert_runtime_session(
            created,
            runtime_target="empyralis_cloud_computer",
            request_id=request_id,
            access_mode="full_access",
            states={"ready"},
            device_or_node_id=session_id,
        )
        stopped = _hardware_stop(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=request_id,
            runtime_target="empyralis_cloud_computer",
            run_id=run_id,
            session_id=session_id,
            timeout=args.hardware_timeout,
            target_request_id=request_id,
        )
        _assert_status(stopped, {"terminated"}, label="cloud computer stop")
        _assert_runtime_session(
            stopped,
            runtime_target="empyralis_cloud_computer",
            request_id=request_id,
            states={"terminated"},
            device_or_node_id=session_id,
        )
        events = _poll_transcript(
            client,
            workspace_id=workspace_id,
            request_id=request_id,
            timeout=args.timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        _assert_transcript_contains(events, event_type="tool.result", runtime_target="empyralis_cloud_computer", status="terminated")
        return {"ids": _cert_ids(created, stopped), "created": created, "stopped": stopped, "transcript_events": events}

    def quota_step() -> dict[str, Any]:
        request_id = f"cert-quota-{uuid.uuid4().hex[:12]}"
        run_id = f"cert-quota-run-{uuid.uuid4().hex[:12]}"
        first_session = f"hrs-quota-{uuid.uuid4().hex[:12]}"
        common_cost = {
            "source": "cloud_computer_quota_certification",
            "session_persistence": {"session_mode": "resumable", "auto_terminate_on_task_complete": False},
            "runtime_quota": {"provider_concurrency_limit": 1, "workspace_concurrency_limit": 1, "agent_concurrency_limit": 1},
        }
        first = _hardware_execute(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=request_id,
            runtime_target="empyralis_cloud_computer",
            action_id="browser.open",
            runtime_access_mode="full_access",
            timeout=args.hardware_timeout,
            arguments={"url": "https://example.com"},
            session_id=first_session,
            run_id=run_id,
            cost_metadata=common_cost,
        )
        _assert_status(first, {"completed"}, label="cloud computer quota setup")
        _assert_runtime_session(
            first,
            runtime_target="empyralis_cloud_computer",
            request_id=request_id,
            access_mode="full_access",
            states={"ready"},
            device_or_node_id=first_session,
        )
        second: dict[str, Any] = {}
        try:
            second = _hardware_execute(
                client,
                workspace_id=workspace_id,
                thread_id=seed["thread_id"],
                request_id=request_id,
                runtime_target="empyralis_cloud_computer",
                action_id="browser.open",
                runtime_access_mode="full_access",
                timeout=args.hardware_timeout,
                arguments={"url": "https://example.com"},
                session_id=f"hrs-quota-{uuid.uuid4().hex[:12]}",
                cost_metadata=common_cost,
            )
            _assert_status(second, {"failed", "degraded"}, label="cloud computer quota rejection")
            _assert_runtime_session(
                second,
                runtime_target="empyralis_cloud_computer",
                request_id=request_id,
                access_mode="full_access",
                states={"failed", "degraded"},
            )
        finally:
            stopped = _hardware_stop(
                client,
                workspace_id=workspace_id,
                thread_id=seed["thread_id"],
                request_id=request_id,
                runtime_target="empyralis_cloud_computer",
                run_id=run_id,
                session_id=first_session,
                timeout=args.hardware_timeout,
                target_request_id=request_id,
            )
        _assert_status(stopped, {"terminated"}, label="cloud computer quota cleanup")
        return {"ids": _cert_ids(first, second, stopped), "first": first, "second": second, "cleanup": stopped}

    return [
        _live_step(name="cloud_computer_seed_chat_turn", command=command, log_dir=log_dir, fn=seed_step),
        _live_step(name="cloud_computer_screenshot_artifact_replay", command=command, log_dir=log_dir, fn=screenshot_step),
        _live_step(name="cloud_computer_stop_terminate_replay", command=command, log_dir=log_dir, fn=stop_step),
        _live_step(name="cloud_computer_quota_failure_cleanup", command=command, log_dir=log_dir, fn=quota_step),
    ]


def gateway_steps(args: argparse.Namespace, log_dir: Path) -> list[StepResult]:
    gateway_id = args.gateway_id or os.getenv("EMPYRALIS_CERT_GATEWAY_ID") or ""
    if not gateway_id:
        return [
            _missing_input_step(
                suite="gateway",
                name="gateway_required_inputs",
                missing=["gateway-id"],
                env={"gateway-id": "EMPYRALIS_CERT_GATEWAY_ID"},
            )
        ]
    client, workspace_id, tenant_id = _authenticate(args, require_existing_workspace=True)
    command = ["scripts/empyralis_hardware_transparency_certification.py", "--suite", "gateway", "--gateway-id", gateway_id]
    seed: dict[str, Any] = {}

    def doctor_step() -> dict[str, Any]:
        return client.request(f"/api/gateway/registrations/{parse.quote(gateway_id)}/doctor?force_provider_probe=1", timeout=args.timeout)

    def seed_step() -> dict[str, Any]:
        nonlocal seed
        seed = _seed_chat_turn(
            client,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            message="Certification seed turn for gateway hardware transparency. Reply with OK.",
            timeout=args.turn_timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        return {"workspace_id": workspace_id, "tenant_id": tenant_id, "gateway_id": gateway_id, **seed}

    def execute_gateway_action(name: str, action_id: str, arguments: dict[str, Any], mode: str, expected: set[str]) -> dict[str, Any]:
        result = _hardware_execute(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=seed["request_id"],
            runtime_target="user_device_gateway",
            action_id=action_id,
            runtime_access_mode=mode,
            timeout=args.hardware_timeout,
            arguments=arguments,
            gateway_id=gateway_id,
        )
        _assert_status(result, expected, label=name)
        expected_states = {
            "completed": {"ready"},
            "waiting_approval": {"waiting_approval"},
            "offline": {"offline"},
            "failed": {"failed"},
            "degraded": {"degraded"},
        }
        states = set().union(*(expected_states.get(status, {status}) for status in expected))
        _assert_runtime_session(
            result,
            runtime_target="user_device_gateway",
            request_id=seed["request_id"],
            access_mode=mode,
            states=states,
            device_or_node_id=gateway_id,
        )
        events = _poll_transcript(
            client,
            workspace_id=workspace_id,
            request_id=seed["request_id"],
            timeout=args.timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        _assert_safe_transcript_events(events)
        return {"ids": _cert_ids(result), "result": result, "transcript_events": events}

    def safe_actions_step() -> dict[str, Any]:
        shell = execute_gateway_action("gateway safe shell", "shell.exec", {"command": args.gateway_safe_command}, "default_guarded", {"completed"})
        expected_stdout = _expected_echo_stdout(args.gateway_safe_command)
        if args.launch_gate and expected_stdout and expected_stdout not in json.dumps(shell["result"], sort_keys=True, default=str):
            raise CertificationError(
                "gateway safe shell result did not include expected stdout proof",
                {"expected_stdout": expected_stdout, "result": shell["result"]},
            )
        file_read = execute_gateway_action(
            "gateway file read/list",
            "file.read",
            {"path": args.gateway_file_path, "mode": "read"},
            "default_guarded",
            {"completed"},
        )
        screenshot = execute_gateway_action("gateway screenshot", "screenshot.capture", {}, "default_guarded", {"completed"})
        return {"shell": shell["result"], "file_read": file_read["result"], "screenshot": screenshot["result"]}

    def sage_chat_step() -> dict[str, Any]:
        chat = _seed_chat_turn(
            client,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            message=args.gateway_chat_message,
            timeout=args.turn_timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        _assert_no_internal_markup_surface(chat.get("stream_events") or [], label="gateway_sage_chat_stream")
        serialized = json.dumps(chat, sort_keys=True, default=str)
        for token in ("Running on your Mac", "Done", "local_gateway"):
            if token not in serialized:
                raise CertificationError(
                    "Sage chat local gateway smoke missed required proof token",
                    {"missing_token": token, "request_id": chat.get("request_id"), "thread_id": chat.get("thread_id")},
                )
        expected_stdout = _expected_echo_stdout(args.gateway_safe_command)
        if expected_stdout and expected_stdout not in serialized:
            raise CertificationError(
                "Sage chat local gateway smoke missed expected stdout proof",
                {"expected_stdout": expected_stdout, "request_id": chat.get("request_id"), "thread_id": chat.get("thread_id")},
            )
        return {"ids": _cert_ids({"request_id": chat.get("request_id")}), **chat}

    def approval_step(decision: str, remember: bool = False) -> dict[str, Any]:
        pending = execute_gateway_action(
            f"gateway approval {decision}",
            "shell.exec",
            {"command": args.gateway_risky_command},
            "default_guarded",
            {"waiting_approval"},
        )["result"]
        approval_id = _text((pending.get("approval") or {}).get("approval_id"))
        if not approval_id:
            raise CertificationError("gateway risky action did not return approval_id", pending)
        body: dict[str, Any] = {
            "decision": "approved" if decision == "approved" else "rejected",
            "note": f"certification {decision}",
            "timeout_seconds": args.hardware_timeout,
        }
        if remember:
            body["remember_for_seconds"] = 300
            body["remember_scope"] = {"mode": "session", "certification": True}
        resolved = client.request(
            f"/api/gateway/registrations/{parse.quote(gateway_id)}/approvals/{parse.quote(approval_id)}/resolve",
            method="POST",
            payload=body,
            timeout=args.hardware_timeout,
        )
        expected = {"executed", "completed"} if decision == "approved" else {"rejected", "terminated"}
        _assert_status(resolved, expected, label=f"gateway approval {decision}")
        if isinstance(resolved.get("runtime_session"), dict):
            _assert_runtime_session(
                resolved,
                runtime_target="user_device_gateway",
                request_id=seed["request_id"],
                states={"ready", "terminated"},
                device_or_node_id=gateway_id,
            )
        events = _poll_transcript(
            client,
            workspace_id=workspace_id,
            request_id=seed["request_id"],
            timeout=args.timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        _assert_transcript_contains(events, event_type="approval.resolved")
        return {"ids": _cert_ids(pending, resolved), "pending": pending, "resolved": resolved, "transcript_events": events}

    def autonomous_step() -> dict[str, Any]:
        payload = execute_gateway_action(
            "gateway autonomous risky",
            "shell.exec",
            {"command": args.gateway_risky_command},
            "full_access",
            {"completed"},
        )
        return payload

    def stop_step() -> dict[str, Any]:
        request_id = seed["request_id"]
        run_id = f"cert-gateway-stop-{uuid.uuid4().hex[:12]}"
        session_id = f"hrs-gateway-stop-{uuid.uuid4().hex[:12]}"

        def _long_action() -> dict[str, Any]:
            return _hardware_execute(
                client,
                workspace_id=workspace_id,
                thread_id=seed["thread_id"],
                request_id=request_id,
                runtime_target="user_device_gateway",
                action_id="shell.exec",
                runtime_access_mode="full_access",
                timeout=args.long_action_timeout,
                arguments={"command": args.gateway_long_command},
                gateway_id=gateway_id,
                run_id=run_id,
                session_id=session_id,
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_long_action)
            time.sleep(args.stop_delay)
            stopped = _hardware_stop(
                client,
                workspace_id=workspace_id,
                thread_id=seed["thread_id"],
                request_id=request_id,
                runtime_target="user_device_gateway",
                gateway_id=gateway_id,
                run_id=run_id,
                session_id=session_id,
                timeout=args.hardware_timeout,
                target_request_id=request_id,
            )
            long_result = future.result(timeout=args.long_action_timeout + 5)
        _assert_status(stopped, {"terminated"}, label="gateway stop")
        _assert_runtime_session(
            stopped,
            runtime_target="user_device_gateway",
            request_id=request_id,
            states={"terminated"},
            device_or_node_id=gateway_id,
        )
        events = _poll_transcript(
            client,
            workspace_id=workspace_id,
            request_id=request_id,
            timeout=args.timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        _assert_transcript_contains(events, event_type="tool.result", runtime_target="user_device_gateway", status="terminated")
        return {"ids": _cert_ids(long_result, stopped), "long_action": long_result, "stopped": stopped, "transcript_events": events}

    def revoke_step() -> dict[str, Any]:
        if not args.allow_revoke:
            raise CertificationError("gateway revoke certification requires --allow-revoke")
        revoked = client.request(
            f"/api/gateway/registrations/{parse.quote(gateway_id)}/revoke",
            method="POST",
            payload={"reason": "certification_revoke"},
            timeout=args.timeout,
        )
        blocked = _hardware_execute(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=seed["request_id"],
            runtime_target="user_device_gateway",
            action_id="shell.exec",
            runtime_access_mode="full_access",
            timeout=args.hardware_timeout,
            arguments={"command": args.gateway_safe_command},
            gateway_id=gateway_id,
        )
        _assert_status(blocked, {"offline", "failed", "degraded"}, label="revoked gateway follow-up")
        if isinstance(blocked.get("runtime_session"), dict):
            _assert_runtime_session(
                blocked,
                runtime_target="user_device_gateway",
                request_id=seed["request_id"],
                access_mode="full_access",
                states={"offline", "failed", "degraded"},
                device_or_node_id=gateway_id,
            )
        return {"ids": _cert_ids(blocked), "revoked": revoked, "blocked": blocked}

    steps = [
        _live_step(name="gateway_doctor", command=command, log_dir=log_dir, fn=doctor_step),
        _live_step(name="gateway_seed_chat_turn", command=command, log_dir=log_dir, fn=seed_step),
        _live_step(name="gateway_default_safe_actions", command=command, log_dir=log_dir, fn=safe_actions_step),
        _live_step(name="gateway_approval_approve_once", command=command, log_dir=log_dir, fn=lambda: approval_step("approved")),
        _live_step(name="gateway_approval_approve_session", command=command, log_dir=log_dir, fn=lambda: approval_step("approved", remember=True)),
        _live_step(name="gateway_approval_deny", command=command, log_dir=log_dir, fn=lambda: approval_step("rejected")),
        _live_step(name="gateway_autonomous_agent_no_empyralis_approval", command=command, log_dir=log_dir, fn=autonomous_step),
        _live_step(name="gateway_stop_cancel", command=command, log_dir=log_dir, fn=stop_step),
        _live_step(name="gateway_revoke_blocks_followup", command=command, log_dir=log_dir, fn=revoke_step),
    ]
    if args.launch_gate:
        steps.insert(2, _live_step(name="gateway_sage_chat_local_gateway_smoke", command=command, log_dir=log_dir, fn=sage_chat_step))
    return steps


def self_hosted_steps(args: argparse.Namespace, log_dir: Path) -> list[StepResult]:
    profile_id = args.self_hosted_profile_id or os.getenv("EMPYRALIS_CERT_SELF_HOSTED_PROFILE_ID") or ""
    node_token = args.self_hosted_node_token or os.getenv("EMPYRALIS_CERT_SELF_HOSTED_NODE_TOKEN") or ""
    node_id = args.self_hosted_node_id or os.getenv("EMPYRALIS_CERT_SELF_HOSTED_NODE_ID") or ""
    missing = [name for name, value in {"self-hosted-profile-id": profile_id, "self-hosted-node-token": node_token}.items() if not value]
    if missing:
        return [
            _missing_input_step(
                suite="self-hosted",
                name="self_hosted_required_inputs",
                missing=missing,
                env={
                    "self-hosted-profile-id": "EMPYRALIS_CERT_SELF_HOSTED_PROFILE_ID",
                    "self-hosted-node-token": "EMPYRALIS_CERT_SELF_HOSTED_NODE_TOKEN",
                    "self-hosted-node-id": "EMPYRALIS_CERT_SELF_HOSTED_NODE_ID",
                },
            )
        ]
    client, workspace_id, tenant_id = _authenticate(args, require_existing_workspace=True)
    command = ["scripts/empyralis_hardware_transparency_certification.py", "--suite", "self-hosted"]
    seed: dict[str, Any] = {}
    running_command: dict[str, Any] = {}

    def heartbeat_step() -> dict[str, Any]:
        return client.request(
            f"/api/runtime/self-hosted-nodes/{parse.quote(profile_id)}/heartbeat",
            method="POST",
            payload={
                "node_session_token": node_token,
                "status": "idle",
                "note": "certification heartbeat",
                "capabilities": ["shell.execute", "file_access", "screenshot.capture"],
                "health_state": "healthy",
            },
            timeout=args.timeout,
        )

    def seed_step() -> dict[str, Any]:
        nonlocal seed
        seed = _seed_chat_turn(
            client,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            message="Certification seed turn for self-hosted hardware transparency. Reply with OK.",
            timeout=args.turn_timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        return {"workspace_id": workspace_id, "tenant_id": tenant_id, **seed}

    def enqueue_step() -> dict[str, Any]:
        nonlocal running_command
        result = _hardware_execute(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=seed["request_id"],
            runtime_target="self_hosted_node",
            action_id="shell.exec",
            runtime_access_mode="full_access",
            timeout=args.hardware_timeout,
            arguments={"command": args.self_hosted_safe_command},
            node_id=node_id or None,
        )
        _assert_status(result, {"running"}, label="self-hosted enqueue")
        _assert_runtime_session(
            result,
            runtime_target="self_hosted_node",
            request_id=seed["request_id"],
            access_mode="full_access",
            states={"running"},
            device_or_node_id=node_id or profile_id,
        )
        running_command = result
        return {"ids": _cert_ids(result), "result": result}

    def claim_and_complete_step(status: str = "completed") -> dict[str, Any]:
        claimed = client.request(
            f"/api/runtime/self-hosted-nodes/{parse.quote(profile_id)}/commands/claim",
            method="POST",
            payload={"node_session_token": node_token, "max_commands": 1, "lease_seconds": 120},
            timeout=args.timeout,
        )
        commands = list(claimed.get("commands") or claimed.get("items") or [])
        if not commands:
            raise CertificationError("self-hosted node did not claim a command", claimed)
        command_row = commands[0]
        command_id = _text(command_row.get("id") or command_row.get("command_id"))
        if not command_id:
            raise CertificationError("claimed self-hosted command is missing id", claimed)
        completed = client.request(
            f"/api/runtime/self-hosted-nodes/{parse.quote(profile_id)}/commands/{parse.quote(command_id)}/result",
            method="POST",
            payload={
                "node_session_token": node_token,
                "status": status,
                "result_payload": {
                    "summary": "Self-hosted certification command completed." if status == "completed" else "Self-hosted certification failure.",
                    "stdout": "empyralis-self-hosted-cert" if status == "completed" else "",
                },
                "artifacts": [{"artifact_id": f"artifact-self-hosted-cert-{uuid.uuid4().hex[:8]}", "type": "text"}],
                "error": None if status == "completed" else "certification simulated failure",
            },
            timeout=args.timeout,
        )
        events = _poll_transcript(
            client,
            workspace_id=workspace_id,
            request_id=seed["request_id"],
            timeout=args.timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        _assert_safe_transcript_events(events)
        _assert_transcript_contains(events, event_type="tool.result", runtime_target="self_hosted_node")
        if status == "completed":
            _assert_transcript_contains(events, event_type="artifact.created")
        return {
            "ids": _cert_ids(running_command, completed if isinstance(completed, dict) else {}),
            "claimed": claimed,
            "completed": completed,
            "transcript_events": events,
            "enqueued": running_command,
        }

    def failure_step() -> dict[str, Any]:
        nonlocal running_command
        failed = _hardware_execute(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=seed["request_id"],
            runtime_target="self_hosted_node",
            action_id="shell.exec",
            runtime_access_mode="full_access",
            timeout=args.hardware_timeout,
            arguments={"command": args.self_hosted_safe_command},
            node_id=node_id or None,
        )
        _assert_status(failed, {"running"}, label="self-hosted failure enqueue")
        _assert_runtime_session(
            failed,
            runtime_target="self_hosted_node",
            request_id=seed["request_id"],
            access_mode="full_access",
            states={"running"},
            device_or_node_id=node_id or profile_id,
        )
        running_command = failed
        return claim_and_complete_step("failed")

    def stop_step() -> dict[str, Any]:
        request_id = seed["request_id"]
        run_id = f"cert-self-stop-{uuid.uuid4().hex[:12]}"
        session_id = f"hrs-self-stop-{uuid.uuid4().hex[:12]}"
        enqueued = _hardware_execute(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=request_id,
            runtime_target="self_hosted_node",
            action_id="shell.exec",
            runtime_access_mode="full_access",
            timeout=args.hardware_timeout,
            arguments={"command": args.self_hosted_long_command},
            node_id=node_id or None,
            run_id=run_id,
            session_id=session_id,
        )
        _assert_status(enqueued, {"running"}, label="self-hosted long enqueue")
        _assert_runtime_session(
            enqueued,
            runtime_target="self_hosted_node",
            request_id=request_id,
            access_mode="full_access",
            states={"running"},
            device_or_node_id=node_id or profile_id,
        )
        stopped = _hardware_stop(
            client,
            workspace_id=workspace_id,
            thread_id=seed["thread_id"],
            request_id=request_id,
            runtime_target="self_hosted_node",
            node_id=node_id or None,
            run_id=run_id,
            session_id=session_id,
            timeout=args.hardware_timeout,
            target_request_id=request_id,
        )
        _assert_status(stopped, {"terminated"}, label="self-hosted stop")
        _assert_runtime_session(
            stopped,
            runtime_target="self_hosted_node",
            request_id=request_id,
            states={"terminated"},
            device_or_node_id=node_id or profile_id,
        )
        events = _poll_transcript(
            client,
            workspace_id=workspace_id,
            request_id=request_id,
            timeout=args.timeout,
            poll_attempts=args.poll_attempts,
            poll_interval=args.poll_interval,
        )
        _assert_transcript_contains(events, event_type="tool.result", runtime_target="self_hosted_node", status="terminated")
        return {"ids": _cert_ids(enqueued, stopped), "enqueued": enqueued, "stopped": stopped, "transcript_events": events}

    return [
        _live_step(name="self_hosted_heartbeat", command=command, log_dir=log_dir, fn=heartbeat_step),
        _live_step(name="self_hosted_seed_chat_turn", command=command, log_dir=log_dir, fn=seed_step),
        _live_step(name="self_hosted_enqueue_safe_command", command=command, log_dir=log_dir, fn=enqueue_step),
        _live_step(name="self_hosted_claim_complete_artifact_replay", command=command, log_dir=log_dir, fn=lambda: claim_and_complete_step("completed")),
        _live_step(name="self_hosted_failure_completion_replay", command=command, log_dir=log_dir, fn=failure_step),
        _live_step(name="self_hosted_stop_cancel", command=command, log_dir=log_dir, fn=stop_step),
    ]


def launch_gate_final_steps(args: argparse.Namespace, log_dir: Path, results: list[StepResult]) -> list[StepResult]:
    if not args.launch_gate:
        return []
    command = ["scripts/empyralis_hardware_transparency_certification.py", "--suite", args.suite, "--launch-gate"]

    def no_skipped_steps() -> dict[str, Any]:
        skipped = [item.name for item in results if item.status == "SKIP"]
        if skipped:
            raise CertificationError("launch gate does not allow skipped steps", {"skipped_steps": skipped})
        return {"skipped_steps": []}

    def required_steps_passed() -> dict[str, Any]:
        by_name = {item.name: item for item in results}
        missing = [name for name in LAUNCH_GATE_REQUIRED_STEPS if name not in by_name]
        failed = [name for name in LAUNCH_GATE_REQUIRED_STEPS if name in by_name and by_name[name].status != "PASS"]
        if missing or failed:
            raise CertificationError(
                "launch gate required proof steps are missing or failed",
                {"missing_steps": missing, "failed_required_steps": failed},
            )
        return {"required_steps": list(LAUNCH_GATE_REQUIRED_STEPS)}

    def no_internal_markup_in_evidence() -> dict[str, Any]:
        checked_steps: list[str] = []
        for item in results:
            if not item.details:
                continue
            checked_steps.append(item.name)
            _assert_no_internal_markup_surface(item.details, label=f"{item.name}.details")
        return {"checked_step_details": checked_steps}

    return [
        _live_step(name="launch_gate_no_skipped_steps", command=command, log_dir=log_dir, fn=no_skipped_steps),
        _live_step(name="launch_gate_required_steps_passed", command=command, log_dir=log_dir, fn=required_steps_passed),
        _live_step(name="launch_gate_no_internal_markup_in_evidence", command=command, log_dir=log_dir, fn=no_internal_markup_in_evidence),
    ]


def _render_markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Empyralis Agent Computer Launch Certification",
        "",
        f"- Generated: `{payload.get('generated_at', '')}`",
        f"- Suite: `{payload.get('suite', '')}`",
        f"- Launch gate: `{bool(payload.get('launch_gate'))}`",
        f"- Result: **{payload.get('result', 'UNKNOWN')}**",
        f"- JSON report: `{payload.get('report_path', '')}`",
        f"- Logs: `{payload.get('logs_dir', '')}`",
        "",
        "## Release Candidate",
        "",
    ]
    git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
    lines.extend(
        [
            f"- Branch: `{git.get('branch', '')}`",
            f"- Commit: `{git.get('sha', '')}`",
            f"- Dirty: `{bool(git.get('dirty'))}`",
            "",
            "## Environment",
            "",
        ]
    )
    environment = payload.get("environment") if isinstance(payload.get("environment"), dict) else {}
    for key, value in environment.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Steps", "", "| Step | Status | Duration | Summary | Log |", "| --- | --- | ---: | --- | --- |"])
    for item in list(payload.get("steps") or []):
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "").replace("\n", "<br>")
        log_path = str(item.get("log_path") or "")
        log_link = f"`{log_path}`" if log_path else ""
        lines.append(
            f"| `{item.get('name', '')}` | **{item.get('status', '')}** | {item.get('duration_seconds', 0)}s | {summary} | {log_link} |"
        )
    blockers = [
        item
        for item in list(payload.get("steps") or [])
        if isinstance(item, dict) and str(item.get("status") or "") == "FAIL"
    ]
    lines.extend(["", "## Launch Verdict", ""])
    if blockers:
        lines.append("**FAIL. Do not ship this runtime as launch-certified.**")
        lines.append("")
        lines.append("Blockers:")
        for item in blockers:
            lines.append(f"- `{item.get('name', '')}`: {item.get('summary', '')}")
    else:
        lines.append("**PASS. This exact release candidate passed the configured launch gate.**")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            str(payload.get("real_hardware_note") or ""),
            "",
            "This certificate is evidence for this exact commit, machine, Gateway, Supervisor, and runtime configuration only.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def run_certification(args: argparse.Namespace) -> int:
    if args.suite not in VALID_SUITES:
        raise SystemExit(f"Unknown suite {args.suite!r}. Expected one of: {', '.join(sorted(VALID_SUITES))}")
    log_dir = ROOT_DIR / ".tmp" / "hardware_transparency_cert"
    results: list[StepResult] = []
    preflight_results = launch_gate_preflight_steps(args, log_dir)
    results.extend(preflight_results)
    preflight_failed = any(item.status == "FAIL" for item in preflight_results)
    if args.launch_gate and preflight_failed:
        results.append(
            StepResult(
                "launch_gate_suite_execution_blocked",
                "FAIL",
                ["scripts/empyralis_hardware_transparency_certification.py", "--suite", args.suite, "--launch-gate"],
                0.0,
                "required launch-gate preflight failed; full certification suites were not run",
                details={"failed_preflight_steps": [item.name for item in preflight_results if item.status == "FAIL"]},
            )
        )
    else:
        if args.suite in {"automated", "full"}:
            results.extend(automated_steps(args, log_dir))
        if args.suite in {"agent-computer", "full"}:
            results.extend(agent_computer_steps(args, log_dir))
        if args.suite in {"cloud-computer", "full"}:
            results.extend(cloud_computer_steps(args, log_dir))
        if args.suite in {"gateway", "full"}:
            results.extend(gateway_steps(args, log_dir))
        if args.suite in {"self-hosted", "full"}:
            results.extend(self_hosted_steps(args, log_dir))
    results.append(_run_step(name="git_diff_check", command=["git", "diff", "--check"], timeout=30, log_dir=log_dir))
    results.extend(launch_gate_final_steps(args, log_dir, results))

    failed = [item for item in results if item.status == "FAIL"]
    skipped = [item for item in results if item.status == "SKIP"]
    payload = {
        "generated_at": _utc_now(),
        "suite": args.suite,
        "launch_gate": bool(args.launch_gate),
        "result": "FAIL" if failed or (args.launch_gate and skipped) else "PASS",
        "logs_dir": str(log_dir.relative_to(ROOT_DIR)),
        "git": _git_metadata(),
        "environment": _redacted_launch_inputs(args),
        "steps": [
            {
                "name": item.name,
                "status": item.status,
                "command": _command_text(item.command),
                "duration_seconds": round(item.duration_seconds, 2),
                "summary": item.summary,
                "log_path": item.log_path,
                "details": item.details,
            }
            for item in results
        ],
        "real_hardware_note": (
            "Gateway and self-hosted suites are production live-hardware certifications. "
            "Missing real hardware identifiers fail in --suite gateway, --suite self-hosted, and --suite full."
        ),
    }
    report_path = Path(args.report_path or (log_dir / "certification_report.json"))
    if not report_path.is_absolute():
        report_path = ROOT_DIR / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload["report_path"] = str(report_path.relative_to(ROOT_DIR))
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path = Path(args.markdown_report_path or report_path.with_suffix(".md"))
    if not markdown_path.is_absolute():
        markdown_path = ROOT_DIR / markdown_path
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload["markdown_report_path"] = str(markdown_path.relative_to(ROOT_DIR))
    markdown_path.write_text(_render_markdown_report(payload), encoding="utf-8")
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if payload["result"] == "FAIL" else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the hardware runtime + inline transparency production certification gate.")
    parser.add_argument("--suite", choices=sorted(VALID_SUITES), default="automated")
    parser.add_argument("--backend-url", default=os.getenv("EMPYRALIS_CERT_BACKEND_URL") or "http://127.0.0.1:8001")
    parser.add_argument("--workspace-id", default="")
    parser.add_argument("--tenant-id", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--bearer-token", default="")
    parser.add_argument("--runtime-key", default="")
    parser.add_argument("--gateway-id", default="")
    parser.add_argument("--self-hosted-profile-id", default="")
    parser.add_argument("--self-hosted-node-token", default="")
    parser.add_argument("--self-hosted-node-id", default="")
    parser.add_argument("--gateway-file-path", default=os.getenv("EMPYRALIS_CERT_GATEWAY_FILE_PATH") or ".")
    parser.add_argument("--gateway-safe-command", default=os.getenv("EMPYRALIS_CERT_GATEWAY_SAFE_COMMAND") or "pwd")
    parser.add_argument(
        "--gateway-risky-command",
        default=os.getenv("EMPYRALIS_CERT_GATEWAY_RISKY_COMMAND")
        or f"rm -rf /tmp/empyralis-cert-nonexistent-{uuid.uuid4().hex[:8]}",
    )
    parser.add_argument("--gateway-long-command", default=os.getenv("EMPYRALIS_CERT_GATEWAY_LONG_COMMAND") or "sleep 30")
    parser.add_argument("--self-hosted-safe-command", default=os.getenv("EMPYRALIS_CERT_SELF_HOSTED_SAFE_COMMAND") or "echo empyralis-self-hosted-cert")
    parser.add_argument("--self-hosted-long-command", default=os.getenv("EMPYRALIS_CERT_SELF_HOSTED_LONG_COMMAND") or "sleep 30")
    parser.add_argument("--gateway-chat-message", default=os.getenv("EMPYRALIS_CERT_GATEWAY_CHAT_MESSAGE") or "run: echo hello from hardware")
    parser.add_argument("--launch-gate", action="store_true")
    parser.add_argument("--allow-revoke", action="store_true")
    parser.add_argument("--include-live-smoke", action="store_true")
    parser.add_argument("--require-live-smoke", action="store_true")
    parser.add_argument("--health-timeout", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--backend-timeout", type=int, default=120)
    parser.add_argument("--frontend-timeout", type=int, default=120)
    parser.add_argument("--mobile-timeout", type=int, default=120)
    parser.add_argument("--e2e-timeout", type=int, default=180)
    parser.add_argument("--live-timeout", type=int, default=180)
    parser.add_argument("--turn-timeout", type=int, default=90)
    parser.add_argument("--hardware-timeout", type=int, default=120)
    parser.add_argument("--long-action-timeout", type=int, default=180)
    parser.add_argument("--poll-attempts", type=int, default=20)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--stop-delay", type=float, default=1.0)
    parser.add_argument("--report-path", default="")
    parser.add_argument("--markdown-report-path", default="")
    return run_certification(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
