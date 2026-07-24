"""Phase L: Fleet control tools for operator agents.

Five platform-management tools available ONLY to agents with role="operator".
Every invocation is ledgered with event_class="fleet_control".

The fleet tools operate on agent install metadata. Sage seeds as "operator";
all other agents default to "specialist". A specialist invoking a fleet tool
is denied with a policy_denial ledger event.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import activity_ledger_service


# ── Role constants ──────────────────────────────────────────────────────────

OPERATOR_ROLE = "operator"
SPECIALIST_ROLE = "specialist"
VALID_ROLES = {OPERATOR_ROLE, SPECIALIST_ROLE}
FLEET_TOOL_PREFIX = "fleet_"

# Allowed patch keys for fleet_configure_agent
_ALLOWED_CONFIGURE_KEYS = {
    "enabled_tools", "connectors", "channel_bindings",
    "subagents_enabled", "hardware_access", "model_config", "display_name",
    "purpose_preset", "instructions", "context_policy", "tool_toggles",
    "preferred_gateway_id", "telegram_first_contact_reply", "mandate",
    "capability_config", "audience",
}
_MAX_MANDATE_AUDIENCE_TOOLS = 200
_MAX_INSTRUCTIONS_CHARS = 8000
_VALID_CONTEXT_FULL_ACTIONS = {"compact", "fresh_session"}
_VALID_MODEL_MODES = {"platform_credits", "byok_api", "cli_subscription", "local"}
# BYO-brain Phase 0: model_config may carry which paired Gateway box runs the
# brain (gateway_binding) and which CLI/engine to spawn there (runtime).
# Storage passes the whole model_config dict through unchanged (see
# fleet_configure_agent below), so these persist WITHOUT a schema/storage
# change — we only validate their VALUES here so a typo can't be stored.
_VALID_MODEL_RUNTIMES = {"claude_code", "codex", "grok_build", "cursor_cli", "ollama"}
# Reasoning-effort picker (Fleet Model tab, model_config.reasoning_effort;
# also what /thinking now persists — see command_registry.py's
# _handle_thinking). Kept in sync with sage_agent_runtime_service.py's own
# _VALID_REASONING_EFFORTS (same duplicate-but-documented-across-layers
# pattern as _VALID_MODEL_RUNTIMES above, not a shared import — that module
# is far heavier and this one must stay importable from lightweight
# contexts). Only meaningful for platform_credits/byok_api — both reach
# stream_provider_backed_direct_chat, which applies this natively for
# models it recognizes as reasoning models, degraded to a system-prompt
# instruction otherwise.
_VALID_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
# cli_subscription's OWN reasoning-effort vocabulary — DIFFERENT from
# _VALID_REASONING_EFFORTS above and DIFFERENT per runtime, verified live
# against each CLI's own --help. claude_code's `--effort` has no "off"/
# "minimal"; codex's `-c model_reasoning_effort=` is its own ReasoningEffort
# enum (off/minimal/low/medium/high/xhigh/max — see empyralis-gateway/src/
# llm/codex-app-server.ts's identical comment). Kept in sync with
# sage_agent_runtime_service.py's _VALID_CLI_REASONING_EFFORTS_BY_RUNTIME.
_VALID_CLI_REASONING_EFFORTS_BY_RUNTIME = {
    "claude_code": {"low", "medium", "high", "xhigh", "max"},
    "codex": {"off", "minimal", "low", "medium", "high", "xhigh", "max"},
    # xAI Grok Build's own canonical vocabulary (docs.x.ai/build's headless-
    # mode guide, fetched 2026-07-24). Cursor CLI has no reasoning-effort
    # flag documented at all, so it gets an empty set (no value is ever
    # valid), same treatment "local" (Ollama) already gets.
    "grok_build": {"none", "minimal", "low", "medium", "high", "xhigh", "max"},
    "cursor_cli": set(),
}
_VALID_PURPOSE_PRESETS = {"customer_facing", "internal_assistant", "operator"}
_PURPOSE_PRESET_INSTRUCTIONS = {
    "customer_facing": (
        "You represent the business directly to its customers. Be professional, "
        "accurate, and helpful in every reply — customers will judge the business "
        "by how you speak to them."
    ),
    "internal_assistant": (
        "You help the team internally. Be concise and direct — you are talking "
        "to people who already know the business context."
    ),
    "operator": (
        "You help manage and coordinate other agents in this workspace."
    ),
}

# credential/connector/memory "facing" flag — separate from purpose_preset
# above (which only seeds default instructions; nothing in authority
# enforcement reads it, see FleetCreateAgentWizard.tsx's own comment).
# `audience` is the stable signal a later credential/connector/memory
# resolver gates on: "owner" (trusted with the owner's connectors/
# credentials/memory — Personal Assistant) or "external" (talks to
# strangers, must not get them — Customer Support). That gating is a
# separate, not-yet-built task; this only defines and persists the flag.
_VALID_AUDIENCES = {"owner", "external"}
_AUDIENCE_BY_PURPOSE_PRESET = {
    "customer_facing": "external",
    "internal_assistant": "owner",
    "operator": "owner",
}


# ── Metadata helpers ────────────────────────────────────────────────────────


def _meta(install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract install_metadata dict from an agent install record."""
    if not install:
        return {}
    return dict(install.get("install_metadata") or install.get("metadata") or {})


def resolve_agent_role(install: Optional[Dict[str, Any]]) -> str:
    """Resolve the role from an agent install record.

    Reads install_metadata.role. Defaults to "specialist".
    Sage is seeded as "operator" at create time.
    """
    role = str(_meta(install).get("role") or "").strip().lower()
    return role if role in VALID_ROLES else SPECIALIST_ROLE


def resolve_purpose_preset(install: Optional[Dict[str, Any]]) -> str:
    """Resolve the purpose preset set at creation time (create-agent wizard step 2).

    Falls back to "operator" for the operator role and "internal_assistant"
    for any specialist created before this field existed.
    """
    preset = str(_meta(install).get("purpose_preset") or "").strip().lower()
    if preset in _VALID_PURPOSE_PRESETS:
        return preset
    return OPERATOR_ROLE if resolve_agent_role(install) == OPERATOR_ROLE else "internal_assistant"


def resolve_agent_audience(install: Optional[Dict[str, Any]]) -> str:
    """Resolve the owner-facing vs external-facing flag for an agent install.

    Reads install_metadata.audience directly when present and valid ("owner"
    | "external"). Falls back to deriving it from purpose_preset (see
    _AUDIENCE_BY_PURPOSE_PRESET) for installs created before this field
    existed, and defaults to "owner" — today's implicit behavior, every
    agent has full run of the owner's credentials/connectors/memory — when
    neither is set. This is only the signal; the actual credential/
    connector/memory gate that reads it is a separate, later task.
    """
    audience = str(_meta(install).get("audience") or "").strip().lower()
    if audience in _VALID_AUDIENCES:
        return audience
    return _AUDIENCE_BY_PURPOSE_PRESET.get(resolve_purpose_preset(install), "owner")


def resolve_subagents_enabled(install: Optional[Dict[str, Any]]) -> bool:
    """Check if sub-agent delegation is enabled for this agent."""
    m = _meta(install)
    if "subagents_enabled" in m:
        return bool(m["subagents_enabled"])
    # Default: operator can delegate, specialist cannot
    return resolve_agent_role(install) == OPERATOR_ROLE


def resolve_hardware_access(install: Optional[Dict[str, Any]]) -> str:
    """Resolve the effective hardware_access bucket for an agent install.

    Reads the workspace_agent_installs.hardware_access COLUMN directly (not
    metadata — see fleet_configure_agent), defaulting to "none". Normalizes
    the legacy "all" value ("Full hardware access", retired 2026-07-15 —
    once an agent has hardware, gateway or vps, it has full run of that box
    by default, so a separate "full access" tier was never real) to
    "gateway", so every consumer of fleet_list_agents' output — the Hardware
    tab's picker, the Overview/card placement badge, an operator agent
    reasoning over this list — sees only the current three-way vocabulary,
    whether or not a given row has been re-saved since the option was
    removed from the UI. fleet_configure_agent performs the same coercion on
    write, so a touched row self-heals to a real "gateway"/"vps"/"none"
    permanently; this is what makes an untouched legacy row still render
    correctly forever, not just until its next edit.
    """
    value = str((install or {}).get("hardware_access") or "none").strip().lower()
    return "gateway" if value == "all" else (value or "none")


