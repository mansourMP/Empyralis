from __future__ import annotations

from typing import Any, Dict, List, Optional

from server_modules import gateway_execution_service, gateway_state_repository
from server_modules import platform_event as _pe


CLI_INSTALL_CAPABILITY = "cli.install"
CLI_LOGIN_START_CAPABILITY = "cli.login.start"
CLI_LOGIN_INPUT_CAPABILITY = "cli.login.input"
CLI_LOGIN_OUTPUT_MESSAGE_TYPE = "cli.login.output"

_SUPPORTED_RUNTIMES = ("claude_code", "codex")
_GATEWAY_EVENTS_PAGE_SIZE = 500


class CliSetupError(RuntimeError):
    """Raised by every cli_setup dispatch function below. Callers (routes)
    translate status_code + this message directly into an HTTPException —
    the message is already platform-voice (see platform_event.CLI_SETUP_*),
    not a raw exception string."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _normalize_runtime(runtime: str) -> str:
    normalized = str(runtime or "").strip().lower()
    if normalized not in _SUPPORTED_RUNTIMES:
        raise CliSetupError(
            f"Heads up: '{runtime}' is not a supported cli_setup runtime. Use claude_code or codex.",
            status_code=400,
        )
    return normalized


def _say(event: _pe.PlatformEvent) -> str:
    return f"Heads up: {event.channel_text}"


def _status_code_for_reason(reason: str) -> int:
    r = str(reason or "").strip().lower()
    if "offline" in r or "heartbeat_stale" in r or "unhealthy" in r or "not currently connected" in r:
        return 409
    return 400


def _friendly_cli_setup_error(reason: str, *, login: bool, runtime: str) -> str:
    """Map a raw dispatch/readiness reason (or a wrapped Gateway-side
    CliInstallError/CliLoginError message) to a platform-voice message, one
    per distinct failure mode — mirrors sage_agent_runtime_service.
    _friendly_cli_subscription_error's shape for the cli_setup (install /
    sign-in) action family."""
    r = str(reason or "").strip().lower()
    is_codex = str(runtime or "").strip().lower() == "codex"

    if (
        "registration_missing" in r or "registration_inactive" in r
        or "device_revoked" in r or "workspace_mismatch" in r
    ):
        return _say(_pe.CLI_SUBSCRIPTION_GATEWAY_NOT_PAIRED)
    if "capability_missing" in r or "capability_not_ready" in r:
        return _say(_pe.CLI_SETUP_NOT_ENABLED_LOCALLY)
    if "offline" in r or "heartbeat_stale" in r or "unhealthy" in r or "not currently connected" in r:
        return _say(_pe.CLI_SETUP_GATEWAY_OFFLINE)
    if "npm_missing" in r:
        return _say(_pe.CLI_SETUP_INSTALL_NPM_MISSING)
    if "permission_denied" in r:
        return _say(_pe.CLI_SETUP_INSTALL_PERMISSION_DENIED)
    if "network_error" in r:
        return _say(_pe.CLI_SETUP_INSTALL_NETWORK_ERROR)
    if "not_installed" in r:
        return _say(_pe.CLI_SETUP_CODEX_LOGIN_NOT_INSTALLED if is_codex else _pe.CLI_SETUP_CLAUDE_LOGIN_NOT_INSTALLED)
    if "cancelled" in r:
        return _say(_pe.CLI_SETUP_LOGIN_CANCELLED)
    if "timeout" in r or "timed out" in r:
        return _say(_pe.CLI_SETUP_LOGIN_TIMEOUT if login else _pe.CLI_SETUP_INSTALL_TIMEOUT)
    if "crash" in r or "exited unexpectedly" in r or "gateway tool invocation failed" in r:
        if login:
            return _say(_pe.CLI_SETUP_CODEX_LOGIN_FAILED if is_codex else _pe.CLI_SETUP_CLAUDE_LOGIN_FAILED)
        return _say(_pe.CLI_SETUP_CODEX_INSTALL_FAILED if is_codex else _pe.CLI_SETUP_CLAUDE_INSTALL_FAILED)
    label = "Codex" if is_codex else "Claude Code"
    action_label = "sign-in" if login else "install"
    return f"Heads up: {label} {action_label} failed on the bound Gateway ({reason})."


async def install_cli_runtime(
    *,
    gateway_id: str,
    workspace_id: str,
    runtime: str,
    run_id: str,
    trace_id: str = "",
    request_id: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_runtime = _normalize_runtime(runtime)
    try:
        return await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=CLI_INSTALL_CAPABILITY,
            arguments={"runtime": normalized_runtime},
            run_id=run_id,
            trace_id=trace_id or run_id,
            workspace_id=workspace_id,
            request_id=request_id,
            actor_id=actor_id,
            agent_scope="sage",
        )
    except (ValueError, PermissionError) as exc:
        reason = str(exc)
        raise CliSetupError(
            _friendly_cli_setup_error(reason, login=False, runtime=normalized_runtime),
            status_code=403 if isinstance(exc, PermissionError) else _status_code_for_reason(reason),
        ) from exc


async def start_cli_login(
    *,
    gateway_id: str,
    workspace_id: str,
    runtime: str,
    run_id: str,
    trace_id: str = "",
    request_id: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_runtime = _normalize_runtime(runtime)
    try:
        return await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=CLI_LOGIN_START_CAPABILITY,
            arguments={"runtime": normalized_runtime},
            run_id=run_id,
            trace_id=trace_id or run_id,
            workspace_id=workspace_id,
            request_id=request_id,
            actor_id=actor_id,
            agent_scope="sage",
        )
    except (ValueError, PermissionError) as exc:
        reason = str(exc)
        raise CliSetupError(
            _friendly_cli_setup_error(reason, login=True, runtime=normalized_runtime),
            status_code=403 if isinstance(exc, PermissionError) else _status_code_for_reason(reason),
        ) from exc


async def submit_cli_login_input(
    *,
    gateway_id: str,
    workspace_id: str,
    run_id: str,
    code: str,
    trace_id: str = "",
    request_id: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    clean_code = str(code or "").strip()
    if not clean_code:
        raise CliSetupError("Heads up: a code is required to continue sign-in.", status_code=400)
    try:
        return await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=CLI_LOGIN_INPUT_CAPABILITY,
            arguments={"code": clean_code},
            run_id=run_id,
            trace_id=trace_id or run_id,
            workspace_id=workspace_id,
            request_id=request_id,
            actor_id=actor_id,
            agent_scope="sage",
        )
    except (ValueError, PermissionError) as exc:
        reason = str(exc)
        raise CliSetupError(
            _friendly_cli_setup_error(reason, login=True, runtime=""),
            status_code=403 if isinstance(exc, PermissionError) else _status_code_for_reason(reason),
        ) from exc


async def cancel_cli_login(
    *,
    gateway_id: str,
    workspace_id: str,
    run_id: str,
    trace_id: str = "",
    request_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        return await gateway_execution_service.interrupt_tool_via_gateway(
            gateway_id=gateway_id,
            run_id=run_id,
            trace_id=trace_id or run_id,
            workspace_id=workspace_id,
            reason=str(reason or "").strip() or "user_cancelled",
            request_id=request_id,
        )
    except ValueError as exc:
        reason_text = str(exc)
        raise CliSetupError(
            _friendly_cli_setup_error(reason_text, login=True, runtime=""),
            status_code=_status_code_for_reason(reason_text),
        ) from exc


def list_cli_login_events(*, gateway_id: str, run_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """cli.login.output events are persisted (already, automatically) by
    gateway_protocol_service's unconditional inbound-frame recording — there
    is no run_id column on gateway_events, so this fetches one page of this
    gateway's cli.login.output events and filters/trims to the requested
    run_id in Python. Proportionate for the expected volume of a single,
    human-driven login session (a handful of events), not a general-purpose
    query path."""
    events = gateway_state_repository.list_gateway_events(
        gateway_id,
        message_type=CLI_LOGIN_OUTPUT_MESSAGE_TYPE,
        limit=_GATEWAY_EVENTS_PAGE_SIZE,
    )
    clean_run_id = str(run_id or "").strip()
    matching = [
        event for event in events
        if str((event.get("payload") or {}).get("run_id") or "").strip() == clean_run_id
    ]
    return matching[-max(int(limit or 0), 1):]
