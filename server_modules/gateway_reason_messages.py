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

# openclaw_provisioning_service.OPENCLAW_PROVISION_CAPABILITY /
# openclaw_channel_setup_service.OPENCLAW_CHANNEL_SETUP_CAPABILITY. Kept as a
# literal pair here (this module must not import those services — they are
# the CALLERS of gateway_reason_message, and importing them back would be a
# cycle) rather than a shared constant. Without this branch, a capability-
# missing openclaw action falls through to the generic message below, which
# does two things wrong for this specific case: it names the raw capability
# id verbatim ("openclaw.provision" is exactly as much an internal enum to a
# customer as the reason token it replaces), and it says "Reconnect the
# gateway, then retry" — a promise this action cannot keep, because the
# transport was never installed and no amount of reconnecting installs it.
_OPENCLAW_TRANSPORT_CAPABILITIES = {"openclaw.provision", "openclaw.channel_setup"}

# service_statuses values gateway_inventory_service.SERVICE_STATUSES actually
# allows (mirrored here as the confirmed-not-ready subset, deliberately
# EXCLUDING "ready" and "unknown" — see _docker_confirmed_not_ready below).
_DOCKER_CONFIRMED_NOT_READY_STATUSES = {"offline", "missing", "degraded", "blocked"}


def _service_statuses(value: object) -> Dict[str, str]:
    """Normalizes whatever a caller passed as `service_statuses` into a
    plain lowercased-key/value dict, or {} for anything not shaped like
    one. Never raises — this only ever feeds a failure-path message."""
    if not isinstance(value, dict):
        return {}
    result: Dict[str, str] = {}
    for key, item in value.items():
        key_token = _text(key).lower()
        if key_token:
            result[key_token] = _text(item).lower()
    return result


def _docker_confirmed_not_ready(service_statuses: Dict[str, str]) -> bool:
    """True only when the GATEWAY ITSELF most recently reported Docker as
    not ready — never inferred from the capability id alone. Proven live on
    production (MAN-XXX): a gateway whose own reported
    metadata.capability_readiness.service_statuses.docker was "ready" still
    hit gateway_capability_missing for shell.execute (a genuinely different
    cause — see gateway_execution_service.gateway_registration_execution_
    readiness's own _has_gateway_capability, which is about whether the
    capability was ever REQUESTED, not about Docker at all), and the
    founder was told across several days to start a Docker Desktop that was
    never the problem. "unknown" (the gateway hasn't reported this service
    at all, or reported a status this module doesn't recognize) is
    deliberately NOT treated as confirmed-not-ready — an absent signal is
    not evidence, and guessing from it is exactly the bug this exists to
    stop. "ready" is the strongest possible signal that Docker is NOT the
    cause."""
    return service_statuses.get("docker", "") in _DOCKER_CONFIRMED_NOT_READY_STATUSES


# The registration's own `platform` field (empyralis-gateway/src/runtime/
# runtime-metadata.ts's os.platform() — "darwin"/"linux"/"win32"). Docker
# Desktop is a real, correctly-named product on macOS AND Windows — it is
# LINUX that has no such app; a headless VPS runs the `docker` daemon
# directly (e.g. via systemd), never "Docker Desktop". CLAUDE.md's own
# record of this message ("DOCKER CHOOSES HOW A COMMAND RUNS, NEVER
# WHETHER") kept it deliberately in place for boxes still on a pre-fix
# gateway build that genuinely gates shell_sandbox on Docker — but never
# checked whether its WORDING was platform-correct. "Start Docker Desktop"
# handed to a Linux VPS owner is an instruction that cannot be followed, on
# any box, old build or new.
_LINUX_PLATFORM_TOKENS = {"linux"}
_MACOS_PLATFORM_TOKENS = {"darwin", "macos", "mac", "osx"}
_WINDOWS_PLATFORM_TOKENS = {"win32", "windows", "win"}


def _normalize_platform(platform: object) -> str:
    """"" for anything unrecognized/absent — never guessed. A caller with no
    evidence of which OS this box runs gets the platform-neutral message
    below, the same "no evidence, don't guess a specific claim" discipline
    _docker_confirmed_not_ready already applies to WHETHER Docker is the
    cause; this applies it to WHICH APP NAME to say."""
    token = _text(platform).lower()
    if token in _MACOS_PLATFORM_TOKENS:
        return "macos"
    if token in _WINDOWS_PLATFORM_TOKENS:
        return "windows"
    if token in _LINUX_PLATFORM_TOKENS:
        return "linux"
    return ""


def _docker_not_running_message(platform: object) -> str:
    normalized = _normalize_platform(platform)
    if normalized == "linux":
        return (
            "Docker isn't running on this machine. Start the Docker service "
            "(for example, `sudo systemctl start docker`), then retry."
        )
    if normalized in {"macos", "windows"}:
        return "Docker isn't running on this machine. Start Docker Desktop, then retry."
    # Unknown platform: never name a specific app we have no evidence this
    # box even has installed.
    return "Docker isn't running on this machine. Start Docker, then retry."


def _capability_missing_message(
    capability_id: str, service_statuses: Optional[Dict[str, str]] = None, platform: object = None
) -> str:
    normalized = _text(capability_id)
    statuses = service_statuses or {}
    if normalized in _DOCKER_GATED_CAPABILITIES:
        if _docker_confirmed_not_ready(statuses):
            return _docker_not_running_message(platform)
        # Docker being ready (or simply unreported) means the real cause of
        # gateway_capability_missing is something else entirely — the
        # capability was never advertised as requested at all (see
        # _docker_confirmed_not_ready's docstring). Falling through to the
        # honest generic message below rather than naming a cause this
        # function did not verify — a wrong, specific, actionable-sounding
        # instruction sends the customer on an errand that can never help,
        # which is worse than an honest "I don't know exactly why."
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
    if normalized in _OPENCLAW_TRANSPORT_CAPABILITIES:
        # No "reconnect"/"retry" promise: the box is reachable (that check
        # already passed), the channel transport has simply never been
        # installed on it, and reconnecting cannot install it. One honest
        # fact, no control implied — see FleetAgentDetail.tsx's toolbar,
        # which renders no "Re-check this computer" button for this state.
        return "Channels aren't set up on this computer yet."
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


