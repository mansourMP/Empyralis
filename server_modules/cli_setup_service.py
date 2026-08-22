from __future__ import annotations

from typing import Any, Dict, List, Optional

from server_modules import gateway_execution_service, gateway_state_repository
from server_modules import platform_event as _pe


CLI_INSTALL_CAPABILITY = "cli.install"
CLI_LOGIN_START_CAPABILITY = "cli.login.start"
CLI_LOGIN_INPUT_CAPABILITY = "cli.login.input"
CLI_LOGIN_OUTPUT_MESSAGE_TYPE = "cli.login.output"

_SUPPORTED_RUNTIMES = ("claude_code", "codex", "grok_build", "cursor_cli")
_GATEWAY_EVENTS_PAGE_SIZE = 500

# Human label per runtime — every error/message helper below reads off this
# instead of a hardcoded claude_code/codex ternary, so adding a runtime is
# one entry here, not N call sites.
_RUNTIME_LABEL: Dict[str, str] = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "grok_build": "Grok Build",
    "cursor_cli": "Cursor CLI",
}

# BYO-brain: which auth methods each runtime advertises to the UI. First
# entry is the default a call to /cli/login/start with no method gets, AND
# the one the frontend marks with the "Recommended" badge. Kept in sync
# with the gateway-side LOGIN_COMMAND in cli-login-session.ts — that map
# is the source of truth for whether a (runtime, method) pair actually
# works; this is the shape the frontend enumerates. Fields per method:
# `key`, human `label`, one-line `description`, `input_kind` (what the
# frontend should collect and submit via cli.login.input for that method,
# or None if the flow is URL-and-code with no extra input).
LOGIN_METHODS: Dict[str, List[Dict[str, Any]]] = {
    "codex": [
        {
            "key": "device_auth",
            "label": "Your ChatGPT account (device code)",
            "description": (
                "Uses your ChatGPT Plus / Pro / Team plan quota. Sign in on any browser — "
                "no callback needed. Recommended for a paired remote box."
            ),
            "input_kind": None,
        },
        {
            "key": "api_key",
            "label": "An OpenAI API key",
            "description": (
                "Bring your own sk-… key. Charged per token to your OpenAI billing. "
                "Best for teams already spending on the API."
            ),
            "input_kind": "api_key",
        },
        {
            "key": "access_token",
            "label": "A pre-obtained access token",
            "description": (
                "Advanced — paste a token you already hold. Skips the auth handshake entirely."
            ),
            "input_kind": "access_token",
        },
    ],
    "claude_code": [
        {
            "key": "claudeai",
            "label": "Your Claude subscription (Pro / Max / Team)",
            "description": (
                "Uses your Claude.ai plan quota. Sign in on any browser — device-code flow, "
                "works reliably on a headless box. Recommended for a paired remote box."
            ),
            "input_kind": None,
        },
        {
            "key": "console",
            "label": "Anthropic Console (API billing)",
            "description": (
                "Uses your Anthropic Console account — per-token billing. Same device-code flow "
                "as the subscription option, for teams who bill per token instead."
            ),
            "input_kind": None,
        },
        {
            "key": "api_key",
            "label": "An Anthropic API key",
            "description": "Bring your own sk-ant-… key. Charged per token.",
            "input_kind": "api_key",
        },
    ],
    # xAI Grok Build — docs.x.ai/build, verified live 2026-07-24. `grok login
    # --device-auth` (alias `--device-code`) is the only method: it's a real,
    # documented device-authorization grant (prints a URL + code, polls until
    # confirmed) — same reliability class as Codex's device_auth. There is no
    # api_key login-session method here: XAI_API_KEY is documented as an
    # env-var fallback the CLI reads at each invocation, not something a
    # `grok login`-style subcommand reads from stdin and persists — see
    # empyralis-gateway/src/llm/cli-login-session.ts's grok_build comment for
    # the full reasoning (same shape as why `claude setup-token` isn't wired
    # as a login-session method either).
    "grok_build": [
        {
            "key": "device_auth",
            "label": "Your SuperGrok / X Premium+ subscription (device code)",
            "description": (
                "Uses your SuperGrok/X Premium+ plan quota. Sign in on any browser — device-code "
                "flow, works reliably on a headless box."
            ),
            "input_kind": None,
        },
    ],
    # Cursor CLI — cursor.com/docs/cli, verified live 2026-07-24. `agent login`
    # (spawned with NO_OPEN_BROWSER=1 so it prints the URL instead of trying
    # to open a browser on the headless Gateway) is the only method. Real,
    # documented caveat, not a hidden risk: Cursor's own community forum has
    # reports of this flow failing to complete over SSH/headless connections
    # — its docs don't disclose the underlying OAuth mechanism the way
    # Codex's/Grok's device-authorization grant is disclosed. If it fails or
    # times out, that surfaces as a real, loud sign-in failure (never
    # silently). No api_key login-session method here either, for the same
    # reason as Grok Build above — CURSOR_API_KEY is an env-var fallback with
    # no persisting stdin subcommand.
    "cursor_cli": [
        {
            "key": "login",
            "label": "Your Cursor subscription (Pro / Pro+ / Ultra)",
            "description": (
                "Uses your Cursor plan quota. Sign in on any browser. If this doesn't complete over "
                "a remote connection, set CURSOR_API_KEY directly in this Gateway's own environment instead."
            ),
            "input_kind": None,
        },
    ],
}


