"""Legacy direct loopback client for local worker and demo scripts only.

Production backend code must route local device control through the gateway
execution path (`gateway_execution_service` -> `gateway_protocol_service` ->
gateway websocket -> supervisor). This module remains only for local scripts
that execute on the same machine as `empyralis-supervisor`.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

import requests


SUPERVISOR_URL = "http://127.0.0.1:7788"
SUPERVISOR_TIMEOUT_SECONDS = 10
OVERLAY_BRIDGE_URL = os.getenv("EMPYRALIS_OVERLAY_BRIDGE_URL", "http://127.0.0.1:7790/overlay-events")
OVERLAY_BRIDGE_TIMEOUT_SECONDS = 0.2
SUPERVISOR_UNREACHABLE_MESSAGE = (
    "Supervisor not running. Start empyralis-supervisor before using computer control."
)
SUPERVISOR_INTERRUPTED_MESSAGE = "execution interrupted by operator"
DIRECT_SUPERVISOR_BLOCKED_MESSAGE = (
    "Direct supervisor loopback is disabled in the API server process. "
    "Route local control through the gateway execution path instead."
)


@dataclass(frozen=True, slots=True)
class SupervisorExecutionContext:
    run_id: str
    workspace_id: str = "default"
    trace_id: str = ""
    machine_id: Optional[str] = None
    interrupt_controller: Any = None


class SupervisorInterruptedError(RuntimeError):
    pass


_ACTIVE_EXECUTION_CONTEXT: ContextVar[Optional[SupervisorExecutionContext]] = ContextVar(
    "empyralis_supervisor_execution_context",
    default=None,
)


@contextmanager
def bind_execution_context(context: Optional[SupervisorExecutionContext]) -> Iterator[Optional[SupervisorExecutionContext]]:
    token = _ACTIVE_EXECUTION_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ACTIVE_EXECUTION_CONTEXT.reset(token)


def capture_screenshot(monitor: str = "primary", region: dict[str, Any] | None = None) -> dict[str, Any]:
    return _execute(
        "screenshot.capture",
        {
            "monitor": monitor,
            "region": region,
        },
    )


def click(
    x: int | None = None,
    y: int | None = None,
    *,
    text: str | None = None,
    button: str = "left",
    double: bool = False,
) -> dict[str, Any]:
    if x is not None and y is not None:
        _emit_overlay_event(
            "click",
            {
                "x": int(x),
                "y": int(y),
                "button": button,
                "double": bool(double),
                "phase": "before",
            },
        )
    result = _execute(
        "computer_control.click",
        {
            "x": x,
            "y": y,
            "text": str(text or "").strip() or None,
            "button": button,
            "double": double,
        },
    )
    resolved_x = result.get("x", x)
    resolved_y = result.get("y", y)
    if resolved_x is not None and resolved_y is not None:
        _emit_overlay_event(
            "click",
            {
                "x": int(resolved_x),
                "y": int(resolved_y),
                "button": button,
                "double": bool(double),
                "phase": "after",
            },
        )
    return result


def move_mouse(x: int, y: int) -> dict[str, Any]:
    _emit_overlay_event(
        "move",
        {
            "x": int(x),
            "y": int(y),
        },
    )
    return _execute(
        "computer_control.move",
        {
            "x": x,
            "y": y,
        },
    )


def type_text(text: str, delay_ms: int | None = None) -> dict[str, Any]:
    payload = str(text or "")
    _emit_overlay_event(
        "type",
        {
            "text": payload[:48],
            "length": len(payload),
        },
    )
    return _execute(
        "computer_control.type",
        {
            "text": payload,
            "delay_ms": delay_ms,
        },
    )


def press_key(key: str) -> dict[str, Any]:
    return _execute(
        "computer_control.key",
        {
            "key": key,
        },
    )


def clipboard_read() -> dict[str, Any]:
    return _execute("computer_control.clipboard_read", {})


def clipboard_write(text: str) -> dict[str, Any]:
    return _execute(
        "computer_control.clipboard_write",
        {
            "text": text,
        },
    )


def list_windows() -> dict[str, Any]:
    return _execute("computer_control.list_windows", {})


def launch(target: str) -> dict[str, Any]:
    return _execute(
        "computer_control.launch",
        {
            "target": target,
        },
    )


def ocr(monitor: str = "primary", region: dict[str, Any] | None = None) -> dict[str, Any]:
    return _execute(
        "computer_control.ocr",
        {
          "monitor": monitor,
          "region": region,
        },
    )


def notify(title: str, message: str) -> dict[str, Any]:
    return _execute(
        "computer_control.notify",
        {
            "title": title,
            "message": message,
        },
    )


def list_apps() -> dict[str, Any]:
    return _execute("computer_control.list_apps", {})


def launch_app(target: str) -> dict[str, Any]:
    return _execute(
        "computer_control.launch_app",
        {
            "target": target,
        },
    )


def run_applescript(script: str) -> dict[str, Any]:
    return _execute(
        "computer_control.applescript",
        {
            "script": script,
        },
    )


def speak(text: str, voice: str | None = None) -> dict[str, Any]:
    return _execute(
        "computer_control.speak",
        {
            "text": text,
            "voice": voice,
        },
    )


def interrupt_execution(
    *,
    run_id: str,
    target_request_id: str | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
    workspace_id: str = "default",
) -> dict[str, Any]:
    _assert_direct_supervisor_allowed()
    interrupt_request_id = str(uuid.uuid4())
    nonce = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    run_token = str(run_id or "").strip()
    workspace_token = str(workspace_id or "default").strip() or "default"
    payload = {
        "request_id": interrupt_request_id,
        "run_id": run_token,
        "target_request_id": str(target_request_id or "").strip() or None,
        "trace_id": str(trace_id or interrupt_request_id).strip() or interrupt_request_id,
        "workspace_id": workspace_token,
        "reason": str(reason or "").strip() or None,
        "nonce": nonce,
        "expires_at": expires_at,
        "signature": _sign_interrupt_request(
            request_id=interrupt_request_id,
            run_id=run_token,
            target_request_id=str(target_request_id or "").strip() or None,
            workspace_id=workspace_token,
            nonce=nonce,
            expires_at=expires_at,
        ),
    }
    try:
        response = requests.post(
            f"{SUPERVISOR_URL}/interrupt",
            json=payload,
            timeout=SUPERVISOR_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(SUPERVISOR_UNREACHABLE_MESSAGE) from exc
    return _decode_response(response, request_type="interrupt")


def _execute(
    capability_id: str,
    arguments: dict[str, Any],
    *,
    execution_context: Optional[SupervisorExecutionContext] = None,
) -> dict[str, Any]:
    _assert_direct_supervisor_allowed()
    resolved_context = execution_context or _ACTIVE_EXECUTION_CONTEXT.get()
    request_id = str(uuid.uuid4())
    nonce = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    run_id = str((resolved_context.run_id if resolved_context else "") or "supervisor-client").strip() or "supervisor-client"
    trace_id = str((resolved_context.trace_id if resolved_context else "") or request_id).strip() or request_id
    workspace_id = str((resolved_context.workspace_id if resolved_context else "") or "local").strip() or "local"
    payload = {
        "request_id": request_id,
        "capability_id": capability_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "workspace_id": workspace_id,
        "arguments": arguments,
        "runtime_access_mode": None,
        "empyralis_approved": False,
        "agent_scope": None,
        "policy": None,
        "signature_version": "v2",
        "nonce": nonce,
        "expires_at": expires_at,
        "signature": _sign_request(
            request_id=request_id,
            capability_id=capability_id,
            run_id=run_id,
            trace_id=trace_id,
            workspace_id=workspace_id,
            arguments=arguments,
            runtime_access_mode=None,
            empyralis_approved=False,
            agent_scope=None,
            policy=None,
            nonce=nonce,
            expires_at=expires_at,
        ),
    }
    interrupt_controller = getattr(resolved_context, "interrupt_controller", None) if resolved_context is not None else None
    if interrupt_controller is not None and hasattr(interrupt_controller, "register_supervisor_execution"):
        interrupt_controller.register_supervisor_execution(
            run_id=run_id,
            request_id=request_id,
            interrupt_fn=lambda: interrupt_execution(
                run_id=run_id,
                target_request_id=request_id,
                reason="Hard kill requested by operator.",
                trace_id=trace_id,
                workspace_id=workspace_id,
            ),
        )
    try:
        response = requests.post(
            f"{SUPERVISOR_URL}/execute",
            json=payload,
            timeout=SUPERVISOR_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(SUPERVISOR_UNREACHABLE_MESSAGE) from exc
    finally:
        if interrupt_controller is not None and hasattr(interrupt_controller, "clear_supervisor_execution"):
            interrupt_controller.clear_supervisor_execution(request_id=request_id)
    return _decode_response(response, request_type="execute")


def _emit_overlay_event(kind: str, payload: dict[str, Any]) -> None:
    event = {
        "kind": str(kind or "").strip(),
        **payload,
    }
    try:
        requests.post(
            OVERLAY_BRIDGE_URL,
            json=event,
            timeout=OVERLAY_BRIDGE_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        # Overlay visualization is best-effort and must never block supervisor execution.
        return


def _decode_response(response: requests.Response, *, request_type: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Supervisor returned invalid JSON (HTTP {response.status_code}).") from exc

    if response.status_code == 401:
        message = body.get("error") or "Supervisor rejected the signed request."
        raise RuntimeError(message)
    if response.status_code >= 400:
        message = body.get("error") or f"Supervisor {request_type} failed with HTTP {response.status_code}."
        raise RuntimeError(message)
    if not body.get("success", False):
        message = str(body.get("error") or f"Supervisor {request_type} failed.").strip() or f"Supervisor {request_type} failed."
        if SUPERVISOR_INTERRUPTED_MESSAGE in message.lower():
            raise SupervisorInterruptedError(message)
        raise RuntimeError(message)

    if request_type == "interrupt":
        return dict(body)

    result = body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Supervisor returned an invalid result payload.")
    return result


def _assert_direct_supervisor_allowed() -> None:
    if _env_truthy("EMPYRALIS_ALLOW_DIRECT_SUPERVISOR_LOOPBACK"):
        return
    if _running_inside_api_server():
        raise RuntimeError(DIRECT_SUPERVISOR_BLOCKED_MESSAGE)
    if "PYTEST_CURRENT_TEST" in os.environ:
        return


def _running_inside_api_server() -> bool:
    argv = [str(item or "").strip().lower() for item in sys.argv]
    executable = Path(sys.argv[0]).name.lower() if sys.argv else ""
    server_markers = ("uvicorn", "server:app", "fastapi")
    return any(marker in executable for marker in server_markers) or any(
        marker in token for marker in server_markers for token in argv
    )


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _sign_request(
    *,
    request_id: str,
    capability_id: str,
    run_id: str,
    trace_id: str,
    workspace_id: str,
    arguments: dict[str, Any],
    runtime_access_mode: str | None = None,
    empyralis_approved: bool = False,
    agent_scope: str | None = None,
    policy: dict[str, Any] | None = None,
    nonce: str,
    expires_at: str,
) -> str:
    secret = os.getenv("EMPYRALIS_SUPERVISOR_SECRET")
    if not secret:
        raise RuntimeError("EMPYRALIS_SUPERVISOR_SECRET is required for supervisor calls.")
    sign_str = _canonical_json(
        {
            "type": "execute",
            "version": "v2",
            "request_id": request_id,
            "capability_id": capability_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "workspace_id": workspace_id,
            "arguments": arguments or {},
            "runtime_access_mode": str(runtime_access_mode or ""),
            "empyralis_approved": bool(empyralis_approved),
            "agent_scope": str(agent_scope or ""),
            "policy": policy if isinstance(policy, dict) else None,
            "nonce": nonce,
            "expires_at": expires_at,
        }
    )
    return hmac.new(secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sign_interrupt_request(
    *,
    request_id: str,
    run_id: str,
    target_request_id: str | None,
    workspace_id: str,
    nonce: str,
    expires_at: str,
) -> str:
    secret = os.getenv("EMPYRALIS_SUPERVISOR_SECRET")
    if not secret:
        raise RuntimeError("EMPYRALIS_SUPERVISOR_SECRET is required for supervisor calls.")
    sign_str = (
        f"interrupt:{request_id}:{run_id}:{str(target_request_id or '').strip()}:{workspace_id}:{nonce}:{expires_at}"
    )
    return hmac.new(secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).hexdigest()
