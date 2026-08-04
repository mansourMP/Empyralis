"""Human-facing translations for gateway/hardware-action offline reason
tokens (Linear MAN-295, subsuming MAN-269).

Diagnosed mechanism: gateway_execution_service.gateway_registration_
execution_readiness() and hardware_runtime_adapters.gateway_adapter.
registration_is_usable() each fail closed with a correct, SPECIFIC reason
token — gateway_capability_missing, gateway_offline, gateway_heartbeat_
stale, and so on. Before this module existed, every one of those distinct
tokens reached the user as the same generic sentence:
hardware_runtime_adapters/gateway_adapter.py hardcoded "Gateway is not
available for this workspace." / "Gateway is not ready for this hardware
action." regardless of which check actually failed. The token carried the
real diagnosis; it was being thrown away at the exact point it should have
become the user-facing message.

gateway_reason_message() is the one place that translation happens now.
Both call sites in gateway_adapter.py already know the reason token AND
(for gateway_capability_missing / gateway_capability_not_ready) the
capability_id that was being requested — passing that through lets a
Docker-gated action (shell.execute / filesystem.read_write — see
empyralis-gateway/src/runtime/desktop-permissions.ts's
DESKTOP_CAPABILITY_PERMISSIONS shell_sandbox entries) get Docker-specific
guidance instead of a generic "capability missing" sentence, matching the
brief's example: "Docker isn't running on this machine. Start Docker
Desktop, then retry."

Every message says what's wrong AND what to do next, in one sentence — per
this project's craft doctrine ("a professional tool labels, it does not
lecture"). Unknown tokens (a reason this module hasn't been taught about
yet, including a future one added on the gateway/backend side without a
matching update here) fall back to a safe, still-actionable generic
sentence — this must never raise, and must never hand the raw snake_case
token straight to a human.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional


def _text(value: object) -> str:
    return str(value or "").strip()


# Capabilities gated behind a locally-confirmed Docker sandbox — mirrors
# empyralis-gateway/src/shell/docker-sandbox.ts (every shell.execute /
# filesystem.read_write call runs inside a debian:bookworm-slim container)
# and desktop-permissions.ts's shell_sandbox permission map. Kept as a
# literal set here (this is Python, that map lives in TypeScript) — must
# stay in sync with DESKTOP_CAPABILITY_PERMISSIONS's shell_sandbox entries.
_DOCKER_GATED_CAPABILITIES = {"shell.execute", "filesystem.read_write"}

_SCREEN_RECORDING_CAPABILITIES = {
    "screenshot.capture",
    "screenshot",
    "screen.capture",
    "ocr",
    "screen.ocr",
    "computer_control.ocr",
}

_ACCESSIBILITY_CAPABILITIES = {
    "mouse",
    "keyboard",
    "mouse.click",
    "mouse.move",
    "keyboard.type",
    "keyboard.press",
    "input.click",
    "input.type",
    "app.focus",
    "window.control",
    "computer_control.move",
    "computer_control.click",
    "computer_control.type",
    "computer_control.key",
}

_BROWSER_CAPABILITIES = {
    "browser.session.start",
    "browser.session.action",
    "browser.session.takeover",
    "browser.session.resume",
    "browser.session.interrupt",
}


def _capability_missing_message(capability_id: str) -> str:
    normalized = _text(capability_id)
    if normalized in _DOCKER_GATED_CAPABILITIES:
        return "Docker isn't running on this machine. Start Docker Desktop, then retry."
    if normalized in _SCREEN_RECORDING_CAPABILITIES:
        return (
            "Screen Recording permission isn't granted on this machine. Grant it in "
            "System Settings › Privacy & Security › Screen Recording, then reconnect."
        )
    if normalized in _ACCESSIBILITY_CAPABILITIES:
        return (
            "Accessibility permission isn't granted on this machine. Grant it in "
            "System Settings › Privacy & Security › Accessibility, then reconnect."
        )
    if normalized in _BROWSER_CAPABILITIES:
        return "Browser control isn't available on this machine yet. Reconnect the gateway, then retry."
    if normalized == "llm.generate":
        return (
            "No local model runtime is ready on this machine. Start Ollama, or sign in "
            "to a CLI (Claude Code, Codex, etc.) on it, then retry."
        )
    if normalized.startswith("cli."):
        return "CLI setup isn't turned on for this machine yet. Enable it in the gateway's own settings, then retry."
    if normalized:
        return f'This machine hasn’t advertised "{normalized}" yet. Reconnect the gateway, then retry.'
    return "This machine hasn't advertised the capability this action needs. Reconnect the gateway, then retry."


def _capability_not_ready_message(capability_id: str) -> str:
    normalized = _text(capability_id)
    if normalized in _DOCKER_GATED_CAPABILITIES:
        return "Docker is starting up on this machine but isn't ready yet. Wait a moment, then retry."
    if normalized:
        return f'"{normalized}" is registered on this machine but isn’t ready yet. Wait a moment, then retry.'
    return "This action's capability is registered but isn't ready yet. Wait a moment, then retry."


# Reasons whose message never depends on which capability was requested —
# these come from registration/connection-level checks (gateway_adapter.
# registration_is_usable / the first half of gateway_registration_execution_
# readiness), not from the specific capability being dispatched.
_STATIC_REASON_MESSAGES: Dict[str, str] = {
    "gateway_registration_missing": (
        "No paired computer is registered for this workspace. Pair one from the Hardware page."
    ),
    "gateway_registration_inactive": (
        "This computer's pairing isn't active. Re-pair it from the Hardware page."
    ),
    "gateway_device_revoked": (
        "This computer's access was revoked. Re-pair it from the Hardware page to use it again."
    ),
    "gateway_workspace_mismatch": (
        "This computer is paired with a different workspace. Pair it with this workspace, "
        "or switch to the workspace it's paired with."
    ),
    "gateway_offline": (
        "This computer isn't connected right now. Make sure it's powered on and the "
        "Empyralis gateway is running, then retry."
    ),
    "gateway_heartbeat_stale": (
        "This computer hasn't checked in recently, so its status can't be trusted right now. "
        "Confirm it's online and reachable, then retry."
    ),
    "gateway_unhealthy": (
        "This computer reported itself unhealthy. Check its status on the Hardware page, then retry."
    ),
}

# Reasons whose message depends on WHICH capability was being requested —
# see _capability_missing_message / _capability_not_ready_message above.
_DYNAMIC_REASON_MESSAGE_BUILDERS: Dict[str, Callable[[str], str]] = {
    "gateway_capability_missing": _capability_missing_message,
    "gateway_capability_not_ready": _capability_not_ready_message,
}

_FALLBACK_MESSAGE = (
    "This computer isn't ready for this action right now. Check its status on the Hardware page, then retry."
)


def gateway_reason_message(reason: object, *, capability_id: object = None) -> str:
    """Translates a gateway/hardware-action reason token into one plain-
    language, actionable sentence — the single boundary where an internal
    reason token becomes user-facing text.

    Never raises. An unrecognized or empty token degrades to a safe, still-
    actionable generic message rather than surfacing the raw token or
    blowing up the caller — this function sits on a failure path, so it
    must not introduce a second one.
    """
    normalized_reason = _text(reason)
    if not normalized_reason:
        return _FALLBACK_MESSAGE
    static_message = _STATIC_REASON_MESSAGES.get(normalized_reason)
    if static_message:
        return static_message
    builder = _DYNAMIC_REASON_MESSAGE_BUILDERS.get(normalized_reason)
    if builder:
        return builder(_text(capability_id))
    return _FALLBACK_MESSAGE
