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
_VALID_MODEL_RUNTIMES = {"claude_code", "codex", "ollama"}
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


def resolve_subagents_enabled(install: Optional[Dict[str, Any]]) -> bool:
    """Check if sub-agent delegation is enabled for this agent."""
    m = _meta(install)
    if "subagents_enabled" in m:
        return bool(m["subagents_enabled"])
    # Default: operator can delegate, specialist cannot
    return resolve_agent_role(install) == OPERATOR_ROLE


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
    """Return the install_metadata for a new specialist."""
    return {
        "role": SPECIALIST_ROLE,
        "subagents_enabled": False,
        "model_config": {"mode": "platform_credits"},
    }


# ── Ledger helper ───────────────────────────────────────────────────────────


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
            tenant_id="system",
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
            title=f"Fleet: {action}" + (f" → {target_agent_id}" if target_agent_id else ""),
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


def _resolve_hardware_status(
    inst: Dict[str, Any],
    heartbeats: Dict[str, dict],
) -> tuple[str, Optional[str], Optional[str]]:
    """Resolve hardware status, last heartbeat, and current run for an
    agent install.

    Returns (status, last_heartbeat_iso, current_run_id):
      - "online" — heartbeat received within the freshness window
      - "offline" — a worker registered here before, but not recently
      - "unknown" — no gateway/VPS worker has ever registered for this agent
    """
    profile = _runtime_profile_dict(inst)
    machine_id = str(profile.get("machine_id") or "").strip()

    hb = heartbeats.get(machine_id) if machine_id else None

    if hb and isinstance(hb, dict):
        status = "online" if bool(hb.get("online", False)) else "offline"
        last_hb = str(hb.get("last_heartbeat_at") or "").strip() or None
        current_run_id = str(hb.get("current_run_id") or "").strip() or None
        return status, last_hb, current_run_id

    # Cloud agents have no worker/heartbeat concept — the platform runs
    # their turns synchronously, so "online" always, no run-in-progress
    # tracking (there's no queue for a cloud text-agent turn to sit in).
    target = str(profile.get("default_execution_target") or "").strip().lower()
    if target in ("cloud", "empyralis-cloud"):
        return "online", None, None

    # No machine_id at all — never paired with a gateway/VPS worker.
    if not machine_id:
        return "unknown", None, None

    return "offline", None, None


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
    """Latest activity_ledger_events row per agent (actor_id) — one bulk
    DISTINCT ON query for the whole workspace, not N+1. Backs the
    agents/project list's "last active" column and the row's activity-preview
    line ("what it just did"). Keyed by actor_id -> {last_active_at, title}."""
    try:
        from server_modules import control_plane_repository as cpr

        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            return {}
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (actor_id) actor_id, created_at, title, action
            FROM activity_ledger_events
            WHERE workspace_id = $1
            ORDER BY actor_id, created_at DESC
            """,
            str(workspace_id or "").strip(),
        )
        out: Dict[str, Dict[str, Optional[str]]] = {}
        for r in rows or []:
            actor_id = str(r["actor_id"] or "").strip()
            ts = r["created_at"]
            if not actor_id or ts is None:
                continue
            title = str(r["title"] or "").strip() or str(r["action"] or "").strip().replace("_", " ")
            out[actor_id] = {
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
        _hardware_status, _last_heartbeat, _current_run_id = _resolve_hardware_status(
            inst_dict, _heartbeats
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
            "project_id": str(inst_dict.get("project_id") or "").strip(),
            "status": str(inst_dict.get("status") or "active").strip(),
            "enabled": bool(inst_dict.get("enabled", True)),
            "subagents_enabled": resolve_subagents_enabled(inst_dict),
            "model_config": resolve_model_config(inst_dict),
            "capability_preset": str(_meta_i.get("capability_preset") or "").strip(),
            "hardware_access": str(inst_dict.get("hardware_access") or "none").strip(),
            "hardware_access_locked": bool(_meta_i.get("hardware_access_locked") or _pco_i.get("hardware_access_locked")),
            "context_policy": dict(_ctx_pol),
            "instructions": str(_meta_i.get("instructions") or "").strip(),
            "preferred_gateway_id": str(_meta_i.get("preferred_gateway_id") or "").strip(),
            "telegram_first_contact_reply": bool(_meta_i.get("telegram_first_contact_reply")),
            "stopped": dict(_meta_i.get("stopped") or {}) if bool((_meta_i.get("stopped") or {}).get("active")) else {"active": False},
            # Phase U3: placement visibility
            "runtime_target": _runtime_target,
            "hardware_status": _hardware_status,
            "last_heartbeat": _last_heartbeat,
            "current_run_id": _current_run_id,
            "last_activity": (_last_active.get(str(inst_dict.get("id") or "").strip()) or {}).get("last_active_at"),
            "activity_preview": (_last_active.get(str(inst_dict.get("id") or "").strip()) or {}).get("title") or "",
            "channel": _channels.get(str(inst_dict.get("id") or "").strip(), ""),
        })

    await _ledger_fleet_action(
        action="list_agents",
        actor_id=actor_id,
        workspace_id=workspace_id,
        metadata={"count": len(agents)},
    )
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

    await _ledger_fleet_action(
        action="get_agent_activity",
        actor_id=actor_id,
        workspace_id=workspace_id,
        target_agent_id=agent_id,
        metadata={"event_count": len(events), "since": since},
    )
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

    Core tools (always on for every agent, never gated by tool_toggles) are
    returned separately under "core_tools" for read-only display — they
    aren't real toggles because there's nothing to turn off.
    """
    from server_modules import agent_registry_repository as repo
    from server_modules import skill_registry
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

    definitions = skill_registry.list_skill_definitions(workspace_id=workspace_id, include_disabled=True)
    tools: List[Dict[str, Any]] = []
    for d in definitions:
        # `id` is the canonical enforcement tool name (not skill_registry's
        # own hyphenated id) so this list's toggle state — and the PATCH the
        # frontend sends back using this same `id` — matches what
        # _specialist_tool_allowed() actually checks. See
        # skill_registry.enforcement_tool_name.
        enforcement_id = skill_registry.enforcement_tool_name(d.id)
        tools.append({
            "id": enforcement_id,
            "label": d.label,
            "description": d.description or "",
            "action_class": d.action_class,
            # Sage (operator) isn't gated by tool_toggles at all — every tool
            # is already available to it, so the toggle would be misleading.
            "enabled": True if is_master else bool(toggles.get(enforcement_id, False)),
        })

    core_tools = sorted(_core_direct_tool_names())
    return {"ok": True, "tools": tools, "core_tools": core_tools, "agent_id": agent_id, "is_master": is_master}


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
        gateway_binding = mc.get("gateway_binding")
        if gateway_binding is not None and not isinstance(gateway_binding, str):
            return {
                "ok": False,
                "error": "model_config gateway_binding must be a gateway id string.",
            }

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
            _next_label = requested_label
        if "telegram_first_contact_reply" in clean_patch:
            meta["telegram_first_contact_reply"] = bool(clean_patch["telegram_first_contact_reply"])
        if "preferred_gateway_id" in clean_patch:
            value = clean_patch["preferred_gateway_id"]
            if value is not None and not isinstance(value, str):
                return {"ok": False, "error": "preferred_gateway_id must be a gateway id string."}
            meta["preferred_gateway_id"] = str(value or "").strip()
        if "mandate" in clean_patch:
            # The owner-declared mandate: which tools (connector/MCP actions,
            # "{connector_id}.{action_id}") this agent's audience-tier callers
            # (end-customers over a channel) may trigger, on top of whatever
            # the tool catalog already marks audience_safe. Connector/MCP
            # actions have no catalog-level audience_safe flag at all — they
            # default to NOT audience_safe (fail-safe) until explicitly
            # listed here. Consulted by both the skills_service and
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
            if requested not in {"none", "gateway", "vps", "all"}:
                return {
                    "ok": False,
                    "error": "hardware_access must be one of: none, gateway, vps, all",
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
    return {"ok": True, "agent_id": agent_id, "applied": sorted(clean_patch.keys())}


async def fleet_message_agent(
    *,
    actor_id: str,
    workspace_id: str,
    tenant_id: str = "system",
    agent_id: str = "",
    message: str = "",
) -> Dict[str, Any]:
    """Enqueue a message for the target agent.

    The message is stored in the target agent's install_metadata.fleet_inbox.
    The target agent's next turn may read and process it.
    Reply routing back to the operator is handled by the fleet caller.
    """
    from server_modules import agent_registry_repository as repo

    if not str(agent_id or "").strip():
        return {"ok": False, "error": "agent_id is required"}
    if not str(message or "").strip():
        return {"ok": False, "error": "message is required"}

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
        inbox: List[Dict[str, Any]] = list(meta.get("fleet_inbox") or [])

        inbox.append({
            "from_agent_id": actor_id,
            "message": str(message).strip(),
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "message_id": f"fleet_msg_{len(inbox):06d}",
        })
        # Cap inbox at 20 messages
        meta["fleet_inbox"] = inbox[-20:]

        await repo.update_workspace_agent_install(
            agent_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            metadata=meta,
        )
    except Exception as exc:
        await _ledger_fleet_action(
            action="message_agent_failed",
            actor_id=actor_id,
            workspace_id=workspace_id,
            target_agent_id=agent_id,
            status="failed",
            metadata={"error": str(exc)[:200]},
        )
        return {"ok": False, "error": str(exc)}

    await _ledger_fleet_action(
        action="message_agent",
        actor_id=actor_id,
        workspace_id=workspace_id,
        target_agent_id=agent_id,
        metadata={"message_length": len(str(message))},
    )
    return {"ok": True, "agent_id": agent_id, "status": "enqueued"}


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

    await activity_ledger_service.append_activity_event(
        tenant_id="system",
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(actor_id or "").strip() or "unknown",
        install_id=agent_id,
        event_class="fleet_control",
        detail_level="audit_reference",
        action="agent_stopped",
        title=f"Agent stopped: {agent_id}",
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

    await activity_ledger_service.append_activity_event(
        tenant_id="system",
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(actor_id or "").strip() or "unknown",
        install_id=agent_id,
        event_class="fleet_control",
        detail_level="audit_reference",
        action="agent_resumed",
        title=f"Agent resumed: {agent_id}",
        summary=f"{actor_label or actor_id} resumed this agent.",
        status="executed",
        metadata={"agent_id": agent_id, "resumed_by_user_id": actor_id},
    )
    return {"ok": True, "agent_id": agent_id, "stopped": {"active": False}}


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
        tenant_id="system",
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(actor_id or "").strip() or "unknown",
        event_class="fleet_control",
        detail_level="audit_reference",
        action="workspace_stopped",
        title=f"All agents stopped in workspace {workspace_id}",
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
        tenant_id="system",
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(actor_id or "").strip() or "unknown",
        event_class="fleet_control",
        detail_level="audit_reference",
        action="workspace_resumed",
        title=f"All agents resumed in workspace {workspace_id}",
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
    the default instructions. `capability_preset` (knowledge | standard) seeds
    hardware/tools/model/subagents/context DEFAULTS; 'operator' is reserved
    (Sage-class) and not creatable via this flow. All fields remain overridable
    afterward except a knowledge agent's policy-locked hardware access.
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
    return {"ok": True, "agent_id": agent_id, "role": SPECIALIST_ROLE, "name": agent_label, "project_id": _project_id}


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
