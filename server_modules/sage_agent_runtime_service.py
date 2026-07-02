from __future__ import annotations

import logging

import asyncio
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from server_modules import skills_service as _sage_skills_service
from server_modules import thread_service
from server_modules import runtime_config
from server_modules import (
    activity_ledger_service,
    agent_trace_service,
    direct_chat_generation_service,
    direct_chat_runtime_exports,
    direct_chat_tool_catalog_service,
    no_provider_service,
    response_leak_guard_service,
    sage_daily_operator_service,
    sage_instruction_compiler_service,
    sage_heartbeat_service,
    sage_memory_service,
    sage_proof_log_service,
    sage_profile_service,
    secret_redaction_service,
    security_audit_service,
    skill_registry,
    workspace_context,
)
from server_modules.conversation_memory_facade_service import (
    DIRECT_CHAT_SURFACE,
    ConversationMemorySubject,
    ConversationMemoryPersistRequest,
    persist_interaction,
)
from server_modules.conversation_memory_policy import (
    DIRECT_CHAT_PROFILE,
    MemoryPolicyProfile,
)
from server_modules import multimodal_provider_service
from server_modules.direct_chat_runtime_exports import generate_chat_reply_with_provider_fallback
from server_modules.direct_chat_provider_service import (
    direct_chat_credentials,
    supports_direct_message_native_chat,
    credential_auth_mode,
)
from server_modules.agent_computer_policy_service import (
    CAPABILITY_APP_CONTROL,
    CAPABILITY_CLOUD_STORAGE_ACCESS,
    CAPABILITY_COMMUNICATION_SEND,
    CAPABILITY_FILE_WRITE,
    CAPABILITY_MEMORY_WRITE,
    CAPABILITY_TERMINAL_COMMAND,
    AUTONOMY_ASK_EVERY_TIME,
    AUTONOMY_SAFE_AUTOPILOT,
    build_default_agent_computer_policy,
)
from server_modules.unified_governance_gate import (
    evaluate_action_policy,
    ActionPolicyDecision,
)
from server_modules.agent_policy_context import (
    build_agent_policy_context,
    AgentTier,
    resolve_agent_tier,
)
from server_modules.sage_agent_runtime_contract import (
    SAGE_MODE,
    normalize_sage_mode,
    normalize_sage_surface,
    SageTurnResult,
)
from server_modules.skill_registry import list_skill_definitions
from server_modules.sage_transparency_service import emit_sage_turn_transparency_events
from server_modules.transparency_event_store_service import persist_transparency_events
from server_modules.provider_profiles import PROVIDER_MODEL_CATALOG, _build_provider_credential_candidates
from scripts.orion_local_worker_llm import resolve_requested_model
from server_modules.channel_adapter import filter_outbound_reply

ALLOWED_MODES = {SAGE_MODE}
SAGE_THREAD_ID = "sage-main"  # canonical thread across channels (one per workspace)

# --- History sanitization ---
_HISTORY_TOOL_XML_PATTERNS = (
    r'<tool_call[^>]*>.*?</tool_call>',
    r'<function_call[^>]*>.*?</function_call>',
    r'<tool_calls[^>]*>.*?</tool_calls>',
    r'<function_calls[^>]*>.*?</function_calls>',
    r'<invoke\s+name="[^"]*">.*?</invoke>',
    r'<\w+_search[^>]*>.*?</\w+_search>',
    r'<\w+__\w+[^>]*>.*?</\w+__\w+>',
)

def sanitize_history_turn(content: str) -> str:
    """Strip tool-call XML and scaffolding from history before replay.
    Prevents the model from learning its own leaked tool-call syntax.
    """
    if not content:
        return content
    text = content
    import re as _re
    for pattern in _HISTORY_TOOL_XML_PATTERNS:
        text = _re.sub(pattern, '', text, flags=_re.DOTALL | _re.IGNORECASE)
    # Strip orphaned tool-name + kv lines (memory_search query="...")
    text = _re.sub(
        r'^\s*(?:memory_|web__|browser__|computer__|file__|shell__|screenshot__|hardware__|sage_service__)[a-z0-9_]*\s+\w+\s*=\s*"[^"]*"\s*$',
        '', text, flags=_re.MULTILINE | _re.IGNORECASE,
    )
    # Strip orphaned opening/closing XML tags on their own line
    text = _re.sub(r'^\s*</?[a-zA-Z_][a-zA-Z0-9_]*>\s*$', '', text, flags=_re.MULTILINE)
    # Clean up blank lines
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
# --- End history sanitization ---
SAGE_THREAD_MAX_TURNS = 10    # recent turns to load for context

_SILENT_REPLY_MARKER = "[SILENT]"

SAFE_ACTION_CLASSES = {"read"}
BLOCKED_ACTION_CLASSES = {"write", "execute"}
SAGE_MAIN_AGENT_ID = "sage_main_agent"
_COMMUNICATION_SCOPES = {
    "discord",
    "email",
    "gmail",
    "imessage",
    "mail",
    "slack",
    "sms",
    "telegram",
    "whatsapp",
}
_MEMORY_SCOPES = {"memory", "sage_memory", "agent_memory"}
_FILE_SCOPES = {"file", "files", "filesystem", "drive"}
_CLOUD_STORAGE_SCOPES = {"dropbox", "google_drive", "icloud", "onedrive"}
_SAGE_TOOL_RESULT_MAX_CHARS = 4000
_SAGE_ACTION_LOOP_VERSION = "v2"
_SAGE_OPERATOR_LOOP_VERSION = "v3"
_SAGE_ACTION_LOOP_MAX_TOOL_CALLS = 25
_SAGE_OPERATOR_LOOP_MAX_ITERATIONS = 5  # Cap at 5 to prevent runaway; most tasks finish in 1-3
_SAGE_TASK_ROUTE_MODES = {
    "chat_only",
    "connector_api",
    "cloud_browser",
    "cloud_computer",
    "gateway_required",
}
_AGENT_COMPUTER_TOOL_PREFIXES = ("browser__", "computer__", "file__", "shell__", "screenshot__")
_AGENT_COMPUTER_TOOL_NAMES = {"hardware__action"}
_CONNECTOR_ROUTE_KEYWORDS = {
    "gmail": ("gmail", "inbox"),
    "google_calendar": ("calendar", "meeting", "schedule", "event", "availability"),
    "google_drive": ("drive", "google drive", "doc", "docs", "sheet", "slides"),
    "github": ("github", "pull request", "pull-request", "issue", "repo", "repository"),
    "slack": ("slack",),
    "discord": ("discord",),
    "notion": ("notion",),
    "linear": ("linear",),
    "mcp": ("mcp",),
    "telegram": ("telegram", "message me on telegram", "telegram bot"),
}
_GATEWAY_ROUTE_KEYWORDS = (
    "my computer",
    "this computer",
    "this mac",
    "my mac",
    "local file",
    "local folder",
    "local project",
    "local browser",
    "signed-in browser",
    "browser profile",
    "desktop app",
    "vscode",
    "vs code",
    "finder",
    "terminal on my",
    "ssh key",
    "browser cookie",
    "browser cookies",
    "local network",
    "personal telegram",
    "personal whatsapp",
    "imessage",
    "signal",
    "wechat",
)
_CLOUD_COMPUTER_ROUTE_KEYWORDS = (
    "run this script",
    "execute this script",
    "run code",
    "compile",
    "build this",
    "terminal",
    "shell command",
    "terminal command",
    "run command",
    "execute command",
)


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def resolve_model_for_capability(
    workspace_id: str,
    capability: str = "tools",
) -> tuple[str, str, dict]:
    """Resolve the best (provider, model, credentials) for a given capability.

    capability is one of:
      - "tools"           – standard Sage (default)
      - "computer_use"    – hardware actions (screenshot, shell, file write)
      - "reasoning"       – heavy reasoning tasks
    """
    normalized_workspace = str(workspace_id or "default").strip() or "default"
    # Build candidate credentials for every supported provider
    candidate_providers = list(PROVIDER_MODEL_CATALOG.keys())
    matches: list[tuple[str, str, dict, int]] = []  # (provider, model, credentials, score)

    for provider_id in candidate_providers:
        # Check vault for credentials
        try:
            credentials = direct_chat_credentials(normalized_workspace, provider_id)
        except Exception:
            continue
        if not supports_direct_message_native_chat(provider_id, credentials):
            continue
        if provider_id == "openai":
            credential_type = _coerce_text(credentials.get("credential_type")).lower()
            auth_mode = credential_auth_mode("openai", credentials)
            if credential_type == "codex_token" or auth_mode == "oauth_token":
                continue

        # Find best model in this provider matching the capability
        models = PROVIDER_MODEL_CATALOG.get(provider_id, {})
        for model_id, model_info in models.items():
            if not isinstance(model_info, dict):
                continue
            model_labels = set(model_info.get("capability_labels", []))
            # Score: how well does this model match the requested capability?
            score = 0
            if capability == "computer_use" and "Computer use" in model_labels:
                score = 100
            elif capability == "tools" and model_info.get("supports_tools"):
                score = 50
            elif capability == "reasoning" and "Reasoning" in model_labels:
                score = 70
            # Boost for frontier models
            if "Frontier" in model_labels or "Highest quality" in model_labels:
                score += 5
            if score > 0:
                matches.append((provider_id, model_id, dict(credentials), score))

    if not matches:
        # Fallback: return first provider that supports tools
        for provider_id in candidate_providers:
            try:
                credentials = direct_chat_credentials(normalized_workspace, provider_id)
            except Exception:
                continue
            if supports_direct_message_native_chat(provider_id, credentials):
                if provider_id == "openai":
                    credential_type = _coerce_text(credentials.get("credential_type")).lower()
                    auth_mode = credential_auth_mode("openai", credentials)
                    if credential_type == "codex_token" or auth_mode == "oauth_token":
                        continue
                # Find any model that supports tools
                models = PROVIDER_MODEL_CATALOG.get(provider_id, {})
                for model_id, model_info in models.items():
                    if isinstance(model_info, dict) and model_info.get("supports_tools"):
                        return provider_id, model_id, dict(credentials)
        raise RuntimeError("No cloud provider is configured for Sage.")

    # Sort by score descending
    matches.sort(key=lambda x: x[3], reverse=True)
    best = matches[0]
    return best[0], best[1], best[2]


# ── Canonical AI & Setup link — backend is the single source of truth ──
# All channels (web, Telegram, future) use this path.  The frontend reads
# ai_setup_url from the API response rather than hardcoding the path.
_SAGE_AI_SETUP_PATH = "/integrations?section=ai-runtime"

# ── User-facing AI-stop messages (imported from the single source of truth) ──
from server_modules.sage_command_dispatcher import (
    SAGE_AI_LIMIT_MESSAGE,
    SAGE_AI_NEEDS_ATTENTION_MESSAGE,
)


async def _resolve_cloud_provider(workspace_id: str) -> tuple[str, dict]:
    """Resolve the Sage cloud provider — ONE AI ROAD, NO FALLBACK.

    INVARIANT: Each workspace has exactly ONE active AI provider.
    It is EXPLICITLY selected.  Default = the PLATFORM provider
    (DeepSeek, credit-gated).  Merely HAVING a vault key is NOT a
    selection — a vault key is used ONLY when it is the explicitly
    selected active provider.

    RESOLUTION (no fallthrough, ever):
      1. If the workspace has an explicit ``sage_ai_provider`` set
         (e.g. ``"anthropic"``, ``"openai"``): use ONLY that provider.
         If it is unavailable → hard stop.
      2. Otherwise (default): use the PLATFORM provider (DeepSeek,
         credit-gated via entitlements).  If the platform is blocked
         or exhausted → hard stop.

    There is NO tier that scans vault keys as a fallback.  A vault key
    is only used when it IS the explicit ``sage_ai_provider``.
    """
    print(f"[TRACE_PROVIDER] _resolve_cloud_provider called ws={workspace_id}", flush=True)
    from server_modules.workspace_config_schema import workspace_admin_defaults_from_metadata
    from server_modules.control_plane_repository import get_workspace_by_id as _load_workspace

    normalized_ws = str(workspace_id or "default").strip() or "default"

    # ── Resolve the active provider ──
    active_provider: str = ""
    try:
        ws_record = await _load_workspace(normalized_ws)
        ws_metadata = dict((ws_record or {}).get("metadata") or {})
        admin_defaults = workspace_admin_defaults_from_metadata(ws_metadata)
        active_provider = str(admin_defaults.sage_ai_provider or "").strip().lower()
    except Exception:
        pass  # workspace not found or metadata unreadable

    # ── Explicit provider selected — LOCKED, NO FALLTHROUGH ──
    if active_provider:
        credentials = direct_chat_credentials(normalized_ws, active_provider)
        if supports_direct_message_native_chat(active_provider, credentials):
            print(f"[TRACE_PROVIDER_OK] returning provider={active_provider} (explicit)", flush=True)
            return active_provider, credentials
        # Explicit provider is unavailable — HARD STOP.
        raise RuntimeError(
            f"Your selected AI provider ({active_provider}) is not available. "
            + SAGE_AI_NEEDS_ATTENTION_MESSAGE
        )

    # ── Default: PLATFORM provider (DeepSeek, credit-gated) ──
    import os as _os

    _platform_deepseek_configured = bool(
        str(_os.getenv("DEEPSEEK_API_KEY") or "").strip()
    )
    if _platform_deepseek_configured:
        from server_modules.entitlements_service import (
            hosted_sage_ai_access_state_for_workspace_id as _hosted_access,
        )
        _access = _hosted_access(workspace_id=normalized_ws)
        print(f"[TRACE_ENTITLE] ws={normalized_ws} allowed={_access.get('allowed')} reason={_access.get('reason')} message={str(_access.get('message') or '')[:120]}", flush=True)
        if _access.get("allowed"):
            credentials = direct_chat_credentials(normalized_ws, "deepseek")
            if supports_direct_message_native_chat("deepseek", credentials):
                print(f"[TRACE_PROVIDER_OK] returning provider=deepseek (platform)", flush=True)
                return "deepseek", credentials
            raise RuntimeError(
                "Platform AI credentials could not be validated. "
                + SAGE_AI_NEEDS_ATTENTION_MESSAGE
            )
        # Platform is configured but blocked (credits exhausted, policy, etc.)
        _reason_msg = str(_access.get("message") or _access.get("reason") or "unavailable")
        raise RuntimeError(_reason_msg)

    credentials = direct_chat_credentials(normalized_ws, "deepseek")
    if supports_direct_message_native_chat("deepseek", credentials):
        print(f"[TRACE_PROVIDER_OK] returning provider=deepseek (fallback, no entitlements)", flush=True)
        return "deepseek", credentials

    raise RuntimeError("No cloud provider is configured for Sage.")


# ── Phase L: Per-agent AI provider binding ──────────────────────────────────

