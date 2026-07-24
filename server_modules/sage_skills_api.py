from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends

from server_modules.auth import enforce_workspace_access, workspace_tenant_id
from server_modules import mcp_registry_service
from server_modules import skill_registry
from server_modules import skills_service
from server_modules.installed_skills import skill_availability_state
from server_modules.secret_redaction_service import redact_text


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_skill_document(value: Any) -> str | None:
    text = _coerce_text(value)
    if not text:
        return None
    return redact_text(text).strip() or None


def _skill_status(item: Dict[str, Any]) -> str:
    return skill_availability_state(item)


def _status_label(status: str) -> str:
    labels = {
        "ready": "Ready",
        "needs_setup": "Needs setup",
        "unsupported_device": "Unsupported on this device",
        "disabled_policy": "Disabled by policy",
    }
    return labels.get(status, "Needs setup")


def _skill_reason(item: Dict[str, Any]) -> str | None:
    reasons = [
        _coerce_text(token)
        for token in list(item.get("availability_reasons") or [])
        if _coerce_text(token)
    ]
    if reasons:
        return "; ".join(reasons)
    missing_bins = [
        _coerce_text(token)
        for token in list(item.get("missing_bins") or [])
        if _coerce_text(token)
    ]
    if missing_bins:
        return f"Missing runtime dependencies: {', '.join(missing_bins)}"
    if not bool(item.get("enabled")):
        return "Disabled for this workspace."
    return None


def _skill_setup_requirement(item: Dict[str, Any]) -> str | None:
    if not bool(item.get("enabled")):
        return "Enable this skill for the workspace before Sage can use it."
    missing_bins = [
        _coerce_text(token)
        for token in list(item.get("missing_bins") or [])
        if _coerce_text(token)
    ]
    missing_env_vars = [
        _coerce_text(token)
        for token in list(item.get("missing_env_vars") or [])
        if _coerce_text(token)
    ]
    missing_packages = [
        _coerce_text(token)
        for token in list(item.get("missing_python_packages") or [])
        if _coerce_text(token)
    ]
    setup_bits: list[str] = []
    if missing_bins:
        setup_bits.append(f"Install: {', '.join(missing_bins)}")
    if missing_env_vars:
        setup_bits.append(f"Set environment variables: {', '.join(missing_env_vars)}")
    if missing_packages:
        setup_bits.append(f"Install Python packages: {', '.join(missing_packages)}")
    if setup_bits:
        return ". ".join(setup_bits) + "."
    # skill_registry.SkillDefinition flattens missing_bins/missing_env_vars/
    # missing_python_packages into a single unavailable_reason string rather
    # than preserving them as structured lists (a real, disclosed gap from
    # the pre-unification payload — see docs/design/audit-skills.md §3 item
    # 3's catalog-reconciliation note). Fall back to that flattened reason
    # so a catalog-sourced item still gets SOME actionable setup text
    # instead of silently going blank.
    if not bool(item.get("available", True)):
        reasons = [_coerce_text(token) for token in list(item.get("availability_reasons") or []) if _coerce_text(token)]
        if reasons:
            return "; ".join(reasons)
    return None