_VALID_INPUT_KINDS_BY_METHOD = {
    "device_auth": None,
    "claudeai": None,
    "console": None,
    "login": None,
    "api_key": "api_key",
    "access_token": "access_token",
}


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
            f"Heads up: '{runtime}' is not a supported cli_setup runtime. "
            f"Use one of: {', '.join(_SUPPORTED_RUNTIMES)}.",
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


# Runtime-keyed lookups for the three failure families below that need a
# distinct PlatformEvent per runtime — one row per runtime here, not a growing
# is_codex-style boolean ternary. Falls back to the claude_code event for an
# unrecognized runtime (never raises here; _normalize_runtime already rejects
# anything outside _SUPPORTED_RUNTIMES before this function is ever reached
# with a bogus value in production use).
_LOGIN_NOT_INSTALLED_EVENT_BY_RUNTIME = {
    "claude_code": _pe.CLI_SETUP_CLAUDE_LOGIN_NOT_INSTALLED,
    "codex": _pe.CLI_SETUP_CODEX_LOGIN_NOT_INSTALLED,
    "grok_build": _pe.CLI_SETUP_GROK_BUILD_LOGIN_NOT_INSTALLED,
    "cursor_cli": _pe.CLI_SETUP_CURSOR_LOGIN_NOT_INSTALLED,
}
_INSTALL_FAILED_EVENT_BY_RUNTIME = {
    "claude_code": _pe.CLI_SETUP_CLAUDE_INSTALL_FAILED,
    "codex": _pe.CLI_SETUP_CODEX_INSTALL_FAILED,
    "grok_build": _pe.CLI_SETUP_GROK_BUILD_INSTALL_FAILED,
    "cursor_cli": _pe.CLI_SETUP_CURSOR_INSTALL_FAILED,
}
_LOGIN_FAILED_EVENT_BY_RUNTIME = {
    "claude_code": _pe.CLI_SETUP_CLAUDE_LOGIN_FAILED,
    "codex": _pe.CLI_SETUP_CODEX_LOGIN_FAILED,
    "grok_build": _pe.CLI_SETUP_GROK_BUILD_LOGIN_FAILED,
    "cursor_cli": _pe.CLI_SETUP_CURSOR_LOGIN_FAILED,
}


