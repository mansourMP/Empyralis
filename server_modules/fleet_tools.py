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
    "purpose_preset",
}
_VALID_MODEL_MODES = {"platform_credits", "byok_api", "cli_subscription", "local"}
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


def _resolve_runtime_target(inst: Dict[str, Any]) -> str:
    """Resolve human-readable runtime target for an agent install.

    Returns one of:
      - "cloud" — agent runs on Empyralis cloud infrastructure
      - "gateway:<name>" — agent runs on user-owned gateway hardware
      - "vps:<name>" — agent runs on user-provisioned VPS
      - "unknown" — no runtime target information available
    """
    target = str(inst.get("default_execution_target") or "").strip().lower()
    runtime_id = str(inst.get("runtime_id") or "").strip()
    machine_id = str(inst.get("machine_id") or "").strip()
    runtime_class = str(inst.get("runtime_class") or "").strip().lower()
    placement = str(inst.get("placement_mode") or "").strip().lower()

    if target in ("cloud", "empyralis-cloud"):
        return "cloud"
    if target in ("gateway", "local_gateway", "desktop_companion"):
        label = str(inst.get("runtime_profile_label") or machine_id or runtime_id or "").strip()
        return f"gateway:{label}" if label else "gateway"
    if target in ("vps", "self_hosted", "self_hosted_business_node"):
        label = str(inst.get("runtime_profile_label") or runtime_id or "").strip()
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
) -> tuple[str, Optional[str]]:
    """Resolve hardware status and last heartbeat for an agent install.

    Returns (status, last_heartbeat_iso):
      - "online" — heartbeat received within lease window
      - "offline" — no recent heartbeat, agent may be down
      - "unknown" — no heartbeat tracking for this agent type
    """
    runtime_id = str(inst.get("runtime_id") or "").strip()
    machine_id = str(inst.get("machine_id") or "").strip()
    agent_id = str(inst.get("id") or "").strip()

    # Check heartbeats by runtime_id first, then agent_id
    hb = heartbeats.get(runtime_id) or heartbeats.get(agent_id) or heartbeats.get(machine_id)

    if hb and isinstance(hb, dict):
        status = "online" if bool(hb.get("online", False)) else "offline"
        last_hb = str(hb.get("last_heartbeat_at") or hb.get("last_seen_at") or "").strip() or None
        return status, last_hb

    # Cloud agents are always "online" (platform manages them)
    target = str(inst.get("default_execution_target") or "").strip().lower()
    if target in ("cloud", "empyralis-cloud"):
        return "online", None

    # Agents without a runtime_id can't have heartbeat tracking yet
    if not runtime_id and not machine_id:
        return "unknown", None

    return "offline", None