def _skill_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    status = _skill_status(item)
    supported_os = [token for token in list(item.get("supported_os") or []) if _coerce_text(token)]
    description = _coerce_text(item.get("description"))
    what_it_does = _coerce_text(item.get("what_it_does")) or description
    runtime_metadata = item.get("runtime_metadata") if isinstance(item.get("runtime_metadata"), dict) else {}
    return {
        "id": _coerce_text(item.get("id")),
        "name": _coerce_text(item.get("name")) or "Skill",
        "description": description or None,
        "what_it_does": what_it_does or None,
        "enabled": bool(item.get("enabled")),
        "available": bool(item.get("available")),
        "active_now": status == "ready",
        "status": status,
        "status_label": _status_label(status),
        "reason": _skill_reason(item),
        "setup_requirement": _skill_setup_requirement(item),
        "source": _coerce_text(item.get("source")) or None,
        "required_bins": [token for token in list(item.get("required_bins") or []) if _coerce_text(token)],
        "missing_bins": [token for token in list(item.get("missing_bins") or []) if _coerce_text(token)],
        "required_env_vars": [token for token in list(item.get("required_env_vars") or []) if _coerce_text(token)],
        "missing_env_vars": [token for token in list(item.get("missing_env_vars") or []) if _coerce_text(token)],
        "required_python_packages": [token for token in list(item.get("required_python_packages") or []) if _coerce_text(token)],
        "missing_python_packages": [token for token in list(item.get("missing_python_packages") or []) if _coerce_text(token)],
        "supported_os": supported_os,
        "tools": [token for token in list(item.get("tools") or []) if _coerce_text(token)],
        "slash_commands": [token for token in list(item.get("slash_commands") or []) if _coerce_text(token)],
        "permission_label": _coerce_text(runtime_metadata.get("permission_label")) or None,
        "action_class": _coerce_text(runtime_metadata.get("action_class")) or None,
        "requires_approval": bool(runtime_metadata.get("requires_approval")),
        "execution_mode": _coerce_text(runtime_metadata.get("execution_mode")) or None,
        "skill_body": _safe_skill_document(item.get("skill_body")),
        "readme": _safe_skill_document(item.get("readme")),
        "allowed_runtime_modes": [
            token for token in list(runtime_metadata.get("allowed_runtime_modes") or []) if _coerce_text(token)
        ],
        # curated/curated_rank are gone with the hardcoded curated pack
        # (docs/design/audit-skills.md §2.2.C — 1Password/Apple Notes/Apple
        # Reminders/tmux had zero execution implementation anywhere and were
        # never distinguishable from a real skill in this payload). Every
        # item here now comes from skill_registry.list_skill_definitions,
        # the same catalog skill_invoke dispatches against.
        "curated": False,
        "curated_rank": None,
    }


def _capability_record(
    *,
    capability_id: str,
    label: str,
    source: str,
    capability_type: str,
    status: str,
    tool_id: str | None = None,
    connector_id: str | None = None,
    skill_id: str | None = None,
    mcp_server_id: str | None = None,
    mcp_tool_id: str | None = None,
    action_class: str | None = None,
    risk_level: str | None = None,
    requires_approval: bool = False,
    runtime_requirement: str | None = None,
    setup_action: str | None = None,
    description: str | None = None,
) -> Dict[str, Any]:
    normalized_status = _coerce_text(status) or "needs_setup"
    return {
        "id": _coerce_text(capability_id) or _coerce_text(tool_id) or _coerce_text(label),
        "label": _coerce_text(label) or _coerce_text(capability_id) or "Capability",
        "description": _coerce_text(description) or None,
        "type": _coerce_text(capability_type) or "tool",
        "source": _coerce_text(source) or "workspace",
        "status": normalized_status,
        "active_now": normalized_status == "ready",
        "tool_id": _coerce_text(tool_id) or None,
        "connector_id": _coerce_text(connector_id) or None,
        "skill_id": _coerce_text(skill_id) or None,
        "mcp_server_id": _coerce_text(mcp_server_id) or None,
        "mcp_tool_id": _coerce_text(mcp_tool_id) or None,
        "action_class": _coerce_text(action_class) or None,
        "risk_level": _coerce_text(risk_level) or None,
        "requires_approval": bool(requires_approval),
        "runtime_requirement": _coerce_text(runtime_requirement) or None,
        "setup_action": _coerce_text(setup_action) or None,
    }