def _capability_not_ready_message(
    capability_id: str, service_statuses: Optional[Dict[str, str]] = None, platform: object = None
) -> str:
    # `platform` is accepted (never used) only so this builder shares one
    # call signature with _capability_missing_message — its own Docker
    # message ("...isn't ready yet. Wait a moment...") never names an app,
    # so there is nothing here to get platform-wrong.
    del platform
    normalized = _text(capability_id)
    statuses = service_statuses or {}
    if normalized in _DOCKER_GATED_CAPABILITIES:
        if _docker_confirmed_not_ready(statuses):
            return "Docker is starting up on this machine but isn't ready yet. Wait a moment, then retry."
        # Same evidence discipline as _capability_missing_message above:
        # gateway_capability_not_ready means the capability WAS requested
        # but the gateway hasn't reported it ready yet (see
        # gateway_execution_service._heartbeat_capability_ready) — that can
        # be Docker still starting, but only the gateway's own reported
        # service_statuses.docker actually says so. Falls through to the
        # honest generic message below otherwise.
    if normalized in _OPENCLAW_TRANSPORT_CAPABILITIES:
        # Distinct from _capability_missing_message above: the box HAS
        # advertised this one, it just isn't finished yet — "wait" is an
        # honest instruction here in a way it is not for capability_missing.
        return "This computer is still finishing channel setup. Wait a moment, then retry."
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
_DYNAMIC_REASON_MESSAGE_BUILDERS: Dict[str, Callable[[str, Dict[str, str], object], str]] = {
    "gateway_capability_missing": _capability_missing_message,
    "gateway_capability_not_ready": _capability_not_ready_message,
}

_FALLBACK_MESSAGE = (
    "This computer isn't ready for this action right now. Check its status on the Hardware page, then retry."
)

# Every reason token this module recognizes, from BOTH tables — the single
# source callers use to tell "an internal token we can translate" from
# "already-human prose from a different raise site on the same seam" (see
# humanize_if_reason_token below). Also what
# test_openclaw_readiness_reason_leak.py's drift test checks the PRODUCER
# (gateway_execution_service.gateway_registration_execution_readiness)
# against — enumerated by source-scanning that function rather than hand-
# copied here, so a token added to the producer without a matching entry in
# either table below fails that test loudly instead of leaking a raw token
# to a customer screen.
KNOWN_REASON_TOKENS: frozenset[str] = frozenset(_STATIC_REASON_MESSAGES) | frozenset(
    _DYNAMIC_REASON_MESSAGE_BUILDERS
)


def gateway_reason_message(
    reason: object, *, capability_id: object = None, service_statuses: object = None, platform: object = None,
) -> str:
    """Translates a gateway/hardware-action reason token into one plain-
    language, actionable sentence — the single boundary where an internal
    reason token becomes user-facing text.

    `service_statuses` is the gateway's own most recently reported
    metadata.capability_readiness.service_statuses (e.g. {"docker": "ready"}
    — see gateway_registry_service.capability_service_statuses), passed by
    a caller who actually looked it up. It is EVIDENCE, not a hint: a
    capability-specific claim (e.g. "Docker isn't running") is only ever
    made when this confirms it. Omitting it does not make the message
    wrong — it makes the message fall back to something honestly generic
    instead of guessing, which is the whole point (see
    _docker_confirmed_not_ready's docstring for the live incident this
    closes: a gateway that had already reported Docker "ready" was still
    told, across several days, to go start Docker Desktop).

    `platform` is the SAME kind of evidence, one level down: WHICH Docker
    app to name once Docker actually is the confirmed cause. It is the
    registration's own `platform` field ("darwin"/"linux"/"win32") — a
    caller who has it should pass it (see gateway_adapter.py's/skills_
    service.py's call sites, which already have `registration` in scope for
    the service_statuses lookup above). Omitting it does not make the
    message wrong either — the Docker sentence degrades to a platform-
    neutral "Start Docker" rather than guessing "Desktop" on a box that
    might be a headless Linux VPS.

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
        return builder(_text(capability_id), _service_statuses(service_statuses), platform)
    return _FALLBACK_MESSAGE


def humanize_if_reason_token(
    raw_message: object, *, capability_id: object = None, service_statuses: object = None, platform: object = None,
) -> str:
    """Like gateway_reason_message() above, but for a caller that caught an
    exception whose message MIGHT be one of this module's internal snake_case
    tokens, or might already be human prose from a different raise site on
    the same seam (e.g. `_require_active_gateway_registration` in
    gateway_execution_service.py raises "Gateway registration was not
    found." directly — already a sentence, not a token).

    gateway_reason_message() itself cannot be reused for this: it must
    degrade any UNRECOGNIZED input to the generic fallback (that is its own
    contract, documented above), which would silently discard whatever
    specific — and often already fine — prose the caller had. This function
    only translates input that IS one of the known tokens; anything else
    passes through byte-for-byte.

    Never raises, for the same reason gateway_reason_message never does:
    this sits on a failure path and must not introduce a second one.
    """
    text = _text(raw_message)
    if text in KNOWN_REASON_TOKENS:
        return gateway_reason_message(
            text, capability_id=capability_id, service_statuses=service_statuses, platform=platform
        )
    return text