async def _fetch_latest_heartbeats(workspace_id: str) -> Dict[str, dict]:
    """Fetch latest heartbeat for each runtime in the workspace."""
    try:
        from server_modules import control_plane_repository as cpr

        pool = await cpr.ensure_control_plane_schema()
        if pool is None:
            return {}

        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (runtime_profile_id)
                runtime_profile_id,
                online,
                last_heartbeat_at,
                last_seen_at
            FROM runtime_heartbeats
            WHERE workspace_id = $1
            ORDER BY runtime_profile_id, last_heartbeat_at DESC
            """,
            str(workspace_id or "").strip(),
        )
        result: Dict[str, dict] = {}
        for r in (rows or []):
            rid = str(r["runtime_profile_id"] or "").strip()
            if rid:
                result[rid] = {
                    "online": bool(r["online"]),
                    "last_heartbeat_at": str(r["last_heartbeat_at"] or "").strip() or None,
                    "last_seen_at": str(r["last_seen_at"] or "").strip() or None,
                }
        return result
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

    agents = []
    for inst in (installs or []):
        inst_dict = dict(inst) if isinstance(inst, dict) else {}
        role = resolve_agent_role(inst_dict)

        # ── Phase U3: runtime target + hardware status ──
        _runtime_target = _resolve_runtime_target(inst_dict)
        _hardware_status, _last_heartbeat = _resolve_hardware_status(
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
            # Phase U3: placement visibility
            "runtime_target": _runtime_target,
            "hardware_status": _hardware_status,
            "last_heartbeat": _last_heartbeat,
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

    Queries the activity_ledger_events table for the given actor_id.
    Redacts payloads — only returns event metadata, never raw content.
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
            SELECT id, action, event_class, title, status, created_at
            FROM activity_ledger_events
            WHERE workspace_id = $1
              AND actor_id = $2
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


async def fleet_get_agent_tools(
    *,
    workspace_id: str,
    agent_id: str,
) -> Dict[str, Any]:
    """Return the tool manifest for a specific agent.

    Resolves the agent install metadata to find enabled_tools, then
    looks up each skill definition from the skill registry. Returns
    a flat list of {id, label, description, action_class} entries.
    """
    from server_modules import agent_registry_repository as repo
    from server_modules import skill_registry

    try:
        installs = await repo.list_workspace_agent_installs(
            tenant_id="system",
            workspace_id=workspace_id,
            include_master=True,
        )
    except Exception:
        installs = []

    # Find the agent install
    inst: Dict[str, Any] = {}
    for i in (installs or []):
        d = dict(i) if isinstance(i, dict) else {}
        if str(d.get("id") or "") == agent_id:
            inst = d
            break

    if not inst:
        # Agent not found — return empty, not an error
        return {"ok": True, "tools": [], "agent_id": agent_id}

    # Read enabled_tools from metadata
    meta = inst.get("meta") or inst.get("metadata") or {}
    if isinstance(meta, str):
        import json as _json
        try:
            meta = _json.loads(meta)
        except Exception:
            meta = {}
    enabled_ids: List[str] = []
    raw = meta.get("enabled_tools") or []
    if isinstance(raw, list):
        enabled_ids = [str(t).strip() for t in raw if str(t).strip()]

    # Resolve each tool ID to a skill definition
    definitions = skill_registry.list_skill_definitions(workspace_id=workspace_id)
    tools: List[Dict[str, Any]] = []
    for sid in enabled_ids:
        match = None
        for d in definitions:
            if d.id == sid:
                match = d
                break
        if match:
            tools.append({
                "id": match.id,
                "label": match.label,
                "description": match.description or "",
                "action_class": match.action_class,
            })
        else:
            # Tool ID referenced but not in registry — include as unknown
            tools.append({
                "id": sid,
                "label": sid,
                "description": "This tool is referenced but not in the skill registry.",
                "action_class": "unknown",
            })

    return {"ok": True, "tools": tools, "agent_id": agent_id}


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

    # Validate model_config mode if present
    if "model_config" in clean_patch:
        mc = dict(clean_patch.get("model_config") or {})
        mode = str(mc.get("mode") or "").strip()
        if mode and mode not in _VALID_MODEL_MODES:
            return {
                "ok": False,
                "error": f"Invalid model_config mode: {mode}. Must be one of: {', '.join(sorted(_VALID_MODEL_MODES))}",
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
        if "hardware_access" in clean_patch:
            # Phase 5B: a knowledge agent's hardware access is policy-locked. It
            # can only be granted by changing the capability preset — a direct
            # grant here is refused and ledgered.
            from server_modules import capability_presets as _caps_cfg

            _granting_hw = bool(clean_patch["hardware_access"]) and str(clean_patch["hardware_access"]).strip().lower() not in {"none", "false", "0"}
            if _granting_hw and _caps_cfg.hardware_is_locked(bundle_dict):
                await _ledger_fleet_action(
                    action="hardware_grant_denied",
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    target_agent_id=agent_id,
                    status="blocked",
                    metadata={
                        "reason": "knowledge_agent_hardware_locked",
                        "capability_preset": "knowledge",
                        "requested_hardware_access": clean_patch["hardware_access"],
                    },
                )
                return {
                    "ok": False,
                    "error": (
                        "This is a knowledge agent — hardware access is policy-locked. "
                        "Change its capability preset (not just this field) to grant hardware."
                    ),
                }
            meta["hardware_access"] = bool(clean_patch["hardware_access"])
        if "model_config" in clean_patch:
            meta["model_config"] = dict(clean_patch["model_config"] or {})

        # Persist via update
        await repo.update_workspace_agent_install(
            agent_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            metadata=meta,
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

    agent_label = str(name or "").strip() or "Fleet Specialist"
    meta = seed_specialist_metadata()
    meta["fleet_created_by"] = actor_id
    meta["fleet_created_at"] = datetime.now(timezone.utc).isoformat()
    # Apply capability-preset defaults into metadata (capability_preset,
    # model_tier, context_policy, hardware lock flag, subagents default).
    meta.update(dict(_preset_defaults.get("metadata") or {}))
    meta["subagents_enabled"] = bool(_preset_defaults.get("subagents_enabled"))
    meta["hardware_access"] = str(_preset_defaults.get("hardware_access") or "none")
    if _preset_defaults.get("enabled_tools") is not None:
        meta["enabled_tools"] = list(_preset_defaults["enabled_tools"])

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
        )
        if not result:
            return {"ok": False, "error": "Failed to create agent install — check agent definition exists"}

        agent_id = str(result.get("id") or "").strip()

        # Assign to the chosen project (agents otherwise land in the default
        # project). Best-effort: a bad project id shouldn't fail creation.
        _project_id = str(project_id or "").strip()
        if agent_id and _project_id:
            try:
                from server_modules import projects_repository as _projects
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
    return {"ok": True, "agent_id": agent_id, "role": SPECIALIST_ROLE}


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
) -> Dict[str, Any]:
    """Schedule a future task for this agent.

    The agent will be woken at *when* with *instruction* as its prompt.
    Results are delivered to the agent's bound channel.

    *when* can be:
      - ``"in 2 minutes"``, ``"in 30 min"`` — relative time
      - ``"in 1 hour"`` — relative hours
      - ``"2026-07-04T09:00:00Z"`` — ISO-8601 UTC datetime

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