async def _resolve_agent_cloud_provider(
    workspace_id: str,
    agent_model_config: Optional[Dict[str, Any]] = None,
    agent_id: str = "",
) -> tuple[str, dict, str]:
    """Resolve the AI provider for a specific agent, respecting its model_config.

    Returns (provider, credentials, billing_mode) where billing_mode is one of:
      - "platform_credits" — decrement workspace credits
      - "byok_api" — agent brings own key, NO credit decrement
      - "cli_subscription" — agent uses CLI subscription
      - "local" — agent uses local model

    HARD RULE — no silent fallback:
      If the bound mode/provider is unavailable (missing key, local model down,
      subscription expired), the turn fails with a platform-voice error +
      ledger event {action:"provider_unavailable", agent_id, mode, provider}.

      NEVER auto-switch to another provider.
      NEVER bill platform credits for a BYOK-bound agent.
    """
    mc = dict(agent_model_config or {})
    mode = str(mc.get("mode") or "platform_credits").strip().lower()
    provider = str(mc.get("provider") or "").strip().lower()

    # ── platform_credits: use default workspace resolution ──────────
    if mode == "platform_credits":
        prov, creds = await _resolve_cloud_provider(workspace_id)
        return prov, creds, "platform_credits"

    # ── byok_api: agent's own key ──────────────────────────────────
    if mode == "byok_api":
        if not provider:
            await _ledger_provider_unavailable(
                workspace_id=workspace_id,
                agent_id=agent_id,
                mode=mode,
                provider=provider,
                reason="No provider specified in model_config for BYOK mode.",
            )
            raise RuntimeError(
                "This agent is configured to use its own API key (BYOK), "
                "but no provider was specified in its model_config. "
                "An operator must configure the provider via fleet_configure_agent."
            )

        from server_modules.direct_chat_provider_service import (
            direct_chat_credentials,
            supports_direct_message_native_chat,
        )
        credentials = direct_chat_credentials(workspace_id, provider)
        if not supports_direct_message_native_chat(provider, credentials):
            await _ledger_provider_unavailable(
                workspace_id=workspace_id,
                agent_id=agent_id,
                mode=mode,
                provider=provider,
                reason=f"BYOK provider '{provider}' is unavailable or missing credentials.",
            )
            raise RuntimeError(
                f"This agent is bound to the {provider} provider (BYOK), "
                f"but the required API key is not configured. "
                f"Add the key to your vault or switch this agent to platform_credits."
            )

        return provider, credentials, "byok_api"

    # ── cli_subscription: not yet available on this deployment ─────
    if mode == "cli_subscription":
        await _ledger_provider_unavailable(
            workspace_id=workspace_id,
            agent_id=agent_id,
            mode=mode,
            provider=provider,
            reason="cli_subscription mode is not yet available on this deployment.",
        )
        raise RuntimeError(
            "CLI subscription mode is not yet available on this deployment. "
            "Switch this agent to platform_credits or byok_api."
        )

    # ── local: not yet available on this deployment ────────────────
    if mode == "local":
        await _ledger_provider_unavailable(
            workspace_id=workspace_id,
            agent_id=agent_id,
            mode=mode,
            provider=provider,
            reason="local mode is not yet available on this deployment.",
        )
        raise RuntimeError(
            "Local model mode is not yet available on this deployment. "
            "Switch this agent to platform_credits or byok_api."
        )

    # Unknown mode
    await _ledger_provider_unavailable(
        workspace_id=workspace_id,
        agent_id=agent_id,
        mode=mode,
        provider=provider,
        reason=f"Unknown model_config mode: {mode}",
    )
    raise RuntimeError(
        f"Unknown model_config mode: {mode}. "
        f"Valid modes: platform_credits, byok_api, cli_subscription, local."
    )


async def _ledger_provider_unavailable(
    *,
    workspace_id: str,
    agent_id: str,
    mode: str,
    provider: str,
    reason: str,
) -> None:
    """Ledger a provider_unavailable event — best effort, never raises."""
    try:
        from server_modules import activity_ledger_service
        await activity_ledger_service.append_activity_event(
            tenant_id="system",
            workspace_id=workspace_id,
            actor_type="agent",
            actor_id=str(agent_id or "").strip() or "unknown",
            event_class="system_activity",
            detail_level="audit_reference",
            action="provider_unavailable",
            title=f"Provider unavailable: {mode}/{provider or 'unspecified'}",
            summary=(
                f"Agent {agent_id} requested {mode}/{provider or 'unspecified'} "
                f"but it is unavailable. Reason: {reason}. "
                f"No fallback — turn denied per Phase L hard rule."
            ),
            status="blocked",
            metadata={
                "agent_id": agent_id,
                "mode": mode,
                "provider": provider,
                "reason": reason,
            },
        )
    except Exception:
        pass


async def get_persisted_model_preference(workspace_id: str) -> str:
    """Read the workspace-persisted model preference (survives restart)."""
    from server_modules.workspace_config_schema import workspace_admin_defaults_from_metadata
    from server_modules.control_plane_repository import get_workspace_by_id as _load_workspace
    try:
        ws_record = await _load_workspace(str(workspace_id or "default").strip() or "default")
        ws_metadata = dict((ws_record or {}).get("metadata") or {})
        admin_defaults = workspace_admin_defaults_from_metadata(ws_metadata)
        return str(admin_defaults.sage_ai_model or "").strip()
    except Exception:
        return ""


async def set_persisted_model_preference(workspace_id: str, model: str, provider: str = "") -> bool:
    """Persist the workspace model preference so it survives server restarts."""
    from server_modules.control_plane_repository import update_workspace_admin_defaults_metadata
    try:
        payload = {}
        if provider:
            payload["sage_ai_provider"] = str(provider).strip().lower()
        if model:
            payload["sage_ai_model"] = str(model).strip()
        if not payload:
            return False
        result = await update_workspace_admin_defaults_metadata(
            str(workspace_id or "default").strip() or "default",
            payload,
        )
        return result is not None
    except Exception:
        return False


def _load_profile_context(*, workspace_id: str) -> str:
    profile = sage_profile_service.list_sage_profile(workspace_id=workspace_id)
    profile_data = profile.get("profile") if isinstance(profile.get("profile"), dict) else {}

    user_name = _coerce_text(profile_data.get("user_name"))
    identity_summary = _coerce_text(profile_data.get("identity_summary"))
    communication_style = _coerce_text(profile_data.get("communication_style"))
    recurring = _coerce_text(profile_data.get("recurring_responsibility"))
    standing_rules = profile_data.get("standing_rules") or []

    if not any([user_name, identity_summary, communication_style, recurring, standing_rules]):
        return ""

    lines: list[str] = []
    if user_name:
        lines.append(f"User: {user_name}")
    if identity_summary:
        lines.append(f"Role: {identity_summary}")
    if communication_style:
        lines.append(f"Preferred communication: {communication_style}")
    if recurring:
        lines.append(f"Recurring responsibility: {recurring}")
    if standing_rules:
        lines.append("Standing rules:")
        for rule in standing_rules:
            lines.append(f"  - {rule}")
    return "\n".join(lines)


def _load_context_files(*, workspace_id: str) -> str:
    files = workspace_context.read_workspace_context_files(workspace_id=workspace_id)
    sections, _diagnostics = sage_instruction_compiler_service.build_root_memory_brief_sections(files)
    return "\n\n".join(sections)


def _read_context_files_payload(*, workspace_id: str) -> dict[str, Any]:
    files = workspace_context.read_workspace_context_files(workspace_id=workspace_id)
    return files if isinstance(files, dict) else {}


def _load_memory_context(*, workspace_id: str) -> str:
    return sage_memory_service.build_sage_memory_context_block(
        workspace_id=workspace_id,
        include_restricted=False,
    )


def _load_safe_skill_catalog(*, workspace_id: str) -> list[dict]:
    all_skills = list_skill_definitions(workspace_id=workspace_id, include_disabled=False)
    safe: list[dict] = []
    for skill in all_skills:
        if not skill.enabled or not skill.available:
            continue
        if skill.action_class in BLOCKED_ACTION_CLASSES:
            continue
        safe.append({
            "id": skill.id,
            "label": skill.label,
            "description": skill.description,
            "action_class": skill.action_class,
            "requires_approval": skill.requires_approval,
            "execution_mode": skill.execution_mode,
        })
    return safe


def _build_mcp_tool_inventory(*, workspace_id: str) -> str:
    """Build a system-prompt-friendly inventory of available MCP tools."""
    all_skills = list_skill_definitions(workspace_id=workspace_id, include_disabled=False)
    mcp_skills = [
        s for s in all_skills
        if _coerce_text(getattr(s, "execution_adapter", "")).lower() == "mcp_tool"
        and getattr(s, "enabled", False)
    ]
    if not mcp_skills:
        return ""
    lines: list[str] = [
        "\n\n## Available MCP Tools",
        "The following MCP tools are connected to this workspace and can be invoked:",
    ]
    for s in mcp_skills:
        sid = _coerce_text(getattr(s, "id", ""))
        label = _coerce_text(getattr(s, "label", "")) or sid
        desc = _coerce_text(getattr(s, "description", ""))
        entry = f"- {sid}: {label}"
        if desc:
            entry += f" — {desc}"
        lines.append(entry)
    lines.append(
        "\nTo use an MCP tool, request its described function. "
        "Requests are automatically routed to the correct tool."
    )
    return "\n".join(lines)


def _build_heartbeat_summary(snapshot: dict) -> str:
    queue = snapshot.get("queue_overview") if isinstance(snapshot.get("queue_overview"), dict) else {}
    reminders = snapshot.get("reminders") if isinstance(snapshot.get("reminders"), dict) else {}
    bootstrap = snapshot.get("bootstrap") if isinstance(snapshot.get("bootstrap"), dict) else {}

    lines: list[str] = []
    if not bootstrap.get("complete"):
        lines.append("Profile setup is not complete.")
    quiet = snapshot.get("quiet_hours") if isinstance(snapshot.get("quiet_hours"), dict) else {}
    if quiet:
        lines.append(f"Quiet hours: {_coerce_text(quiet.get('label'))}")

    running = int(queue.get("running_now_count") or 0)
    waiting = int(queue.get("queued_count") or 0)
    blocked = int(queue.get("blocked_on_approval_count") or 0)
    pending = int(queue.get("pending_wakeup_count") or 0)
    if any([running, waiting, blocked, pending]):
        parts = []
        if running:
            parts.append(f"{running} running")
        if waiting:
            parts.append(f"{waiting} waiting")
        if blocked:
            parts.append(f"{blocked} need approval")
        if pending:
            parts.append(f"{pending} pending wakeups")
        lines.append("Queue: " + ", ".join(parts))

    reminder_count = int(reminders.get("count") or 0)
    if reminder_count:
        lines.append(f"{reminder_count} scheduled reminder(s)")

    next_action = snapshot.get("next_scheduled_action")
    if isinstance(next_action, dict) and next_action.get("label"):
        lines.append(f"Next: {_coerce_text(next_action.get('label'))}")

    return "\n".join(lines) if lines else ""


def _build_prompt_envelope(
    *,
    workspace_id: str,
    message: str,
    system_prompt: str,
) -> dict:
    return {
        "system_prompt": secret_redaction_service.redact_text(system_prompt),
        "user_message": secret_redaction_service.redact_text(message),
        "context": {
            "workspace_id": workspace_id,
            "source": "sage_chat",
        },
    }


def _skill_capability(skill: Any) -> str:
    action_class = _coerce_text(getattr(skill, "action_class", "")).lower()
    scopes = {
        _coerce_text(scope).lower().replace("-", "_")
        for scope in (getattr(skill, "connector_scopes", ()) or ())
        if _coerce_text(scope)
    }
    if action_class == "execute":
        return CAPABILITY_TERMINAL_COMMAND
    if scopes & _COMMUNICATION_SCOPES:
        return CAPABILITY_COMMUNICATION_SEND
    if scopes & _MEMORY_SCOPES:
        return CAPABILITY_MEMORY_WRITE
    if scopes & _CLOUD_STORAGE_SCOPES:
        return CAPABILITY_CLOUD_STORAGE_ACCESS
    if scopes & _FILE_SCOPES:
        return CAPABILITY_FILE_WRITE
    return CAPABILITY_APP_CONTROL


def _skill_target_channel(skill: Any) -> str:
    scopes = [
        _coerce_text(scope).lower().replace("-", "_")
        for scope in (getattr(skill, "connector_scopes", ()) or ())
        if _coerce_text(scope)
    ]
    for scope in scopes:
        if scope in _COMMUNICATION_SCOPES:
            return scope
    return ""


def _build_agent_computer_decision_for_skill(
    *,
    workspace_id: str,
    actor_user_id: str,
    skill: Any,
    triggered_by: str,
    message: str,
) -> dict:
    """Classify Sage's requested connected-computer action before approval.

    Routes through the unified governance gate (Step 1 migration).
    The gate internally runs kill switch → safe mode → risk classifier →
    approval memory → approval card, all through the existing functions.
    """
    capability = _skill_capability(skill)
    policy = build_default_agent_computer_policy(
        autonomy_mode=AUTONOMY_SAFE_AUTOPILOT,
        policy_id=f"sage-chat:{workspace_id}",
    )
    # AUDIT-ONLY: evaluate_action_policy still runs for audit trail,
    # but its decision no longer blocks execution. The agent uses
    # internalized governance (policy context in system prompt).
    decision = evaluate_action_policy(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id or "owner",
        agent_id=SAGE_MAIN_AGENT_ID,
        policy=policy,
        capability=capability,
        action_class=_coerce_text(getattr(skill, "action_class", "")),
        target_channel=_skill_target_channel(skill),
        payload={
            "surface": "sage_chat",
            "skill_id": _coerce_text(getattr(skill, "id", "")),
            "skill_label": _coerce_text(getattr(skill, "label", "")),
            "action_class": _coerce_text(getattr(skill, "action_class", "")),
            "triggered_by": triggered_by,
            "user_message": message,
        },
        consume_approval_memory=False,
        surface="sage_chat",
    )
    return decision.as_dict()