def _builtin_capability_records() -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    for tool in skills_service.build_builtin_direct_chat_tools():
        if not isinstance(tool, dict):
            continue
        name = _coerce_text(tool.get("name"))
        if not name:
            continue
        connector_id = _coerce_text(tool.get("connector_id"))
        capability_type = "memory" if connector_id == "memory" else "tool"
        action_class = _coerce_text(tool.get("action_class"))
        requires_approval = bool(tool.get("requires_approval"))
        status = "approval_required" if requires_approval else "ready"
        records.append(
            _capability_record(
                capability_id=_coerce_text(tool.get("capability_id")) or name,
                label=_coerce_text(tool.get("label")) or name,
                description=_coerce_text(tool.get("description")),
                source="builtin",
                capability_type=capability_type,
                status=status,
                tool_id=name,
                connector_id=connector_id or None,
                action_class=action_class or None,
                risk_level=_coerce_text(tool.get("risk_level")) or None,
                requires_approval=requires_approval,
                runtime_requirement="cloud",
                setup_action=None,
            )
        )
    return records


def _skill_capability_records(skill_items: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """One capability record per catalog skill, all pointing at the single
    real Level-2 tool (skill_invoke) rather than a per-skill tool name.

    Before this, a skill with no explicit `tools:` list (true of nearly
    every entry — the curated pack, and every skill_registry SkillDefinition
    that isn't backed by its own bespoke LLM tool) got `tool_id=None` here,
    which sage_instruction_compiler_service.build_model_capability_manifest
    silently drops (`if not tool_id: continue`) — so those skills could
    NEVER reach the live "## Callable Tools" prompt text, independent of
    which catalog fed this function. Routing every record through
    skill_invoke fixes that: every skill_id is now a real, callable
    (`tool_id`, `skill_id`) pair, and the description spells out the exact
    skill_id argument the model needs to pass.
    """
    records: list[Dict[str, Any]] = []
    for skill in skill_items:
        skill_id = _coerce_text(skill.get("id"))
        if not skill_id:
            continue
        status = _coerce_text(skill.get("status")) or "needs_setup"
        setup_action = "open_skills" if status != "ready" else None
        base_description = _coerce_text(skill.get("description")) or _coerce_text(skill.get("what_it_does"))
        invoke_hint = f'Call skill_invoke with skill_id="{skill_id}" to run it.'
        description = f"{base_description} {invoke_hint}".strip() if base_description else invoke_hint
        records.append(
            _capability_record(
                capability_id=f"skill:{skill_id}",
                label=_coerce_text(skill.get("name")) or skill_id,
                description=description,
                source=_coerce_text(skill.get("source")) or "skill",
                capability_type="skill",
                status=status,
                tool_id="skill_invoke",
                skill_id=skill_id,
                action_class=_coerce_text(skill.get("action_class")) or None,
                requires_approval=bool(skill.get("requires_approval")),
                runtime_requirement=_coerce_text(skill.get("execution_mode")) or None,
                setup_action=setup_action,
            )
        )
    return records


def _mcp_capability_records(workspace_id: str) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    try:
        servers = mcp_registry_service.list_workspace_mcp_servers(workspace_id)
    except Exception:
        servers = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        server_id = _coerce_text(server.get("server_id") or server.get("id"))
        server_enabled = server.get("enabled") is not False
        for tool in list(server.get("tools") or []):
            if not isinstance(tool, dict):
                continue
            tool_name = _coerce_text(tool.get("name"))
            if not tool_name:
                continue
            approved = bool(tool.get("approved"))
            status = "ready" if server_enabled and approved else "needs_approval"
            records.append(
                _capability_record(
                    capability_id=f"mcp:{server_id}:{tool_name}",
                    label=_coerce_text(tool.get("label")) or tool_name,
                    description=_coerce_text(tool.get("description")),
                    source="mcp",
                    capability_type="mcp",
                    status=status,
                    tool_id=f"mcp__{server_id}__{tool_name}",
                    connector_id="mcp",
                    mcp_server_id=server_id or None,
                    mcp_tool_id=tool_name,
                    action_class=_coerce_text(tool.get("action_class")) or "read",
                    risk_level=_coerce_text(tool.get("risk_level")) or None,
                    requires_approval=bool(tool.get("requires_approval")),
                    runtime_requirement="mcp_server",
                    setup_action=None if approved else "approve_mcp_tool",
                )
            )
    return records


def _capability_summary(items: list[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "total_count": len(items),
        "ready_count": len([item for item in items if item.get("status") == "ready"]),
        "needs_setup_count": len([item for item in items if item.get("status") in {"needs_setup", "unsupported_device", "disabled_policy"}]),
        "needs_approval_count": len([item for item in items if item.get("status") in {"approval_required", "needs_approval"}]),
        "memory_count": len([item for item in items if item.get("type") == "memory"]),
        "skill_count": len([item for item in items if item.get("type") == "skill"]),
        "mcp_count": len([item for item in items if item.get("type") == "mcp"]),
    }


def _health_checks(items: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    has_memory = any(item.get("type") == "memory" and item.get("status") in {"ready", "approval_required"} for item in items)
    has_skill_scanner = any(item.get("type") == "skill" for item in items)
    has_mcp = any(item.get("type") == "mcp" for item in items)
    return [
        {"id": "model_route", "label": "Model route", "status": "ready", "surface": "sage"},
        {"id": "memory_retrieval", "label": "Memory retrieval", "status": "ready" if has_memory else "needs_setup", "surface": "sage"},
        {"id": "memory_write", "label": "Memory writes", "status": "allowed" if has_memory else "needs_setup", "surface": "sage"},
        {"id": "skill_scanner", "label": "Skill scanner", "status": "ready" if has_skill_scanner else "needs_setup", "surface": "skills"},
        {"id": "mcp_endpoint_safety", "label": "MCP endpoint safety", "status": "ready" if has_mcp else "available", "surface": "mcp"},
        {"id": "local_companion", "label": "Local companion", "status": "setup_required", "surface": "runtime"},
        {"id": "billing_metering", "label": "Action and credit metering", "status": "ready", "surface": "billing"},
    ]


def build_sage_capabilities_payload(*, workspace_id: str, tenant_id: str) -> Dict[str, Any]:
    skills_payload = _build_sage_skills_payload(workspace_id=workspace_id, tenant_id=tenant_id)
    skill_items = [item for item in list(skills_payload.get("items") or []) if isinstance(item, dict)]
    items = [
        *_builtin_capability_records(),
        *_skill_capability_records(skill_items),
        *_mcp_capability_records(workspace_id),
    ]
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "version": 1,
        "items": items,
        "summary": _capability_summary(items),
        "health_checks": _health_checks(items),
    }


def _skill_definition_body(definition: "skill_registry.SkillDefinition") -> str | None:
    """Level-2 content for a filesystem-backed skill (Tools-tab detail view
    only — never fed into the Level-1 manifest text). None for a purely
    hardcoded SkillDefinition with no on-disk SKILL.md (e.g. email-access,
    calendar-access, the fleet-management skills)."""
    if not definition.path:
        return None
    try:
        from pathlib import Path as _Path

        skill_md = _Path(definition.path) / "SKILL.md"
        if skill_md.exists():
            return skill_md.read_text(encoding="utf-8")
    except Exception:
        pass
    return None


def _skill_definition_readme(definition: "skill_registry.SkillDefinition", body: str | None) -> str | None:
    """A dedicated README.md if the skill directory has one; otherwise the
    same SKILL.md body used for skill_body (matching installed_skills.
    list_installed_skills' own README-or-SKILL.md fallback)."""
    if definition.path:
        try:
            from pathlib import Path as _Path

            readme_md = _Path(definition.path) / "README.md"
            if readme_md.exists():
                return readme_md.read_text(encoding="utf-8")
        except Exception:
            pass
    return body


def _skill_definition_to_item(definition: "skill_registry.SkillDefinition") -> Dict[str, Any]:
    """Adapt a skill_registry.SkillDefinition (the unified catalog — merges
    the built-in skills, the filesystem-scanned installed_skills.py roots,
    and workspace MCP skill entries, see skill_registry._skill_registry_map)
    into the dict shape _skill_payload already knows how to render. This is
    the seam docs/design/audit-skills.md §3 item 3 calls for: the Tools tab
    (this payload) and the model's live "## Callable Tools" manifest
    (_skill_capability_records, below) now both read the SAME catalog
    skill_invoke dispatches against — no more silently-different lists."""
    availability_reasons: list[str] = []
    if not definition.available:
        availability_reasons.append(
            _coerce_text(definition.unavailable_reason)
            or f"{definition.label} is not available in this environment."
        )
    body = _skill_definition_body(definition)
    return {
        "id": definition.id,
        "name": definition.label,
        "description": definition.description,
        "enabled": bool(definition.enabled),
        "available": bool(definition.available),
        "supported_os": [],
        "availability_reasons": availability_reasons,
        "source": definition.source,
        "tools": [],
        "slash_commands": [],
        "required_bins": [],
        "missing_bins": [],
        "required_env_vars": [],
        "missing_env_vars": [],
        "required_python_packages": [],
        "missing_python_packages": [],
        "runtime_metadata": {
            "skill_class": definition.skill_class,
            "permission_label": definition.permission_label,
            "execution_mode": definition.execution_mode,
            "action_class": definition.action_class,
            "connector_scopes": list(definition.connector_scopes),
            "trigger_terms": list(definition.trigger_terms),
            "allowed_runtime_modes": list(definition.allowed_runtime_modes),
            "requires_approval": bool(definition.requires_approval),
            "execution_adapter": definition.execution_adapter or "",
        },
        "skill_body": body,
        "readme": _skill_definition_readme(definition, body),
    }


def _build_sage_skills_payload(*, workspace_id: str, tenant_id: str) -> Dict[str, Any]:
    # include_disabled=True: this feeds the human-facing Tools/Skills tab (and,
    # via build_sage_capabilities_payload below, the live model manifest) —
    # a disabled or not-yet-available skill still needs to be LISTED with an
    # honest status/reason so the owner can enable or fix it, matching this
    # payload's pre-unification behavior. skill_registry.list_skill_definitions
    # defaults to include_disabled=False because ITS default caller
    # (execute_skill's dispatch path, via get_skill_definition) must never
    # silently resolve a disabled skill — that safety property is unaffected
    # here since disabled/unavailable items are filtered back out downstream,
    # in build_model_capability_manifest's status allow-list (only "ready"/
    # "approval_required" reach the live prompt).
    definitions = skill_registry.list_skill_definitions(workspace_id=workspace_id, include_disabled=True)
    items = [_skill_payload(_skill_definition_to_item(definition)) for definition in definitions]
    items.sort(key=lambda item: (item.get("name") or "").lower())
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "items": items,
        "summary": {
            "total_count": len(items),
            "ready_count": len([item for item in items if item["status"] == "ready"]),
            "needs_setup_count": len([item for item in items if item["status"] == "needs_setup"]),
            "unsupported_count": len([item for item in items if item["status"] == "unsupported_device"]),
            "disabled_count": len([item for item in items if item["status"] == "disabled_policy"]),
        },
    }


def register_sage_skills_routes(app) -> None:
    import server as _server

    module_globals = globals()
    for key, value in _server.__dict__.items():
        if key not in module_globals:
            module_globals[key] = value

    member_dependency = getattr(_server, "require_api_key")

    @app.get("/api/sage-skills", dependencies=[Depends(member_dependency)])
    async def list_workspace_sage_skills(
        workspace_id: Optional[str] = None,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        return _build_sage_skills_payload(workspace_id=resolved_workspace_id, tenant_id=tenant_id)

    @app.get("/api/sage-capabilities", dependencies=[Depends(member_dependency)])
    async def list_workspace_sage_capabilities(
        workspace_id: Optional[str] = None,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        return build_sage_capabilities_payload(workspace_id=resolved_workspace_id, tenant_id=tenant_id)
