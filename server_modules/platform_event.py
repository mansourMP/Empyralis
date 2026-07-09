"""
PlatformEvent — structured platform notification for channels and web chat.

Every system-generated user-facing message must use a PlatformEvent constant.
The channel layer NEVER speaks as the agent. It is a transport pigeon.

PlatformEvent instances are FROZEN constants defined at module level.
Callers reference `.channel_text` (for Telegram/Discord/SMS) or
`.to_intervention()` (for web chat UI cards).

The channel_text MUST NOT contain: I, I'm, my, you've, your, sorry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True, frozen=True)
class PlatformEvent:
    """Immutable platform-generated notification.

    Attributes:
        code: Machine-readable identifier (e.g. "ai_limit_reached").
        title: Short heading for web UI cards.
        detail: Human-readable explanation.
        channel_text: Generic, non-personalized text for channels.
        severity: "info" | "warning" | "error".
        kind: AgentTurnInterventionKind subset.
        status: Optional status label ("blocked", "failed", "ready", etc.).
    """

    code: str
    title: str
    detail: str
    channel_text: str
    severity: str = "info"
    kind: str = "system_notice"
    status: Optional[str] = None

    def to_intervention(
        self,
        *,
        run_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Build a web chat intervention dict from this event.

        Delegates to the existing build_intervention() so web chat rendering
        works identically.
        """
        from server_modules.direct_chat_intervention_service import build_intervention

        return build_intervention(
            kind=self.kind,
            title=self.title,
            detail=self.detail,
            severity=self.severity,
            status=self.status,
            code=self.code,
            run_id=run_id,
            metadata=metadata,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Sage availability
# ═══════════════════════════════════════════════════════════════════════════

SAGE_OVERFLOW = PlatformEvent(
    code="sage_context_overflow",
    title="Context compacted",
    detail="Context was too full and has been automatically compacted. Please resend the message.",
    channel_text="Context was too full and has been compacted. Please resend the message.",
    severity="warning",
)

SAGE_UNAVAILABLE = PlatformEvent(
    code="sage_unavailable",
    title="Temporarily unavailable",
    detail="The assistant is temporarily unavailable. Please try again in a moment.",
    channel_text="The assistant is temporarily unavailable. Please try again in a moment.",
    severity="warning",
)

SAGE_COMPACTED = PlatformEvent(
    code="sage_compacted",
    title="Context compacted",
    detail="Context has been compacted.",
    channel_text="Context compacted.",
    severity="info",
)

SAGE_COMPACT_NOT_NEEDED = PlatformEvent(
    code="sage_compact_not_needed",
    title="Nothing to compact",
    detail="Nothing to compact — context is still small.",
    channel_text="Nothing to compact — context is still small.",
    severity="info",
)

SAGE_NEW_SESSION = PlatformEvent(
    code="sage_new_session",
    title="New session started",
    detail="New session started. Type /main to return to the main thread.",
    channel_text="New session started. Type /main to return to the main thread.",
    severity="info",
)

SAGE_MAIN_RETURN = PlatformEvent(
    code="sage_main_return",
    title="Main thread",
    detail="Back to the main thread.",
    channel_text="Back to the main thread.",
    severity="info",
)

SAGE_NO_MEMORIES = PlatformEvent(
    code="sage_no_memories",
    title="No memories",
    detail="No memories saved yet.",
    channel_text="No memories saved yet.",
    severity="info",
)

SAGE_HELP = PlatformEvent(
    code="sage_help",
    title="Help",
    detail="Available commands:\n"
    "/compact — summarize and clear old context\n"
    "/new — start a new task session\n"
    "/main — return to the main Sage thread\n"
    "/memory — show stored memory\n"
    "/help — show this message",
    channel_text="Available commands:\n"
    "/compact — summarize and clear old context\n"
    "/new — start a new task session\n"
    "/main — return to the main Sage thread\n"
    "/memory — show stored memory\n"
    "/help — show this message",
    severity="info",
)


# ═══════════════════════════════════════════════════════════════════════════
# Error classification (classify_error buckets)
# ═══════════════════════════════════════════════════════════════════════════

AI_LIMIT_REACHED = PlatformEvent(
    code="ai_limit_reached",
    title="AI credit limit reached",
    detail="Credit exhausted. Add an API key or top up to continue.",
    channel_text="Credit exhausted. Add an API key or top up to continue.",
    severity="error",
)

SERVICE_RATE_LIMITED = PlatformEvent(
    code="service_rate_limited",
    title="Service rate limited",
    detail="The service is being rate limited. Try again in a moment.",
    channel_text="The service is being rate limited. Try again in a moment.",
    severity="warning",
)

AUTH_FAILED = PlatformEvent(
    code="ai_auth_failed",
    title="AI authentication failed",
    detail="AI service authentication failed. Verify the API key configuration.",
    channel_text="AI service authentication failed. Verify the API key configuration.",
    severity="error",
)

# Platform-credits variant: the customer never configured a key, so the
# message must never send them to "verify" one. Same failure class as
# AUTH_FAILED, different ownership — this is a platform-side issue.
AUTH_FAILED_PLATFORM = PlatformEvent(
    code="ai_auth_failed_platform",
    title="AI connection needs attention",
    detail="The AI connection for this workspace needs attention on the platform side. Try again shortly.",
    channel_text="The AI connection for this workspace needs attention on the platform side. Try again shortly.",
    severity="error",
)

# Provider-side balance/payment failure (HTTP 402 or equivalent) — distinct
# from AUTH_FAILED (401/403): the key works, the account behind it is empty.
# Split by who owns that account.
PROVIDER_PAYMENT_REQUIRED_PLATFORM = PlatformEvent(
    code="provider_payment_required_platform",
    title="AI credits unavailable",
    detail="The shared AI credits for this workspace are unavailable right now. This is a platform-side issue — try again shortly.",
    channel_text="The shared AI credits for this workspace are unavailable right now. This is a platform-side issue — try again shortly.",
    severity="error",
)

PROVIDER_PAYMENT_REQUIRED_BYOK = PlatformEvent(
    code="provider_payment_required_byok",
    title="Provider balance exhausted",
    detail="The connected provider account is out of balance. Add credit with the provider to continue.",
    channel_text="The connected provider account is out of balance. Add credit with the provider to continue.",
    severity="error",
)

PROVIDER_UNREACHABLE = PlatformEvent(
    code="provider_unreachable",
    title="AI service unreachable",
    detail="The AI service is unreachable right now. Try again shortly.",
    channel_text="The AI service is unreachable right now. Try again shortly.",
    severity="warning",
)

# A turn ended with nothing substantive to say and at least one tool got
# blocked by policy (not enabled for this agent) along the way. Generic on
# purpose — the blocked-tool records available here are internal codes, not
# reliably human-readable tool names, so naming a specific tool risks
# surfacing something confusing. Still strictly better than the silence or
# unrelated error this replaces.
TOOLS_LIMITED_NO_REPLY = PlatformEvent(
    code="tools_limited_no_reply",
    title="Limited by current tool settings",
    detail="This agent doesn't have every tool turned on, which may be why nothing came back. An owner can enable more under Tools.",
    channel_text="This agent doesn't have every tool turned on, which may be why nothing came back. An owner can enable more under Tools.",
    severity="warning",
)

GENERIC_ERROR = PlatformEvent(
    code="generic_error",
    title="Something went wrong",
    detail="Something went wrong. Try again.",
    channel_text="Something went wrong. Try again.",
    severity="error",
)

NO_AI_PROVIDER = PlatformEvent(
    code="no_ai_provider",
    title="AI provider not configured",
    detail="Your agent needs an AI provider. Connect one →",
    channel_text="Your agent needs an AI provider. Connect one →",
    severity="error",
)

NO_AI_PROVIDER_WEB = PlatformEvent(
    code="no_ai_provider_web",
    title="AI provider not configured",
    detail="Your agent needs an AI provider. Open AI & Setup →",
    channel_text="Your agent needs an AI provider. Open AI & Setup →",
    severity="error",
)

# Web-chat variants (plain text, no escaping)
AI_LIMIT_REACHED_WEB = PlatformEvent(
    code="ai_limit_reached_web",
    title="AI limit reached",
    detail="AI usage limit reached. Open AI & Setup →",
    channel_text="AI usage limit reached. Open AI & Setup →",
    severity="error",
)

AUTH_FAILED_WEB = PlatformEvent(
    code="ai_auth_failed_web",
    title="AI needs attention",
    detail="AI configuration needs attention. Open AI & Setup →",
    channel_text="AI configuration needs attention. Open AI & Setup →",
    severity="error",
)


# ═══════════════════════════════════════════════════════════════════════════
# Quota / rate limiting
# ═══════════════════════════════════════════════════════════════════════════

THREAD_BUSY = PlatformEvent(
    code="thread_busy",
    title="Conversation busy",
    detail="This conversation is still processing the previous message. One moment.",
    channel_text="This conversation is still processing the previous message. One moment.",
    severity="info",
)

AGENT_LIMIT_EXCEEDED = PlatformEvent(
    code="agent_limit_exceeded",
    title="Agent busy",
    detail="This Business Agent is helping other customers right now. Please try again in a moment.",
    channel_text="This Business Agent is helping other customers right now. Please try again in a moment.",
    severity="warning",
)

WORKSPACE_LIMIT_EXCEEDED = PlatformEvent(
    code="workspace_limit_exceeded",
    title="Workspace busy",
    detail="The workspace is helping other customers right now. Please try again in a moment.",
    channel_text="The workspace is helping other customers right now. Please try again in a moment.",
    severity="warning",
)

WORKSPACE_RATE_LIMITED = PlatformEvent(
    code="workspace_rate_limited",
    title="Too many requests",
    detail="Too many requests right now. Please try again in a moment.",
    channel_text="Too many requests right now. Please try again in a moment.",
    severity="warning",
)

RUNTIME_CAP_EXCEEDED = PlatformEvent(
    code="runtime_cap_exceeded",
    title="Request taking too long",
    detail="The request is taking longer than the current service window allows. Please try again in a moment.",
    channel_text="The request is taking longer than the current service window allows. Please try again in a moment.",
    severity="warning",
)

SYSTEM_BUSY_FALLBACK = PlatformEvent(
    code="system_busy",
    title="System busy",
    detail="The system is busy. Please try again in a moment.",
    channel_text="The system is busy. Please try again in a moment.",
    severity="warning",
)


# ═══════════════════════════════════════════════════════════════════════════
# Channel execution
# ═══════════════════════════════════════════════════════════════════════════

CHANNEL_EXECUTION_FAILED = PlatformEvent(
    code="channel_execution_failed",
    title="Internal error",
    detail="An internal problem occurred while handling this message. Please try again in a moment.",
    channel_text="An internal problem occurred while handling this message. Please try again in a moment.",
    severity="error",
)


# ═══════════════════════════════════════════════════════════════════════════
# Run summaries (autopilot / telegram run dispatch)
# ═══════════════════════════════════════════════════════════════════════════

RUN_GENERIC_ERROR = PlatformEvent(
    code="run_generic_error",
    title="Something went wrong",
    detail="Something went wrong. Please try again.",
    channel_text="Something went wrong. Please try again.",
    severity="error",
)

RUN_GATEWAY_TIMEOUT = PlatformEvent(
    code="run_gateway_timeout",
    title="Gateway taking too long",
    detail="Still working on it, but Gateway is taking too long. One moment and try again.",
    channel_text="Still working on it, but Gateway is taking too long. One moment and try again.",
    severity="warning",
)

RUN_GATEWAY_OFFLINE = PlatformEvent(
    code="run_gateway_offline",
    title="Gateway offline",
    detail="Gateway is offline right now. Start it in Setup and try again.",
    channel_text="Gateway is offline right now. Start it in Setup and try again.",
    severity="warning",
)

RUN_NOT_FOUND = PlatformEvent(
    code="run_not_found",
    title="Request not found",
    detail="That request could not be found. Please send it again.",
    channel_text="That request could not be found. Please send it again.",
    severity="error",
)

RUN_MODEL_REPLY_FAILED = PlatformEvent(
    code="run_model_reply_failed",
    title="Model reply unavailable",
    detail="A model reply could not be generated right now. Please retry in a moment.",
    channel_text="A model reply could not be generated right now. Please retry in a moment.",
    severity="error",
)

RUN_AI_AUTH_FAILED = PlatformEvent(
    code="run_ai_auth_failed",
    title="AI auth failed",
    detail="AI account authorization failed. Open Setup and reconnect the AI account.",
    channel_text="AI account authorization failed. Open Setup and reconnect the AI account.",
    severity="error",
)

RUN_NO_AI_ACCOUNT = PlatformEvent(
    code="run_no_ai_account",
    title="No AI account",
    detail="No valid AI account is connected. Open Setup and connect an account.",
    channel_text="No valid AI account is connected. Open Setup and connect an account.",
    severity="error",
)

RUN_NO_MODEL_CONNECTION = PlatformEvent(
    code="run_no_model_connection",
    title="No model connection",
    detail="No working model connection is available right now. Please retry in a moment.",
    channel_text="No working model connection is available right now. Please retry in a moment.",
    severity="error",
)

RUN_NEEDS_GATEWAY = PlatformEvent(
    code="run_needs_gateway",
    title="Gateway required",
    detail="That task needs Gateway first. Start Gateway in Setup and try again.",
    channel_text="That task needs Gateway first. Start Gateway in Setup and try again.",
    severity="warning",
)

RUN_SAFETY_BLOCKED = PlatformEvent(
    code="run_safety_blocked",
    title="Action blocked by safety",
    detail="That action is blocked by the current safety settings. Review trust settings and try again.",
    channel_text="That action is blocked by the current safety settings. Review trust settings and try again.",
    severity="warning",
)

RUN_FAILED = PlatformEvent(
    code="run_failed",
    title="Run failed",
    detail="Something went wrong while handling that request. Please try again.",
    channel_text="Something went wrong while handling that request. Please try again.",
    severity="error",
)

RUN_TIMEOUT = PlatformEvent(
    code="run_timeout",
    title="Taking longer than expected",
    detail="The request is taking longer than expected. Please try again in a moment.",
    channel_text="The request is taking longer than expected. Please try again in a moment.",
    severity="warning",
)

RUN_FINISHED = PlatformEvent(
    code="run_finished",
    title="Run finished",
    detail="Run finished.",
    channel_text="Run finished.",
    severity="info",
)


# ═══════════════════════════════════════════════════════════════════════════
# AI account / scope errors
# ═══════════════════════════════════════════════════════════════════════════

AI_SCOPE_MISSING = PlatformEvent(
    code="ai_scope_missing",
    title="AI account permissions needed",
    detail="Additional permissions are required for this action. Open Setup and reconnect the affected app.",
    channel_text="Additional permissions are required for this action. Open Setup and reconnect the affected app.",
    severity="error",
)


# ═══════════════════════════════════════════════════════════════════════════
# Guaranteed fallback
# ═══════════════════════════════════════════════════════════════════════════

GUARANTEED_FALLBACK = PlatformEvent(
    code="guaranteed_fallback",
    title="No response produced",
    detail="The message was processed but no response could be produced. Please try again.",
    channel_text="The message was processed but no response could be produced. Please try again.",
    severity="warning",
)


# ═══════════════════════════════════════════════════════════════════════════
# Command registry
# ═══════════════════════════════════════════════════════════════════════════

SERVICE_TEMPORARILY_UNAVAILABLE = PlatformEvent(
    code="service_temporarily_unavailable",
    title="Temporarily unavailable",
    detail="This service is temporarily unavailable. Please try again shortly.",
    channel_text="This service is temporarily unavailable. Please try again shortly.",
    severity="warning",
)


# ═══════════════════════════════════════════════════════════════════════════
# Dict mappings (for existing dict-based lookup code paths)
# ═══════════════════════════════════════════════════════════════════════════

# Maps classify_error keys to their PlatformEvents
ERROR_CLASSIFICATION_MAP: dict[str, PlatformEvent] = {
    "ai_limit": AI_LIMIT_REACHED,
    "rate_limited": SERVICE_RATE_LIMITED,
    "auth_failed": AUTH_FAILED,
    "provider_unreachable": PROVIDER_UNREACHABLE,
    "generic_error": GENERIC_ERROR,
}

# Maps quota reasons to PlatformEvents (used by _CHANNEL_REPLY_BY_REASON)
QUOTA_REPLY_MAP: dict[str, PlatformEvent] = {
    "thread_busy": THREAD_BUSY,
    "agent_limit_exceeded": AGENT_LIMIT_EXCEEDED,
    "workspace_limit_exceeded": WORKSPACE_LIMIT_EXCEEDED,
    "workspace_rate_limited": WORKSPACE_RATE_LIMITED,
    "runtime_cap_exceeded": RUNTIME_CAP_EXCEEDED,
}

# Maps run summary keywords to PlatformEvents
RUN_SUMMARY_MAP: dict[str, PlatformEvent] = {
    "gateway_timeout": RUN_GATEWAY_TIMEOUT,
    "gateway_offline": RUN_GATEWAY_OFFLINE,
    "run_not_found": RUN_NOT_FOUND,
    "model_reply_failed": RUN_MODEL_REPLY_FAILED,
    "ai_auth_failed": RUN_AI_AUTH_FAILED,
    "no_ai_account": RUN_NO_AI_ACCOUNT,
    "no_model_connection": RUN_NO_MODEL_CONNECTION,
    "needs_gateway": RUN_NEEDS_GATEWAY,
    "safety_blocked": RUN_SAFETY_BLOCKED,
    "run_failed": RUN_FAILED,
    "run_timeout": RUN_TIMEOUT,
    "scope_missing": AI_SCOPE_MISSING,
}
