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
    "/main — return to the main agent thread\n"
    "/memory — show stored memory\n"
    "/help — show this message",
    channel_text="Available commands:\n"
    "/compact — summarize and clear old context\n"
    "/new — start a new task session\n"
    "/main — return to the main agent thread\n"
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
    # 2026-08-14 fix: this substitution now only fires when
    # sage_agent_runtime_service has positively identified a genuine
    # tool-capability policy block (see _classify_sage_no_reply_outcome) —
    # a KNOWN fact at that point, not a guess, so the copy states it rather
    # than hedging with "may be why".
    detail="This agent doesn't have every tool turned on, which is why nothing came back. An owner can enable more under Tools.",
    channel_text="This agent doesn't have every tool turned on, which is why nothing came back. An owner can enable more under Tools.",
    severity="warning",
)

# 2026-08-14: sibling to TOOLS_LIMITED_NO_REPLY and GENERIC_ERROR for a turn
# that ran and produced no reply for a reason the runtime cannot positively
# name. Before this existed, sage_agent_runtime_service._run_sage_action_
# loop_v3 treated ANY non-empty `blocked_tools` entry as proof the cause was
# disabled tools — but blocked_tools is also where claude_agent_sdk_bridge
# (the production-default engine, provider-general — DeepSeek's Anthropic-
# compatible endpoint included) records provider/execution failures
# (auth/billing/rate-limit/server errors, a raw SDK ResultMessage subtype
# like "error_max_turns", or its own bookkeeping anomalies: a foreign tool
# call, an orphan tool result) that have nothing to do with an agent's tool
# settings. Firing TOOLS_LIMITED_NO_REPLY for those told the customer to go
# fix a setting that was never the cause — a fabricated, unverified
# diagnosis dressed as guidance. This event is the honest alternative: the
# turn ran, nothing came back, the cause is not known from here — with a
# real, actionable next step (retry; the Work tab, which is a real surface
# reachable from this same chat) instead of a guess.
SAGE_TURN_NO_REPLY_UNKNOWN = PlatformEvent(
    code="sage_turn_no_reply_unknown",
    title="No reply came back",
    detail="This turn ran and produced no reply. The cause is not known from here — check the Work tab for what happened, or try again.",
    channel_text="This turn ran and produced no reply. The cause is not known from here — check the Work tab for what happened, or try again.",
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
# cli_subscription (Gateway brain) — an agent's turn runs on the OWNER's own
# Claude Code / Codex subscription, executing on their own paired Gateway.
# Every distinct failure mode gets its own honest event here rather than one
# blanket string — callers compose the final platform-voice line as
# f"Heads up: {event.channel_text}" (sage_agent_runtime_service.
# _friendly_cli_subscription_error). No fallback to platform credits ever.
# ═══════════════════════════════════════════════════════════════════════════

CLI_SUBSCRIPTION_NO_GATEWAY = PlatformEvent(
    code="cli_subscription_no_gateway",
    title="Gateway required",
    detail="cli_subscription mode requires a paired Gateway. Bind one in agent settings.",
    channel_text="cli_subscription requires a Gateway. Bind one in agent settings.",
    severity="error",
)

CLI_SUBSCRIPTION_GATEWAY_NOT_PAIRED = PlatformEvent(
    code="cli_subscription_gateway_not_paired",
    title="Gateway not paired",
    detail="The Gateway bound to this agent is no longer paired to this workspace. Re-pair a Gateway and bind it to this agent.",
    channel_text="the bound Gateway is no longer paired to this workspace. Re-pair a Gateway and bind it to this agent, then retry.",
    severity="error",
)

CLI_SUBSCRIPTION_GATEWAY_OFFLINE = PlatformEvent(
    code="cli_subscription_gateway_offline",
    title="Gateway offline",
    detail="The Gateway bound to this agent is offline. Start it and retry.",
    channel_text="the bound Gateway is offline. Start it on the paired machine and retry.",
    severity="warning",
)

CLI_SUBSCRIPTION_CLAUDE_NOT_INSTALLED = PlatformEvent(
    code="cli_subscription_claude_not_installed",
    title="Claude Code not installed",
    detail="Claude Code is not installed on the bound Gateway. Install it with `npm install -g @anthropic-ai/claude-code`.",
    channel_text="Claude Code is not installed on the Gateway. Run `npm install -g @anthropic-ai/claude-code` on that machine.",
    severity="error",
)

CLI_SUBSCRIPTION_CODEX_NOT_INSTALLED = PlatformEvent(
    code="cli_subscription_codex_not_installed",
    title="Codex not installed",
    detail="Codex is not installed on the bound Gateway. Install it with `npm install -g @openai/codex`.",
    channel_text="Codex is not installed on the Gateway. Run `npm install -g @openai/codex` on that machine.",
    severity="error",
)

CLI_SUBSCRIPTION_CLAUDE_NOT_AUTHENTICATED = PlatformEvent(
    code="cli_subscription_claude_not_authenticated",
    title="Claude Code not signed in",
    detail="Claude Code on the bound Gateway is not signed in. Run `claude login` on that machine.",
    channel_text="Claude Code on the Gateway is not logged in. Run `claude login` on that machine.",
    severity="error",
)

CLI_SUBSCRIPTION_CODEX_NOT_AUTHENTICATED = PlatformEvent(
    code="cli_subscription_codex_not_authenticated",
    title="Codex not signed in",
    detail="Codex on the bound Gateway is not signed in. Run `codex login` on that machine.",
    channel_text="Codex on the Gateway is not logged in. Run `codex login` on that machine.",
    severity="error",
)

CLI_SUBSCRIPTION_TIMEOUT = PlatformEvent(
    code="cli_subscription_timeout",
    title="Generation timed out",
    detail="cli_subscription generation timed out. The CLI may be rate-limited or busy. Retry.",
    channel_text="generation timed out. The CLI may be rate-limited or busy. Retry.",
    severity="warning",
)

CLI_SUBSCRIPTION_CRASH = PlatformEvent(
    code="cli_subscription_crash",
    title="CLI exited unexpectedly",
    detail="The CLI exited unexpectedly on the bound Gateway. Check Gateway logs.",
    channel_text="the CLI exited unexpectedly. Check Gateway logs.",
    severity="error",
)

# xAI Grok Build / Cursor CLI addition — same shape as the Claude/Codex pairs
# above, one distinct event per failure mode.

CLI_SUBSCRIPTION_GROK_BUILD_NOT_INSTALLED = PlatformEvent(
    code="cli_subscription_grok_build_not_installed",
    title="Grok Build not installed",
    detail="Grok Build is not installed on the bound Gateway. Install it with `curl -fsSL https://x.ai/cli/install.sh | bash`.",
    channel_text="Grok Build is not installed on the Gateway. Run `curl -fsSL https://x.ai/cli/install.sh | bash` on that machine.",
    severity="error",
)

CLI_SUBSCRIPTION_CURSOR_NOT_INSTALLED = PlatformEvent(
    code="cli_subscription_cursor_not_installed",
    title="Cursor CLI not installed",
    detail="Cursor CLI is not installed on the bound Gateway. Install it with `curl https://cursor.com/install -fsS | bash`.",
    channel_text="Cursor CLI is not installed on the Gateway. Run `curl https://cursor.com/install -fsS | bash` on that machine.",
    severity="error",
)

CLI_SUBSCRIPTION_GROK_BUILD_NOT_AUTHENTICATED = PlatformEvent(
    code="cli_subscription_grok_build_not_authenticated",
    title="Grok Build not signed in",
    detail="Grok Build on the bound Gateway is not signed in. Run `grok login --device-auth` on that machine.",
    channel_text="Grok Build on the Gateway is not logged in. Run `grok login --device-auth` on that machine.",
    severity="error",
)

CLI_SUBSCRIPTION_CURSOR_NOT_AUTHENTICATED = PlatformEvent(
    code="cli_subscription_cursor_not_authenticated",
    title="Cursor CLI not signed in",
    detail="Cursor CLI on the bound Gateway is not signed in. Run `cursor-agent login` on that machine.",
    channel_text="Cursor CLI on the Gateway is not logged in. Run `cursor-agent login` on that machine.",
    severity="error",
)


# ═══════════════════════════════════════════════════════════════════════════
# cli_setup (BYO-brain install + sign-in) — the one-click install and the
# device-code sign-in handshake for a box operator's OWN Claude Code / Codex
# subscription on their paired Gateway. Distinct from cli_subscription above:
# these fire from the setup/onboarding flow (install, start login, submit a
# pasted-back code), not from a generation turn. The Gateway never reads or
# transmits the credential itself — see cli-login-session.ts.
# ═══════════════════════════════════════════════════════════════════════════

CLI_SETUP_GATEWAY_OFFLINE = PlatformEvent(
    code="cli_setup_gateway_offline",
    title="Gateway offline",
    detail="This Gateway is offline, so install/sign-in cannot run. Start the Gateway on the paired machine and retry.",
    channel_text="the Gateway is offline. Start it on the paired machine and retry.",
    severity="warning",
)

CLI_SETUP_NOT_ENABLED_LOCALLY = PlatformEvent(
    code="cli_setup_not_enabled_locally",
    title="Install/sign-in not enabled on this Gateway",
    detail="The box operator hasn't enabled CLI install/sign-in on this Gateway yet. Set EMPYRALIS_GATEWAY_CLI_SETUP_ENABLED=true on that machine and restart the Gateway.",
    channel_text="CLI install/sign-in isn't enabled on this Gateway yet. The box operator needs to turn it on there first.",
    severity="warning",
)

CLI_SETUP_INSTALL_NPM_MISSING = PlatformEvent(
    code="cli_setup_install_npm_missing",
    title="npm not found",
    detail="npm was not found on the Gateway's PATH, so the CLI could not be installed. Install Node.js/npm on that machine and retry.",
    channel_text="npm isn't on the Gateway's PATH, so nothing could be installed. Install Node.js on that machine and retry.",
    severity="error",
)

CLI_SETUP_INSTALL_DEPENDENCY_MISSING = PlatformEvent(
    code="cli_setup_install_dependency_missing",
    title="A required tool was not found",
    detail="curl or a POSIX shell was not found on the Gateway's PATH, so the install script could not run. Install curl/sh on that machine and retry.",
    channel_text="curl or a shell isn't on the Gateway's PATH, so nothing could be installed. Install curl on that machine and retry.",
    severity="error",
)

CLI_SETUP_INSTALL_PERMISSION_DENIED = PlatformEvent(
    code="cli_setup_install_permission_denied",
    title="Install permission denied",
    detail="npm did not have permission to install globally on the Gateway. Fix npm's global prefix permissions on that machine and retry.",
    channel_text="the install failed with a permission error. Fix npm's global install permissions on the Gateway and retry.",
    severity="error",
)

CLI_SETUP_INSTALL_NETWORK_ERROR = PlatformEvent(
    code="cli_setup_install_network_error",
    title="Install failed — network error",
    detail="The Gateway could not reach the npm registry to install the CLI. Check that machine's network connection and retry.",
    channel_text="the install failed — the Gateway couldn't reach the npm registry. Check its network connection and retry.",
    severity="error",
)

CLI_SETUP_INSTALL_TIMEOUT = PlatformEvent(
    code="cli_setup_install_timeout",
    title="Install timed out",
    detail="The install did not finish in time and was stopped. The Gateway's network or npm registry may be slow. Retry.",
    channel_text="the install timed out and was stopped. Retry — the Gateway's connection or the npm registry may be slow right now.",
    severity="warning",
)

CLI_SETUP_CLAUDE_INSTALL_FAILED = PlatformEvent(
    code="cli_setup_claude_install_failed",
    title="Claude Code install failed",
    detail="Installing Claude Code on the Gateway failed. Check Gateway logs for the underlying npm error.",
    channel_text="installing Claude Code on the Gateway failed. Check Gateway logs for details.",
    severity="error",
)

CLI_SETUP_CODEX_INSTALL_FAILED = PlatformEvent(
    code="cli_setup_codex_install_failed",
    title="Codex install failed",
    detail="Installing Codex on the Gateway failed. Check Gateway logs for the underlying npm error.",
    channel_text="installing Codex on the Gateway failed. Check Gateway logs for details.",
    severity="error",
)

CLI_SETUP_GROK_BUILD_INSTALL_FAILED = PlatformEvent(
    code="cli_setup_grok_build_install_failed",
    title="Grok Build install failed",
    detail="Installing Grok Build on the Gateway failed. Check Gateway logs for the underlying install-script error.",
    channel_text="installing Grok Build on the Gateway failed. Check Gateway logs for details.",
    severity="error",
)

CLI_SETUP_CURSOR_INSTALL_FAILED = PlatformEvent(
    code="cli_setup_cursor_install_failed",
    title="Cursor CLI install failed",
    detail="Installing Cursor CLI on the Gateway failed. Check Gateway logs for the underlying install-script error.",
    channel_text="installing Cursor CLI on the Gateway failed. Check Gateway logs for details.",
    severity="error",
)

CLI_SETUP_CLAUDE_LOGIN_NOT_INSTALLED = PlatformEvent(
    code="cli_setup_claude_login_not_installed",
    title="Claude Code not installed",
    detail="Claude Code is not installed on this Gateway yet. Install it first, then sign in.",
    channel_text="Claude Code isn't installed on the Gateway yet. Install it first, then sign in.",
    severity="error",
)

CLI_SETUP_CODEX_LOGIN_NOT_INSTALLED = PlatformEvent(
    code="cli_setup_codex_login_not_installed",
    title="Codex not installed",
    detail="Codex is not installed on this Gateway yet. Install it first, then sign in.",
    channel_text="Codex isn't installed on the Gateway yet. Install it first, then sign in.",
    severity="error",
)

CLI_SETUP_GROK_BUILD_LOGIN_NOT_INSTALLED = PlatformEvent(
    code="cli_setup_grok_build_login_not_installed",
    title="Grok Build not installed",
    detail="Grok Build is not installed on this Gateway yet. Install it first, then sign in.",
    channel_text="Grok Build isn't installed on the Gateway yet. Install it first, then sign in.",
    severity="error",
)

CLI_SETUP_CURSOR_LOGIN_NOT_INSTALLED = PlatformEvent(
    code="cli_setup_cursor_login_not_installed",
    title="Cursor CLI not installed",
    detail="Cursor CLI is not installed on this Gateway yet. Install it first, then sign in.",
    channel_text="Cursor CLI isn't installed on the Gateway yet. Install it first, then sign in.",
    severity="error",
)

CLI_SETUP_LOGIN_TIMEOUT = PlatformEvent(
    code="cli_setup_login_timeout",
    title="Sign-in timed out",
    detail="Sign-in was not completed in time and the session was closed. Start sign-in again and approve it promptly in your browser.",
    channel_text="sign-in timed out and was closed. Start sign-in again and approve it promptly in your browser.",
    severity="warning",
)

CLI_SETUP_CLAUDE_LOGIN_FAILED = PlatformEvent(
    code="cli_setup_claude_login_failed",
    title="Claude Code sign-in failed",
    detail="The Claude Code sign-in session on the Gateway exited unexpectedly. Check Gateway logs and retry.",
    channel_text="Claude Code sign-in on the Gateway failed unexpectedly. Check Gateway logs and retry.",
    severity="error",
)

CLI_SETUP_CODEX_LOGIN_FAILED = PlatformEvent(
    code="cli_setup_codex_login_failed",
    title="Codex sign-in failed",
    detail="The Codex sign-in session on the Gateway exited unexpectedly. Check Gateway logs and retry.",
    channel_text="Codex sign-in on the Gateway failed unexpectedly. Check Gateway logs and retry.",
    severity="error",
)

CLI_SETUP_GROK_BUILD_LOGIN_FAILED = PlatformEvent(
    code="cli_setup_grok_build_login_failed",
    title="Grok Build sign-in failed",
    detail="The Grok Build sign-in session on the Gateway exited unexpectedly. Check Gateway logs and retry.",
    channel_text="Grok Build sign-in on the Gateway failed unexpectedly. Check Gateway logs and retry.",
    severity="error",
)

CLI_SETUP_CURSOR_LOGIN_FAILED = PlatformEvent(
    code="cli_setup_cursor_login_failed",
    title="Cursor CLI sign-in failed",
    detail=(
        "The Cursor CLI sign-in session on the Gateway exited unexpectedly or timed out. Cursor's own "
        "browser-login flow has known reliability issues over SSH/headless connections — if this keeps "
        "failing, set CURSOR_API_KEY directly in the Gateway's own environment instead."
    ),
    channel_text=(
        "Cursor CLI sign-in on the Gateway failed unexpectedly. If this keeps failing over a remote "
        "connection, set CURSOR_API_KEY directly on that machine instead."
    ),
    severity="error",
)

CLI_SETUP_LOGIN_CANCELLED = PlatformEvent(
    code="cli_setup_login_cancelled",
    title="Sign-in cancelled",
    detail="The sign-in session was cancelled before it completed.",
    channel_text="sign-in was cancelled before it completed.",
    severity="info",
    status="cancelled",
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


# ═══════════════════════════════════════════════════════════════════════════
# Channel suppression — which PlatformEvents may ever reach a channel
# ═══════════════════════════════════════════════════════════════════════════
#
# ABSOLUTE RULE (2026-07-18 incident): no hardcoded status/error/failure
# message may EVER be sent into a channel — Telegram, WhatsApp, Discord,
# Signal, iMessage, WeChat, Slack, DM or GROUP. On any turn error, quota
# denial, entitlement block, or timeout, the channel gets NOTHING; the
# failure is logged and surfaced on the dashboard/activity feed only. Web
# chat / dashboard surfaces are exempt from this — they may still render
# these events as UI cards (see PlatformEvent.to_intervention()).
#
# Default posture is DENY: every PlatformEvent defined in this module is
# channel-suppressed UNLESS its code is explicitly listed in
# CHANNEL_SAFE_CODES below. That allowlist holds ONLY direct responses to
# an explicit user command (/compact, /new, /main, /memory, /help) — never
# a failure/degradation notice. A new PlatformEvent added later is
# suppressed in channels by default until someone deliberately allowlists
# it — silence-by-default, not leak-by-default.
#
# See server_modules.channel_adapter.filter_channel_outbound_reply(), the
# single choke point that applies this suppression at every channel send.

CHANNEL_SAFE_CODES: frozenset[str] = frozenset({
    SAGE_COMPACTED.code,
    SAGE_COMPACT_NOT_NEEDED.code,
    SAGE_NEW_SESSION.code,
    SAGE_MAIN_RETURN.code,
    SAGE_NO_MEMORIES.code,
    SAGE_HELP.code,
})


def _all_platform_events() -> tuple[PlatformEvent, ...]:
    return tuple(value for value in globals().values() if isinstance(value, PlatformEvent))


# Rendered channel_text -> suppressed. Matched on the rendered string
# (rather than the originating PlatformEvent object) because most call
# sites only have the string left by the time they reach the channel-send
# boundary — the object identity is long gone.
CHANNEL_SUPPRESSED_TEXTS: frozenset[str] = frozenset(
    event.channel_text.strip()
    for event in _all_platform_events()
    if event.code not in CHANNEL_SAFE_CODES and event.channel_text.strip()
)


def is_channel_suppressed_text(text: str) -> bool:
    """True if *text* is a hardcoded platform status/error string that must
    never reach a channel send — see the module docstring above."""
    return str(text or "").strip() in CHANNEL_SUPPRESSED_TEXTS


# ═══════════════════════════════════════════════════════════════════════════
# Audience — the OWNER's own channel is not a stranger's channel
# ═══════════════════════════════════════════════════════════════════════════
#
# CHANNEL_SAFE_CODES above is AUDIENCE-BLIND: one flat allowlist applied
# identically to a stranger messaging a business agent and to the owner
# texting their own agent from their own phone. That blindness is why an
# owner whose credits ran out, whose provider was unreachable, or whose turn
# crashed got EXACTLY the same thing a stranger got — nothing — which is
# indistinguishable from "my agent is ignoring me". Silence is right for the
# stranger (platform error prose in a customer's chat reads as broken) and
# wrong for the owner (they are the only person who can fix it).
#
# This is a SECOND allowlist, not a widening of the first. Three properties
# make it narrow, and each is asserted in test_channel_silence_on_error.py:
#
#   1. CHANNEL_SUPPRESSED_TEXTS is UNCHANGED — every code below is STILL in
#      it, so filter_channel_outbound_reply() keeps suppressing this text on
#      the default (stranger) path, byte for byte.
#   2. owner_channel_text_for_code() takes a CODE, never text. There is no
#      string parameter, so no exception message, model output, provider
#      response, classified error prose, ErrorNotification.raw_detail, or
#      secret can be routed through it — only a frozen module-level literal
#      selected by an exact code match. An unknown code returns None.
#   3. The set is enumerated by hand, holds only codes an owner can ACT on,
#      and holds only codes that are actually produced today. It is not
#      derived from severity, from a category, or from anything that grows
#      on its own when a new PlatformEvent is added.
#
# Today exactly one code is produced on this path: channel_execution_failed,
# emitted by personal_channel_sage_bridge_service._build_error_reply_dict
# when a personal-channel turn raises. Adding a second is a one-line change
# HERE plus a producer that carries the code — deliberately not a keyword
# match on the exception's prose, which is the stale-string-matching failure
# mode this codebase has already been bitten by.
CHANNEL_OWNER_SAFE_CODES: frozenset[str] = frozenset({
    CHANNEL_EXECUTION_FAILED.code,
})


_OWNER_SAFE_CHANNEL_TEXT_BY_CODE: dict[str, str] = {
    event.code: event.channel_text.strip()
    for event in _all_platform_events()
    if event.code in CHANNEL_OWNER_SAFE_CODES and event.channel_text.strip()
}


def owner_channel_text_for_code(code: str) -> Optional[str]:
    """Frozen channel text for *code*, but ONLY for the workspace OWNER.

    Returns the PlatformEvent's own ``channel_text`` when *code* is in
    CHANNEL_OWNER_SAFE_CODES, else None. Callers must have ROBUSTLY
    established ownership first (personal_channels_service._is_owner_message
    — self-chat or a sender matching the channel's linked owner id; never a
    claimed name and never message text).

    Deliberately code-in / literal-out: this function cannot be handed text
    to pass through, so it can never become a route for an error string, a
    provider response, or a secret. See the block comment above.
    """
    return _OWNER_SAFE_CHANNEL_TEXT_BY_CODE.get(str(code or "").strip()) or None