def _friendly_cli_setup_error(reason: str, *, login: bool, runtime: str) -> str:
    """Map a raw dispatch/readiness reason (or a wrapped Gateway-side
    CliInstallError/CliLoginError message) to a platform-voice message, one
    per distinct failure mode — mirrors agent_turn_runtime_service.
    _friendly_cli_subscription_error's shape for the cli_setup (install /
    sign-in) action family."""
    r = str(reason or "").strip().lower()
    normalized_runtime = str(runtime or "").strip().lower()

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
    if "dependency_missing" in r:
        return _say(_pe.CLI_SETUP_INSTALL_DEPENDENCY_MISSING)
    if "permission_denied" in r:
        return _say(_pe.CLI_SETUP_INSTALL_PERMISSION_DENIED)
    if "network_error" in r:
        return _say(_pe.CLI_SETUP_INSTALL_NETWORK_ERROR)
    if "not_installed" in r:
        return _say(_LOGIN_NOT_INSTALLED_EVENT_BY_RUNTIME.get(normalized_runtime, _pe.CLI_SETUP_CLAUDE_LOGIN_NOT_INSTALLED))
    if "cancelled" in r:
        return _say(_pe.CLI_SETUP_LOGIN_CANCELLED)
    if "timeout" in r or "timed out" in r:
        return _say(_pe.CLI_SETUP_LOGIN_TIMEOUT if login else _pe.CLI_SETUP_INSTALL_TIMEOUT)
    if "crash" in r or "exited unexpectedly" in r or "gateway tool invocation failed" in r:
        if login:
            return _say(_LOGIN_FAILED_EVENT_BY_RUNTIME.get(normalized_runtime, _pe.CLI_SETUP_CLAUDE_LOGIN_FAILED))
        return _say(_INSTALL_FAILED_EVENT_BY_RUNTIME.get(normalized_runtime, _pe.CLI_SETUP_CLAUDE_INSTALL_FAILED))
    label = _RUNTIME_LABEL.get(normalized_runtime, runtime or "this runtime")
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
    method: Optional[str] = None,
    trace_id: str = "",
    request_id: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_runtime = _normalize_runtime(runtime)
    normalized_method = str(method or "").strip() or None
    if normalized_method and normalized_method not in _VALID_INPUT_KINDS_BY_METHOD.keys():
        # Not a hard reject — the gateway itself will authoritatively
        # validate the (runtime, method) combination against LOGIN_COMMAND
        # and return "unsupported_method" if it's not populated. Passing an
        # unknown method through keeps the mapping trustable end-to-end.
        pass
    arguments: Dict[str, Any] = {"runtime": normalized_runtime}
    if normalized_method:
        arguments["method"] = normalized_method
    try:
        return await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=CLI_LOGIN_START_CAPABILITY,
            arguments=arguments,
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
    value: Optional[str] = None,
    code: Optional[str] = None,
    kind: Optional[str] = None,
    trace_id: str = "",
    request_id: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    # Back-compat: the pre-multi-method signature took `code=`; callers
    # (including existing tests and pre-upgrade UI) still pass it. New
    # callers pass `value=` + optional `kind=`. If both are given, `value`
    # wins.
    clean_value = str(value if value is not None else code or "").strip()
    if not clean_value:
        raise CliSetupError("Heads up: a value is required to continue sign-in.", status_code=400)
    normalized_kind = str(kind or "").strip().lower() or "code"
    if normalized_kind not in {"code", "api_key", "access_token"}:
        raise CliSetupError(
            f"Heads up: unknown input kind '{normalized_kind}' (expected code, api_key, or access_token).",
            status_code=400,
        )
    # Send both legacy `code` (for older gateways still on Build F's
    # single-shape input path) and new `value`/`kind` fields. The gateway
    # falls back to `code` when neither `value` nor `kind` is set — see
    # cli-setup-runtime.ts.
    arguments = {"code": clean_value, "value": clean_value, "kind": normalized_kind}
    try:
        return await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=CLI_LOGIN_INPUT_CAPABILITY,
            arguments=arguments,
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
    query path.

    Shape note (this bit every caller once, so it's spelled out here): each
    row gateway_state_repository.list_gateway_events returns has `payload`
    set to the ENTIRE inbound frame — record_gateway_event is called with
    payload=frame, not payload=frame["payload"] (see gateway_protocol_
    service.py's unconditional inbound-event recording, and
    test_gateway_state_repository_cli_login_events.py which proves the
    round-trip). So the run_id/event/kind/text fields the Gateway actually
    published live one level deeper, at row["payload"]["payload"], not at
    row["payload"] directly. Filtering (or returning) row["payload"] as if
    it already were that inner dict silently matches nothing — every
    real run_id lookup comes back empty, with no exception anywhere, which
    is exactly the "start succeeds, then the UI sees no url/code/done,
    forever" bug this function used to have. The items returned here have
    `payload` replaced with that unwrapped inner dict — the flat
    {run_id, runtime, event, kind, text} / {run_id, runtime, event, ok,
    error, error_kind} shape routes_gateway.py's /cli/login/{run_id}/events
    hands back verbatim, and the one the frontend's CliLoginOutputPayload
    type (page.tsx) expects at item.payload.event / .kind / .text."""
    events = gateway_state_repository.list_gateway_events(
        gateway_id,
        message_type=CLI_LOGIN_OUTPUT_MESSAGE_TYPE,
        limit=_GATEWAY_EVENTS_PAGE_SIZE,
    )
    clean_run_id = str(run_id or "").strip()
    matching: List[Dict[str, Any]] = []
    for event in events:
        frame = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        inner_payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
        if str(inner_payload.get("run_id") or "").strip() != clean_run_id:
            continue
        matching.append({**event, "payload": inner_payload})
    return matching[-max(int(limit or 0), 1):]