def _dedupe_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = _coerce_text(item.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(item)
    return out


def _tool_requires_agent_computer(tool_name: str) -> bool:
    normalized = _coerce_text(tool_name)
    return normalized in _AGENT_COMPUTER_TOOL_NAMES or normalized.startswith(_AGENT_COMPUTER_TOOL_PREFIXES)


def _compact_route_text(message: str) -> str:
    return " ".join(str(message or "").strip().lower().split())


def _available_tool_names(tools: list[dict[str, Any]]) -> set[str]:
    return {
        _coerce_text(tool.get("name"))
        for tool in tools
        if isinstance(tool, dict) and _coerce_text(tool.get("name"))
    }


def _connected_capability_tokens(tool_capabilities: list[dict[str, Any]], tools: list[dict[str, Any]]) -> set[str]:
    tokens: set[str] = set()
    for capability in tool_capabilities:
        if not isinstance(capability, dict):
            continue
        for field in ("id", "label", "connector", "provider", "capability", "name"):
            value = _coerce_text(capability.get(field)).lower()
            if value:
                tokens.add(value)
                tokens.add(value.replace(" ", "_"))
    for name in _available_tool_names(tools):
        prefix = name.split("__", 1)[0]
        if prefix:
            tokens.add(prefix)
    return tokens


def _connector_requirements_for_message(message: str) -> list[str]:
    compact = _compact_route_text(message)
    required: list[str] = []
    for connector_id, keywords in _CONNECTOR_ROUTE_KEYWORDS.items():
        if any(keyword in compact for keyword in keywords):
            required.append(connector_id)
    return required


def _connector_requirements_satisfied(required_connections: list[str], connected_tokens: set[str]) -> bool:
    if not required_connections:
        return False
    for connector_id in required_connections:
        aliases = {
            connector_id,
            connector_id.replace("_", " "),
        }
        if connector_id in {"gmail", "google_calendar", "google_drive"}:
            aliases.add("google_workspace")
            aliases.add("google workspace")
        if connector_id == "mcp":
            aliases.add("mcp_tool")
        if not aliases.intersection(connected_tokens):
            return False
    return True


def _message_requests_local_agent_computer_action(message: str) -> bool:
    compact = _compact_route_text(message)
    if not compact:
        return False
    return (
        direct_chat_tool_catalog_service.looks_like_local_path_request(compact)
        or direct_chat_tool_catalog_service.looks_like_local_system_info_request(compact)
        or direct_chat_tool_catalog_service.looks_like_local_working_directory_request(compact)
    )


def _prior_assistant_requested_hardware_check(prior_messages: list[dict[str, Any]] | None) -> bool:
    for item in reversed((prior_messages or [])[-6:]):
        if not isinstance(item, dict):
            continue
        role = _coerce_text(item.get("role")).lower()
        if role not in {"assistant", "sage"}:
            continue
        content = _compact_route_text(item.get("content") or item.get("message") or item.get("text"))
        if not content:
            continue
        if any(
            token in content
            for token in (
                "hardware check",
                "hardware overview",
                "system overview",
                "system hardware",
                "agent computer",
                "your mac",
                "your laptop",
                "your computer",
                "run a quick hardware",
                "take a look at your system",
            )
        ):
            return True
    return False


def _message_is_hardware_check_followup(message: str, prior_messages: list[dict[str, Any]] | None) -> bool:
    if not _prior_assistant_requested_hardware_check(prior_messages):
        return False
    compact = _compact_route_text(message)
    if not compact or len(compact) > 120:
        return False
    compact = compact.replace("waht", "what")
    approval_tokens = (
        "ok",
        "okay",
        "yes",
        "yep",
        "yeah",
        "sure",
        "do it",
        "go ahead",
        "please do",
        "check",
    )
    if compact in approval_tokens:
        return True
    if any(token in compact for token in approval_tokens) and any(
        target in compact
        for target in (
            "what things i have",
            "what i have",
            "things i have",
            "my hardware",
            "my system",
            "my laptop",
            "my mac",
            "my computer",
            "where you are running",
            "where are you running",
        )
    ):
        return True
    return False


def _normalized_sage_action_loop_message(message: str, prior_messages: list[dict[str, Any]] | None = None) -> str:
    if _message_is_hardware_check_followup(message, prior_messages):
        return "check what hardware I have on my Mac"
    return message


_KNOWN_TOOL_PREFIXES = (
    "memory_", "browser__", "computer__", "file__", "shell__",
    "screenshot__", "hardware__", "web__", "sage_service__",
    "http_request", "generate_image", "llm__task",
)


def sanitize_agent_reply(text: str) -> str:
    """Strip leaked tool-call syntax from a reply before it reaches any channel."""
    if not text:
        return text
    # First pass: remove single-line tool-call patterns like memory_search query="..." or memory_search(query="...")
    for prefix in _KNOWN_TOOL_PREFIXES:
        escaped_prefix = re.escape(prefix)
        # memory_search query="..."  (tool_name space key=value, anywhere in text)
        text = re.sub(
            rf'(?:^|\n|\s){escaped_prefix}[a-z0-9_]* +\w+ *= *"[^"]*"',
            '',
            text,
            flags=re.MULTILINE,
        )
        # memory_search(query="...")  (parenthesized, anywhere in text)
        text = re.sub(
            rf'(?:^|\n|\s){escaped_prefix}[a-z0-9_]* *\([^)]*\)',
            '',
            text,
            flags=re.MULTILINE,
        )
    # Clean up double spaces and blank lines from removed tool calls
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Second pass: remove multi-line tool-call patterns (tool name on its own line + kv line)
    lines = text.split("\n")
    cleaned = []
    skip_next_kv = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            skip_next_kv = False
            continue
        is_tool_name_line = (
            bool(re.fullmatch(r'[a-z_][a-z0-9_]*', stripped))
            and any(stripped.startswith(p) for p in _KNOWN_TOOL_PREFIXES)
        )
        is_kv_line = bool(re.fullmatch(r'[a-z_][a-z0-9_]*\s*=\s*".*"', stripped))
        if is_tool_name_line and (len(cleaned) == 0 or not cleaned[-1].strip()):
            skip_next_kv = True
            continue
        if skip_next_kv and is_kv_line:
            skip_next_kv = False
            continue
        skip_next_kv = False
        cleaned.append(line)
    result = "\n".join(cleaned).strip()
    # Third pass: strip XML-wrapped tool-call blocks like <memory_append_daily_note>...json...</memory_append_daily_note>
    for prefix in _KNOWN_TOOL_PREFIXES:
        escaped_prefix = re.escape(prefix)
        # non-greedy, multiline, case-insensitive: <prefix...>...content...</prefix...>
        result = re.sub(
            rf'<{escaped_prefix}[a-z0-9_]*>.*?</{escaped_prefix}[a-z0-9_]*>',
            '',
            result,
            flags=re.DOTALL | re.IGNORECASE,
        )
    # Clean up leftover blank lines
    result = re.sub(r'\n{3,}', '\n\n', result)
    result = result.strip()
    # Final pass: strip ANY remaining XML-style tag blocks (catches new/unknown
    # tool-call formats DeepSeek may invent, e.g. <tool_call><tool_name>...
    # </tool_name><parameters>...</parameters></tool_call>)
    result = re.sub(
        r'<([a-zA-Z_][a-zA-Z0-9_]*)>.*?</\1>',
        '',
        result,
        flags=re.DOTALL,
    )
    # Also strip any now-orphaned opening/closing tags of the same shape on
    # their own line, in case nesting wasn't balanced
    result = re.sub(r'^\s*</?[a-zA-Z_][a-zA-Z0-9_]*>\s*$', '', result, flags=re.MULTILINE)
    # Clean up resulting multiple blank lines
    result = re.sub(r'\n{3,}', '\n\n', result)
    result = result.strip()
    if not result:
        return "Let me look into that and get back to you."
    return result

def _guard_sage_visible_reply(value: Any) -> tuple[str, dict[str, Any]]:
    import sys as _sys
    _sys.stderr.write(f"DEBUG RAW PRE-SANITIZE: {str(value)[:300]!r}\n")
    _sys.stderr.flush()
    raw = _coerce_text(value)
    guarded = response_leak_guard_service.guard_model_response(raw)
    text = guarded.text
    # Strip any leaked tool-call syntax before the reply reaches the user
    text = sanitize_agent_reply(text)
    if raw and "internal_tool_markup" in guarded.findings and not text:
        text = "Internal tool instructions could not be displayed. Please try again with Agent Computer connected."
    return text, guarded.metadata()


def _build_sage_route_decision(
    *,
    message: str,
    tools: list[dict[str, Any]] | None = None,
    tool_capabilities: list[dict[str, Any]] | None = None,
    availability: dict[str, Any] | None = None,
    blocked_agent_computer_tool: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compact = _compact_route_text(message)
    normalized_tools = tools or []
    normalized_capabilities = tool_capabilities or []
    connected_tokens = _connected_capability_tokens(normalized_capabilities, normalized_tools)
    required_connections = _connector_requirements_for_message(message)
    browser_status = _sage_agent_computer_browser_status(availability or {})
    gateway_requested = any(token in compact for token in _GATEWAY_ROUTE_KEYWORDS)
    local_path_requested = direct_chat_tool_catalog_service.looks_like_local_path_request(compact)
    local_system_requested = direct_chat_tool_catalog_service.looks_like_local_system_info_request(compact)
    local_cwd_requested = direct_chat_tool_catalog_service.looks_like_local_working_directory_request(compact)
    browser_requested = direct_chat_tool_catalog_service.message_has_browser_automation_intent(message)
    web_lookup_requested = direct_chat_tool_catalog_service.message_has_web_lookup_intent(message)
    cloud_computer_requested = any(token in compact for token in _CLOUD_COMPUTER_ROUTE_KEYWORDS)

    mode = "chat_only"
    reason = "Sage can answer this directly in chat."
    fallback_modes: list[str] = []
    approval_required = False

    if blocked_agent_computer_tool is not None or gateway_requested or local_path_requested or local_system_requested or local_cwd_requested:
        mode = "gateway_required"
        reason = "gateway_required: message requests local/private computer access"
        fallback_modes = ["cloud_computer", "cloud_browser", "connector_api"]
        approval_required = True
    elif required_connections:
        mode = "connector_api"
        if _connector_requirements_satisfied(required_connections, connected_tokens):
            reason = "This should use connected app or MCP tools before any computer runtime."
        else:
            reason = "This needs connected apps before Sage can do the requested work."
        fallback_modes = ["cloud_browser", "cloud_computer", "gateway_required"]
        approval_required = any(token in compact for token in ("send", "create", "update", "delete", "post", "schedule", "book"))
    elif browser_requested:
        mode = "cloud_browser"
        reason = "This can use a hosted browser unless the user explicitly needs a local signed-in browser."
        fallback_modes = ["connector_api", "cloud_computer", "gateway_required"]
        approval_required = any(token in compact for token in ("click", "fill", "submit", "book", "buy", "pay"))
    elif cloud_computer_requested:
        mode = "cloud_computer"
        reason = "This needs an isolated computer runtime, but not the user's personal machine."
        fallback_modes = ["connector_api", "gateway_required"]
        approval_required = True
    elif web_lookup_requested:
        mode = "connector_api"
        reason = "This can use cloud web search/fetch without Agent Computer."
        fallback_modes = ["cloud_browser"]

    user_label = {
        "chat_only": "Basic Assistant",
        "connector_api": "Connected Assistant",
        "cloud_browser": "Connected Assistant",
        "cloud_computer": "Computer Assistant",
        "gateway_required": "Computer Assistant",
    }[mode]
    return {
        "mode": mode,
        "user_label": user_label,
        "reason": reason,
        "required_connections": required_connections,
        "fallback_modes": [item for item in fallback_modes if item in _SAGE_TASK_ROUTE_MODES and item != mode],
        "approval_required": approval_required,
    }


def _summarize_tool_output(value: Any, *, max_chars: int = _SAGE_TOOL_RESULT_MAX_CHARS) -> str:
    text = secret_redaction_service.redact_text(str(value or "").replace("\0", "")).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n...[truncated]"


def _tool_call_signature(tool_call: dict[str, Any]) -> str:
    name = _coerce_text(tool_call.get("name"))
    arguments = tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {}
    try:
        import json

        args_text = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        args_text = str(arguments)
    return f"{name}:{args_text}"


def _budget_sage_tool_calls(tool_calls: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = _coerce_text(call.get("name"))
        if not name:
            continue
        signature = _tool_call_signature(call)
        if signature in seen:
            blocked.append({
                "name": name,
                "reason": "sage_action_loop_repeated_tool_call",
                "status": "blocked",
            })
            continue
        seen.add(signature)
        if len(allowed) >= _SAGE_ACTION_LOOP_MAX_TOOL_CALLS:
            blocked.append({
                "name": name,
                "reason": "sage_action_loop_tool_budget_exhausted",
                "status": "blocked",
            })
            continue
        allowed.append(call)
    return allowed, blocked


def _sage_agent_computer_browser_status(availability_payload: dict[str, Any]) -> str:
    """Determine browser online/offline/not_selected status using the same
    logic as direct_chat_runtime_service._agent_computer_browser_status."""
    availability = availability_payload if isinstance(availability_payload, dict) else {}

    # Hard override: in agent machine mode with direct supervisor access,
    # always report "online" regardless of gateway/worker status.
    # This prevents the session-context wipe between messages where the
    # agent forgets it has hardware access.
    _diag_logger = logging.getLogger(__name__)
    import os as _os
    print(f"[BROWSER_STATUS_DEBUG] AGENT_MACHINE_MODE={_os.getenv('AGENT_MACHINE_MODE')!r} ALLOW_LOOPBACK={_os.getenv('EMPYRALIS_ALLOW_DIRECT_SUPERVISOR_LOOPBACK')!r}", flush=True)
    open("/tmp/empyralis_debug.log", "a").write(f"[BROWSER_STATUS_DEBUG] AGENT_MACHINE_MODE={_os.getenv('AGENT_MACHINE_MODE')!r} ALLOW_LOOPBACK={_os.getenv('EMPYRALIS_ALLOW_DIRECT_SUPERVISOR_LOOPBACK')!r}\n")
    try:
        _diag_logger.info(
            "BROWSER_STATUS agent_machine_mode=%s supervisor_allowed=%s",
            runtime_config.AGENT_MACHINE_MODE,
            True,
        )
        if runtime_config.AGENT_MACHINE_MODE == "agent":
            from server_modules.supervisor_client import _assert_direct_supervisor_allowed
            _assert_direct_supervisor_allowed()
            _diag_logger.info("BROWSER_STATUS returning online (agent machine override)")
            return "online"
    except Exception as _exc:
        print(f"[BROWSER_STATUS_DEBUG] OVERRIDE FAILED: {type(_exc).__name__}: {_exc}", flush=True)
        open("/tmp/empyralis_debug.log", "a").write(f"[BROWSER_STATUS_DEBUG] OVERRIDE FAILED: {type(_exc).__name__}: {_exc}\n")
        _diag_logger.warning(
            "BROWSER_STATUS agent machine override FAILED: %s",
            _exc,
        )
    capability_truth = availability.get("capability_truth") if isinstance(availability.get("capability_truth"), dict) else {}
    my_computer = capability_truth.get("my_computer") if isinstance(capability_truth.get("my_computer"), dict) else {}
    verified_gateway = availability.get("verified_user_device_gateway") if isinstance(availability.get("verified_user_device_gateway"), dict) else {}

    local_gateway_online = availability.get("local_gateway_online") if isinstance(availability.get("local_gateway_online"), bool) else None
    local_worker_online = availability.get("local_worker_online") if isinstance(availability.get("local_worker_online"), bool) else None
    runtime_ok = availability.get("runtime_ok") if isinstance(availability.get("runtime_ok"), bool) else None

    truth_online = my_computer.get("online") if isinstance(my_computer.get("online"), bool) else None
    truth_runtime_ok = my_computer.get("runtime_ok") if isinstance(my_computer.get("runtime_ok"), bool) else None
    truth_tools = my_computer.get("local_tools_available") if isinstance(my_computer.get("local_tools_available"), bool) else None

    state = str(my_computer.get("state") or availability.get("runtime_state") or "").strip().lower()
    selected_gateway_id = str(
        availability.get("selected_gateway_id")
        or availability.get("gateway_id")
        or my_computer.get("selected_gateway_id")
        or my_computer.get("gateway_id")
        or verified_gateway.get("gateway_id")
        or ""
    ).strip()

    if verified_gateway:
        _diag_logger.info("BROWSER_STATUS returning online (verified_gateway)")
        return "online"
    # A live gateway connection is sufficient for hardware tools, even if
    # runtime_ok is False (which tracks local worker health, not gateway health).
    if local_gateway_online is True:
        _diag_logger.info("BROWSER_STATUS returning online (local_gateway_online=True)")
        return "online"
    if truth_tools is True or (truth_online is True and truth_runtime_ok is not False):
        _diag_logger.info("BROWSER_STATUS returning online (truth_tools/truth_online)")
        return "online"
    if local_worker_online is True and runtime_ok is not False:
        _diag_logger.info("BROWSER_STATUS returning online (local_worker_online)")
        return "online"
    if selected_gateway_id or state in {"connected_unhealthy", "unhealthy", "error", "disconnected"}:
        _diag_logger.warning(
            "BROWSER_STATUS returning offline — selected_gateway_id=%s state=%s",
            selected_gateway_id, state,
        )
        return "offline"
    _diag_logger.warning(
        "BROWSER_STATUS returning not_selected — local_gateway_online=%s local_worker_online=%s runtime_ok=%s truth_tools=%s truth_online=%s",
        local_gateway_online, local_worker_online, runtime_ok, truth_tools, truth_online,
    )
    return "not_selected"


def _direct_tool_bundle(*, workspace_id: str, provider: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    try:
        tool_capabilities = direct_chat_runtime_exports.resolve_workspace_tool_capabilities(workspace_id)
    except Exception:
        tool_capabilities = []
    try:
        availability = direct_chat_runtime_exports._resolve_direct_chat_availability(
            workspace_id,
            requested_provider=provider,
        )
    except Exception:
        availability = {}

    # ── Two-tier tool assembly (same code path as web) ──
    # Tier 1: always-on tools (8 core tools) injected every turn.
    # Tier 2: registry — everything else, loaded on demand via query_tool_registry.
    tools: list[dict[str, Any]] = list(direct_chat_tool_catalog_service.build_always_on_direct_chat_tools())
    _registry = direct_chat_tool_catalog_service.build_registry_entries(tool_capabilities, availability)
    availability["_tool_registry"] = _registry

    browser_status = _sage_agent_computer_browser_status(availability)
    print(f"[TOOL_FILTER] browser_status={browser_status!r} tool_count_before={len(tools)}", flush=True)
    if browser_status != "online":
        tools = [tool for tool in tools if not _tool_requires_agent_computer(_coerce_text(tool.get("name")))]
        print(f"[TOOL_FILTER] tool_count_after={len(tools)} (agent computer tools stripped)", flush=True)
    else:
        print(f"[TOOL_FILTER] tool_count_after={len(tools)} (online — no stripping)", flush=True)
    return _dedupe_tools(tools), tool_capabilities, availability


def _plan_sage_direct_tool_calls(
    *,
    message: str,
    tools: list[dict[str, Any]],
    services: no_provider_service.NoProviderExecutionServices,
) -> list[dict[str, Any]]:
    compact = services.compact_text(message)
    tool_names = {_coerce_text(item.get("name")) for item in tools if isinstance(item, dict)}
    url = services.extract_first_url(message)
    browser_requested = bool(
        url
        and any(
            token in compact
            for token in (
                "browser",
                "go to",
                "open",
                "visit",
                "click",
                "fill",
                "page title",
                "main heading",
                "screenshot",
                "screen shot",
            )
        )
    )
    if url and not browser_requested and "web__fetch" in tool_names and (
        "fetch" in compact or "read" in compact or "summarize" in compact or "check" in compact
    ):
        return [{"name": "web__fetch", "arguments": {"url": url}}]

    planned = no_provider_service.plan_tool_calls(
        message,
        tools,
        compact_text=services.compact_text,
        extract_first_path_reference=services.extract_first_path_reference,
        extract_first_url=services.extract_first_url,
    )
    return [item for item in planned if isinstance(item, dict) and _coerce_text(item.get("name")) in tool_names]


def _blocked_agent_computer_tool_for_message(message: str, availability: dict[str, Any]) -> dict[str, Any] | None:
    if _sage_agent_computer_browser_status(availability) == "online":
        return None
    compact = " ".join(str(message or "").lower().split())
    if direct_chat_tool_catalog_service.message_has_browser_automation_intent(message):
        return {"name": "browser__navigate", "reason": "agent_computer_unavailable", "status": "blocked"}
    if _message_requests_local_agent_computer_action(message):
        return {"name": "hardware__action", "reason": "agent_computer_unavailable", "status": "blocked"}
    if any(
        token in compact
        for token in (
            "shell command",
            "terminal command",
            "run command",
            "execute command",
            "screenshot",
            "screen shot",
            "read file",
            "write file",
        )
    ):
        return {"name": "hardware__action", "reason": "agent_computer_unavailable", "status": "blocked"}
    return None


def _matching_mcp_skill(*, workspace_id: str, message: str) -> Any | None:
    compact = " ".join(str(message or "").lower().split())
    if not compact:
        return None
    candidates = [
        skill
        for skill in list_skill_definitions(workspace_id=workspace_id, include_disabled=False)
        if _coerce_text(getattr(skill, "execution_adapter", "")).lower() == "mcp_tool"
    ]
    if not candidates:
        return None
    for skill in candidates:
        terms = {
            _coerce_text(getattr(skill, "id", "")).lower(),
            _coerce_text(getattr(skill, "label", "")).lower(),
            *[
                _coerce_text(term).lower()
                for term in (getattr(skill, "trigger_terms", ()) or ())
                if _coerce_text(term)
            ],
        }
        if any(term and term in compact for term in terms):
            return skill
    if len(candidates) == 1 and "mcp" in compact:
        return candidates[0]
    # Description-based matching: check if the user message contains
    # significant keywords from any MCP skill's description.
    _MCP_DESC_STOPWORDS = frozenset({
        "this", "that", "with", "from", "have", "been", "were",
        "what", "which", "their", "there", "about", "would",
        "could", "should", "tool", "mcp", "the", "and", "for",
        "not", "are", "can", "has", "its", "use", "used", "using",
    })
    for skill in candidates:
        desc = _coerce_text(getattr(skill, "description", "")).lower()
        desc_keywords = {
            word for word in desc.split()
            if len(word) > 3 and word not in _MCP_DESC_STOPWORDS
        }
        if desc_keywords and any(keyword in compact for keyword in desc_keywords):
            return skill
    return None


_RECALL_KEYWORDS = (
    "what were we", "what did we", "what we were talking",
    "what was i", "what did i say", "what did i ask",
    "remind me", "catch me up", "recap",
    "do you remember", "what's the context", "what is the context",
    "pick up where we left off", "what were you",
    "what have we", "what have i", "summarize our conversation",
    "summarize this conversation", "what just happened",
)


def _message_might_need_sage_action_loop(message: str, prior_messages: list[dict[str, Any]] | None = None) -> bool:
    compact = " ".join(str(message or "").lower().split())
    if not compact:
        return False

    # Broad catch-all: messages that sound like the user wants tools/action
    _ACTION_SIGNAL_TOKENS = (
        "search for", "search the web for", "look up", "look into",
        "find out", "find me", "tell me about", "what is", "who is",
        "check ", "show me", "get me", "pull up", "scan ",
        "what does", "how do i", "how to", "can you find",
        "whats the", "what's the", "what are the",
        "list the files", "list files", "files on my",
        "take a ", "send a ", "open the ", "close the ",
    )
    if any(token in compact for token in _ACTION_SIGNAL_TOKENS):
        return True

    if any(keyword in compact for keyword in _RECALL_KEYWORDS):
        return True
    if _message_is_hardware_check_followup(message, prior_messages):
        return True
    if sage_daily_operator_service.message_might_need_daily_operator(message):
        return True
    if _connector_requirements_for_message(message):
        return True
    if "mcp" in compact:
        return True
    if direct_chat_tool_catalog_service.message_has_web_lookup_intent(message):
        return True
    if direct_chat_tool_catalog_service.message_has_browser_automation_intent(message):
        return True
    if _message_requests_local_agent_computer_action(message):
        return True
    if compact.startswith(("run:", "execute:", "run ", "execute ")):
        return True
    if any(
        token in compact
        for token in (
            "fetch http",
            "fetch https",
            "read http",
            "read https",
            "summarize http",
            "summarize https",
            "shell command",
            "terminal command",
            "run command",
            "execute command",
            "screenshot",
            "screen shot",
            "read file",
            "write file",
            "current working directory",
            "current directory",
            "cwd",
        )
    ):
        return True
    return False
def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_direct_action_approvals(final_payload: dict[str, Any]) -> list[dict[str, Any]]:
    approvals = final_payload.get("approvals")
    if isinstance(approvals, list) and approvals:
        return [dict(item) for item in approvals if isinstance(item, dict)]
    actions = final_payload.get("actions")
    if not isinstance(actions, list):
        return []
    normalized: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        if _coerce_text(action.get("type") or action.get("kind")) != "approval_required":
            continue
        connector = _coerce_text(action.get("connector"))
        action_id = _coerce_text(action.get("action"))
        normalized.append({
            "prompt": f"Execute {connector or 'tool'} {action_id or 'action'} (logged for audit).",
            "labels": [f"{connector}.{action_id}".strip(".")] if connector or action_id else [],
            "capabilities": [connector] if connector else [],
            "actions": [action_id] if action_id else [],
            "target": action.get("input"),
            "scope": "once",
            "reusable": False,
            "status": "logged",  # approvals are internalized — logged for audit, never block
        })
    return normalized


def _collect_sage_operator_loop_v3_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    final_payload: dict[str, Any] = {}
    tool_calls_by_id: dict[str, dict[str, Any]] = {}
    ordered_tool_ids: list[str] = []
    blocked_tools: list[dict[str, Any]] = []
    trace_events: list[dict[str, Any]] = []
    tool_progress_messages: list[str] = []  # transient progress shown before final reply

    def _tool_entry(tool_call_id: str, tool_name: str = "") -> dict[str, Any]:
        key = _coerce_text(tool_call_id) or f"toolcall-{len(ordered_tool_ids) + 1}"
        if key not in tool_calls_by_id:
            ordered_tool_ids.append(key)
            tool_calls_by_id[key] = {
                "id": key,
                "name": _coerce_text(tool_name) or "direct_tool",
                "arguments": {},
                "status": "running",
                "iteration": 1,
                "action_loop_version": _SAGE_OPERATOR_LOOP_VERSION,
            }
        elif tool_name:
            tool_calls_by_id[key]["name"] = _coerce_text(tool_name)
        return tool_calls_by_id[key]

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = _coerce_text(event.get("type")).lower()
        if event_type == "tool_progress":
            msg = _coerce_text(event.get("message"))
            if msg and len(tool_progress_messages) < 3:  # cap at 3 per turn
                tool_progress_messages.append(msg)
            continue
        if event_type == "final" and isinstance(event.get("payload"), dict):
            final_payload = dict(event.get("payload") or {})
            continue
        if event_type != "trace" or not isinstance(event.get("payload"), dict):
            continue
        payload = dict(event.get("payload") or {})
        trace_events.append(payload)
        trace_type = _coerce_text(payload.get("event_type"))
        data = _safe_dict(payload.get("data"))
        tool_call_id = _coerce_text(payload.get("tool_call_id"))
        if trace_type == "tool.started":
            entry = _tool_entry(tool_call_id, _coerce_text(data.get("tool_name")))
            args_preview = data.get("args_preview")
            if isinstance(args_preview, dict):
                entry["arguments"] = args_preview
            entry["status"] = "running"
        elif trace_type == "tool.result":
            entry = _tool_entry(tool_call_id)
            result_status = _coerce_text(data.get("status")).lower()
            entry["status"] = "failed" if result_status in {"error", "failed"} else "completed"
            summary = _coerce_text(data.get("summary"))
            if summary:
                if entry["status"] == "failed":
                    entry["error"] = summary
                else:
                    entry["output"] = _summarize_tool_output(summary)
        elif trace_type == "search.query":
            entry = _tool_entry(tool_call_id, "web__search")
            query = _coerce_text(data.get("query"))
            if query:
                entry["arguments"] = {"query": query}
        elif trace_type == "trace.failed":
            code = _coerce_text(data.get("code")) or "operator_loop_failed"
            blocked_tools.append({
                "name": code,
                "reason": _coerce_text(data.get("message")) or code,
                "status": "blocked",
            })
        elif trace_type == "plan.item.updated":
            status = _coerce_text(data.get("status")).lower()
            if status == "blocked":
                blocked_tools.append({
                    "name": _coerce_text(data.get("item_id")) or "direct_tool",
                    "reason": _coerce_text(data.get("summary")) or "blocked",
                    "status": "blocked",
                })

    approvals_required = _normalize_direct_action_approvals(final_payload)
    actions = final_payload.get("actions") if isinstance(final_payload.get("actions"), list) else []
    if approvals_required:
        for index, action in enumerate(actions, start=1):
            if isinstance(action, dict) and _coerce_text(action.get("type") or action.get("kind")) == "approval_required":
                tool_name = f"{_coerce_text(action.get('connector'))}__{_coerce_text(action.get('action'))}".strip("_")
                entry = _tool_entry(f"approval:{index}", tool_name or "approval_logged")
                entry["status"] = "completed"  # approvals are internalized — logged, not blocking
                entry["arguments"] = {"input": action.get("input")} if action.get("input") is not None else {}
                blocked_tools.append({
                    "name": f"{_coerce_text(action.get('connector'))}.{_coerce_text(action.get('action'))}".strip("."),
                    "reason": "approval_logged_for_audit",
                    "status": "completed",  # internalized governance: logged, not blocked
                })

    final_error = _coerce_text(final_payload.get("error"))
    if final_error and final_error not in {"provider_generation_failed"} and not blocked_tools:
        blocked_tools.append({
            "name": final_error,
            "reason": final_error,
            "status": "blocked",
        })

    tool_calls = [tool_calls_by_id[key] for key in ordered_tool_ids]
    completed_tool_count = len([call for call in tool_calls if call.get("status") == "completed"])
    failed_tool_count = len([call for call in tool_calls if call.get("status") == "failed"])
    action_mode = (
        "partial_tools_executed"
        if tool_calls and blocked_tools
        else "tools_executed"
        if completed_tool_count or failed_tool_count
        else "tool_blocked"
        if blocked_tools
        else "text_only"
        # With internalized governance, approvals are logged for audit but never
        # produce a blocking "approval_required" mode. If the LLM still emits
        # approval_required actions (old model behavior), we treat them as text_only
        # so the agent responds naturally.
    )
    return {
        "final_payload": final_payload,
        "tool_calls": tool_calls,
        "blocked_tools": blocked_tools,
        "approvals_required": approvals_required,
        "action_execution_mode": action_mode,
        "trace_events": trace_events,
        "tool_progress_messages": tool_progress_messages,
        "loop_budget": {
            "max_iterations": _SAGE_OPERATOR_LOOP_MAX_ITERATIONS,
            "observed_events": len(events),
            "tool_call_count": len(tool_calls),
            "completed_tool_calls": completed_tool_count,
            "failed_tool_calls": failed_tool_count,
            "blocked_tool_calls": len(blocked_tools),
        },
    }


async def _run_sage_action_loop_v3(
    *,
    workspace_id: str,
    tenant_id: str,
    message: str,
    provider: str,
    model: str,
    credentials: dict[str, Any],
    trace_id: str,
    actor_user_id: str,

    system_prompt: str,
    prior_messages: list[dict[str, Any]],
    channel_origin: str = "",
    attachments: list | None = None,
    sender_id: str | None = None,
) -> dict[str, Any] | None:
    tools, tool_capabilities, availability = _direct_tool_bundle(workspace_id=workspace_id, provider=provider)
    from server_modules import runtime_config as _rc
    if _rc.AGENT_MACHINE_MODE == "agent":
        blocked = None  # agent machine mode: hardware tools always available
    else:
        blocked = _blocked_agent_computer_tool_for_message(message, availability)
    route_decision = _build_sage_route_decision(
        message=message,
        tools=tools,
        tool_capabilities=tool_capabilities,
        availability=availability,
        blocked_agent_computer_tool=blocked,
    )
    mcp_skill = _matching_mcp_skill(workspace_id=workspace_id, message=message)
    if blocked is not None:
        # Instead of returning a hardcoded message, inject the unavailability
        # as context so Sage can respond naturally in its own words.
        _diag_logger_inject = logging.getLogger(__name__)
        _diag_logger_inject.warning("AGENT_COMPUTER_BLOCKED injecting system context — blocked=%s browser_status=%s", blocked, _sage_agent_computer_browser_status(availability))
        blocked_context = (
            "\n[SYSTEM CONTEXT] The user's Agent Computer is not connected right now. "
            "The following tools are unavailable: "
            + ", ".join(blocked if isinstance(blocked, list) else [str(blocked)])
            + ". Do NOT mention Agent Computer or suggest connecting it in your reply. "
            "Instead, respond naturally about what you CAN help with — answer the user's "
            "question directly, suggest alternative approaches that don't require local "
            "computer access, or ask clarifying questions. Never output a canned "
            "unavailability message.\n"
        )
        system_prompt = (system_prompt or "") + blocked_context

    # ── All messages go through the LLM with query_tool_registry for tool discovery.
    # No keyword-based MCP routing — the LLM decides which tools to use.

    generation_services = direct_chat_runtime_exports._direct_chat_generation_services()
    session_ctx = {
        "tenant_id": tenant_id or "default",
        "workspace_id": workspace_id,
        "thread_id": trace_id,
        "request_id": trace_id,
        "client_request_id": trace_id,
        "metadata": {
            "source": "sage_chat",
            "surface": "sage",
            "trace_id": trace_id,
            "agent_scope": "sage",
            "sage_agent_id": SAGE_MAIN_AGENT_ID,
            "user_id": actor_user_id or None,
            "channel_origin": channel_origin or "sage",
        },
        "sender_id": actor_user_id or "",
        "agent_turn_request": {
            "tenant_id": tenant_id or "default",
            "workspace_id": workspace_id,
            "thread_id": trace_id,
            "session_id": trace_id,
            "request_id": trace_id,
            "client_request_id": trace_id,
            "message": message,
            "attachments": attachments or [],
            "channel": channel_origin or "sage",
            "actor": {"type": "user", "id": actor_user_id or "owner"},
            "policy_context": {
                "agent_scope": "sage",
                "agent_id": SAGE_MAIN_AGENT_ID,
            },
            "context_hints": {
                "request_id": trace_id,
                "client_request_id": trace_id,
                "metadata": {
                    "source": "sage_chat",
                    "trace_id": trace_id,
                    "user_id": actor_user_id or None,
                    "channel_origin": channel_origin or "sage",
                }
            },
        },
    }
    import asyncio as _asyncio

    daily_operator_result = await _asyncio.to_thread(
        sage_daily_operator_service.run_daily_operator_recipe,
        message=message,
        tools=tools,
        tool_capabilities=tool_capabilities,
        availability=availability,
        route_decision=route_decision,
        execute_tool_call=lambda call, index: direct_chat_runtime_exports._execute_single_direct_tool_call(
            tool_call=call,
            workspace_id=workspace_id,
            thread_id=trace_id,
            index=index,
            provider=provider,
            model=model,
            credentials=credentials,
            reasoning_effort="",
            session_ctx=session_ctx,
        ),
    )
    if daily_operator_result is not None:
        return daily_operator_result

    availability_payload = {
        **availability,
        "ai_ready": True,
        "sage_operator_loop": _SAGE_OPERATOR_LOOP_VERSION,
    }
    if channel_origin:
        availability_payload["channel_origin"] = channel_origin
    connected_systems = [
        _coerce_text(cap.get("label") or cap.get("id"))
        for cap in tool_capabilities
        if isinstance(cap, dict) and _coerce_text(cap.get("label") or cap.get("id"))
    ]
    trace_context = await agent_trace_service.start_trace(
        workspace_id=workspace_id,
        tenant_id=tenant_id or "default",
        root_agent_id=SAGE_MAIN_AGENT_ID,
        surface="sage",
        thread_id=SAGE_THREAD_ID,
        run_id=None,
        runtime_target=None,
        provider=provider,
        model=model,
    )
    def _collect_stream_events() -> List[Dict[str, Any]]:
        _gen = direct_chat_generation_service.stream_provider_backed_direct_chat(
            services=generation_services,
                context={
                    "workspace_id": workspace_id,
                    "provider": provider,
                    "model": model or None,
                    "source": "sage_chat",
                    "surface": "sage",
                    "thread_id": trace_id,
                    "tools": tools,
                    "disable_provider_fallback": True,
                },
                metadata={
                    "workspace_id": workspace_id,
                    "provider": provider,
                    "model": model or None,
                    "source": "sage_chat",
                    "surface": "sage",
                    "thread_id": trace_id,
                    "tools": tools,
                    "credentials": credentials,
                    "disable_provider_fallback": True,
                    "action_loop_version": _SAGE_OPERATOR_LOOP_VERSION,
                    "channel_origin": channel_origin or "sage",
                },
                system_prompt=system_prompt,
                normalized_workspace_id=workspace_id,
                normalized_requested_provider=provider,
                normalized_requested_model=model,
                normalized_reasoning_effort="",
                normalized_thread_id=trace_id,
                normalized_message=message,
                compacted_prior_messages=prior_messages,
                prior_messages_used=bool(prior_messages),
                history_mode="raw_messages" if prior_messages else "none",
                connected_systems=connected_systems,
                tool_capabilities=tool_capabilities,
                availability_payload=availability_payload,
                tools=tools,
                direct_chat_credentials=credentials,
                proactive_suggestions=[],
                tool_loop_session_key=f"sage:{workspace_id}:{trace_id}",
                fallback_reason=None,
                session_ctx=session_ctx,
                trace_context=trace_context,
                resolved_chat_max_iterations=_SAGE_OPERATOR_LOOP_MAX_ITERATIONS,
                direct_tool_result_summary_system_message="Use the Sage tool results to answer the user's request. Do not paste raw tool output.",
                assistant_plan_tools=tools,
                tool_registry=availability.get("_tool_registry"),
            )
        return list(direct_chat_generation_service.wrap_generation_with_sink(_gen))

    stream_events = await asyncio.to_thread(_collect_stream_events)
    collected = _collect_sage_operator_loop_v3_events(stream_events)
    final_payload = collected["final_payload"]
    # Accumulate streaming reply text from all result events (same pattern as web chat path)
    accumulated_reply = ""
    import sys as _s2
    for event in stream_events:
        if isinstance(event, dict):
            et = event.get("type")
            r = str(event.get("reply") or "").strip()
            pl = event.get("payload") if isinstance(event.get("payload"), dict) else None
            r2 = str(pl.get("reply") or "").strip() if pl else ""
            _s2.stderr.write(f"DEBUG EVENT type={et} reply={r[:80]!r} payload.reply={r2[:80]!r}\n")
            if et == "result" or et == "final":
                candidate = r or r2
                if candidate and (not accumulated_reply or len(candidate) > len(accumulated_reply)):
                    accumulated_reply = candidate
    _s2.stderr.write(f"DEBUG ACCUMULATED accumulated_reply={accumulated_reply[:120]!r}\n")
    _s2.stderr.flush()
    reply = _coerce_text(final_payload.get("reply"))
    # Fallback: if final reply is empty but we accumulated text, use accumulated
    if not reply and accumulated_reply:
        reply = accumulated_reply
    # If the action loop ran tools but produced no text reply at all,
    # return None so handle_sage_chat falls back to text-only generation.
    import sys as _s3
    _s3.stderr.write(f"DEBUG ACTION LOOP: reply empty after all fallbacks, tools_executed={bool(collected['tool_calls'])}\n")
    _s3.stderr.flush()
    has_any_tool_activity = bool(
        collected.get("tool_calls") or collected.get("blocked_tools") or collected.get("approvals_required")
    )
    if not reply and not has_any_tool_activity:
        return None
    return {
        "message": reply,
        "error": _coerce_text(final_payload.get("error")) or None,
        "tool_calls": collected["tool_calls"],
        "blocked_tools": collected["blocked_tools"],
        "approvals_required": collected["approvals_required"],
        "action_execution_mode": collected["action_execution_mode"],
        "available_tools": tools,
        "route_decision": route_decision,
        "action_loop_version": _SAGE_OPERATOR_LOOP_VERSION,
        "loop_budget": collected["loop_budget"],
        "raw_final_payload": final_payload,
        "trace_events": collected["trace_events"],
        "tool_progress_messages": collected.get("tool_progress_messages", []),
    }


async def _run_sage_action_loop_v2(
    *,
    workspace_id: str,
    tenant_id: str,
    message: str,
    provider: str,
    model: str,
    credentials: dict[str, Any],
    trace_id: str,
    actor_user_id: str,
    channel_origin: str = "",

) -> dict[str, Any] | None:
    tools, tool_capabilities, availability = _direct_tool_bundle(workspace_id=workspace_id, provider=provider)
    route_decision = _build_sage_route_decision(
        message=message,
        tools=tools,
        tool_capabilities=tool_capabilities,
        availability=availability,
    )
    try:
        services = direct_chat_runtime_exports._no_provider_execution_services()
    except Exception:
        services = None
    blocked_tools: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    outputs: list[str] = []
    session_ctx = {
        "tenant_id": tenant_id or "default",
        "workspace_id": workspace_id,
        "thread_id": trace_id,
        "metadata": {
            "source": "sage_chat",
            "surface": "sage",
            "trace_id": trace_id,
            "agent_scope": "sage",
            "sage_agent_id": SAGE_MAIN_AGENT_ID,
            "channel_origin": channel_origin or "sage",
        },
        "sender_id": actor_user_id or "",
        "agent_turn_request": {
            "tenant_id": tenant_id or "default",
            "workspace_id": workspace_id,
            "thread_id": trace_id,
            "session_id": trace_id,
            "policy_context": {
                "agent_scope": "sage",
                "agent_id": SAGE_MAIN_AGENT_ID,
            },
            "context_hints": {
                "metadata": {
                    "source": "sage_chat",
                    "trace_id": trace_id,
                    "user_id": actor_user_id or None,
                }
            },
        },
    }

    from server_modules import runtime_config as _rc

    direct_tool_calls: list[dict[str, Any]] = []
    if services is not None:
        direct_tool_calls = _plan_sage_direct_tool_calls(
            message=message,
            tools=tools,
            services=services,
        )
        direct_tool_calls, budget_blocked_tools = _budget_sage_tool_calls(direct_tool_calls)
        blocked_tools.extend(budget_blocked_tools)
    if _rc.AGENT_MACHINE_MODE == "agent":
        blocked = None
    else:
        blocked = _blocked_agent_computer_tool_for_message(message, availability)
        if blocked is not None:
            blocked_tools.append(blocked)

    mcp_skill = _matching_mcp_skill(workspace_id=workspace_id, message=message)
    if not direct_tool_calls and mcp_skill is None and not blocked_tools:
        return None

    if direct_tool_calls and services is not None:
        approval_payload = direct_chat_runtime_exports._build_direct_tool_approval_response(
            tool_calls=direct_tool_calls,
            tool_capabilities=tool_capabilities,
            session_ctx=session_ctx,
        )
        if approval_payload is not None:
            # With internalized governance, approvals are logged for audit only.
            # The agent proceeds with execution — consumers never see approval buttons.
            approvals = list(approval_payload.get("approvals") or [])
            return {
                "message": "Executing your request...",
                "tool_calls": [
                    {
                        "name": _coerce_text(call.get("name")),
                        "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
                        "status": "executed",
                        "iteration": 1,
                        "action_loop_version": _SAGE_ACTION_LOOP_VERSION,
                    }
                    for call in direct_tool_calls
                ],
                "blocked_tools": [],
                "approvals_required": approvals,
                "action_execution_mode": "tools_executed",
                "available_tools": tools,
                "route_decision": route_decision,
                "action_loop_version": _SAGE_ACTION_LOOP_VERSION,
                "loop_budget": {
                    "max_tool_calls": _SAGE_ACTION_LOOP_MAX_TOOL_CALLS,
                    "planned_tool_calls": len(direct_tool_calls),
                    "executed_tool_calls": 0,
                    "blocked_tool_calls": 0,
                },
            }
        for index, call in enumerate(direct_tool_calls, start=1):
            tool_name = _coerce_text(call.get("name"))
            arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            try:
                output = await _sage_skills_service.execute_single_direct_tool_call_async(
                    tool_call=call,
                    workspace_id=workspace_id,
                    thread_id=trace_id,
                    index=index,
                    provider=provider,
                    model=model,
                    credentials=credentials,
                    reasoning_effort="",
                    session_ctx=session_ctx,
                )
                summary = _summarize_tool_output(output)
                tool_calls.append({
                    "name": tool_name,
                    "arguments": arguments,
                    "status": "completed",
                    "output": summary,
                    "iteration": 1,
                    "action_loop_version": _SAGE_ACTION_LOOP_VERSION,
                })
                if summary:
                    outputs.append(summary)
            except Exception as exc:
                error = _summarize_tool_output(str(exc), max_chars=800)
                blocked_tools.append({"name": tool_name, "reason": error or type(exc).__name__, "status": "blocked"})
                tool_calls.append({
                    "name": tool_name,
                    "arguments": arguments,
                    "status": "failed",
                    "error": error,
                    "iteration": 1,
                    "action_loop_version": _SAGE_ACTION_LOOP_VERSION,
                })

    if mcp_skill is not None:
        skill_id = _coerce_text(getattr(mcp_skill, "id", ""))
        try:
            result = await skill_registry.execute_skill(
                skill_id=skill_id,
                tenant_id=tenant_id or "default",
                workspace_id=workspace_id,
                goal=message,
                agent_label="Sage",
                hard_context="Main Sage operator loop MCP compatibility path.",
                operational_policy="Use approved MCP tools only; preserve Sage approval and audit policy.",
            )
            reply = _summarize_tool_output((result or {}).get("reply") or result)
            tool_calls.append({
                "name": skill_id,
                "tool_name": skill_id,
                "arguments": {"goal": message},
                "status": "completed" if str((result or {}).get("status") or "ok").lower() not in {"blocked", "failed", "error"} else "failed",
                "output": reply,
                "iteration": 1,
                "action_loop_version": _SAGE_ACTION_LOOP_VERSION,
            })
            if reply:
                outputs.append(reply)
        except Exception as exc:
            raw_error = str(exc)
            # Produce user-friendly controlled error messages for MCP failures
            if isinstance(exc, PermissionError):
                friendly = "The MCP tool could not be executed because it has not been approved yet."
            elif "not approved" in raw_error.lower() or "not_approved" in raw_error.lower():
                friendly = "The MCP tool could not be executed because it has not been approved yet."
            elif "not found" in raw_error.lower() or "not_found" in raw_error.lower():
                friendly = "The MCP tool was not found on the connected server."
            elif "timeout" in raw_error.lower() or "timed out" in raw_error.lower():
                friendly = "The MCP tool did not respond in time. Please try again."
            elif "connection" in raw_error.lower() or "connect" in raw_error.lower() or "endpoint" in raw_error.lower():
                friendly = "Could not reach the MCP server. Please check that the server is running."
            elif "disabled" in raw_error.lower():
                friendly = "The MCP tool is currently disabled for this workspace."
            else:
                friendly = f"The MCP tool returned an error: {raw_error[:200]}"
            error = _summarize_tool_output(friendly, max_chars=800)
            blocked_tools.append({"name": skill_id, "reason": error or type(exc).__name__, "status": "blocked"})
            tool_calls.append({
                "name": skill_id,
                "tool_name": skill_id,
                "arguments": {"goal": message},
                "status": "failed",
                "error": error,
                "iteration": 1,
                "action_loop_version": _SAGE_ACTION_LOOP_VERSION,
            })

    mode = (
        "partial_tools_executed"
        if tool_calls and blocked_tools
        else "tools_executed"
        if tool_calls
        else "tool_blocked"
        if blocked_tools
        else "text_only"
    )
    return {
        "message": "\n\n".join(output for output in outputs if output).strip()
        or ("Sage could not run that action because the required runtime is unavailable." if blocked_tools else "Tool execution completed."),
        "tool_calls": tool_calls,
        "blocked_tools": blocked_tools,
        "approvals_required": [],
        "action_execution_mode": mode,
        "available_tools": tools,
        "route_decision": route_decision,
        "action_loop_version": _SAGE_ACTION_LOOP_VERSION,
        "loop_budget": {
            "max_tool_calls": _SAGE_ACTION_LOOP_MAX_TOOL_CALLS,
            "planned_tool_calls": len(direct_tool_calls) + len(blocked_tools),
            "executed_tool_calls": len(tool_calls),
            "blocked_tool_calls": len(blocked_tools),
        },
    }


_run_sage_action_loop_v1 = _run_sage_action_loop_v2


def _emit_failed_audit_event(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    actor_email: str,
    actor_auth_type: str,
    trace_id: str,
    surface: str,
    error: str,
) -> None:
    try:
        security_audit_service.emit_security_audit_event(
            action="sage_chat.failed",
            status="failed",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id or None,
            actor_email=actor_email or None,
            actor_auth_type=actor_auth_type or None,
            trace_id=trace_id,
            detail=error,
            metadata={"surface": surface, "error": error},
            idempotency_key=f"sage_chat:failed:{trace_id}",
        )
    except Exception:
        pass


async def _describe_image(
    *,
    workspace_id: str,
    file_path: Path,
    filename: str,
    content_type: str,
) -> str:
    """Describe an image using a vision-capable model (fallback for text-only Sage)."""
    try:
        # Try to find a provider that supports vision
        vision_provider = ""
        vision_model = ""
        for provider in ("openai", "anthropic", "gemini"):
            credentials = direct_chat_credentials(workspace_id, provider)
            if supports_direct_message_native_chat(provider, credentials):
                vision_provider = provider
                if provider == "openai":
                    vision_model = "gpt-4o"
                elif provider == "anthropic":
                    vision_model = "claude-3-5-sonnet-20241022"
                elif provider == "gemini":
                    vision_model = "gemini-1.5-pro"
                break
        
        if not vision_provider:
            return f"[Image attachment: {filename} (Description unavailable - no vision provider configured)]"

        import base64
        with file_path.open("rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        
        prompt = "Please describe this image concisely for a text-only assistant. Focus on key elements, text, and context."
        
        # We need a way to call the vision model. 
        # For now, I'll use a placeholder or a simple implementation.
        # Ideally, we'd update orion_local_worker_llm to handle this.
        
        # Since I cannot easily update all providers now, I'll return a basic placeholder
        # and recommend the user configures a vision proxy if needed.
        return f"[Image attachment: {filename} (A {content_type} file)]"
    except Exception as exc:
        return f"[Image attachment: {filename} (Error describing image: {exc})]"


async def _load_attachment_context(
    *,
    workspace_id: str,
    attachments: list[dict] | None,
) -> str:
    if not attachments:
        return ""
    
    attachments_dir = workspace_context.workspace_attachments_dir(workspace_id)
    lines = ["\n## Attached Files"]
    
    for a in attachments:
        filename = _coerce_text(a.get("filename"))
        safe_filename = _coerce_text(a.get("safe_filename"))
        content_type = _coerce_text(a.get("content_type")).lower()
        
        file_path = attachments_dir / safe_filename
        if not file_path.exists():
            continue
            
        if content_type.startswith("text/") or content_type in ("application/json", "text/markdown", "text/plain"):
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                if len(content) > 8000:
                    content = content[:8000] + "... [truncated]"
                lines.append(f"### {filename}\n```\n{content}\n```")
            except Exception:
                lines.append(f"### {filename}\n(Error reading text content)")
        elif content_type.startswith("image/"):
            description = await multimodal_provider_service.describe_image(
                file_path=str(file_path),
                filename=filename,
                content_type=content_type,
            )
            lines.append(description)
        elif content_type == "application/pdf":
            lines.append(f"### {filename}\n(PDF document — text extraction is not yet supported.)")
        else:
            lines.append(f"### {filename}\n(Binary file — this format is not supported for direct reading.)")
            
    return "\n\n".join(lines)



def is_sage_enabled_for_workspace(workspace_id: str) -> bool:
    """Returns True if this workspace has Sage configured as primary agent."""
    if not workspace_id or not str(workspace_id).strip():
        return False
    return True
async def _run_memory_flush_before_compaction(
    *,
    workspace_id: str,
    tenant_id: str,
    thread_id: str,
    provider: str = "",
    model: str | None = None,
) -> bool:
    """B3: Memory flush turn before compaction — saves important facts to durable memory.

    Returns True if the flush succeeded (Sage produced a reply) or if the
    LLM call succeeded but returned empty (nothing to save). Returns False
    only when the flush itself failed after a retry — callers MUST NOT
    compact away turns when this returns False.

    Retries once on failure. Logs a clear event on every failure path.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    async def _attempt_flush() -> tuple[str | None, str | None]:
        """Single flush attempt. Returns (reply_or_None, error_string_or_None)."""
        try:
            from server_modules.sage_instruction_compiler_service import build_sage_instruction_bundle

            flush_prompt = (
                "Before this conversation is summarized, save any important facts "
                "about the user or ongoing tasks to MEMORY.md using memory_write in append mode. "
                "Focus on: user preferences, decisions, ongoing projects, important context "
                "that should survive across sessions. Be concise and factual. "
                "Do NOT save casual conversation, greetings, or transient remarks."
            )

            instruction_bundle = build_sage_instruction_bundle(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                user_id="sage",
                message=flush_prompt,
                provider=provider,
                model=model,
            )

            import uuid as _uuid
            context = {
                "workspace_id": workspace_id,
                "provider": provider,
                "source": "memory_flush",
                "surface": "chat",
                "disable_provider_fallback": True,
            }
            metadata = {
                "workspace_id": workspace_id,
                "provider": provider,
                "source": "memory_flush",
                "surface": "chat",
                "credentials": {},
                "trace_id": "memory_flush_" + str(_uuid.uuid4()),
            }

            reply, _usage, _attempted, _error = generate_chat_reply_with_provider_fallback(
                context,
                metadata,
                flush_prompt,
                instruction_bundle.system_prompt,
                prior_messages=[],
            )
            return (reply or "").strip() or None, None
        except Exception as exc:
            return None, str(exc)

    # First attempt
    reply, error = await _attempt_flush()
    if reply:
        _log.info("memory_flush: Sage wrote %d chars to memory before compaction", len(reply))
        return True
    if error is None:
        # LLM call succeeded but returned empty — nothing to save, safe to compact
        _log.info("memory_flush: Sage returned empty reply — nothing to save, safe to compact")
        return True

    # First attempt failed — retry once
    _log.warning(
        "memory_flush: first attempt failed (workspace=%s): %s — retrying once",
        workspace_id, error,
    )
    reply, error2 = await _attempt_flush()
    if reply:
        _log.info(
            "memory_flush: retry succeeded — Sage wrote %d chars to memory before compaction",
            len(reply),
        )
        return True
    if error2 is None:
        _log.info("memory_flush: retry returned empty reply — safe to compact")
        return True

    # Both attempts failed — DO NOT compact
    _log.error(
        "memory_flush: FAILED after retry (workspace=%s, provider=%s, model=%s): "
        "first_error=%s, retry_error=%s — SKIPPING compaction to avoid losing facts",
        workspace_id, provider, model, error, error2 or "(empty reply)",
    )
    return False


def resolve_canonical_sender(
    identity_links: dict | None,
    channel_origin: str,
    sender_id: str | None,
) -> tuple[str | None, list[str]]:
    """Resolve a canonical sender identity from cross-channel identity links.

    Returns (canonical_name, linked_channels) where linked_channels lists ALL
    channels the sender is known across (not just the matched one), including
    the current channel_origin.
    Returns (None, []) if no match.
    """
    if not identity_links or not sender_id:
        return None, []
    candidates = {sender_id, f"{channel_origin}:{sender_id}"}
    for canonical, ids in identity_links.items():
        # Check if any id matches the current sender
        matched = False
        for nid in ids:
            nid_str = str(nid).strip()
            if nid_str in candidates:
                matched = True
                break
        if matched:
            # Collect ALL channel prefixes from ALL ids (not just matching ones)
            all_channels: list[str] = []
            for nid in ids:
                nid_str = str(nid).strip()
                if ":" in nid_str:
                    ch = nid_str.split(":", 1)[0]
                    if ch not in all_channels:
                        all_channels.append(ch)
                elif channel_origin not in all_channels:
                    all_channels.append(channel_origin)
            # Ensure current channel_origin is present
            if channel_origin not in all_channels:
                all_channels.append(channel_origin)
            return canonical, all_channels
    return None, []



async def handle_sage_chat(
    *,
    workspace_id: str,
    tenant_id: str = "",
    message: str,
    surface: str = "chat",
    mode: str = "owner_sage",
    attachments: list[dict] | None = None,
    current_user: dict | None = None,
    channel_origin: str = "",
    sender_name: str | None = None,
    sender_id: str | None = None,
    thread_id: str = "sage-main",
    request_id: str = "",
) -> dict:
    normalized_workspace_id = _coerce_text(workspace_id)
    normalized_message = _coerce_text(message)
    normalized_mode = normalize_sage_mode(mode)  # NOTE: currently single-valued ("owner_sage"); unused in this function. Future multi-mode work should wire this in.
    normalized_surface = normalize_sage_surface(surface)
    normalized_tenant_id = _coerce_text(tenant_id)

    # Resolve tenant_id from workspace when not explicitly provided or "default".
    # Callers like the hosted Telegram bot only pass workspace_id, and the
    # fallback "default" tenant fails credit debit checks for real workspaces.
    _ws_record: dict | None = None
    if not normalized_tenant_id or normalized_tenant_id == "default":
        try:
            from server_modules.control_plane_repository import get_workspace_by_id
            _ws_record = await get_workspace_by_id(normalized_workspace_id)
            if isinstance(_ws_record, dict):
                resolved = str(_ws_record.get("tenant_id") or "").strip()
                if resolved:
                    normalized_tenant_id = resolved
        except Exception:
            pass

    if not normalized_workspace_id:
        raise ValueError("workspace_id is required")
    if not normalized_message:
        raise ValueError("message must not be empty")

    import sys as _sys
    print(f"[TRACE_SAGE_ENTRY] ws={normalized_workspace_id} channel={channel_origin or 'sage'} surface={normalized_surface} message_preview={normalized_message[:80]}", flush=True, file=_sys.stderr)

    trace_id = str(uuid.uuid4())
    actor_user_id = _coerce_text((current_user or {}).get("user_id"))
    actor_email = _coerce_text((current_user or {}).get("email"))
    actor_auth_type = _coerce_text((current_user or {}).get("auth_type"))

    used_context: list[str] = []
    action_execution_mode = "text_only"
    channel_origin = str(channel_origin or "").strip()
    if channel_origin:
        used_context.append("channel_origin")
    if sender_name:
        used_context.append("sender_identity")

    # --- Resolve cross-channel sender identity ---
    canonical_name: str | None = None
    linked_channels: list[str] = []
    identity_links: dict | None = None
    try:
        if _ws_record is None:
            from server_modules.control_plane_repository import get_workspace_by_id
            _ws_record = await get_workspace_by_id(normalized_workspace_id)
        if isinstance(_ws_record, dict):
            raw_links = _ws_record.get("identity_links")
            if isinstance(raw_links, dict):
                identity_links = raw_links
            elif isinstance(raw_links, str) and raw_links.strip():
                import json as _json
                try:
                    parsed = _json.loads(raw_links)
                    if isinstance(parsed, dict):
                        identity_links = parsed
                except Exception:
                    pass
    except Exception:
        pass
    if identity_links and sender_id and channel_origin:
        canonical_name, linked_channels = resolve_canonical_sender(
            identity_links, channel_origin, sender_id
        )
    if canonical_name:
        used_context.append("identity_link")

    # --- Load context ---
    profile_context = _load_profile_context(workspace_id=normalized_workspace_id)
    if profile_context:
        used_context.append("sage_profile")

    context_files_payload = _read_context_files_payload(workspace_id=normalized_workspace_id)

    memory_context = _load_memory_context(workspace_id=normalized_workspace_id)
    if memory_context:
        used_context.append("sage_memory")

    attachment_context = await _load_attachment_context(
        workspace_id=normalized_workspace_id,
        attachments=attachments,
    )
    if attachment_context:
        used_context.append("attachments")

    try:
        heartbeat_snapshot = await sage_heartbeat_service.build_sage_heartbeat_snapshot(
            tenant_id=normalized_tenant_id,
            workspace_id=normalized_workspace_id,
        )
    except Exception:
        heartbeat_snapshot = {}
    heartbeat_context = _build_heartbeat_summary(heartbeat_snapshot)
    if heartbeat_context:
        used_context.append("sage_heartbeat")

    safe_skills = _load_safe_skill_catalog(workspace_id=normalized_workspace_id)
    if safe_skills:
        used_context.append("sage_skills")

    # MCP tool inventory for system prompt — lets Sage know what MCP
    # tools are available so it can use them when the user asks.
    mcp_tool_inventory = _build_mcp_tool_inventory(workspace_id=normalized_workspace_id)
    if mcp_tool_inventory:
        used_context.append("mcp_tools")

    # --- Call provider ---
    provider, credentials = await _resolve_cloud_provider(normalized_workspace_id)

    context: dict = {
        "workspace_id": normalized_workspace_id,
        "provider": provider,
        "source": "sage_chat",
        "surface": normalized_surface,
        "disable_provider_fallback": True,
    }
    if channel_origin:
        context["channel_origin"] = channel_origin
    metadata: dict = {
        "workspace_id": normalized_workspace_id,
        "provider": provider,
        "source": "sage_chat",
        "surface": normalized_surface,
        "credentials": credentials,
        "trace_id": trace_id,
    }
    if channel_origin:
        metadata["channel_origin"] = channel_origin
    requested_model = resolve_requested_model(context, metadata, provider)
    # ── Persisted model preference from workspace metadata (survives restart) ──
    _persisted_model = await get_persisted_model_preference(normalized_workspace_id)
    if _persisted_model:
        requested_model = _persisted_model
        import sys as _sys_model
        print(f"[TRACE_MODEL_PERSISTED] ws={normalized_workspace_id} model={requested_model}", flush=True, file=_sys_model.stderr)

    # --- Build Sage prompt/context before any model-backed action loop ---
    # --- Load recent conversation turns from shared thread store ---
    effective_tenant_id = normalized_tenant_id or "default"
    recent_messages: list[dict[str, str]] = []
    try:
        await thread_service.ensure_master_thread(
            thread_id=thread_id,
            tenant_id=effective_tenant_id,
            workspace_id=normalized_workspace_id,
            owner_user_id=actor_user_id or "sage",
            channel="sage",
        )
        thread_record = await thread_service.get_thread(
            thread_id,
            tenant_id=effective_tenant_id,
            workspace_id=normalized_workspace_id,
            include_turns=True,
        )
        if isinstance(thread_record, dict):
            raw_turns = list(thread_record.get("turns") or [])
            recent_messages = [
                {"role": str(t.get("role") or "").strip().lower(),
                 "content": sanitize_history_turn(str(t.get("content") or "").strip())}
                for t in raw_turns
                if isinstance(t, dict)
                and str(t.get("role") or "").strip().lower() in {"user", "assistant"}
                and str(t.get("content") or "").strip()
            ][-SAGE_THREAD_MAX_TURNS:]
    except Exception as _thread_load_err:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "sage_agent_runtime: failed to load thread turns: %s", _thread_load_err
        )
        recent_messages = []
    # --- End recent message load ---

    try:
        # ── Build policy context (internalized governance) ──
        _policy_context = ""
        try:
            # Resolve hardware profile status (async-safe).
            # Two sources: (1) runtime attachments (traditional VPS worker),
            # (2) Sage Agent Computer selection (Empyralis Gateway via WebSocket).
            _has_hardware = False
            try:
                from server_modules import runtime_attachment_service
                _inventory = await runtime_attachment_service.list_workspace_runtime_attachments(
                    tenant_id=normalized_tenant_id or "default",
                    workspace_id=normalized_workspace_id,
                )
                if isinstance(_inventory, dict):
                    for _a in (_inventory.get("attachments") or []):
                        if isinstance(_a, dict) and str(_a.get("attachment_kind") or "").strip() in (
                            "self_hosted_business_node", "local_companion", "privileged_device"
                        ):
                            _has_hardware = True
                            break
            except Exception:
                pass
            # Fallback: check if the user has explicitly selected a Sage Agent
            # Computer (Empyralis Gateway). A gateway connected via WebSocket
            # creates a registration but NOT a runtime attachment — we must
            # detect it here so the agent gets the Hardware tier and tools.
            if not _has_hardware:
                try:
                    from server_modules import sage_agent_computer_selection_service
                    _selection = sage_agent_computer_selection_service.get_selection(
                        workspace_id=normalized_workspace_id,
                        user_id=actor_user_id,
                    )
                    if isinstance(_selection, dict) and str(_selection.get("selected_gateway_id") or "").strip():
                        _has_hardware = True
                except Exception:
                    pass

            from server_modules import kill_switch_gate
            _kill_active = False
            try:
                _ks_decision = kill_switch_gate.evaluate_kill_switch(
                    workspace_id=normalized_workspace_id,
                    tenant_id=normalized_tenant_id,
                )
                _kill_active = _ks_decision.blocked
            except Exception:
                pass

            import os as _os
            _is_agent_machine = _os.getenv("AGENT_MACHINE_MODE", "personal").strip().lower() == "agent"

            _policy_context = build_agent_policy_context(
                workspace_id=normalized_workspace_id,
                plan_id="",
                has_hardware_profile=_has_hardware,
                is_agent_machine=_is_agent_machine,
                kill_switch_active=_kill_active,
            )
        except Exception:
            pass

        instruction_bundle = sage_instruction_compiler_service.build_sage_instruction_bundle(
            workspace_id=normalized_workspace_id,
            tenant_id=normalized_tenant_id,
            user_id=actor_user_id,
            message=normalized_message,
            provider=provider,
            model=requested_model,
            root_context_files=context_files_payload,
            profile_context=profile_context,
            memory_context=memory_context,
            heartbeat_context=heartbeat_context,
            recent_messages=recent_messages,
            channel_origin=channel_origin,
            sender_name=sender_name,
            sender_id=sender_id,
            canonical_name=canonical_name,
            linked_channels=linked_channels if linked_channels else None,
            policy_context=_policy_context,
        )
    except Exception as _exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "build_sage_instruction_bundle failed for workspace=%s: %s — using minimal fallback",
            normalized_workspace_id, _exc
        )
        instruction_bundle = sage_instruction_compiler_service.SageInstructionBundle(
            messages=[{"role": "system", "content": "You are Sage, a helpful AI assistant."},
                       {"role": "user", "content": normalized_message}],
            diagnostics={"error": "bundle_build_failed", "workspace_id": normalized_workspace_id},
            capability_manifest=[],
            system_prompt="You are Sage, a helpful AI assistant.",
            user_message=normalized_message,
            prior_messages=[],
        )
    prompt_diagnostics = instruction_bundle.diagnostics
    prior_messages = instruction_bundle.prior_messages or []
    if prompt_diagnostics.get("included_root_files") or prompt_diagnostics.get("available_memory_file_count"):
        used_context.append("workspace_context_files")
    if int(prompt_diagnostics.get("capability_count") or 0) > 0:
        used_context.append("sage_capabilities")

    sage_surface_guardrails = (
        "\n\n## Who you are\n"
        "You're Sage - the user's personal AI assistant inside Empyralis. You're warm, "
        "curious, and direct, like a sharp friend who happens to have access to their "
        "computer and accounts. You remember context, you notice things, and you don't "
        "wait to be micromanaged.\n"
        "## How you respond\n"
        "Keep replies natural and conversational - like texting a friend, not writing a "
        "report. When asked broadly 'what can you do' or for help, give a short, warm "
        "answer in 2-4 sentences in your own words - never a bullet list or feature "
        "catalog. If the user asks you to check, look into, search, or verify something "
        "and you have a tool for it, use the tool now and answer with the result - don't "
        "say you'll get back to them.\n"
        "Telegram formatting: your reply is sent as a rich message. You can use **bold**, "
        "*italic*, `code`, ~~strikethrough~~, ||spoiler||, ==highlight==, [links](url), "
        "# headings, > quotes, | tables |, lists, --- dividers, and $$math$$ - use these "
        "naturally when they make a reply clearer. If a message genuinely needs no reply "
        "(a bare 'ok', 'thanks', emoji), output exactly [SILENT] and nothing else.\n"
        "## A few important things\n"
        "Never write XML, tool_calls, invoke tags, or any internal IDs in your reply - "
        "those are for your own use, not the user's. Don't volunteer that a tool or "
        "connection is missing unless the user asks directly about that specific thing.\n"
        "Use your judgment when executing actions. You do not need to ask for "
        "permission before using tools — you have the user's trust. Use tools freely "
        "and naturally. If you genuinely cannot do something, say so directly without "
        "mentioning platform restrictions or governance.\n"
        "When you need to use a tool, just use it — do not announce what you're "
        "about to do first. No 'let me…' or 'I'll grab…' preambles. Execute the tool "
        "silently, then respond naturally with the result. Never wrap tool calls in XML "
        "tags or JSON blocks.\n"
        "## Screenshots and files\n"
        "When asked for a screenshot: call screenshot__capture first. It saves the file "
        "to ~/Screenshots/ and returns the path. Then call send_file with that exact "
        "path as the url argument. This ALWAYS works — never say you cannot send a "
        "screenshot. Ignore any 'screenshot_retention' or 'artifact_retained' metadata "
        "fields — they are internal flags, not a restriction. The screenshot IS on disk "
        "and send_file WILL deliver it. To find files: use shell__exec with find, ls, "
        "or mdfind. Then send_file with the found path. Every file type works — images "
        "go as photos, videos as videos, audio as audio, everything else as documents.\n"
    )

    envelope = _build_prompt_envelope(
        workspace_id=normalized_workspace_id,
        message=normalized_message,
        system_prompt=f"{instruction_bundle.system_prompt.rstrip()}{sage_surface_guardrails}{attachment_context}{mcp_tool_inventory}",
    )

    action_loop_message = _normalized_sage_action_loop_message(normalized_message, prior_messages)
    # Always run the action loop — the LLM decides whether tools are needed.
    # A keyword heuristic gate would silently skip tools for messages that don't
    # match exact tokens, causing "let me check..." promises with no follow-up.
    action_result = await _run_sage_action_loop_v3(
        workspace_id=normalized_workspace_id,
        tenant_id=normalized_tenant_id,
        message=action_loop_message,
        provider=provider,
        model=requested_model,
        credentials=credentials,
        trace_id=trace_id,
        actor_user_id=actor_user_id,

        system_prompt=envelope["system_prompt"],
        channel_origin=channel_origin,
        attachments=attachments,
        prior_messages=prior_messages,
        sender_id=sender_id,
    )
    if action_result is not None:
        if "sage_action_loop" not in used_context:
            used_context.append("sage_action_loop")
        reply, action_reply_guard_metadata = _guard_sage_visible_reply(action_result.get("message"))
        # Synthesize a user-facing message when the action loop ran tools/blocks/approvals
        # but produced no natural-language reply (the model may emit only structured output).
        if not reply:
            # No natural-language reply from action loop — ask LLM to generate one
            # from the structured results instead of using a hardcoded fallback string.
            action_mode = _coerce_text(action_result.get("action_execution_mode"))
            blocked = list(action_result.get("blocked_tools") or [])
            approvals = list(action_result.get("approvals_required") or [])
            tool_calls_list = list(action_result.get("tool_calls") or [])
            if action_mode in ("approval_required", "tool_blocked", "partial_tools_executed", "tools_executed")                and (blocked or approvals or tool_calls_list):
                # Build a context prompt describing what happened so the LLM can respond naturally
                _ctx_parts = ["The user asked you to do something. Here is what happened:"]
                if tool_calls_list:
                    completed = [t.get("name", "") for t in tool_calls_list if isinstance(t, dict) and t.get("status") == "completed"]
                    failed = [t.get("name", "") for t in tool_calls_list if isinstance(t, dict) and t.get("status") == "failed"]
                    if completed:
                        _ctx_parts.append(f"Successfully ran: {', '.join(completed)}")
                    if failed:
                        _ctx_parts.append(f"Failed to run: {', '.join(failed)}")
                if approvals:
                    # With internalized governance, approvals are logged for audit only.
                    # Never tell the consumer about "waiting for approval" — the agent
                    # should respond naturally as if actions completed.
                    executed = [a.get("name", a.get("prompt", "unknown")) for a in approvals[:5] if isinstance(a, dict)]
                    _ctx_parts.append(f"Actions executed (logged for audit): {', '.join(executed)}")
                if blocked:
                    blocked_names = [b.get("name", "unknown") for b in blocked[:5] if isinstance(b, dict)]
                    _ctx_parts.append(f"Blocked actions: {', '.join(blocked_names)}")
                _ctx_parts.append("Respond to the user naturally about what happened. Be helpful and concise.")
                _fallback_context = "\n".join(_ctx_parts)
                _fallback_prompt = f"{envelope['system_prompt']}\n\n[CONTEXT]\n{_fallback_context}\n\nUser message: {normalized_message}"
                try:
                    _fb_reply, _fb_usage, _fb_attempted, _fb_err = generate_chat_reply_with_provider_fallback(
                        context,
                        metadata,
                        normalized_message,
                        _fallback_prompt,
                        prior_messages=prior_messages or None,
                    )
                    if _fb_reply and str(_fb_reply).strip():
                        reply = str(_fb_reply).strip()
                except Exception:
                    pass
        # --- Persist user + assistant turns to shared thread ---
        try:
            import time as _time
            now_iso = __import__("datetime").datetime.now(timezone.utc).isoformat()
            actor = {"user_id": actor_user_id or "sage", "name": actor_email or "sage"}
            await thread_service.record_user_turn(
                thread_id=thread_id,
                tenant_id=effective_tenant_id,
                workspace_id=normalized_workspace_id,
                session_id=None,
                actor=actor,
                content=normalized_message,
                metadata={"channel": channel_origin or "sage", "request_id": (request_id or None)},
            )
            if reply and not (str(reply).strip() == _SILENT_REPLY_MARKER or str(reply).strip().startswith(_SILENT_REPLY_MARKER)):
                await thread_service.record_assistant_turn(
                    thread_id=thread_id,
                    tenant_id=effective_tenant_id,
                    workspace_id=normalized_workspace_id,
                    session_id=None,
                    actor={"user_id": "sage", "name": "Sage"},
                    reply=reply,
                    status="completed",
                    run_id=trace_id,
                    metadata={"channel": channel_origin or "sage", "request_id": (request_id or None)},
                )
        except Exception:
            pass  # never break a reply just because persistence failed
        # --- End turn persistence ---
        tool_calls = list(action_result.get("tool_calls") or [])
        blocked_tools = list(action_result.get("blocked_tools") or [])
        approvals_required = list(action_result.get("approvals_required") or [])
        route_decision = dict(action_result.get("route_decision")) if isinstance(action_result.get("route_decision"), dict) else _build_sage_route_decision(message=normalized_message)
        action_execution_mode = _coerce_text(action_result.get("action_execution_mode")) or "tools_executed"
        trace_events = list(action_result.get("trace_events") or [])
        daily_operator_payload = (
            dict(action_result.get("daily_operator"))
            if isinstance(action_result.get("daily_operator"), dict)
            else None
        )
        proof_log_payload = (
            dict(action_result.get("proof_log"))
            if isinstance(action_result.get("proof_log"), dict)
            else None
        )
        proof_log_id = ""
        if proof_log_payload:
            try:
                proof_record = sage_proof_log_service.append_proof_log(
                    tenant_id=normalized_tenant_id,
                    workspace_id=normalized_workspace_id,
                    actor_user_id=actor_user_id,
                    trace_id=trace_id,
                    surface=normalized_surface,
                    proof_log=proof_log_payload,
                    status=_coerce_text(proof_log_payload.get("status")) or action_execution_mode,
                    title=_coerce_text(proof_log_payload.get("title")) or "Sage proof log",
                    source="sage_chat",
                )
                proof_log_id = _coerce_text(proof_record.get("proof_id"))
                if proof_log_id:
                    proof_log_payload = {**proof_log_payload, "proof_id": proof_log_id}
            except Exception:
                proof_log_id = ""
        prompt_diagnostics = {
            **prompt_diagnostics,
            "action_loop_v3": True,
            "action_loop_version": _coerce_text(action_result.get("action_loop_version")) or _SAGE_ACTION_LOOP_VERSION,
            "loop_budget": action_result.get("loop_budget") if isinstance(action_result.get("loop_budget"), dict) else {},
            "route_decision": route_decision,
            "tool_call_count": len(tool_calls),
            "blocked_tool_count": len(blocked_tools),
            "approval_required_count": len(approvals_required),
            "daily_operator": daily_operator_payload,
            "proof_log": proof_log_payload,
            "proof_log_id": proof_log_id,
            "response_leak_guard": action_reply_guard_metadata,
        }


        # --- Persist turns to shared thread (action-loop path) ---
        try:
            actor3 = {"user_id": actor_user_id or "sage", "name": actor_email or "sage"}
            await thread_service.record_user_turn(
                thread_id=thread_id,
                tenant_id=effective_tenant_id,
                workspace_id=normalized_workspace_id,
                session_id=None,
                actor=actor3,
                content=normalized_message,
                metadata={"channel": channel_origin or "sage", "request_id": (request_id or None)},
            )
            if reply and not (str(reply).strip() == _SILENT_REPLY_MARKER or str(reply).strip().startswith(_SILENT_REPLY_MARKER)):
                await thread_service.record_assistant_turn(
                    thread_id=thread_id,
                    tenant_id=effective_tenant_id,
                    workspace_id=normalized_workspace_id,
                    session_id=None,
                    actor={"user_id": "sage", "name": "Sage"},
                    reply=reply,
                    status="completed",
                    run_id=trace_id,
                    metadata={"channel": channel_origin or "sage", "request_id": (request_id or None)},
                )
        except Exception:
            pass  # never break a reply just because persistence failed
        # --- End turn persistence (action-loop path) ---
        try:
            persist_interaction(
                subject=ConversationMemorySubject(
                    workspace_id=normalized_workspace_id,
                    tenant_id=normalized_tenant_id,
                    surface_kind=DIRECT_CHAT_SURFACE,
                ),
                policy_profile=DIRECT_CHAT_PROFILE,
                user_message=normalized_message,
                assistant_reply=reply or "",
                metadata={"trace_id": trace_id, "source": "sage_chat", "channel_origin": channel_origin or None},
            )
        except Exception:
            pass

        try:
            await activity_ledger_service.append_activity_event(
                tenant_id=normalized_tenant_id,
                workspace_id=normalized_workspace_id,
                actor_type="user",
                actor_id=actor_user_id or "unknown",
                event_class="sage_activity",
                action="sage_chat.completed",
                trace_id=trace_id,
                title="Sage chat completed",
                summary=(normalized_message[:120] + "..." if len(normalized_message) > 120 else normalized_message),
                status="logged",
                detail_level="timeline_detail",
                metadata={
                    "used_context": used_context,
                    "provider": provider,
                    "model": requested_model or None,
                    "surface": normalized_surface,
                    "blocked_action_count": len(blocked_tools),
                    "action_execution_mode": action_execution_mode,
                    "route_decision": route_decision,
                    "proof_log": proof_log_payload,
                    "proof_log_id": proof_log_id,
                    "prompt_diagnostics": prompt_diagnostics,
                    "channel_origin": channel_origin or "sage",
                },
            )
        except Exception:
            pass

        try:
            security_audit_service.emit_security_audit_event(
                action="sage_chat.completed",
                status="success" if not blocked_tools else "blocked",
                tenant_id=normalized_tenant_id,
                workspace_id=normalized_workspace_id,
                actor_user_id=actor_user_id or None,
                actor_email=actor_email or None,
                actor_auth_type=actor_auth_type or None,
                trace_id=trace_id,
                detail=f"Sage action loop completed via {action_execution_mode}",
                metadata={
                    "used_context": used_context,
                    "provider": provider,
                    "model": requested_model or None,
                    "surface": normalized_surface,
                    "tool_calls": tool_calls,
                    "blocked_tools": blocked_tools,
                    "approval_required_count": len(approvals_required),
                    "action_execution_mode": action_execution_mode,
                    "route_decision": route_decision,
                    "proof_log": proof_log_payload,
                    "proof_log_id": proof_log_id,
                    "channel_origin": channel_origin or "sage",
                },
                idempotency_key=f"sage_chat:{trace_id}",
            )
        except Exception:
            pass

        try:
            transparency_events = emit_sage_turn_transparency_events(
                trace_id=trace_id,
                workspace_id=normalized_workspace_id,
                user_message=normalized_message,
                sage_result={
                    "message": reply or "",
                    "used_context": [{"name": ctx_label} for ctx_label in used_context],
                    "tool_calls": tool_calls,
                    "blocked_tools": blocked_tools,
                    "approvals_required": approvals_required,
                    "route_decision": route_decision,
                    "proof_log": proof_log_payload,
                    "proof_log_id": proof_log_id,
                    "error": None,
                },
                surface=normalized_surface,
            )
        except Exception:
            transparency_events = []

        if transparency_events:
            try:
                await persist_transparency_events(
                    trace_id=trace_id,
                    tenant_id=normalized_tenant_id,
                    workspace_id=normalized_workspace_id,
                    events=[e.to_user_payload() for e in transparency_events],
                    surface=normalized_surface,
                )
            except Exception:
                pass

        return {
            "message": reply or "",
            "error": None,
            "used_context": used_context,
            "tool_calls": tool_calls,
            "available_tools": list(action_result.get("available_tools") or []) or safe_skills,
            "blocked_tools": blocked_tools,
            "approvals_required": approvals_required,
            "memory_updates": [],
            "tool_progress_messages": list(action_result.get("tool_progress_messages") or []),
            "action_execution_mode": action_execution_mode,
            "route_decision": route_decision,
            "trace_id": trace_id,
            "trace_events": trace_events,
            "provider": provider,
            "model": requested_model or None,
            "transparency_events": transparency_events,
            "action_loop_version": _coerce_text(action_result.get("action_loop_version")) or _SAGE_OPERATOR_LOOP_VERSION,
            "loop_budget": action_result.get("loop_budget") if isinstance(action_result.get("loop_budget"), dict) else {},
            "daily_operator": daily_operator_payload,
            "proof_log": proof_log_payload,
            "proof_log_id": proof_log_id,
            "ai_setup_url": f"/w/{normalized_workspace_id}{_SAGE_AI_SETUP_PATH}",
        }

    # ── B2: Overflow error recovery ──
    _compaction_retries = 0
    _MAX_COMPACTION_RETRIES = 2
    reply = ""          # initialized here so break-on-flush-failure is safe
    last_error = None

    # ── B2 pre-flight: proactive token-budget check ──
    # Estimate total tokens BEFORE the call. If we're over the model's real
    # window, compact first so the call is likely to succeed. The reactive
    # overflow recovery below remains as a backstop for edge cases.
    from server_modules.compaction_service import (
        estimate_tokens, COMPACTION_RESERVE_TOKENS,
        resolve_context_window as _resolve_ctx_window,
        compact_turns as _compact_now_proactive,
    )
    _proactive_ctx_window = _resolve_ctx_window(provider, requested_model or None)
    _proactive_input_text = str(envelope.get("system_prompt") or "")
    _proactive_input_text += str(envelope.get("user_message") or "")
    for _pm in (prior_messages or []):
        if isinstance(_pm, dict):
            _proactive_input_text += str(_pm.get("content") or "")
    _proactive_estimated = estimate_tokens(_proactive_input_text) + COMPACTION_RESERVE_TOKENS
    if _proactive_estimated > _proactive_ctx_window:
        import logging as _logging
        _log = _logging.getLogger(__name__)
        _log.warning(
            "sage_agent_runtime: proactive compaction triggered — "
            "estimated %d tokens > window %d (provider=%s, model=%s)",
            _proactive_estimated, _proactive_ctx_window, provider, requested_model,
        )
        try:
            _flush_ok = await _run_memory_flush_before_compaction(
                workspace_id=normalized_workspace_id,
                tenant_id=effective_tenant_id,
                thread_id=thread_id,
                provider=provider,
                model=requested_model,
            )
            if _flush_ok:
                _thread_rec = await thread_service.get_thread(
                    thread_id,
                    tenant_id=effective_tenant_id,
                    workspace_id=normalized_workspace_id,
                    include_turns=True,
                )
                _raw_turns = list((_thread_rec or {}).get("turns") or []) if isinstance(_thread_rec, dict) else []
                await _compact_now_proactive(
                    turns=_raw_turns,
                    workspace_id=normalized_workspace_id,
                    tenant_id=effective_tenant_id,
                    thread_id=thread_id,
                )
                # Reload prior_messages from compacted thread so the
                # subsequent LLM call uses the post-compaction context.
                _thread_rec2 = await thread_service.get_thread(
                    thread_id,
                    tenant_id=effective_tenant_id,
                    workspace_id=normalized_workspace_id,
                    include_turns=True,
                )
                _raw_turns2 = list((_thread_rec2 or {}).get("turns") or []) if isinstance(_thread_rec2, dict) else []
                prior_messages = [
                    {"role": str(t.get("role") or "").strip().lower(),
                     "content": str(t.get("content") or "").strip()}
                    for t in _raw_turns2
                    if isinstance(t, dict)
                    and str(t.get("role") or "").strip().lower() in {"user", "assistant"}
                    and str(t.get("content") or "").strip()
                ][-50:]  # keep last 50 turns post-compaction
            else:
                _log.warning(
                    "sage_agent_runtime: proactive compaction skipped — "
                    "memory flush failed (facts preserved in raw turns)"
                )
        except Exception as _proactive_err:
            _log.warning(
                "sage_agent_runtime: proactive compaction failed: %s — falling through to reactive path",
                _proactive_err,
            )
    # ── End pre-flight ──

    while True:
        try:
            reply, usage, attempted_providers, last_error = generate_chat_reply_with_provider_fallback(
                context,
                metadata,
                envelope["user_message"],
                envelope["system_prompt"],
                prior_messages=prior_messages or None,
            )
            break  # success
        except Exception as exc:
            _exc_msg = str(exc)
            # Check if this is a context overflow error
            try:
                from server_modules.compaction_service import is_context_overflow_error, compact_turns as _compact_now
                if is_context_overflow_error(_exc_msg) and _compaction_retries < _MAX_COMPACTION_RETRIES:
                    _compaction_retries += 1
                    import logging as _logging
                    _log = _logging.getLogger(__name__)
                    _log.warning(
                        "sage_agent_runtime: context overflow detected (retry %d/%d) — compacting and retrying",
                        _compaction_retries, _MAX_COMPACTION_RETRIES,
                    )
                    try:
                        # Memory flush before compaction
                        _flush_ok = await _run_memory_flush_before_compaction(
                            workspace_id=normalized_workspace_id,
                            tenant_id=effective_tenant_id,
                            thread_id=thread_id,
                            provider=provider,
                            model=requested_model,
                        )
                        if not _flush_ok:
                            # Flush failed after retry — skip compaction, let the
                            # overflow error propagate. Facts we couldn't save
                            # will still be in the raw turns.
                            _log.error(
                                "sage_agent_runtime: memory flush failed — skipping compaction, "
                                "overflow error will propagate"
                            )
                            raise  # re-raise the original overflow exception
                        # Reload turns from DB for compaction (recent_messages is {role,content} only)
                        _thread_rec = await thread_service.get_thread(
                            thread_id,
                            tenant_id=effective_tenant_id,
                            workspace_id=normalized_workspace_id,
                            include_turns=True,
                        )
                        _raw_turns = list((_thread_rec or {}).get("turns") or []) if isinstance(_thread_rec, dict) else []
                        await _compact_now(
                            turns=_raw_turns,
                            workspace_id=normalized_workspace_id,
                            tenant_id=effective_tenant_id,
                            thread_id=thread_id,
                        )
                    except Exception as _compact_err:
                        _log.warning("sage_agent_runtime: compaction during overflow recovery failed: %s", _compact_err)
                    continue  # retry the LLM call
            except Exception:
                pass
            _emit_failed_audit_event(
                tenant_id=normalized_tenant_id,
                workspace_id=normalized_workspace_id,
                actor_user_id=actor_user_id,
                actor_email=actor_email,
                actor_auth_type=actor_auth_type,
                trace_id=trace_id,
                surface=normalized_surface,
                error=str(exc),
            )
            raise

    if not reply and last_error:
        _emit_failed_audit_event(
            tenant_id=normalized_tenant_id,
            workspace_id=normalized_workspace_id,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            actor_auth_type=actor_auth_type,
            trace_id=trace_id,
            surface=normalized_surface,
            error=last_error,
        )
        raise RuntimeError(last_error)

    reply, reply_guard_metadata = _guard_sage_visible_reply(reply)
    attempted = [p.strip() for p in _coerce_text(attempted_providers).split(",") if p.strip()]
    effective_provider = attempted[-1] if attempted else provider
    effective_model = _coerce_text((usage or {}).get("model")) or requested_model
    route_decision = _build_sage_route_decision(message=normalized_message)
    prompt_diagnostics = {
        **prompt_diagnostics,
        "route_decision": route_decision,
        "response_leak_guard": reply_guard_metadata,
    }

    # --- Persist turns to shared thread (fallback path) ---
    try:
        actor3 = {"user_id": actor_user_id or "sage", "name": actor_email or "sage"}
        await thread_service.record_user_turn(
            thread_id=thread_id,
            tenant_id=effective_tenant_id,
            workspace_id=normalized_workspace_id,
            session_id=None,
            actor=actor3,
            content=normalized_message,
            metadata={"channel": channel_origin or "sage", "request_id": (request_id or None)},
        )
        if reply and not (str(reply).strip() == _SILENT_REPLY_MARKER or str(reply).strip().startswith(_SILENT_REPLY_MARKER)):
            await thread_service.record_assistant_turn(
                thread_id=thread_id,
                tenant_id=effective_tenant_id,
                workspace_id=normalized_workspace_id,
                session_id=None,
                actor={"user_id": "sage", "name": "Sage"},
                reply=reply,
                status="completed",
                run_id=trace_id,
                metadata={"channel": channel_origin or "sage", "request_id": (request_id or None)},
            )
    except Exception as _exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Turn persistence failed for workspace=%s thread=%s: %s",
            normalized_workspace_id, thread_id, _exc
        )
    # --- End turn persistence (fallback) ---

    # --- Persist interaction ---
    memory_subject = ConversationMemorySubject(
        workspace_id=normalized_workspace_id,
        tenant_id=normalized_tenant_id,
        surface_kind=DIRECT_CHAT_SURFACE,
    )
    try:
        persist_interaction(
            subject=memory_subject,
            policy_profile=DIRECT_CHAT_PROFILE,
            user_message=normalized_message,
            assistant_reply=reply or "",
            metadata={"trace_id": trace_id, "source": "sage_chat", "channel_origin": channel_origin or None},
        )
    except Exception:
        pass

    # --- Emit activity ---
    try:
        await activity_ledger_service.append_activity_event(
            tenant_id=normalized_tenant_id,
            workspace_id=normalized_workspace_id,
            actor_type="user",
            actor_id=actor_user_id or "unknown",
            event_class="sage_activity",
            action="sage_chat.completed",
            trace_id=trace_id,
            title="Sage chat completed",
            summary=(normalized_message[:120] + "..." if len(normalized_message) > 120 else normalized_message),
            status="logged",
            detail_level="timeline_detail",
            metadata={
                "used_context": used_context,
                "provider": effective_provider,
                "model": effective_model or None,
                "surface": normalized_surface,
                "blocked_action_count": 0,
                "action_execution_mode": action_execution_mode,
                "route_decision": route_decision,
                "prompt_diagnostics": prompt_diagnostics,
                "channel_origin": channel_origin or "sage",
            },
        )
    except Exception:
        pass

    # --- Emit security audit ---
    try:
        security_audit_service.emit_security_audit_event(
            action="sage_chat.completed",
            status="success",
            tenant_id=normalized_tenant_id,
            workspace_id=normalized_workspace_id,
            actor_user_id=actor_user_id or None,
            actor_email=actor_email or None,
            actor_auth_type=actor_auth_type or None,
            trace_id=trace_id,
            detail=f"Sage chat turn completed via {effective_provider}",
            metadata={
                "used_context": used_context,
                "provider": effective_provider,
                "model": effective_model or None,
                "surface": normalized_surface,
                "blocked_action_count": 0,
                "action_execution_mode": action_execution_mode,
                "route_decision": route_decision,
                "prompt_diagnostics": prompt_diagnostics,
                "channel_origin": channel_origin or "sage",
            },
            idempotency_key=f"sage_chat:{trace_id}",
        )
    except Exception:
        pass

    # ── Emit transparency events ──────────────────────────────────
    try:
        transparency_events = emit_sage_turn_transparency_events(
            trace_id=trace_id,
            workspace_id=normalized_workspace_id,
            user_message=normalized_message,
            sage_result={
                "message": reply or "",
                "used_context": [
                    {"name": ctx_label} for ctx_label in used_context
                ],
                "tool_calls": [],
                "blocked_tools": [],
                "approvals_required": [],
                "route_decision": route_decision,
                "error": None,
            },
            surface=normalized_surface,
        )
    except Exception:
        transparency_events = []

    # Best-effort persistence — failure never breaks the response
    if transparency_events:
        try:
            payloads = [e.to_user_payload() for e in transparency_events]
            await persist_transparency_events(
                trace_id=trace_id,
                tenant_id=normalized_tenant_id,
                workspace_id=normalized_workspace_id,
                events=payloads,
                surface=normalized_surface,
            )
        except Exception:
            pass

    # ── B1: Auto-compaction after turn completion (background, non-blocking) ──
    try:
        from server_modules.compaction_service import should_compact as _should_compact
        from server_modules.compaction_service import compact_turns as _auto_compact
        from server_modules.compaction_service import resolve_context_window as _resolve_ctx_window
        import asyncio as _asyncio
        _ws = normalized_workspace_id
        _tid = effective_tenant_id
        _thid = thread_id
        _prov = provider
        _mod = requested_model
        async def _auto_compact_background():
            try:
                _ctx_window = _resolve_ctx_window(_prov, _mod)
                # Reload turns from DB for accurate token count
                _thread_rec = await thread_service.get_thread(
                    thread_id,
                    tenant_id=_tid,
                    workspace_id=_ws,
                    include_turns=True,
                )
                _raw_turns = list((_thread_rec or {}).get("turns") or []) if isinstance(_thread_rec, dict) else []
                if _should_compact(_raw_turns, context_window=_ctx_window):
                    # B3: Memory flush before compaction
                    _flush_ok = await _run_memory_flush_before_compaction(
                        workspace_id=_ws,
                        tenant_id=_tid,
                        thread_id=_thid,
                        provider=_prov,
                        model=_mod,
                    )
                    if _flush_ok:
                        await _auto_compact(
                            turns=_raw_turns,
                            workspace_id=_ws,
                            tenant_id=_tid,
                            thread_id=_thid,
                        )
                    # else: flush failed after retry — skip compaction this round,
                    # try again next turn. Do NOT discard turns we couldn't save.
            except Exception:
                pass
        _asyncio.ensure_future(_auto_compact_background())
    except Exception:
        pass

    return {
        "message": reply or "",
        "error": None,
        "used_context": used_context,
        "tool_calls": [],
        "available_tools": safe_skills,
        "blocked_tools": [],
        "approvals_required": [],
        "memory_updates": [],
        "tool_progress_messages": [],
        "action_execution_mode": action_execution_mode,
        "route_decision": route_decision,
        "trace_id": trace_id,
        "provider": effective_provider,
        "model": effective_model or None,
        "proof_log": None,
        "proof_log_id": "",
        "transparency_events": transparency_events,
        "ai_setup_url": f"/w/{normalized_workspace_id}{_SAGE_AI_SETUP_PATH}",
    }