def resolve_model_config(install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve the per-agent model_config.

    Returns {mode, provider, model, credential_ref?}. When mode is
    platform_credits and no provider/model override is set, also fills
    resolved_provider_label/resolved_model — the actual provider+model the
    platform runs today — so the fleet UI's Model tab can show what's
    really in effect instead of the opaque literal "Platform default".
    """
    mc = dict(_meta(install).get("model_config") or {})
    if not mc.get("mode"):
        mc["mode"] = "platform_credits"
    if mc["mode"] == "platform_credits" and not mc.get("provider") and not mc.get("model"):
        try:
            from server_modules.empyralis_model_tier_contract import MODEL_TIER_CONTRACTS
            from server_modules.provider_profiles import PROVIDER_CATALOG

            default_tier = MODEL_TIER_CONTRACTS.get("light")
            if default_tier and default_tier.internal_provider and default_tier.internal_model:
                mc["resolved_provider_label"] = str(
                    PROVIDER_CATALOG.get(default_tier.internal_provider, {}).get("label")
                    or default_tier.internal_provider
                )
                mc["resolved_model"] = default_tier.internal_model
        except Exception:
            pass
    return mc


def seed_operator_metadata() -> Dict[str, Any]:
    """Return the install_metadata for a new operator (Sage)."""
    return {
        "role": OPERATOR_ROLE,
        "subagents_enabled": True,
        "model_config": {"mode": "platform_credits"},
    }


def seed_specialist_metadata() -> Dict[str, Any]:
    """Return the install_metadata for a new specialist.

    model is explicit here (not left for the runtime's own deepseek-chat
    fallback in resolve_requested_model()) specifically so that changing the
    default only affects NEW agents — an agent created before this default
    changed keeps an empty model_config and keeps falling through to
    whatever the runtime fallback was at the time, untouched. Deny-a-
    successful-tool rate empirically measured this session: deepseek-chat
    5/5, deepseek-reasoner 1/5 (guard stays on regardless either way).
    """
    return {
        "role": SPECIALIST_ROLE,
        "subagents_enabled": False,
        "model_config": {"mode": "platform_credits", "model": "deepseek-reasoner"},
    }


# ── Ledger helper ───────────────────────────────────────────────────────────

# Human-readable titles for fleet_control events — never a raw "Fleet: {action}
# → {id}" string. This is what a list row's activity_preview shows a real
# person, so it reads like something happened, not like an internal log line.
_FLEET_ACTION_TITLES: Dict[str, str] = {
    "create_agent": "Created",
    "create_agent_failed": "Setup failed",
    "configure_agent": "Configured",
    "configure_agent_failed": "Configuration failed",
    "message_agent": "Received a message",
    "message_agent_failed": "Message delivery failed",
    "message_agent_refused": "Message not deliverable (not implemented)",
    "hardware_grant_denied": "Hardware access denied",
    "operator_bootstrap": "Operator set up",
    "operator_bootstrap_failed": "Operator setup failed",
    "schedule_task": "Wake-up scheduled",
    "schedule_cancelled": "Wake-up cancelled",
}


def _humanize_fleet_action(action: str) -> str:
    key = str(action or "").strip().lower()
    return _FLEET_ACTION_TITLES.get(key) or key.replace("_", " ").capitalize() or "Updated"


async def _resolve_ledger_tenant_id(workspace_id: str) -> str:
    """The activity ledger's read path (list_activity_ledger_events) filters
    strictly on `tenant_id = $1` — no fallback, no wildcard. Every
    fleet_control write in this file used to hardcode the literal string
    "system" here, which is never a real workspace's resolved tenant_id, so
    every one of these rows was written into a scope no normal per-workspace
    read could ever match — invisible by construction, not by the de-noise
    filter (2026-07-10 fix, found live-verifying B3: fleet_control was
    correctly un-hidden from the Inbox query but stop/resume events still
    didn't appear, because they'd never match on tenant_id in the first
    place). Best-effort: on lookup failure, "system" is still a *reasonable*
    fallback for a row where nothing else identifies it either."""
    try:
        from server_modules.control_plane_repository import get_workspace_by_id
        ws = await get_workspace_by_id(workspace_id)
        tenant_id = str((ws or {}).get("tenant_id") or "").strip()
        return tenant_id or "system"
    except Exception:
        return "system"


async def _ledger_fleet_action(
    *,
    action: str,
    actor_id: str,
    workspace_id: str,
    target_agent_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    status: str = "executed",
) -> None:
    """Best-effort fleet control ledger event."""
    try:
        await activity_ledger_service.append_activity_event(
            tenant_id=await _resolve_ledger_tenant_id(workspace_id),
            workspace_id=workspace_id,
            actor_type="agent",
            actor_id=str(actor_id or "").strip() or "unknown",
            # Phase 5B: the target install is the subject of the action — populate
            # the ledger install_id column (was left empty for hardware_grant_denied
            # and every other fleet_control event with a target).
            install_id=str(target_agent_id or "").strip() or None,
            event_class="fleet_control",
            detail_level="audit_reference",
            action=str(action or "").strip().lower(),
            title=_humanize_fleet_action(action),
            summary=(
                f"Fleet control action '{action}' by {actor_id}"
                + (f" on agent {target_agent_id}" if target_agent_id else "")
            ),
            status=status,
            metadata=dict(metadata or {}),
        )
    except Exception:
        pass


# ── Phase U3: runtime target + hardware status helpers ────────────────────────


def _runtime_profile_dict(inst: Dict[str, Any]) -> Dict[str, Any]:
    """agent_registry_repository._row_to_install_summary() nests runtime
    placement fields (runtime_id, machine_id, default_execution_target,
    runtime_class, placement_mode, runtime_profile_label) under a
    "runtime_profile" sub-dict — it is None when the install has no
    runtime_profile_id set. Every reader in this module goes through this
    helper so there is one place that knows about the nesting."""
    profile = inst.get("runtime_profile")
    return profile if isinstance(profile, dict) else {}


def _resolve_runtime_target(inst: Dict[str, Any]) -> str:
    """Resolve human-readable runtime target for an agent install.

    Returns one of:
      - "cloud" — agent runs on Empyralis cloud infrastructure
      - "gateway:<name>" — agent runs on user-owned gateway hardware
      - "vps:<name>" — agent runs on user-provisioned VPS
      - "unknown" — no runtime target information available
    """
    profile = _runtime_profile_dict(inst)
    target = str(profile.get("default_execution_target") or "").strip().lower()
    runtime_id = str(profile.get("runtime_id") or "").strip()
    machine_id = str(profile.get("machine_id") or "").strip()
    runtime_class = str(profile.get("runtime_class") or "").strip().lower()
    placement = str(profile.get("placement_mode") or "").strip().lower()

    if target in ("cloud", "empyralis-cloud"):
        return "cloud"
    if target in ("gateway", "local_gateway", "desktop_companion"):
        label = str(profile.get("label") or machine_id or runtime_id or "").strip()
        return f"gateway:{label}" if label else "gateway"
    if target in ("vps", "self_hosted", "self_hosted_business_node"):
        label = str(profile.get("label") or runtime_id or "").strip()
        return f"vps:{label}" if label else "vps"
    if target in ("local_companion", "local"):
        return "local_companion"
    if runtime_class and target == "auto":
        if "gateway" in runtime_class:
            return "gateway"
        if "vps" in runtime_class or "self" in runtime_class:
            return "vps"
        return "cloud"

    # Fallback: use runtime_id or placement to guess
    if runtime_id:
        return f"runtime:{runtime_id[:12]}"
    if placement == "local":
        return "local_companion"
    return "unknown"


async def _resolve_cloud_agent_readiness(
    workspace_id: str,
    model_config: Dict[str, Any],
) -> tuple[bool, str]:
    """Check whether a CLOUD-placement agent's model_config resolves to a
    usable AI provider + credential RIGHT NOW — the honesty check backing
    _resolve_hardware_status's "online" for cloud agents (see there for why
    this exists: cloud placement used to be reported "online"
    unconditionally, regardless of whether the bound model_config could
    actually produce a turn).

    Mirrors sage_agent_runtime_service._resolve_agent_cloud_provider's mode
    dispatch (platform_credits / byok_api / cli_subscription / local) and
    calls the SAME credential-resolution primitives it uses — but
    deliberately does NOT call that function directly. Both this function
    and _resolve_agent_cloud_provider never make a live model call (config/
    credential presence only), so that's not the reason; the reason is its
    ledger-on-failure side effect: _resolve_agent_cloud_provider's
    byok_api/cli_subscription/local/unknown branches write a
    "provider_unavailable" ledger event on failure, which is correct for a
    real denied turn but wrong here. fleet_list_agents polls every ~30s
    (see its "Not ledgered" note above) — ledgering a "turn denied" event on
    every poll of a permanently-misconfigured agent would fabricate
    thousands of fake denied-turn events for turns that were never
    attempted, exactly the kind of dishonest signal this fix exists to
    remove, not add.

    Returns (ready, reason) — reason is "" when ready.
    """
    mc = dict(model_config or {})
    mode = str(mc.get("mode") or "platform_credits").strip().lower()
    provider = str(mc.get("provider") or "").strip().lower()

    try:
        if mode == "platform_credits":
            # §29 per-agent-provider fix: mirrors _resolve_agent_cloud_
            # provider's own platform_credits branch (this agent's OWN
            # stored provider wins; the workspace default is a fallback
            # for an agent that's never had one of its own) — NOT the
            # shared resolver, for the same no-ledger-side-effect-on-
            # every-poll reason documented above. Same primitives as the
            # byok_api branch just below.
            if provider:
                from server_modules.direct_chat_provider_service import (
                    direct_chat_credentials,
                    supports_direct_message_native_chat,
                )

                credentials = direct_chat_credentials(workspace_id, provider)
                if supports_direct_message_native_chat(provider, credentials):
                    return True, ""
                return False, f"This agent's {provider} provider is not available (missing credentials or entitlement)."

            # No provider stored — the workspace's shared default, the SAME
            # resolution a platform_credits turn actually uses at turn time.
            # check_master_model_config=False: this checks THIS agent's own
            # platform_credits mode, not Sage's — must never fail because of
            # an unrelated misconfiguration on Sage's own card (see
            # _resolve_cloud_provider's docstring).
            from server_modules.sage_agent_runtime_service import _resolve_cloud_provider

            await _resolve_cloud_provider(workspace_id, check_master_model_config=False)
            return True, ""

        if mode == "byok_api":
            if not provider:
                return False, "No provider is configured for this agent's own API key (BYOK)."
            from server_modules.direct_chat_provider_service import (
                direct_chat_credentials,
                supports_direct_message_native_chat,
            )

            credentials = direct_chat_credentials(workspace_id, provider)
            if supports_direct_message_native_chat(provider, credentials):
                return True, ""
            return False, f"This agent's {provider} API key is missing or invalid."

        if mode in ("cli_subscription", "local"):
            gateway_binding = str(mc.get("gateway_binding") or "").strip()
            if not gateway_binding:
                return False, f"{mode} mode requires a paired computer, but none is bound."
            if mode == "cli_subscription":
                from server_modules.sage_agent_runtime_service import _VALID_CLI_SUBSCRIPTION_RUNTIMES

                runtime = str(mc.get("runtime") or "claude_code").strip().lower() or "claude_code"
                if runtime not in _VALID_CLI_SUBSCRIPTION_RUNTIMES:
                    return False, f"Unsupported cli_subscription runtime: {runtime}."
            return True, ""

        return False, f"Unrecognized model_config mode: {mode or '(none)'}."
    except Exception as exc:
        # Fail CLOSED, not open: an unexpected error here must never read as
        # "ready" — that would silently reintroduce the exact always-online
        # lie this function exists to remove.
        return False, str(exc).strip() or "Could not verify this agent's AI provider configuration."


def recommended_model_config_for_gateway(
    gateway_id: str,
    *,
    workspace_id: str,
) -> Optional[Dict[str, str]]:
    """BYO-brain creation-flow hint (§29 per-agent-provider): when the box an
    agent is being placed on already has an authenticated subscription CLI
    (Claude Code, Codex, Grok Build, or Cursor CLI), recommend reusing it as
    a cli_subscription binding instead of steering the create-agent wizard's
    Brain step toward Empyralis credits by default — "you already have Codex
    on this box, use it" rather than making the owner reconfigure or re-login.

    Reuses gateway_registry_service.gateway_registration_public_payload's
    llm_runtimes — the SAME installed+authenticated signal
    fleet_configure_agent's own cli_subscription save-time check (above) and
    the frontend's GatewayBoxPicker/ModelTab already read off
    GET /api/gateway/registrations. No new detection source.

    Returns a ready-to-patch model_config dict — {mode, provider, runtime,
    gateway_binding} — when a subscription is ready to reuse, else None.
    Claude Code wins when a box happens to have several ready (checked in the
    order below), matching the create-agent wizard's own default
    subscriptionProvider. Best-effort: any lookup failure returns None — this
    is a UX hint, never a gate.
    """
    gid = str(gateway_id or "").strip()
    if not gid:
        return None
    try:
        from server_modules import gateway_state_repository, gateway_registry_service

        registration = gateway_state_repository.get_gateway_registration(gid)
        if not isinstance(registration, dict) or not registration:
            return None
        registration_workspace_id = str(registration.get("workspace_id") or "").strip()
        if registration_workspace_id and registration_workspace_id != (str(workspace_id or "").strip() or "default"):
            return None
        payload = gateway_registry_service.gateway_registration_public_payload(registration)
        llm_runtimes = payload.get("llm_runtimes") if isinstance(payload.get("llm_runtimes"), dict) else {}
    except Exception:
        return None

    for runtime, provider_id in (
        ("claude_code", "claude_code_cli"),
        ("codex", "openai-codex"),
        ("grok_build", "xai_grok_cli"),
        ("cursor_cli", "cursor_cli"),
    ):
        entry = llm_runtimes.get(runtime)
        if isinstance(entry, dict) and bool(entry.get("installed")) and bool(entry.get("authenticated")):
            return {
                "mode": "cli_subscription",
                "provider": provider_id,
                "runtime": runtime,
                "gateway_binding": gid,
            }
    return None


async def _resolve_hardware_status(
    inst: Dict[str, Any],
    heartbeats: Dict[str, dict],
    *,
    workspace_id: str = "",
) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    """Resolve hardware status, last heartbeat, current run, and (when not
    ready) a human reason for an agent install.

    Returns (status, last_heartbeat_iso, current_run_id, not_ready_reason):
      - "online" — heartbeat received within the freshness window, OR (for
        a cloud-placement agent) its model_config resolves to a usable
        provider + credential right now
      - "offline" — a worker registered here before, but not recently
      - "error" — a cloud-placement agent whose model_config can NOT
        currently produce a turn (dead/missing BYOK key, exhausted/blocked
        platform entitlement, unbound cli_subscription/local gateway, ...)
        — see not_ready_reason. Cloud agents have no heartbeat/liveness
        concept, so this is a config-honesty check, not a liveness ping.
      - "unknown" — no gateway/VPS worker has ever registered for this agent
    """
    profile = _runtime_profile_dict(inst)
    machine_id = str(profile.get("machine_id") or "").strip()

    hb = heartbeats.get(machine_id) if machine_id else None

    if hb and isinstance(hb, dict):
        status = "online" if bool(hb.get("online", False)) else "offline"
        last_hb = str(hb.get("last_heartbeat_at") or "").strip() or None
        current_run_id = str(hb.get("current_run_id") or "").strip() or None
        return status, last_hb, current_run_id, None

    # Cloud agents have no worker/heartbeat concept — the platform runs
    # their turns synchronously, no run-in-progress tracking (there's no
    # queue for a cloud text-agent turn to sit in). "online" here used to be
    # unconditional ("always online") regardless of whether the bound
    # model_config could actually produce a turn, which lied to the Status
    # row, sidebar dots, and "Online: N/M" count whenever a cloud agent's
    # credential was dead, missing, or exhausted. Fixed: report ready only
    # when the SAME provider-resolution logic the runtime uses to dispatch a
    # real turn would actually resolve a usable credential — see
    # _resolve_cloud_agent_readiness for what "usable" means and why it
    # doesn't just call _resolve_agent_cloud_provider directly.
    target = str(profile.get("default_execution_target") or "").strip().lower()
    if target in ("cloud", "empyralis-cloud"):
        ready, reason = await _resolve_cloud_agent_readiness(
            workspace_id, resolve_model_config(inst),
        )
        if ready:
            return "online", None, None, None
        return "error", None, None, (reason or "This agent's AI provider is not configured.")

    # No machine_id at all — never paired with a gateway/VPS worker.
    if not machine_id:
        return "unknown", None, None, None

    return "offline", None, None, None


async def _fetch_latest_heartbeats(workspace_id: str) -> Dict[str, dict]:
    """Fetch latest fleet_worker_registrations row per machine in the
    workspace — the real heartbeat/current-run source for gateway and
    self-hosted (VPS) agents. Keyed by machine_id.

    "online" is derived from heartbeat freshness (matches the lease-window
    convention used elsewhere in the fleet UI) rather than a stored flag,
    since fleet_worker_registrations has no boolean "online" column.
    """
    try:
        from server_modules import control_plane_repository as cpr

        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            return {}

        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (machine_id)
                machine_id,
                current_run_id,
                last_heartbeat_at,
                (last_heartbeat_at IS NOT NULL AND last_heartbeat_at > NOW() - INTERVAL '120 seconds') AS online
            FROM fleet_worker_registrations
            WHERE workspace_id = $1
              AND machine_id IS NOT NULL
            ORDER BY machine_id, last_heartbeat_at DESC NULLS LAST
            """,
            str(workspace_id or "").strip(),
        )
        result: Dict[str, dict] = {}
        for r in (rows or []):
            mid = str(r["machine_id"] or "").strip()
            if mid:
                result[mid] = {
                    "online": bool(r["online"]),
                    "last_heartbeat_at": str(r["last_heartbeat_at"] or "").strip() or None,
                    "current_run_id": str(r["current_run_id"] or "").strip() or None,
                }
        return result
    except Exception:
        return {}


