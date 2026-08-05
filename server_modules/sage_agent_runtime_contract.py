from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

SAGE_MODE = "owner_sage"
SAGE_RUNTIME_KIND = "sage_main_runtime"
SAGE_ALLOWED_SURFACES = {"chat", "mobile", "web", "desktop", "voice"}

SAGE_RESPONSE_KEYS: tuple[str, ...] = (
    "message",
    "error",
    "used_context",
    "tool_calls",
    "available_tools",
    "blocked_tools",
    "approvals_required",
    "memory_updates",
    "proof_log",
    "proof_log_id",
    "trace_id",
)

SAGE_MODE_ALIASES: dict[str, str] = {
    "sage": SAGE_MODE,
    "owner": SAGE_MODE,
    "personal": SAGE_MODE,
}


@dataclass(frozen=True, slots=True)
class SageTurnContract:
    workspace_id: str
    tenant_id: str
    message: str
    mode: str = SAGE_MODE
    surface: str = "chat"
    trace_id: str = ""
    actor_user_id: str = ""
    actor_email: str = ""
    actor_auth_type: str = ""
    # Mandate: defaults to "owner" because SAGE_MODE ("owner_sage") is only
    # ever constructed for an authenticated owner session today — see
    # authority_mandate_service.py. Not currently constructed outside tests;
    # a future caller reusing this contract for non-owner turns must set it
    # explicitly rather than rely on this default.
    authority_tier: str = "owner"


@dataclass(frozen=True, slots=True)
class SageTurnResult:
    message: str
    error: Optional[str] = None
    used_context: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    available_tools: list[dict] = field(default_factory=list)
    blocked_tools: list[dict] = field(default_factory=list)
    approvals_required: list[dict] = field(default_factory=list)
    memory_updates: list[dict] = field(default_factory=list)
    tool_progress_messages: list[str] = field(default_factory=list)
    proof_log: Optional[dict] = None
    proof_log_id: str = ""
    trace_id: str = ""
    provider: str = ""
    model: Optional[str] = None
    ai_setup_url: str = ""
    # Outbound attachments (image/voice/audio/video/file) the turn wants sent
    # alongside `message` — populated by the send_image tool and by
    # generate_image's channel-context auto-attach (see skills_service.py's
    # execute_single_direct_tool_call). Each item is shaped
    # {kind, source_path|source_url, mime_type?, caption?, as_voice?},
    # matching gateway_protocol_service.dispatch_channel_outbound's media
    # contract. Empty for every turn that never called a media-producing tool.
    media: list[dict] = field(default_factory=list)
    # claude_agent_sdk-engine turns only — a verbatim copy of claude_agent_sdk
    # .ContextUsageResponse (categories/totalTokens/maxTokens/percentage/...),
    # best-effort attached by run_claude_agent_sdk_turn via
    # ClaudeSDKClient.get_context_usage() and threaded through unmodified by
    # handle_sage_chat. None for every other engine/mode (legacy tool loop,
    # gateway_brain local/cli_subscription) — those never populate this key,
    # so it stays None rather than a fabricated/zeroed chart.
    context_usage: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "message": self.message,
            "error": self.error,
            "used_context": list(self.used_context),
            "tool_calls": list(self.tool_calls),
            "available_tools": list(self.available_tools),
            "blocked_tools": list(self.blocked_tools),
            "approvals_required": list(self.approvals_required),
            "memory_updates": list(self.memory_updates),
            "tool_progress_messages": list(self.tool_progress_messages),
            "proof_log": self.proof_log,
            "proof_log_id": self.proof_log_id,
            "trace_id": self.trace_id,
            "provider": self.provider,
            "model": self.model,
            "ai_setup_url": self.ai_setup_url,
            "media": list(self.media),
            "context_usage": self.context_usage,
        }


def normalize_sage_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if not normalized:
        raise ValueError("Sage mode is required.")
    if normalized in SAGE_MODE_ALIASES:
        return SAGE_MODE_ALIASES[normalized]
    if normalized == SAGE_MODE:
        return SAGE_MODE
    raise ValueError(f"Unsupported mode: {mode}. Only {SAGE_MODE} is allowed.")


def normalize_sage_surface(surface: str) -> str:
    normalized = str(surface or "").strip().lower()
    if not normalized:
        return "chat"
    if normalized in SAGE_ALLOWED_SURFACES:
        return normalized
    raise ValueError(f"Unsupported surface: {surface}. Allowed: {sorted(SAGE_ALLOWED_SURFACES)}")


def is_sage_mode(mode: str) -> bool:
    try:
        normalize_sage_mode(mode)
        return True
    except ValueError:
        return False


def build_sage_policy_context() -> dict:
    return {
        "sage_mode": SAGE_MODE,
        "runtime_kind": SAGE_RUNTIME_KIND,
    }