async def _fetch_latest_activity(workspace_id: str) -> Dict[str, Dict[str, Optional[str]]]:
    """Latest activity_ledger_events row per agent (install_id) — one bulk
    DISTINCT ON query for the whole workspace, not N+1. Backs the
    agents/project list's "last active" column and the row's activity-preview
    line ("what it just did"). Keyed by install_id -> {last_active_at, title}.

    Was grouping on actor_id — the human sender, not the agent — the same
    stale-attribution bug fleet_get_agent_activity() had before the 2026-07-09
    fix. Since actor_id never matches an agent install id, fleet_list_agents()'s
    lookup by install id always missed, showing "never" and no activity
    preview on the list even when the agent's own detail page had real data.

    Prefers the latest non-fleet_control event over the latest fleet_control
    one (2026-07-09 U3-A fix): fleet_control is plumbing (create_agent,
    configure_agent, ...) — a row's activity preview should show what the
    agent DID, not the last time its config was touched. Only falls back to
    a fleet_control event when that's literally the only activity an agent
    has; _humanize_fleet_action() keeps that fallback readable rather than a
    raw "action → id" string.
    """
    try:
        from server_modules import control_plane_repository as cpr

        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            return {}
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (install_id) install_id, created_at, title, action
            FROM activity_ledger_events
            WHERE workspace_id = $1 AND install_id IS NOT NULL
            ORDER BY install_id, (event_class = 'fleet_control') ASC, created_at DESC
            """,
            str(workspace_id or "").strip(),
        )
        out: Dict[str, Dict[str, Optional[str]]] = {}
        for r in rows or []:
            install_id = str(r["install_id"] or "").strip()
            ts = r["created_at"]
            if not install_id or ts is None:
                continue
            title = str(r["title"] or "").strip() or str(r["action"] or "").strip().replace("_", " ")
            out[install_id] = {
                "last_active_at": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "title": title or None,
            }
        return out
    except Exception:
        return {}


async def _fetch_agent_channels(*, tenant_id: str, workspace_id: str) -> Dict[str, str]:
    """Primary enabled channel key per agent — one bulk query for the whole
    workspace via the RLS-scoped bindings repository. Backs the agents/project
    list's "channel" column; an agent with more than one enabled channel shows
    its first plus a "+N" suffix."""
    try:
        from server_modules import agent_bindings_repository as bindings

        rows = await bindings.list_workspace_channel_bindings(
            tenant_id=tenant_id, workspace_id=workspace_id, enabled_only=True,
        )
        by_agent: Dict[str, list] = {}
        for r in rows or []:
            agent_id = str(r.get("agent_install_id") or "").strip()
            key = str(r.get("key") or "").strip()
            if agent_id and key:
                by_agent.setdefault(agent_id, []).append(key)
        out: Dict[str, str] = {}
        for agent_id, keys in by_agent.items():
            extra = len(keys) - 1
            out[agent_id] = keys[0] + (f" +{extra}" if extra > 0 else "")
        return out
    except Exception:
        return {}


# ── Fleet tool implementations ──────────────────────────────────────────────


async def fleet_list_agents(
    *,
    actor_id: str,
    workspace_id: str,
    tenant_id: str = "system",
) -> Dict[str, Any]:
    """List all agents in the workspace with their roles and status."""
    from server_modules import agent_registry_repository as repo

    try:
        installs = await repo.list_workspace_agent_installs(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            include_master=True,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "agents": []}

    # ── Resolve runtime heartbeats for hardware status ──
    _heartbeats: dict[str, dict] = {}
    try:
        _heartbeats = await _fetch_latest_heartbeats(workspace_id)
    except Exception:
        pass

    # ── Phase UI-2: last-active + channel, for the agents/project list density ──
    _last_active = await _fetch_latest_activity(workspace_id)
    _channels = await _fetch_agent_channels(tenant_id=tenant_id, workspace_id=workspace_id)

    agents = []
    for inst in (installs or []):
        inst_dict = dict(inst) if isinstance(inst, dict) else {}
        role = resolve_agent_role(inst_dict)

        # ── Phase U3: runtime target + hardware status ──
        _runtime_target = _resolve_runtime_target(inst_dict)
        _hardware_status, _last_heartbeat, _current_run_id, _hardware_status_reason = await _resolve_hardware_status(
            inst_dict, _heartbeats, workspace_id=workspace_id,
        )

        # ── Phase 7B: capability preset + hardware access + context policy ──
        _meta_i = inst_dict.get("metadata") if isinstance(inst_dict.get("metadata"), dict) else {}
        _pco_i = inst_dict.get("policy_context_overrides") if isinstance(inst_dict.get("policy_context_overrides"), dict) else {}
        _ctx_pol = _meta_i.get("context_policy") if isinstance(_meta_i.get("context_policy"), dict) else {}

        agents.append({
            "agent_id": str(inst_dict.get("id") or "").strip(),
            "label": str(inst_dict.get("label") or "").strip(),
            "role": role,
            "purpose_preset": resolve_purpose_preset(inst_dict),
            "audience": resolve_agent_audience(inst_dict),
            "project_id": str(inst_dict.get("project_id") or "").strip(),
            "status": str(inst_dict.get("status") or "active").strip(),
            "enabled": bool(inst_dict.get("enabled", True)),
            "subagents_enabled": resolve_subagents_enabled(inst_dict),
            "model_config": resolve_model_config(inst_dict),
            "capability_preset": str(_meta_i.get("capability_preset") or "").strip(),
            "hardware_access": resolve_hardware_access(inst_dict),
            "hardware_access_locked": bool(_meta_i.get("hardware_access_locked") or _pco_i.get("hardware_access_locked")),
            "context_policy": dict(_ctx_pol),
            "instructions": str(_meta_i.get("instructions") or "").strip(),
            "preferred_gateway_id": str(_meta_i.get("preferred_gateway_id") or "").strip(),
            "telegram_first_contact_reply": bool(_meta_i.get("telegram_first_contact_reply")),
            "stopped": dict(_meta_i.get("stopped") or {}) if bool((_meta_i.get("stopped") or {}).get("active")) else {"active": False},
            # Phase U3: placement visibility
            "runtime_target": _runtime_target,
            "hardware_status": _hardware_status,
            # Populated only when hardware_status is not a ready state (e.g.
            # a cloud agent whose model_config can't currently produce a
            # turn) — the honest "why" behind the Status row/sidebar dot.
            "hardware_status_reason": _hardware_status_reason,
            "last_heartbeat": _last_heartbeat,
            "current_run_id": _current_run_id,
            "last_activity": (_last_active.get(str(inst_dict.get("id") or "").strip()) or {}).get("last_active_at"),
            "activity_preview": (_last_active.get(str(inst_dict.get("id") or "").strip()) or {}).get("title") or "",
            "channel": _channels.get(str(inst_dict.get("id") or "").strip(), ""),
        })

    # Not ledgered: this is a pure read, polled every ~30s by the agents/
    # project list UI. The ledger records actions, not observations — logging
    # every list_agents call buried real events ("Meridian chat completed")
    # under a wall of "Fleet: list_agents" noise within seconds.
    return {"ok": True, "agents": agents}


async def fleet_get_agent_activity(
    *,
    actor_id: str,
    workspace_id: str,
    agent_id: str = "",
    since: Optional[str] = None,
) -> Dict[str, Any]:
    """Read recent ledger activity for a specific agent.

    Queries the activity_ledger_events table for the given install_id — the
    acting agent's own identity, stamped by every ledger writer (2026-07-09
    attribution fix). actor_id tracks the human sender, not the agent, and
    matching against it here always returned zero rows for any real agent
    turn — this is why Overview read "No activity yet" even after real
    conversations. Redacts payloads — only returns event metadata, never
    raw content.

    fleet_control rows are excluded (2026-07-10): owner-fleet administrative
    actions ("Fleet: configure_agent → ainstall_...") are plumbing on an
    agent's own timeline, the same disease the Inbox feed had — rows written
    before the U3-A title-humanization fix still carry the raw un-humanized
    string forever, and even humanized ones aren't "this agent's activity"
    in the sense this section means. Dropped here rather than humanized:
    unlike the Inbox (a workspace-wide feed where a completed config action
    is still worth a line), this is the agent's OWN work log — routine
    owner configuration isn't part of that story.
    """
    from server_modules import control_plane_repository as cpr

    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required", "events": []}

    try:
        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            return {"ok": False, "error": "Database unavailable", "events": []}

        rows = await pool.fetch(
            """
            SELECT id, action, event_class, title, status, channel, created_at
            FROM activity_ledger_events
            WHERE workspace_id = $1
              AND install_id = $2
              AND event_class != 'fleet_control'
              AND ($3::timestamptz IS NULL OR created_at >= $3::timestamptz)
            ORDER BY created_at DESC
            LIMIT 50
            """,
            str(workspace_id or "").strip(),
            str(agent_id or "").strip(),
            str(since or "").strip() or None,
        )
        events = [
            {
                "event_id": str(r["id"]),
                "action": str(r["action"] or ""),
                "event_class": str(r["event_class"] or ""),
                "title": str(r["title"] or ""),
                "status": str(r["status"] or ""),
                # Who this ran for (channel) is present on the ledger row when
                # the event came from a channel turn — None for owner/Sage-only
                # activity. There is no customer_label on this table (that
                # concept only exists on the separate deployed-agent
                # conversation subsystem) — deliberately not fabricated here.
                "channel": str(r["channel"] or "").strip() or None,
                "created_at": str(r["created_at"] or ""),
            }
            for r in (rows or [])
        ]
    except Exception as exc:
        return {"ok": False, "error": str(exc), "events": []}

    # Not ledgered: this is a pure read, polled every few seconds by the
    # agent Overview tab. The ledger records actions, not observations —
    # logging every get_agent_activity call buried real events ("Meridian
    # chat completed") under a wall of "Fleet: get_agent_activity" noise
    # within seconds of it happening.
    return {"ok": True, "agent_id": agent_id, "events": events}


async def fleet_get_project_activity(
    *,
    workspace_id: str,
    tenant_id: str = "system",
    project_id: str = "",
    limit: int = 20,
) -> Dict[str, Any]:
    """Read recent ledger activity across every agent in a project — backs the
    project detail right panel's Activity section (panel-only, no separate
    tab). Same activity_ledger_events source as fleet_get_agent_activity, just
    scoped to a set of install_ids instead of one."""
    from server_modules import agent_registry_repository as repo
    from server_modules import control_plane_repository as cpr

    if not str(project_id or "").strip():
        return {"ok": False, "error": "project_id is required", "events": []}

    try:
        installs = await repo.list_workspace_agent_installs(
            tenant_id=tenant_id, workspace_id=workspace_id, include_master=True,
        )
        agent_ids = [
            str(dict(i).get("id") or "").strip()
            for i in (installs or [])
            if str(dict(i).get("project_id") or "").strip() == str(project_id).strip()
        ]
        agent_ids = [a for a in agent_ids if a]
        if not agent_ids:
            return {"ok": True, "project_id": project_id, "events": []}

        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            return {"ok": False, "error": "Database unavailable", "events": []}

        rows = await pool.fetch(
            """
            SELECT id, install_id, action, event_class, title, status, created_at
            FROM activity_ledger_events
            WHERE workspace_id = $1
              AND install_id = ANY($2::text[])
            ORDER BY created_at DESC
            LIMIT $3
            """,
            str(workspace_id or "").strip(),
            agent_ids,
            max(1, min(int(limit or 20), 100)),
        )
        events = [
            {
                "event_id": str(r["id"]),
                "agent_id": str(r["install_id"] or ""),
                "action": str(r["action"] or ""),
                "event_class": str(r["event_class"] or ""),
                "title": str(r["title"] or ""),
                "status": str(r["status"] or ""),
                "created_at": str(r["created_at"] or ""),
            }
            for r in (rows or [])
        ]
        return {"ok": True, "project_id": project_id, "events": events}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "events": []}


# Truth Map B1: these tools are real (skill_registry entries, real
# permission/approval semantics) but execution_mode="manual" with no direct
# executor in the registry-building path — the only real executor is behind
# a bound connector, so the tool_toggles switch above does nothing on its
# own. In this deployment that connector is Google Workspace for all four
# (email/calendar/task/CRM scopes route through the same OAuth connection).
# A generic connector_scopes -> connector_id resolver would be wrong here:
# scopes like "email"/"calendar" could equally be satisfied by Microsoft 365,
# so mapping by scope name isn't guaranteed correct — this maps the four
# specific tools this deployment actually routes through Google Workspace.
_CONNECTOR_REQUIRED_TOOLS: Dict[str, str] = {
    "email-access": "google_workspace",
    "calendar-access": "google_workspace",
    "task-runner": "google_workspace",
    "crm-notes": "google_workspace",
}


async def fleet_get_agent_tools(
    *,
    workspace_id: str,
    tenant_id: str = "default",
    agent_id: str,
) -> Dict[str, Any]:
    """Return the FULL toggleable tool catalog for a specific agent, with each
    tool's real enabled state.

    "Real" here means read from the install's `tool_toggles` column — the
    field _resolve_specialist_toolset actually enforces at runtime (see
    sage_agent_runtime_service.py). metadata.enabled_tools is a separate,
    display-only list nothing in the tool-dispatch path consults; toggling
    it would be a fake control, so this function ignores it as a source of
    truth (fleet_create_agent still seeds it, kept only for back-compat
    display in older callers).

    Core (always-on) tools split two ways. Most of them — Web Search,
    Memory read/write/update — have a real skill_registry-backed toggle and
    already have their own entry in `tools` above (enforced by
    _core_tool_allowed, not bypassed); listing them again below would claim
    they ignore a toggle that, since the Truth Map fix, they don't. Only the
    remainder — pure plumbing with no toggle anywhere (task_complete,
    query_tool_registry) — is returned separately under "core_tools" for
    read-only display, because there's genuinely nothing to turn off.
    """
    from server_modules import agent_registry_repository as repo
    from server_modules import authority_mandate_service
    from server_modules import skill_registry
    from server_modules import skills_service
    from server_modules.sage_agent_runtime_service import _core_direct_tool_names

    try:
        bundle = await repo.get_workspace_agent_install_bundle(
            agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
        )
    except Exception:
        bundle = None

    if not bundle:
        return {"ok": True, "tools": [], "core_tools": [], "agent_id": agent_id}

    bundle_dict = dict(bundle)
    is_master = resolve_agent_role(bundle_dict) == OPERATOR_ROLE
    toggles = bundle_dict.get("tool_toggles")
    if isinstance(toggles, str):
        import json as _json
        try:
            toggles = _json.loads(toggles)
        except Exception:
            toggles = {}
    if not isinstance(toggles, dict):
        toggles = {}
    meta = bundle_dict.get("install_metadata") or bundle_dict.get("metadata") or {}
    mandate_audience_tools = (meta.get("mandate") or {}).get("audience_tools") or []

    definitions = skill_registry.list_skill_definitions(workspace_id=workspace_id, include_disabled=True)
    tools: List[Dict[str, Any]] = []
    for d in definitions:
        # `id` is the canonical enforcement tool name (not skill_registry's
        # own hyphenated id) so this list's toggle state — and the PATCH the
        # frontend sends back using this same `id` — matches what
        # _specialist_tool_allowed() actually checks. See
        # skill_registry.enforcement_tool_name.
        enforcement_id = skill_registry.enforcement_tool_name(d.id)
        descriptor = skills_service.tool_descriptor_for_name(enforcement_id)
        # Capability-gated tools (generate_image today — see
        # agent_capability_service.py) are decided SOLELY by whether their
        # capability resolved a provider for this agent
        # (_specialist_tool_allowed bypasses tool_toggles for these
        # entirely) — listing one here with a toggle that has zero runtime
        # effect would be exactly the lying-toggle facade the comment below
        # already guards against for core tools. They live on the
        # Capabilities tab instead.
        capability_id = str(getattr(descriptor, "capability_id", "") or "").strip().lower() if descriptor is not None else ""
        if capability_id:
            from server_modules import agent_capability_service as _cap_svc

            if capability_id in _cap_svc.TOOL_GATED_CAPABILITIES:
                continue
        tools.append({
            "id": enforcement_id,
            "label": d.label,
            "description": d.description or "",
            "action_class": d.action_class,
            # Sage (operator) isn't gated by tool_toggles at all — every tool
            # is already available to it, so the toggle would be misleading.
            "enabled": True if is_master else bool(toggles.get(enforcement_id, False)),
            # Customer access (Authority Mandate, Part 10): audience_safe is
            # the platform's own manifest default (informational, can't be
            # toggled off); mandate_granted is this owner's explicit
            # audience_tools grant (see the "mandate" patch branch below —
            # same enforcement_id, checked case-insensitively).
            "audience_safe": bool(descriptor.audience_safe) if descriptor is not None else False,
            "mandate_granted": authority_mandate_service.is_audience_tool_allowed(
                mandate_audience_tools, enforcement_id
            ),
            # Truth Map B1: email-access/calendar-access/task-runner/crm-notes
            # are execution_mode="manual" with no direct executor — the real
            # executor only exists behind a bound connector, independent of
            # this toggle. Flipping it on does nothing by itself, so the
            # Tools tab needs the real requirement, not a switch that looks
            # functional. See _CONNECTOR_REQUIRED_TOOLS below.
            "requires_connector": _CONNECTOR_REQUIRED_TOOLS.get(enforcement_id),
        })

    # A core tool with its own entry above (Web Search, Memory read/write/
    # update — anything skill_registry maps onto a real enforcement id) is
    # now toggle-respecting at runtime (_core_tool_allowed), so listing it
    # again here as "always on regardless of the toggles above" would be
    # exactly the lying-toggle facade this was meant to fix. Only tools with
    # no real toggle at all (task_complete, query_tool_registry, and any
    # core tool skill_registry doesn't map) belong in this read-only bucket.
    _toggleable_ids = {t["id"] for t in tools}
    core_tools = sorted(name for name in _core_direct_tool_names() if name not in _toggleable_ids)
    return {"ok": True, "tools": tools, "core_tools": core_tools, "agent_id": agent_id, "is_master": is_master}


# ── Capabilities (image/video generation, TTS/STT) ─────────────────────────
# See agent_capability_service.py for the resolver this surfaces. This tab is
# the per-agent analog of the Model tab's provider/mode picker, one level
# down (capability instead of "the" chat model) — same
# platform_credits/byok_api spectrum, no separate enable toggle: choosing a
# provider that resolves IS the enable (see FleetAgentDetail.tsx's
# CapabilitiesTab / ToolsTab's identical isMaster convention below).

async def fleet_get_agent_capabilities(
    *, workspace_id: str, tenant_id: str = "default", agent_id: str,
) -> Dict[str, Any]:
    """Resolved capability state for every capability, for this agent. Never
    returns key material — has_byok_key is a boolean, never the ciphertext."""
    from server_modules import agent_registry_repository as repo
    from server_modules import agent_capability_service as _cap_svc

    try:
        bundle = await repo.get_workspace_agent_install_bundle(
            agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
        )
    except Exception:
        bundle = None

    if not bundle:
        return {"ok": True, "capabilities": [], "agent_id": agent_id}

    bundle_dict = dict(bundle)
    is_master = resolve_agent_role(bundle_dict) == OPERATOR_ROLE
    meta = bundle_dict.get("install_metadata") or bundle_dict.get("metadata") or {}
    capability_config = meta.get("capability_config") if isinstance(meta.get("capability_config"), dict) else {}
    capability_secrets = meta.get("capability_secrets") if isinstance(meta.get("capability_secrets"), dict) else {}
    capabilities = _cap_svc.agent_capability_state_payload(
        workspace_id=workspace_id, agent_id=agent_id,
        capability_config=capability_config, capability_secrets=capability_secrets,
    )
    return {"ok": True, "capabilities": capabilities, "agent_id": agent_id, "is_master": is_master}


async def fleet_set_agent_capability_key(
    *, workspace_id: str, tenant_id: str = "default", agent_id: str,
    capability: str, provider: str, api_key: str,
) -> Dict[str, Any]:
    """Store one BYOK key for one capability, scoped to this agent only, and
    switch that capability to byok_api/this provider (pasting a key IS
    choosing "your own API key" for it — no separate mode toggle to also
    flip). Encrypts before writing; the key is never returned or logged."""
    from server_modules import agent_registry_repository as repo
    from server_modules import agent_capability_service as _cap_svc

    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required"}
    try:
        secret_patch = _cap_svc.store_capability_secret_patch(capability=capability, provider=provider, api_key=api_key)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    cap = _cap_svc.canonical_capability(capability)

    bundle = await repo.get_workspace_agent_install_bundle(agent_id, tenant_id=tenant_id, workspace_id=workspace_id)
    if not bundle:
        return {"ok": False, "error": f"Agent {agent_id} not found in workspace"}
    bundle_dict = dict(bundle)
    meta = dict(bundle_dict.get("install_metadata") or bundle_dict.get("metadata") or {})

    next_secrets = dict(meta.get("capability_secrets") or {})
    next_secrets[cap] = secret_patch
    meta["capability_secrets"] = next_secrets

    next_config = dict(meta.get("capability_config") or {})
    next_config[cap] = {"mode": "byok_api", "provider": secret_patch["provider"]}
    meta["capability_config"] = next_config

    await repo.update_workspace_agent_install(
        agent_id, tenant_id=tenant_id, workspace_id=workspace_id, metadata=meta,
    )
    await _ledger_fleet_action(
        action="capability_key_set", actor_id="owner", workspace_id=workspace_id,
        target_agent_id=agent_id, status="ok",
        metadata={"capability": cap, "provider": secret_patch["provider"]},
    )
    return {"ok": True, "capability": cap, "provider": secret_patch["provider"], "mode": "byok_api"}


async def fleet_clear_agent_capability_key(
    *, workspace_id: str, tenant_id: str = "default", agent_id: str, capability: str,
) -> Dict[str, Any]:
    """Remove a stored BYOK key for one capability and fall back to
    platform_credits (byok_api with no key left behind would just resolve to
    a confusing "add your key" dead end)."""
    from server_modules import agent_registry_repository as repo
    from server_modules import agent_capability_service as _cap_svc

    cap = _cap_svc.canonical_capability(capability)
    if cap not in _cap_svc.ALL_CAPABILITIES:
        return {"ok": False, "error": f"Unknown capability '{capability}'."}

    bundle = await repo.get_workspace_agent_install_bundle(agent_id, tenant_id=tenant_id, workspace_id=workspace_id)
    if not bundle:
        return {"ok": False, "error": f"Agent {agent_id} not found in workspace"}
    bundle_dict = dict(bundle)
    meta = dict(bundle_dict.get("install_metadata") or bundle_dict.get("metadata") or {})

    next_secrets = dict(meta.get("capability_secrets") or {})
    next_secrets.pop(cap, None)
    meta["capability_secrets"] = next_secrets

    next_config = dict(meta.get("capability_config") or {})
    provider = str((next_config.get(cap) or {}).get("provider") or _cap_svc.DEFAULT_PROVIDER_BY_CAPABILITY.get(cap, ""))
    next_config[cap] = {"mode": "platform_credits", "provider": provider}
    meta["capability_config"] = next_config

    await repo.update_workspace_agent_install(
        agent_id, tenant_id=tenant_id, workspace_id=workspace_id, metadata=meta,
    )
    await _ledger_fleet_action(
        action="capability_key_cleared", actor_id="owner", workspace_id=workspace_id,
        target_agent_id=agent_id, status="ok", metadata={"capability": cap},
    )
    return {"ok": True, "capability": cap, "mode": "platform_credits"}


async def fleet_configure_agent(
    *,
    actor_id: str,
    workspace_id: str,
    tenant_id: str = "system",
    agent_id: str = "",
    patch: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update an agent's fleet-managed configuration.

    Allowed patch keys: enabled_tools, connectors, channel_bindings,
    subagents_enabled, hardware_access, model_config.

    Model config modes: platform_credits | byok_api | cli_subscription | local
    Model config may also carry gateway_binding (paired Gateway id that runs
    the brain) and runtime (claude_code | codex | ollama). Both persist as-is.
    """
    from server_modules import agent_registry_repository as repo

    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required"}

    patch_dict = dict(patch or {})
    clean_patch = {k: v for k, v in patch_dict.items() if k in _ALLOWED_CONFIGURE_KEYS}

    if not clean_patch:
        return {
            "ok": False,
            "error": "No valid patch keys. Allowed: " + ", ".join(sorted(_ALLOWED_CONFIGURE_KEYS)),
        }

    # Validate model_config mode + BYO-brain sub-fields if present.
    if "model_config" in clean_patch:
        mc = dict(clean_patch.get("model_config") or {})
        mode = str(mc.get("mode") or "").strip()
        if mode and mode not in _VALID_MODEL_MODES:
            return {
                "ok": False,
                "error": f"Invalid model_config mode: {mode}. Must be one of: {', '.join(sorted(_VALID_MODEL_MODES))}",
            }
        runtime = str(mc.get("runtime") or "").strip()
        if runtime and runtime not in _VALID_MODEL_RUNTIMES:
            return {
                "ok": False,
                "error": f"Invalid model_config runtime: {runtime}. Must be one of: {', '.join(sorted(_VALID_MODEL_RUNTIMES))}",
            }
        # reasoning_effort: two DIFFERENT vocabularies depending on mode/
        # runtime — see _VALID_REASONING_EFFORTS and _VALID_CLI_REASONING_
        # EFFORTS_BY_RUNTIME's own docstrings. Rejected here (save time)
        # rather than silently dropped later (turn time) — the latter would
        # let a caller (the Fleet UI, or /thinking) believe a value is in
        # effect when the turn-time consumer actually discards it as
        # invalid for this agent's real mode/runtime.
        reasoning_effort = str(mc.get("reasoning_effort") or "").strip().lower()
        if reasoning_effort:
            _effective_mode = mode or "platform_credits"
            if _effective_mode == "cli_subscription":
                _effective_runtime = runtime or "claude_code"
                _valid_efforts = _VALID_CLI_REASONING_EFFORTS_BY_RUNTIME.get(_effective_runtime, set())
            elif _effective_mode in ("platform_credits", "byok_api"):
                _valid_efforts = _VALID_REASONING_EFFORTS
            else:
                # "local" (Ollama): no CLI reasoning-effort control exists for
                # it today — same scope boundary the Fleet UI's own picker
                # already draws (REASONING_EFFORT_SUPPORTED_MODES /
                # renderReasoningEffortUnsupportedNote in FleetAgentDetail.tsx).
                _valid_efforts = set()
            if reasoning_effort not in _valid_efforts:
                return {
                    "ok": False,
                    "error": (
                        f"Invalid model_config reasoning_effort '{reasoning_effort}' for mode "
                        f"'{_effective_mode}'"
                        + (f" runtime '{runtime or 'claude_code'}'" if _effective_mode == "cli_subscription" else "")
                        + (f". Must be one of: {', '.join(sorted(_valid_efforts))}" if _valid_efforts else " — reasoning effort isn't available for this mode.")
                    ),
                }
        gateway_binding = mc.get("gateway_binding")
        if gateway_binding is not None and not isinstance(gateway_binding, str):
            return {
                "ok": False,
                "error": "model_config gateway_binding must be a gateway id string.",
            }
        # cli_subscription needs a REAL, paired Gateway — not just any string.
        # Confirm it resolves to an active registration for THIS workspace
        # before saving, instead of silently persisting a binding that can
        # never dispatch (the turn-time error would otherwise only surface
        # much later, mid-conversation, instead of at save time).
        if mode == "cli_subscription" and isinstance(gateway_binding, str) and gateway_binding.strip():
            from server_modules import gateway_state_repository, gateway_registry_service

            _gateway_id = gateway_binding.strip()
            _registration = gateway_state_repository.get_gateway_registration(_gateway_id)
            _registration_workspace_id = str((_registration or {}).get("workspace_id") or "").strip()
            _resolves = (
                isinstance(_registration, dict)
                and bool(_registration)
                and str(_registration.get("status") or "").strip().lower() == "active"
                and str(_registration.get("device_trust_state") or "").strip().lower() != "revoked"
                and (
                    not _registration_workspace_id
                    or _registration_workspace_id == (str(workspace_id or "").strip() or "default")
                )
            )
            if not _resolves:
                return {
                    "ok": False,
                    "error": (
                        f"gateway_binding '{_gateway_id}' does not resolve to a Gateway paired "
                        "to this workspace. Pair a Gateway first, then bind it here."
                    ),
                }
            # And the CLI itself must be installed AND authenticated on that
            # Gateway — save-time honesty, so users hear "sign it in first"
            # here instead of getting an opaque "Gateway dispatch could not
            # be delivered" the first time they send a message. Mirrors what
            # the frontend GatewayBoxPicker's `gatewayRuntimeState` already
            # checks client-side — this is the server-side enforcement so
            # a raw API PATCH can't bypass it.
            _runtime = str(mc.get("runtime") or "").strip().lower() or "claude_code"
            if _runtime in {"claude_code", "codex", "grok_build", "cursor_cli"}:
                _payload = gateway_registry_service.gateway_registration_public_payload(_registration)
                _llm_runtimes = _payload.get("llm_runtimes") if isinstance(_payload.get("llm_runtimes"), dict) else {}
                _entry = _llm_runtimes.get(_runtime) if isinstance(_llm_runtimes.get(_runtime), dict) else {}
                _installed = bool(_entry.get("installed"))
                _authenticated = bool(_entry.get("authenticated"))
                if not _installed or not _authenticated:
                    _label = {
                        "claude_code": "Claude Code",
                        "codex": "Codex",
                        "grok_build": "Grok Build",
                        "cursor_cli": "Cursor CLI",
                    }.get(_runtime, _runtime)
                    # display_name is a top-level registration column (set by
                    # pairing/rename — see gateway_registry_service.rename_
                    # gateway_registration), never nested under metadata. Reading
                    # metadata.get("display_name") here always missed it and fell
                    # through to the raw OS hostname, so a box the user had
                    # renamed (e.g. "Production Gateway") showed this error under
                    # its old auto-generated hostname instead — a confusing
                    # "which box is this even talking about" mismatch against the
                    # box-picker dropdown, which already reads display_name
                    # correctly via gatewayLabel(). _payload above already
                    # resolved this the right way; reuse it instead of
                    # re-deriving from raw metadata.
                    _box_label = str(
                        _payload.get("display_name")
                        or _registration.get("metadata", {}).get("hostname")
                        or _gateway_id
                    )
                    if not _installed:
                        _msg = (
                            f"{_label} isn't installed on {_box_label} yet. Open that computer's "
                            "Hardware page, install it there, then bind."
                        )
                    else:
                        _msg = (
                            f"{_label} isn't signed in on {_box_label} yet. Open that computer's "
                            "Hardware page, sign in there, then bind."
                        )
                    return {"ok": False, "error": _msg}

    try:
        bundle = await repo.get_workspace_agent_install_bundle(
            agent_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if not bundle:
            return {"ok": False, "error": f"Agent {agent_id} not found in workspace"}

        bundle_dict = dict(bundle) if isinstance(bundle, dict) else {}
        meta = dict(bundle_dict.get("install_metadata") or bundle_dict.get("metadata") or {})

        # Apply patch to metadata
        for k in ("enabled_tools", "connectors", "channel_bindings"):
            if k in clean_patch:
                meta[k] = clean_patch[k]
        if "subagents_enabled" in clean_patch:
            meta["subagents_enabled"] = bool(clean_patch["subagents_enabled"])
        if "instructions" in clean_patch:
            meta["instructions"] = str(clean_patch["instructions"] or "").strip()[:_MAX_INSTRUCTIONS_CHARS]
        _next_label: Optional[str] = None
        if "display_name" in clean_patch:
            # Inline rename (Overview tab). The real storage target is the
            # workspace_agent_installs.label COLUMN, not metadata — same
            # column fleet_create_agent seeds from `name`/the name pool.
            requested_label = str(clean_patch["display_name"] or "").strip()[:200]
            if not requested_label:
                return {"ok": False, "error": "Name can't be empty."}
            # Collision check (STEP 5, agent-identity plan). fleet_create_agent's
            # auto-naming path already collision-checks every generated name
            # against every existing label in the workspace
            # (agent_name_pool.assign_agent_name, case-insensitive) — but a
            # manual rename through this PATCH never did, and no DB constraint
            # backed it either, so two agents could silently end up sharing a
            # display name. Names are the display layer only (routing is
            # always by agent_id, never label — see fleet_message_agent /
            # get_workspace_agent_install_bundle), but a collision still
            # breaks two real things: a human scanning the fleet roster, and
            # the closed-roster mention autocomplete that has to resolve a
            # typed name back to exactly one agent_id. Held to the same bar
            # as auto-naming: case-insensitive, and excludes this agent's own
            # current row (re-submitting the same name, or only changing
            # case, is a no-op — never a collision with itself).
            _rename_existing_installs = await repo.list_workspace_agent_installs(
                tenant_id=tenant_id, workspace_id=workspace_id, include_master=True,
            )
            _requested_label_lower = requested_label.lower()
            for _existing_install in (_rename_existing_installs or []):
                _existing_agent_id = str(_existing_install.get("id") or "").strip()
                if _existing_agent_id == agent_id:
                    continue
                if str(_existing_install.get("label") or "").strip().lower() == _requested_label_lower:
                    return {
                        "ok": False,
                        "error": (
                            f"'{requested_label}' is already the name of another agent in this "
                            "workspace. Names must be unique so mentions and the fleet roster can "
                            "tell agents apart — pick a different name."
                        ),
                    }
            _next_label = requested_label
        if "telegram_first_contact_reply" in clean_patch:
            meta["telegram_first_contact_reply"] = bool(clean_patch["telegram_first_contact_reply"])
        if "preferred_gateway_id" in clean_patch:
            value = clean_patch["preferred_gateway_id"]
            if value is not None and not isinstance(value, str):
                return {"ok": False, "error": "preferred_gateway_id must be a gateway id string."}
            meta["preferred_gateway_id"] = str(value or "").strip()
        if "mandate" in clean_patch:
            # The owner-declared mandate: which tools this agent's
            # audience-tier callers (end-customers over a channel) may
            # trigger, on top of whatever the tool catalog already marks
            # audience_safe. Two id spaces share this one list: connector/MCP
            # actions ("{connector_id}.{action_id}", no catalog-level
            # audience_safe flag at all — fail-safe until listed here) and
            # local/builtin tools by their literal canonical enforcement id
            # (e.g. "memory_write" — what the Tools tab's Customer access
            # control writes; see fleet_get_agent_tools' mandate_granted and
            # skills_service._authority_mandate_gate, which checks both
            # spaces). Consulted by both the skills_service and
            # runs_execution mandate gates.
            mandate_patch = clean_patch["mandate"]
            if not isinstance(mandate_patch, dict):
                return {"ok": False, "error": "mandate must be an object."}
            next_mandate = dict(meta.get("mandate") or {})
            if "audience_tools" in mandate_patch:
                raw_tools = mandate_patch["audience_tools"]
                if not isinstance(raw_tools, list) or not all(isinstance(t, str) for t in raw_tools):
                    return {"ok": False, "error": "mandate.audience_tools must be an array of tool id strings."}
                clean_tools = sorted({t.strip() for t in raw_tools if t.strip()})
                if len(clean_tools) > _MAX_MANDATE_AUDIENCE_TOOLS:
                    return {
                        "ok": False,
                        "error": f"mandate.audience_tools may list at most {_MAX_MANDATE_AUDIENCE_TOOLS} tools.",
                    }
                next_mandate["audience_tools"] = clean_tools
            meta["mandate"] = next_mandate
        if "context_policy" in clean_patch:
            cp = clean_patch["context_policy"]
            if not isinstance(cp, dict):
                return {"ok": False, "error": "context_policy must be an object."}
            next_cp = dict(meta.get("context_policy") or {})
            if "max_context_tokens" in cp:
                try:
                    max_tok = int(cp["max_context_tokens"])
                except (TypeError, ValueError):
                    return {"ok": False, "error": "context_policy.max_context_tokens must be an integer."}
                if max_tok < 0:
                    return {"ok": False, "error": "context_policy.max_context_tokens must be ≥ 0."}
                next_cp["max_context_tokens"] = max_tok
            if "on_context_full" in cp:
                action = str(cp["on_context_full"] or "").strip().lower()
                if action not in _VALID_CONTEXT_FULL_ACTIONS:
                    return {
                        "ok": False,
                        "error": f"context_policy.on_context_full must be one of: {', '.join(sorted(_VALID_CONTEXT_FULL_ACTIONS))}",
                    }
                next_cp["on_context_full"] = action
            meta["context_policy"] = next_cp
        _next_hardware_access: Optional[str] = None
        if "hardware_access" in clean_patch:
            # Phase 5B: a knowledge agent's hardware access is policy-locked. It
            # can only be granted by changing the capability preset — a direct
            # grant here is refused and ledgered.
            from server_modules import capability_presets as _caps_cfg

            requested = str(clean_patch["hardware_access"] or "").strip().lower()
            # "all" (Full hardware access) was retired 2026-07-15 — founder
            # ruling: once an agent has hardware (gateway or vps) it has full
            # run of that box by default, so a separate "full access" tier
            # was never a real, distinct capability, just a confusing fourth
            # button. It never had distinct runtime behavior either (nothing
            # in the hardware runtime adapters or action broker branches on
            # "all" — grep confirms the only two live references before this
            # change were this validator and the reserved PRESET_OPERATOR
            # default in capability_presets.py). Any caller still sending it —
            # a stale UI bundle mid-deploy, an old script, an operator-agent
            # tool-call built from older context — is coerced to "gateway"
            # rather than rejected: "full reign of a specific paired box" is
            # the closer of the two surviving meanings to what "all" used to
            # promise, and this doesn't touch preferred_gateway_id, so a
            # request that also sets a specific VPS box still lands on that
            # exact box regardless of this bucket. A row already stored as
            # "all" is normalized in fleet_list_agents below on every read,
            # so it never depends on being re-saved to render correctly.
            if requested == "all":
                requested = "gateway"
            if requested not in {"none", "gateway", "vps"}:
                return {
                    "ok": False,
                    "error": "hardware_access must be one of: none, gateway, vps",
                }
            if requested != "none" and _caps_cfg.hardware_is_locked(bundle_dict):
                await _ledger_fleet_action(
                    action="hardware_grant_denied",
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    target_agent_id=agent_id,
                    status="blocked",
                    metadata={
                        "reason": "knowledge_agent_hardware_locked",
                        "capability_preset": "knowledge",
                        "requested_hardware_access": requested,
                    },
                )
                return {
                    "ok": False,
                    "error": (
                        "This is a knowledge agent — hardware access is policy-locked. "
                        "Change its capability preset (not just this field) to grant hardware."
                    ),
                }
            # Real storage target is the workspace_agent_installs.hardware_access
            # COLUMN — HardwareTab / fleet_list_agents read the column, not
            # metadata. (metadata.hardware_access used to be written here as a
            # bare bool, which nothing ever read — a silent no-op.)
            _next_hardware_access = requested
        if "model_config" in clean_patch:
            meta["model_config"] = dict(clean_patch["model_config"] or {})
        if "capability_config" in clean_patch:
            # Per-capability MERGE, not wholesale replace (unlike model_config,
            # which is a single object — capability_config has 4 independent
            # sub-keys, so saving image_generation must not silently clobber
            # an already-configured text_to_speech). Never carries secret
            # material — see agent_capability_service.py's module docstring;
            # BYOK keys go through fleet_set_agent_capability_key instead.
            from server_modules import agent_capability_service as _cap_svc

            try:
                clean_capability_patch = _cap_svc.validate_capability_config_patch(clean_patch["capability_config"])
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            next_capability_config = dict(meta.get("capability_config") or {})
            next_capability_config.update(clean_capability_patch)
            meta["capability_config"] = next_capability_config
        _next_tool_toggles: Optional[Dict[str, bool]] = None
        if "tool_toggles" in clean_patch:
            raw_toggles = clean_patch["tool_toggles"]
            if not isinstance(raw_toggles, dict):
                return {"ok": False, "error": "tool_toggles must be an object of {tool_id: true|false}."}
            _next_tool_toggles = {
                str(tool_id).strip(): bool(enabled_flag)
                for tool_id, enabled_flag in raw_toggles.items()
                if str(tool_id).strip()
            }

        # Persist via update. tool_toggles / hardware_access are real columns —
        # update_workspace_agent_install writes them directly (tool_toggles is
        # merged with the existing dict there), everything else lives in metadata.
        await repo.update_workspace_agent_install(
            agent_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            label=_next_label,
            metadata=meta,
            tool_toggles=_next_tool_toggles,
            hardware_access=_next_hardware_access,
        )
    except Exception as exc:
        await _ledger_fleet_action(
            action="configure_agent_failed",
            actor_id=actor_id,
            workspace_id=workspace_id,
            target_agent_id=agent_id,
            status="failed",
            metadata={"error": str(exc)[:200], "patch_keys": sorted(clean_patch.keys())},
        )
        return {"ok": False, "error": str(exc)}

    await _ledger_fleet_action(
        action="configure_agent",
        actor_id=actor_id,
        workspace_id=workspace_id,
        target_agent_id=agent_id,
        metadata={"patch_keys": sorted(clean_patch.keys())},
    )
    response: Dict[str, Any] = {"ok": True, "agent_id": agent_id, "applied": sorted(clean_patch.keys())}
    # Hardware-aware "recommended" reuse (§29): only worth the lookup when
    # THIS patch just (re)bound the agent's placement — a real gateway id,
    # not the "cloud"/unbind case. Best-effort, additive-only key; existing
    # callers that don't read it are unaffected.
    if "preferred_gateway_id" in clean_patch:
        _bound_gateway_id = str(meta.get("preferred_gateway_id") or "").strip()
        if _bound_gateway_id:
            _recommendation = recommended_model_config_for_gateway(
                _bound_gateway_id, workspace_id=workspace_id,
            )
            if _recommendation:
                response["recommended_model_config"] = _recommendation
    return response


async def fleet_message_agent(
    *,
    actor_id: str,
    workspace_id: str,
    tenant_id: str = "system",
    agent_id: str = "",
    message: str = "",
) -> Dict[str, Any]:
    """Agent-to-agent messaging is NOT implemented. This always fails.

    Historically this wrote the message into the target agent's
    ``install_metadata.fleet_inbox`` and unconditionally returned
    ``{"ok": True, "status": "enqueued"}``. Nothing in the turn-building
    pipeline ever reads ``fleet_inbox`` back -- not
    ``sage_agent_runtime_service.py``, not
    ``sage_instruction_compiler_service.py``, not
    ``direct_chat_generation_service.py``, not ``agent_turn.py`` -- so every
    prior call silently discarded its message while reporting success. See
    docs/design/audit-silent-failures.md C1 for the full verification.

    The platform's decided design for real agent-to-agent handoff is
    task/mention-based delivery through the scheduler -- a separate, later
    build (do NOT resurrect fleet_inbox as a stopgap; fix the real delivery
    path instead). Until that ships, this function fails loudly and
    explicitly instead of lying, per the platform's no-silent-failure rule:
    every caller -- the internal fleet skill, the tool-broker's
    ``fleet__message_agent`` action, and the external ``empyralis_message_agent``
    MCP tool -- gets an explicit, model-facing error telling it what to do
    instead, rather than a false ``ok: true`` for a message that will never
    be read.
    """
    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required"}
    if not str(message or "").strip():
        return {"ok": False, "error": "message is required"}

    error = (
        "Agent-to-agent messaging is not implemented -- this message will "
        "NOT be delivered and will NOT be read by the target agent (there "
        "is no delivery path; see docs/design/audit-silent-failures.md C1). "
        "Do not retry this tool. Instead, ask the workspace owner to create "
        "a task and assign it to the target agent, or route the instruction "
        "through the owner directly, so the work is durable and visible "
        "instead of an unread message."
    )
    await _ledger_fleet_action(
        action="message_agent_refused",
        actor_id=actor_id,
        workspace_id=workspace_id,
        target_agent_id=agent_id,
        status="failed",
        metadata={"reason": "no_delivery_path", "message_length": len(str(message))},
    )
    return {"ok": False, "error": error}


# ── Owner-only stop control ────────────────────────────────────────────────
# kill_switch_gate.py is the real enforcement (agent:{id}/workspace:{id}
# keys, checked by sage_agent_runtime_service._run_sage_action_loop_v3
# before any turn work happens) — it has no actor/timestamp fields of its
# own, so who/when/reason live in the entity's own metadata here, same
# pattern routes_gateway.py's agent-computer emergency-stop already uses.
#
# These four functions are deliberately NOT in _ALLOWED_CONFIGURE_KEYS and
# NOT wired into skills_service.py's tool dispatcher — stopping/resuming is
# an owner-only human action, never something an agent (or a turn acting on
# an agent's behalf) can do to itself or another agent. Only routes_fleet.py
# calls these, gated by enforce_workspace_access(..., minimum_role="owner").


async def fleet_stop_agent(
    *,
    actor_id: str,
    actor_label: str = "",
    workspace_id: str,
    tenant_id: str = "system",
    agent_id: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    from server_modules import agent_registry_repository as repo
    from server_modules import kill_switch_gate

    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required"}

    bundle = await repo.get_workspace_agent_install_bundle(agent_id, tenant_id=tenant_id, workspace_id=workspace_id)
    if not bundle:
        return {"ok": False, "error": f"Agent {agent_id} not found in workspace"}

    bundle_dict = dict(bundle) if isinstance(bundle, dict) else {}
    meta = dict(bundle_dict.get("install_metadata") or bundle_dict.get("metadata") or {})
    stopped_state = {
        "active": True,
        "reason": str(reason or "").strip()[:500],
        "stopped_by_user_id": str(actor_id or "").strip(),
        "stopped_by_label": str(actor_label or "").strip(),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    meta["stopped"] = stopped_state

    kill_switch_gate.set_kill_switch(f"{kill_switch_gate.AGENT_KILL_PREFIX}{agent_id}")
    await repo.update_workspace_agent_install(agent_id, tenant_id=tenant_id, workspace_id=workspace_id, metadata=meta)

    # Title carries the agent's own name (matching the "Maple chat completed"
    # convention other ledger rows use) rather than a bare "Stopped" — the
    # Inbox list row shows only `title`, not a resolved install_id, so an
    # un-clicked row needs the name baked in to read clearly once fleet_control
    # rows are no longer filtered out of the workspace-wide feed.
    _agent_label = str(bundle_dict.get("label") or "").strip() or "This agent"
    await activity_ledger_service.append_activity_event(
        tenant_id=await _resolve_ledger_tenant_id(workspace_id),
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(actor_id or "").strip() or "unknown",
        install_id=agent_id,
        event_class="fleet_control",
        detail_level="audit_reference",
        action="agent_stopped",
        title=f"{_agent_label} stopped",
        summary=(
            f"{actor_label or actor_id} stopped this agent."
            + (f" Reason: {reason}" if str(reason or "").strip() else "")
        ),
        status="executed",
        metadata={"agent_id": agent_id, "reason": str(reason or "").strip() or None, "stopped_by_user_id": actor_id},
    )
    return {"ok": True, "agent_id": agent_id, "stopped": stopped_state}


async def fleet_resume_agent(
    *,
    actor_id: str,
    actor_label: str = "",
    workspace_id: str,
    tenant_id: str = "system",
    agent_id: str = "",
) -> Dict[str, Any]:
    from server_modules import agent_registry_repository as repo
    from server_modules import kill_switch_gate

    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required"}

    bundle = await repo.get_workspace_agent_install_bundle(agent_id, tenant_id=tenant_id, workspace_id=workspace_id)
    if not bundle:
        return {"ok": False, "error": f"Agent {agent_id} not found in workspace"}

    bundle_dict = dict(bundle) if isinstance(bundle, dict) else {}
    meta = dict(bundle_dict.get("install_metadata") or bundle_dict.get("metadata") or {})
    meta["stopped"] = {"active": False}

    kill_switch_gate.clear_kill_switch(f"{kill_switch_gate.AGENT_KILL_PREFIX}{agent_id}")
    await repo.update_workspace_agent_install(agent_id, tenant_id=tenant_id, workspace_id=workspace_id, metadata=meta)

    _agent_label = str(bundle_dict.get("label") or "").strip() or "This agent"
    await activity_ledger_service.append_activity_event(
        tenant_id=await _resolve_ledger_tenant_id(workspace_id),
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(actor_id or "").strip() or "unknown",
        install_id=agent_id,
        event_class="fleet_control",
        detail_level="audit_reference",
        action="agent_resumed",
        title=f"{_agent_label} resumed",
        summary=f"{actor_label or actor_id} resumed this agent.",
        status="executed",
        metadata={"agent_id": agent_id, "resumed_by_user_id": actor_id},
    )
    return {"ok": True, "agent_id": agent_id, "stopped": {"active": False}}


async def fleet_delete_agent(
    *,
    actor_id: str,
    actor_label: str = "",
    workspace_id: str,
    tenant_id: str = "system",
    agent_id: str = "",
) -> Dict[str, Any]:
    """Owner-only, irreversible: permanently delete a specialist agent install
    and everything scoped to it.

    Teardown scope:
      1. Discord/Telegram: release the agent-EXCLUSIVE BYO bot (deletes its
         webhook + vault credential + channel binding) via the same
         release_agent_discord/release_agent_telegram helpers the dedicated
         .../agent-channels/{discord,telegram} DELETE routes already use.
      2. Slack: delete this agent's channel binding row (Slack's OAuth
         connection itself is workspace-wide/shared — nothing else to release).
      3. Cancel this agent's pending self-scheduled wake-ups
         (agent_scheduler_wake_requests). That table is append-only by
         established convention — see bounded_scheduler_service.
         cancel_wake_request's own docstring ("this table has no DELETE
         statement anywhere in the codebase") — and isn't FK-linked to a
         SPECIALIST's install id anyway (master_agent_install_id points at
         the OPERATOR that executes the wake-up, not this agent), so a hard
         delete of the install row below would never reach these rows even
         if we wanted it to.
      4. Wipe this agent's on-disk memory tree (MEMORY.md + memory/files/**),
         which is not FK-tracked at all.
      5. Hard-delete the workspace_agent_installs row itself. Its FK
         ON DELETE CASCADE removes agent_manifests, agent_bible_versions,
         agent_skill_bindings, agent_connector_bindings, agent_channel_bindings
         (belt-and-suspenders with steps 1-2 above), agent_runtime_profiles,
         deployed_agents (+ its own usage/cost-ledger cascades),
         agent_channel_execution_leases, and security_control_states — every
         remaining per-agent child row keyed by agent_install_id.
         tool_toggles/connector_bindings/memory_scope_overrides are columns
         ON this row, so they're gone the moment it is.

    Deliberately conservative about what this does NOT touch: connector vault
    credentials are project-scoped and reusable by OTHER agents (see
    connectors_actions.store_agent_connector_credential's "provenance only"
    comment on agent_install_id) — only this agent's binding to them goes
    away (via cascade), never the credential itself. Every step above is
    keyed strictly off this one agent_id; nothing here ever touches a
    workspace-level or shared row.

    Never deletes the workspace's master/operator install (Sage): losing it
    would strand the workspace with no operator, and — more dangerous —
    workspace_context.agent_workspace_context_dir silently resolves an EMPTY
    agent_install_id to the bare WORKSPACE memory root (see that function's
    own security note re: the 2026-07-14 cross-agent memory leak fixed in
    skills_service.py). Treating Sage as "just another agent_id" in step 4
    above would risk wiping shared workspace memory, not just one agent's.
    """
    from server_modules import agent_registry_repository as repo
    from server_modules import control_plane_repository

    agent_id = str(agent_id or "").strip()
    if not agent_id:
        return {"ok": False, "error": "agent_id is required"}

    bundle = await repo.get_workspace_agent_install_bundle(agent_id, tenant_id=tenant_id, workspace_id=workspace_id)
    if not bundle:
        return {"ok": False, "error": f"Agent {agent_id} not found in workspace"}
    bundle_dict = dict(bundle) if isinstance(bundle, dict) else {}
    _agent_label = str(bundle_dict.get("label") or "").strip() or "This agent"

    master = await repo.get_workspace_master_agent_install(tenant_id=tenant_id, workspace_id=workspace_id)
    if master and str(master.get("id") or "").strip() == agent_id:
        return {"ok": False, "error": "Cannot delete the workspace's operator agent."}

    # Fail fast, before any destructive side effect, if the control plane
    # isn't reachable — avoids the worse partial-teardown outcome of ripping
    # out channels/schedules/memory and then failing on the row delete itself.
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return {"ok": False, "error": "Control-plane database unavailable; cannot delete agent."}

    # 1-2. Release channel-owned resources while the binding rows this needs
    # (to find each one's vault credential id) still exist.
    channel_release: Dict[str, Any] = {}
    try:
        from server_modules import discord_bot_provisioning_service as discord_prov
        channel_release["discord"] = await discord_prov.release_agent_discord(
            agent_install_id=agent_id, workspace_id=workspace_id, tenant_id=tenant_id,
        )
    except Exception as exc:
        channel_release["discord"] = {"released": False, "error": str(exc)[:300]}
    try:
        from server_modules import hosted_bot_provisioning_service as telegram_prov
        channel_release["telegram"] = await telegram_prov.release_agent_telegram(
            agent_install_id=agent_id, workspace_id=workspace_id, tenant_id=tenant_id,
        )
    except Exception as exc:
        channel_release["telegram"] = {"released": False, "error": str(exc)[:300]}
    try:
        from server_modules import agent_bindings_repository as bindings
        channel_release["slack"] = {
            "deleted": await bindings.delete_channel_binding(
                tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_id, channel_key="slack",
            )
        }
    except Exception as exc:
        channel_release["slack"] = {"deleted": False, "error": str(exc)[:300]}

    # 3. Cancel pending self-scheduled wake-ups (status transition, not delete).
    cancelled_schedules = 0
    try:
        from server_modules.bounded_scheduler_service import cancel_wake_request, list_wake_requests_for_agent
        pending = await list_wake_requests_for_agent(tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id)
        for wake in pending:
            wake_id = str(wake.get("id") or "").strip()
            if not wake_id:
                continue
            cancel_result = await cancel_wake_request(
                tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id,
                wake_id=wake_id, cancelled_by=actor_id or "owner",
            )
            if cancel_result.get("ok"):
                cancelled_schedules += 1
    except Exception:
        pass

    # 4. Wipe this agent's on-disk memory tree. agent_id is already validated
    # non-empty and non-master above; the parent.name check below is
    # belt-and-suspenders so this rmtree can never land on the bare workspace
    # scope root even if that invariant is ever broken upstream.
    memory_wiped = False
    try:
        from server_modules.workspace_context import agent_workspace_context_dir

        agent_dir = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_id)
        if agent_dir.parent.name == "agents" and agent_dir.exists():
            import shutil

            shutil.rmtree(agent_dir, ignore_errors=True)
            memory_wiped = True
    except Exception:
        pass

    # 5. Hard-delete the install row (see docstring for the full cascade this
    # triggers). Postgres-only, mirroring scale_harness.py's own
    # DELETE FROM workspace_agent_installs — the only other hard-delete of
    # this table in the codebase; there is no local-SQLite-fallback delete
    # path reachable here without reaching into agent_registry_repository's
    # private _local helpers.
    await control_plane_repository.rls_execute(
        pool,
        "DELETE FROM workspace_agent_installs WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3",
        agent_id, tenant_id, workspace_id,
        tenant_id=tenant_id, workspace_id=workspace_id,
    )

    # Best-effort: clear a leftover per-agent kill switch from a prior stop.
    try:
        from server_modules import kill_switch_gate
        kill_switch_gate.clear_kill_switch(f"{kill_switch_gate.AGENT_KILL_PREFIX}{agent_id}")
    except Exception:
        pass

    # install_id is intentionally omitted below — the row is already gone by
    # this point, and activity_ledger_events.install_id is a real FK
    # (ON DELETE SET NULL) to workspace_agent_installs; inserting a NEW row
    # that points at an id that no longer exists would violate that
    # constraint outright, not silently null itself.
    await activity_ledger_service.append_activity_event(
        tenant_id=await _resolve_ledger_tenant_id(workspace_id),
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(actor_id or "").strip() or "unknown",
        event_class="fleet_control",
        detail_level="audit_reference",
        action="agent_deleted",
        title=f"{_agent_label} deleted",
        summary=f"{actor_label or actor_id} deleted this agent.",
        status="executed",
        metadata={
            "agent_id": agent_id,
            "agent_label": _agent_label,
            "deleted_by_user_id": actor_id,
            "channel_release": channel_release,
            "cancelled_schedules": cancelled_schedules,
            "memory_wiped": memory_wiped,
        },
    )
    return {
        "ok": True,
        "agent_id": agent_id,
        "deleted": True,
        "channel_release": channel_release,
        "cancelled_schedules": cancelled_schedules,
        "memory_wiped": memory_wiped,
    }


async def fleet_stop_workspace(
    *,
    actor_id: str,
    actor_label: str = "",
    workspace_id: str,
    reason: str = "",
) -> Dict[str, Any]:
    from server_modules import control_plane_repository
    from server_modules import kill_switch_gate

    if not str(workspace_id or "").strip():
        return {"ok": False, "error": "workspace_id is required"}

    stopped_state = {
        "active": True,
        "reason": str(reason or "").strip()[:500],
        "stopped_by_user_id": str(actor_id or "").strip(),
        "stopped_by_label": str(actor_label or "").strip(),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    kill_switch_gate.set_kill_switch(f"{kill_switch_gate.WORKSPACE_KILL_PREFIX}{workspace_id}")
    saved = await control_plane_repository.update_workspace_kill_switch_metadata(workspace_id, stopped_state)
    if saved is None:
        kill_switch_gate.clear_kill_switch(f"{kill_switch_gate.WORKSPACE_KILL_PREFIX}{workspace_id}")
        return {"ok": False, "error": f"Workspace {workspace_id} not found"}

    await activity_ledger_service.append_activity_event(
        tenant_id=await _resolve_ledger_tenant_id(workspace_id),
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(actor_id or "").strip() or "unknown",
        event_class="fleet_control",
        detail_level="audit_reference",
        action="workspace_stopped",
        title="All agents stopped",
        summary=(
            f"{actor_label or actor_id} stopped all agents in this workspace."
            + (f" Reason: {reason}" if str(reason or "").strip() else "")
        ),
        status="executed",
        metadata={"workspace_id": workspace_id, "reason": str(reason or "").strip() or None, "stopped_by_user_id": actor_id},
    )
    return {"ok": True, "workspace_id": workspace_id, "stopped": stopped_state}


async def fleet_resume_workspace(
    *,
    actor_id: str,
    actor_label: str = "",
    workspace_id: str,
) -> Dict[str, Any]:
    from server_modules import control_plane_repository
    from server_modules import kill_switch_gate

    if not str(workspace_id or "").strip():
        return {"ok": False, "error": "workspace_id is required"}

    kill_switch_gate.clear_kill_switch(f"{kill_switch_gate.WORKSPACE_KILL_PREFIX}{workspace_id}")
    saved = await control_plane_repository.update_workspace_kill_switch_metadata(workspace_id, {"active": False})
    if saved is None:
        return {"ok": False, "error": f"Workspace {workspace_id} not found"}

    await activity_ledger_service.append_activity_event(
        tenant_id=await _resolve_ledger_tenant_id(workspace_id),
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(actor_id or "").strip() or "unknown",
        event_class="fleet_control",
        detail_level="audit_reference",
        action="workspace_resumed",
        title="All agents resumed",
        summary=f"{actor_label or actor_id} resumed all agents in this workspace.",
        status="executed",
        metadata={"workspace_id": workspace_id, "resumed_by_user_id": actor_id},
    )
    return {"ok": True, "workspace_id": workspace_id, "stopped": {"active": False}}


async def fleet_create_agent(
    *,
    actor_id: str,
    workspace_id: str,
    tenant_id: str = "system",
    name: str = "",
    instructions: str = "",
    purpose_preset: str = "",
    audience: str = "",
    capability_preset: str = "standard",
    project_id: str = "",
    enabled_tools: Optional[List[str]] = None,
    connectors: Optional[List[str]] = None,
    channel_bindings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new specialist agent in the workspace.

    Only callable by operator-role agents.
    The new agent is seeded with role="specialist" and subagents_enabled=False.
    `purpose_preset` (customer_facing | internal_assistant | operator) shapes
    the default instructions. `audience` ("owner" | "external") is the
    separate, stable facing flag a later credential/connector/memory
    resolver gates on; when omitted it's derived from purpose_preset (see
    _AUDIENCE_BY_PURPOSE_PRESET). `capability_preset` (knowledge | standard)
    seeds hardware/tools/model/subagents/context DEFAULTS; 'operator' is
    reserved (Sage-class) and not creatable via this flow. All fields remain
    overridable afterward except a knowledge agent's policy-locked hardware
    access.
    """
    from server_modules import agent_registry_repository as repo
    from server_modules import capability_presets as _caps

    # Phase 5B: capability preset — reject the reserved operator preset.
    if not _caps.is_creatable(capability_preset):
        return {
            "ok": False,
            "error": (
                f"capability_preset '{capability_preset}' is reserved (Sage-class operator) "
                "and cannot be created through the normal flow. Use 'knowledge' or 'standard'."
            ),
        }
    _preset_defaults = _caps.build_install_defaults(capability_preset)

    clean_name = str(name or "").strip()
    if clean_name:
        agent_label = clean_name
    else:
        # NAME IS NOT A STEP: the create-agent wizard creates the agent on
        # Placement, before the owner has picked a name. Auto-assign from the
        # curated pool (collision-checked within this workspace) — the owner
        # renames it anytime from the Overview tab.
        from server_modules import agent_name_pool
        _existing_installs = await repo.list_workspace_agent_installs(
            tenant_id=tenant_id, workspace_id=workspace_id, include_master=True,
        )
        agent_label = agent_name_pool.assign_agent_name(
            str(i.get("label") or "") for i in (_existing_installs or [])
        )
    meta = seed_specialist_metadata()
    meta["fleet_created_by"] = actor_id
    meta["fleet_created_at"] = datetime.now(timezone.utc).isoformat()
    # Apply capability-preset defaults into metadata (capability_preset,
    # model_tier, context_policy, hardware lock flag, subagents default).
    meta.update(dict(_preset_defaults.get("metadata") or {}))
    meta["subagents_enabled"] = bool(_preset_defaults.get("subagents_enabled"))
    _preset_hardware_access = str(_preset_defaults.get("hardware_access") or "none")
    meta["hardware_access"] = _preset_hardware_access
    # tool_toggles is the field _resolve_specialist_toolset actually enforces at
    # runtime (metadata.enabled_tools below is display-only, read by the Tools
    # tab). A preset with a concrete allowlist (knowledge) must seed tool_toggles
    # too, or its restriction is cosmetic — the agent gets core tools only.
    _preset_tool_toggles: Dict[str, bool] = {}
    if _preset_defaults.get("enabled_tools") is not None:
        meta["enabled_tools"] = list(_preset_defaults["enabled_tools"])
        _preset_tool_toggles = {str(t): True for t in _preset_defaults["enabled_tools"]}

    clean_preset = str(purpose_preset or "").strip().lower()
    if clean_preset in _VALID_PURPOSE_PRESETS:
        meta["purpose_preset"] = clean_preset

    clean_audience = str(audience or "").strip().lower()
    if clean_audience not in _VALID_AUDIENCES:
        clean_audience = _AUDIENCE_BY_PURPOSE_PRESET.get(clean_preset, "owner")
    meta["audience"] = clean_audience

    clean_instructions = str(instructions or "").strip()
    if not clean_instructions and clean_preset in _PURPOSE_PRESET_INSTRUCTIONS:
        clean_instructions = _PURPOSE_PRESET_INSTRUCTIONS[clean_preset]
    if clean_instructions:
        meta["instructions"] = clean_instructions
    if enabled_tools:
        meta["enabled_tools"] = [str(t).strip() for t in enabled_tools if str(t).strip()]
    if connectors:
        meta["connectors"] = [str(c).strip() for c in connectors if str(c).strip()]
    if channel_bindings:
        meta["channel_bindings"] = dict(channel_bindings)

    # Use the fleet-specialist agent definition (seeded by ensure_workspace_agent_registry_seeded)
    try:
        await repo.ensure_workspace_agent_registry_seeded(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        # Look up the fleet-specialist definition by slug. It is seeded with
        # visibility='private' (an internal template, not a workspace-listed
        # agent), so include_private=True is required or the lookup finds
        # nothing and agent creation always fails.
        definitions = await repo.list_agent_definitions(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            include_private=True,
        )
        fleet_def_id = ""
        for d in (definitions or []):
            if str(d.get("slug") or "").strip() == "fleet-specialist":
                fleet_def_id = str(d.get("id") or "").strip()
                break
        if not fleet_def_id:
            return {"ok": False, "error": "Fleet-specialist agent definition not found. Ensure workspace agent registry is seeded."}

        result = await repo.create_workspace_agent_install(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_definition_id=fleet_def_id,
            label=agent_label,
            metadata=meta,
            policy_context_overrides=dict(_preset_defaults.get("policy_context_overrides") or {}),
            hardware_access=_preset_hardware_access,
            tool_toggles=_preset_tool_toggles or None,
        )
        if not result:
            return {"ok": False, "error": "Failed to create agent install — check agent definition exists"}

        agent_id = str(result.get("id") or "").strip()

        # Assign to the chosen project — an empty project_id resolves to the
        # workspace's default ("General") project rather than leaving the
        # agent unassigned, so the create-agent wizard's Placement step never
        # needs its own project picker. Best-effort: a bad/unresolvable
        # project id shouldn't fail creation.
        _project_id = str(project_id or "").strip()
        if agent_id:
            try:
                from server_modules import projects_repository as _projects
                if not _project_id:
                    _default_project = await _projects.ensure_default_project(
                        tenant_id=tenant_id, workspace_id=workspace_id,
                    )
                    _project_id = str((_default_project or {}).get("id") or "")
                if _project_id:
                    await _projects.assign_install_to_project(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        install_id=agent_id,
                        project_id=_project_id,
                    )
            except Exception:
                pass
    except Exception as exc:
        await _ledger_fleet_action(
            action="create_agent_failed",
            actor_id=actor_id,
            workspace_id=workspace_id,
            status="failed",
            metadata={"error": str(exc)[:200]},
        )
        return {"ok": False, "error": str(exc), "agent_id": ""}

    await _ledger_fleet_action(
        action="create_agent",
        actor_id=actor_id,
        workspace_id=workspace_id,
        target_agent_id=agent_id,
        metadata={"agent_name": agent_label},
    )
    return {"ok": True, "agent_id": agent_id, "role": SPECIALIST_ROLE, "audience": clean_audience, "name": agent_label, "project_id": _project_id}


# ── Phase M: Sage operator bootstrap ─────────────────────────────────────────


async def ensure_sage_is_operator(
    *,
    workspace_id: str,
    tenant_id: str = "system",
) -> Dict[str, Any]:
    """Idempotent: ensure Sage's install has role="operator".

    Call this from the workspace bootstrap / first-turn path.
    If Sage already has a role, this is a no-op.
    If Sage lacks a role (pre-Phase-L install), set role="operator"
    and ledger the migration.
    """
    from server_modules import agent_registry_repository as repo

    try:
        # Find Sage's master agent install
        sage_install = await repo.get_workspace_master_agent_install(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except Exception:
        return {"ok": False, "reason": "failed_to_load_sage_install"}

    if not sage_install or not isinstance(sage_install, dict):
        return {"ok": False, "reason": "sage_install_not_found"}

    sage_install_id = str(sage_install.get("id") or "").strip()
    if not sage_install_id:
        return {"ok": False, "reason": "sage_install_id_missing"}

    # Check current role
    existing_role = resolve_agent_role(sage_install)
    if existing_role == OPERATOR_ROLE:
        return {"ok": True, "already_operator": True, "agent_id": sage_install_id}

    # Migrate: set role="operator" in install_metadata
    meta = dict(sage_install.get("install_metadata") or sage_install.get("metadata") or {})
    meta["role"] = OPERATOR_ROLE
    meta["subagents_enabled"] = bool(meta.get("subagents_enabled", True))
    if not meta.get("model_config"):
        meta["model_config"] = {"mode": "platform_credits"}
    meta["operator_bootstrapped_at"] = datetime.now(timezone.utc).isoformat()

    try:
        await repo.update_workspace_agent_install(
            sage_install_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            metadata=meta,
        )
    except Exception as exc:
        await _ledger_fleet_action(
            action="operator_bootstrap_failed",
            actor_id="system",
            workspace_id=workspace_id,
            target_agent_id=sage_install_id,
            status="failed",
            metadata={"error": str(exc)[:200], "previous_role": existing_role},
        )
        return {"ok": False, "reason": str(exc)}

    await _ledger_fleet_action(
        action="operator_bootstrap",
        actor_id="system",
        workspace_id=workspace_id,
        target_agent_id=sage_install_id,
        metadata={
            "previous_role": existing_role,
            "new_role": OPERATOR_ROLE,
        },
    )
    return {"ok": True, "bootstrapped": True, "agent_id": sage_install_id, "role": OPERATOR_ROLE}


# ── Phase V: Proactive scheduling ───────────────────────────────────────


def _parse_when(when: str) -> Any:
    """Parse a time expression into a UTC datetime.

    Supported forms:
      - ISO-8601: ``2026-07-04T09:00:00Z``
      - Relative minutes: ``in 2 minutes``, ``in 30 min``
      - Relative hours: ``in 1 hour``, ``in 3 hours``
      - Cron: ``*/5 * * * *`` (passed through for cron scheduling)

    Returns a ``datetime.datetime`` or ``None`` if unparseable.
    """
    import re as _re
    from datetime import datetime, timedelta, timezone as _timezone

    when_str = str(when or "").strip()
    if not when_str:
        return None

    # ISO-8601
    if "T" in when_str:
        try:
            ts = when_str.replace("Z", "+00:00")
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            pass

    # Relative: "in N minutes/min" or "in N hours/hour"
    rel = _re.match(r"in\s+(\d+)\s*(minute|minutes|min|m)\w*", when_str, _re.IGNORECASE)
    if rel:
        minutes = int(rel.group(1))
        return datetime.now(_timezone.utc) + timedelta(minutes=minutes)
    rel_h = _re.match(r"in\s+(\d+)\s*(hour|hours|h)\w*", when_str, _re.IGNORECASE)
    if rel_h:
        hours = int(rel_h.group(1))
        return datetime.now(_timezone.utc) + timedelta(hours=hours)
    rel_s = _re.match(r"in\s+(\d+)\s*(second|seconds|s)\w*", when_str, _re.IGNORECASE)
    if rel_s:
        seconds = int(rel_s.group(1))
        return datetime.now(_timezone.utc) + timedelta(seconds=seconds)

    return None


async def schedule_task(
    *,
    workspace_id: str,
    agent_id: str = "",
    actor_id: str = "",
    when: str = "",
    instruction: str = "",
    tenant_id: str = "system",
    authority_tier: Optional[str] = None,
) -> Dict[str, Any]:
    """Schedule a future task for this agent.

    The agent will be woken at *when* with *instruction* as its prompt.
    Results are delivered to the agent's bound channel.

    *when* can be:
      - ``"in 2 minutes"``, ``"in 30 min"`` — relative time
      - ``"in 1 hour"`` — relative hours
      - ``"2026-07-04T09:00:00Z"`` — ISO-8601 UTC datetime

    Mandate: *authority_tier* is the tier of the turn that IS CALLING this
    tool (pass the caller's session_ctx["authority_tier"]) — it is persisted
    onto the wake request and must be carried by whatever later resumes this
    instruction, per authority_mandate_service.inherit_tier(). An
    audience-tier turn scheduling a task must never result in an owner-tier
    execution later just because the wake-up has no live channel sender.
    Defaults to the safe "audience" tier when the caller doesn't pass one.

    Returns ``{ok, wake_request_id, due_at, instruction}``.
    """
    import logging
    _log = logging.getLogger(__name__)

    resolved_instruction = str(instruction or "").strip()
    if not resolved_instruction:
        return {"ok": False, "error": "instruction is required — what should the agent do when it wakes?"}
    resolved_when = str(when or "").strip()
    if not resolved_when:
        return {"ok": False, "error": "when is required — e.g. 'in 2 minutes' or '2026-07-04T09:00:00Z'"}

    due_at = _parse_when(resolved_when)
    if due_at is None:
        return {"ok": False, "error": f"Could not parse 'when' expression: {resolved_when!r}. Use 'in N minutes' or ISO-8601 datetime."}

    from server_modules import authority_mandate_service

    resolved_tier = authority_mandate_service.inherit_tier(authority_tier)

    try:
        from server_modules.bounded_scheduler_service import propose_self_wakeup

        result = await propose_self_wakeup(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            summary=resolved_instruction[:200],
            reason=f"Agent {agent_id or actor_id} scheduled: {resolved_instruction[:100]}",
            due_at=due_at,
            payload={
                "instruction": resolved_instruction,
                "agent_id": agent_id or actor_id,
                "source": "schedule_task_tool",
                "authority_tier": resolved_tier,
            },
            requested_by=agent_id or actor_id or "agent",
        )
        wake_id = (
            result.get("wake_request", {}).get("id", "")
            if isinstance(result.get("wake_request"), dict)
            else ""
        )

        # Ledger the schedule
        await _ledger_fleet_action(
            action="schedule_task",
            actor_id=actor_id or agent_id or "agent",
            workspace_id=workspace_id,
            target_agent_id=agent_id or actor_id,
            metadata={
                "when": resolved_when,
                "instruction": resolved_instruction[:200],
                "due_at": str(due_at),
                "wake_request_id": str(wake_id),
                "accepted": result.get("accepted", False),
                "authority_tier": resolved_tier,
            },
        )
        _log.info(
            "schedule_task: agent=%s workspace=%s due_at=%s accepted=%s wake_id=%s",
            agent_id or actor_id, workspace_id, due_at, result.get("accepted"), wake_id,
        )
        return {
            "ok": True,
            "wake_request_id": str(wake_id),
            "due_at": str(due_at),
            "instruction": resolved_instruction,
            "accepted": result.get("accepted", False),
            "detail": "Task scheduled. The agent will wake and execute this instruction at the specified time.",
        }
    except Exception as exc:
        _log.warning("schedule_task failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


# ── Phase U2: owner-facing schedule surface ─────────────────────────────────
# The list/create/cancel/preview functions behind the Overview tab's Schedule
# section. Deliberately NOT wired into skills_service.py's tool dispatcher —
# same reasoning as fleet_stop_agent/fleet_resume_agent above: these are
# owner-only human actions reached exclusively through the owner-gated REST
# routes in routes_fleet.py, never something an agent calls on itself.


def _schedule_row_view(row: Dict[str, Any]) -> Dict[str, Any]:
    from server_modules import authority_mandate_service

    payload = row.get("payload")
    if isinstance(payload, str):
        import json as _json
        try:
            payload = _json.loads(payload)
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    due_at = row.get("due_at")
    created_at = row.get("created_at")
    description = str(payload.get("instruction") or row.get("summary") or row.get("reason") or "").strip()
    return {
        "id": str(row.get("id") or ""),
        "description": description,
        "due_at": due_at.isoformat() if hasattr(due_at, "isoformat") else str(due_at or ""),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
        "status": str(row.get("status") or "pending").strip().lower(),
        # "system" is provenance, never elevated privilege (authority_mandate_
        # service module docstring) — still shown as its own badge so an
        # audience-created wake-up is never mistaken for an owner one.
        "authority_tier": authority_mandate_service.normalize_tier(payload.get("authority_tier")),
    }


async def fleet_list_agent_schedule(
    *,
    workspace_id: str,
    tenant_id: str = "system",
    agent_id: str = "",
) -> Dict[str, Any]:
    """Owner-facing list of this agent's not-yet-resolved scheduled wake-ups."""
    from server_modules import agent_registry_repository as repo
    from server_modules.bounded_scheduler_service import list_wake_requests_for_agent

    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required"}

    bundle = await repo.get_workspace_agent_install_bundle(
        agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
    )
    if not bundle:
        return {"ok": False, "error": f"Agent {agent_id} not found in workspace"}

    rows = await list_wake_requests_for_agent(tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id)
    return {"ok": True, "agent_id": agent_id, "schedule": [_schedule_row_view(row) for row in rows]}


def fleet_preview_schedule_when(*, when: str) -> Dict[str, Any]:
    """Resolve a natural-language 'when' expression to a concrete UTC time
    without persisting anything, via the same _parse_when schedule_task uses
    — lets the UI show the resolved next run before the owner confirms."""
    from datetime import timezone as _timezone

    resolved = _parse_when(when)
    if resolved is None:
        return {
            "ok": False,
            "error": f"Could not parse 'when' expression: {when!r}. Use 'in N minutes' or ISO-8601 datetime.",
        }
    normalized = resolved.astimezone(_timezone.utc) if resolved.tzinfo is not None else resolved.replace(tzinfo=_timezone.utc)
    return {"ok": True, "due_at": normalized.isoformat()}


async def fleet_create_agent_schedule(
    *,
    actor_id: str,
    actor_label: str = "",
    workspace_id: str,
    tenant_id: str = "system",
    agent_id: str = "",
    when: str = "",
    instruction: str = "",
) -> Dict[str, Any]:
    """Owner-only: schedule a future wake-up for this agent. Always stamps
    owner tier — this is only reachable through the owner-gated REST route,
    never agent-callable, so there's no caller turn to inherit a tier from;
    this call IS the origin of authority for the schedule it creates."""
    from server_modules import agent_registry_repository as repo
    from server_modules import authority_mandate_service

    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required"}

    bundle = await repo.get_workspace_agent_install_bundle(
        agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
    )
    if not bundle:
        return {"ok": False, "error": f"Agent {agent_id} not found in workspace"}

    return await schedule_task(
        workspace_id=workspace_id,
        agent_id=agent_id,
        actor_id=actor_id or "owner",
        when=when,
        instruction=instruction,
        tenant_id=tenant_id,
        authority_tier=authority_mandate_service.TIER_OWNER,
    )


async def fleet_cancel_agent_schedule(
    *,
    actor_id: str,
    actor_label: str = "",
    workspace_id: str,
    tenant_id: str = "system",
    agent_id: str = "",
    wake_request_id: str = "",
) -> Dict[str, Any]:
    """Owner-only: cancel one of this agent's pending wake-ups."""
    from server_modules import agent_registry_repository as repo
    from server_modules.bounded_scheduler_service import cancel_wake_request

    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required"}
    if not str(wake_request_id or "").strip():
        return {"ok": False, "error": "wake_request_id is required"}

    bundle = await repo.get_workspace_agent_install_bundle(
        agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
    )
    if not bundle:
        return {"ok": False, "error": f"Agent {agent_id} not found in workspace"}

    result = await cancel_wake_request(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        wake_id=wake_request_id,
        cancelled_by=actor_id or "owner",
    )
    if result.get("ok"):
        await _ledger_fleet_action(
            action="schedule_cancelled",
            actor_id=actor_id or "owner",
            workspace_id=workspace_id,
            target_agent_id=agent_id,
            metadata={"wake_request_id": wake_request_id},
        )
    return result
