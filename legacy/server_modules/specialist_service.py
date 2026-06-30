from __future__ import annotations

from typing import Any, Dict, List, Optional

from server_modules import activity_ledger_service, mcp_registry_service, runtime_attachment_service, unified_memory_service
from server_modules import agent_registry_repository
from server_modules import skill_registry
from server_modules.agent_specialist_repository import manifest_from_install_bundle


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return list(value or []) if isinstance(value, list) else []


def _token(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    return token or None


def _string_list(value: Any) -> List[str]:
    items: List[str] = []
    for item in _list(value):
        token = str(item or "").strip()
        if token:
            items.append(token)
    return items


def _bool_default_true(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("enabled", True))
    if value is None:
        return True
    return bool(value)


def _project_selected_attachment(value: Any) -> Optional[Dict[str, Any]]:
    payload = _dict(value)
    if not payload:
        return None
    return {
        "attachment_id": _token(payload.get("attachment_id")),
        "attachment_kind": _token(payload.get("attachment_kind")),
        "label": _token(payload.get("label")),
        "runtime_id": _token(payload.get("runtime_id")),
        "machine_id": _token(payload.get("machine_id")),
        "runtime_class": _token(payload.get("runtime_class")),
        "online": bool(payload.get("online")),
        "healthy": bool(payload.get("healthy")),
        "control_state": _token(payload.get("control_state")),
    }


def _manifest_identity_payload(install: Dict[str, Any]) -> Dict[str, Any]:
    manifest = manifest_from_install_bundle(install or {})
    if manifest is None:
        definition = _dict(install.get("agent_definition"))
        return {
            "name": _token(install.get("label")) or _token(definition.get("name")) or "Specialist",
            "role": None,
            "archetype": None,
            "summary": _token(definition.get("description")),
        }
    identity = manifest.identity
    return {
        "name": _token(identity.name),
        "role": _token(identity.role),
        "archetype": _token(identity.archetype),
        "summary": _token(identity.summary),
    }


def _skill_scope_payload(install: Dict[str, Any], *, workspace_id: str) -> Dict[str, Any]:
    manifest = manifest_from_install_bundle(install or {})
    if manifest is None:
        return {"enabled_skills": [], "count": 0, "definitions": []}
    enabled_skills = sorted(
        str(item.id).strip()
        for item in list(manifest.skills or [])
        if str(getattr(item, "id", "") or "").strip() and bool(getattr(item, "enabled", True))
    )
    definitions: List[Dict[str, Any]] = []
    for skill_id in enabled_skills:
        definition = skill_registry.get_skill_definition(skill_id, workspace_id=workspace_id, include_disabled=True)
        if definition is None:
            definitions.append({"id": skill_id, "label": skill_id, "available": False})
            continue
        definitions.append(
            {
                "id": definition.id,
                "label": definition.label,
                "skill_class": definition.skill_class,
                "action_class": definition.action_class,
                "execution_mode": definition.execution_mode,
                "enabled": definition.enabled,
                "available": True,
            }
        )
    return {
        "enabled_skills": enabled_skills,
        "count": len(enabled_skills),
        "definitions": definitions,
    }


def _mcp_scope_payload(install: Dict[str, Any], *, workspace_id: str) -> Dict[str, Any]:
    workspace_token = _token(workspace_id)
    manifest = manifest_from_install_bundle(install or {})
    if manifest is None or workspace_token is None:
        return {
            "advanced_only": True,
            "registered_servers": [],
            "server_count": 0,
            "bound_skill_ids": [],
            "bound_servers": [],
        }
    bound_skill_ids = sorted(
        skill_id
        for skill_id in (
            str(item.id).strip()
            for item in list(manifest.skills or [])
            if str(getattr(item, "id", "") or "").strip() and bool(getattr(item, "enabled", True))
        )
        if mcp_registry_service.parse_mcp_skill_id(skill_id) is not None
    )
    registered_servers = mcp_registry_service.list_workspace_mcp_servers(workspace_token)
    bound_server_ids = {
        parsed["server_id"]
        for parsed in (
            mcp_registry_service.parse_mcp_skill_id(skill_id)
            for skill_id in bound_skill_ids
        )
        if isinstance(parsed, dict)
    }
    return {
        "advanced_only": True,
        "registered_servers": [
            {
                "id": _token(server.get("id")),
                "label": _token(server.get("label")) or _token(server.get("id")),
                "transport": _token(server.get("transport")),
                "enabled": bool(server.get("enabled", True)),
                "tool_count": int(server.get("tool_count") or 0),
                "last_synced_at": _token(server.get("last_synced_at")),
                "skill_ids": _string_list(server.get("skill_ids")),
            }
            for server in registered_servers
        ],
        "server_count": len(registered_servers),
        "bound_skill_ids": bound_skill_ids,
        "bound_servers": sorted(bound_server_ids),
        "policy": "MCP stays advanced-only and optional. Specialists can use registered MCP tools as scoped live data without making MCP part of the normal user path.",
    }


def _tool_scope_payload(install: Dict[str, Any]) -> Dict[str, Any]:
    toggles = _dict(install.get("tool_toggles"))
    folder_grants = [
        str(item or "").strip()
        for item in _list(install.get("folder_grants"))
        if str(item or "").strip()
    ]
    enabled_tools = sorted(
        key for key, enabled in toggles.items()
        if str(key or "").strip() and bool(enabled)
    )
    blocked_tools = sorted(
        key for key, enabled in toggles.items()
        if str(key or "").strip() and not bool(enabled)
    )
    return {
        "enabled_tools": enabled_tools,
        "blocked_tools": blocked_tools,
        "folder_grants": folder_grants,
        "policy": "Tools stay install-scoped and brokered. Specialist power is limited by policy, not downgraded by role.",
    }


def _connector_scope_payload(install: Dict[str, Any]) -> Dict[str, Any]:
    bindings = _dict(install.get("connector_bindings"))
    runtime_profile = _dict(install.get("runtime_profile"))
    binding_details: Dict[str, Any] = {}
    enabled_connectors: List[str] = []
    for connector_key, binding in sorted(bindings.items()):
        token = str(connector_key or "").strip()
        if not token:
            continue
        payload = _dict(binding)
        enabled = _bool_default_true(binding)
        projected = {
            "enabled": enabled,
        }
        if payload:
            projected.update(
                {
                    "scope": _token(payload.get("scope")),
                    "binding_ref": _token(payload.get("credential_id") or payload.get("binding_ref") or payload.get("account_id")),
                }
            )
        elif binding is not None and not isinstance(binding, bool):
            projected["binding_ref"] = _token(binding)
        binding_details[token] = {key: value for key, value in projected.items() if value is not None}
        if enabled:
            enabled_connectors.append(token)
    return {
        "enabled_connectors": enabled_connectors,
        "binding_count": len(binding_details),
        "allowed_connector_scopes": _string_list(runtime_profile.get("allowed_connector_scopes")),
        "bindings": binding_details,
        "policy": "Connectors remain install-scoped and runtime-bound. No specialist receives cross-workspace or captain connector access by default.",
    }


def _memory_scope_payload(memory_payload: Dict[str, Any]) -> Dict[str, Any]:
    layers = _dict(memory_payload.get("layers"))
    accessible_layers: List[str] = []
    summary_only_layers: List[str] = []
    layer_summaries: Dict[str, Any] = {}
    for layer_id, raw_layer in layers.items():
        token = str(layer_id or "").strip()
        layer = _dict(raw_layer)
        if not token:
            continue
        if bool(layer.get("accessible")):
            accessible_layers.append(token)
        else:
            summary_only_layers.append(token)
        summary = _dict(layer.get("summary"))
        layer_summaries[token] = {
            "accessible": bool(layer.get("accessible")),
            "scope": _token(layer.get("scope")),
            "policy": _token(summary.get("policy")),
            "count": summary.get("count"),
            "entry_count": summary.get("entry_count"),
            "local_sources": summary.get("local_sources"),
        }
    return {
        "audience": "specialist",
        "accessible_layers": accessible_layers,
        "summary_only_layers": summary_only_layers,
        "summary": _dict(memory_payload.get("summary")),
        "boundary_map": _dict(memory_payload.get("boundary_map")),
        "layer_summaries": layer_summaries,
        "inherits_sage_private_memory_by_default": False,
        "inherits_other_specialist_memory_by_default": False,
        "policy": "Specialists receive install-scoped memory by default. Captain/private memory must be brokered explicitly.",
    }


def _recent_artifacts_payload(timeline_payload: Dict[str, Any]) -> Dict[str, Any]:
    items = _list(timeline_payload.get("items"))
    artifacts: List[Dict[str, Any]] = []
    seen: set[tuple[Optional[str], Optional[str], Optional[str]]] = set()
    for item in items:
        payload = _dict(item)
        for raw_artifact in _list(payload.get("artifacts")):
            artifact = _dict(raw_artifact)
            key = (
                _token(artifact.get("id")),
                _token(artifact.get("path")),
                _token(artifact.get("name")),
            )
            if key in seen:
                continue
            seen.add(key)
            artifacts.append(
                {
                    "id": _token(artifact.get("id")),
                    "name": _token(artifact.get("name")),
                    "kind": _token(artifact.get("kind")),
                    "path": _token(artifact.get("path")),
                    "preview_path": _token(artifact.get("preview_path")),
                    "review_required": bool(artifact.get("review_required")),
                    "event_id": _token(payload.get("id")),
                    "ts": _token(payload.get("ts")),
                }
            )
            if len(artifacts) >= 8:
                break
        if len(artifacts) >= 8:
            break
    return {
        "recent_artifacts": artifacts,
        "review_required_count": int(_dict(timeline_payload.get("summary")).get("review_required_count") or 0),
    }


def _specialist_mode_payload(install: Dict[str, Any]) -> Dict[str, Any]:
    mode = agent_registry_repository.normalize_specialist_mode(
        install.get("specialist_mode") or _dict(install.get("metadata")).get("specialist_mode"),
        default=agent_registry_repository.DEFAULT_SPECIALIST_MODE,
        strict=False,
    ) or agent_registry_repository.DEFAULT_SPECIALIST_MODE
    contract = agent_registry_repository.specialist_mode_contract(mode) or {}
    return {
        **contract,
        "shared_board_access_posture": "not_modeled_yet",
    }


def _runtime_policy_payload(
    install: Dict[str, Any],
    *,
    runtime_plan: Optional[Dict[str, Any]],
    runtime_error: Optional[runtime_attachment_service.RuntimeAttachmentSelectionError],
) -> Dict[str, Any]:
    runtime_profile = _dict(install.get("runtime_profile"))
    base = {
        "runtime_mode": _token(install.get("runtime_mode")) or "hosted_secure",
        "runtime_profile": {
            "id": _token(runtime_profile.get("id")) or _token(install.get("runtime_profile_id")),
            "label": _token(runtime_profile.get("label") or runtime_profile.get("slug")),
            "runtime_class": _token(runtime_profile.get("runtime_class")),
            "placement_mode": _token(runtime_profile.get("placement_mode")),
            "supported_capabilities": _string_list(runtime_profile.get("supported_capabilities")),
            "allowed_connector_scopes": _string_list(runtime_profile.get("allowed_connector_scopes")),
        },
        "same_runtime_capability_as_platform": True,
        "power_limited_by": [
            "runtime_policy",
            "memory_scope",
            "connector_scope",
            "tool_scope",
            "approval_state",
        ],
    }
    if runtime_error is not None:
        return {
            **base,
            "selection_status": "blocked",
            "selected_attachment": None,
            "execution_target_selected": None,
            "degraded_mode": {},
            "selection_error": {
                "reason": runtime_error.reason,
                "message": runtime_error.message,
                "enforcement_state": runtime_error.enforcement_state,
            },
        }
    plan = _dict(runtime_plan)
    return {
        **base,
        "selection_status": "ready",
        "selected_attachment": _project_selected_attachment(plan.get("selected_attachment")),
        "execution_target_selected": _token(plan.get("execution_target_selected")),
        "deployment_mode": _token(plan.get("deployment_mode")),
        "selection_reason": _token(plan.get("selection_reason")),
        "privacy_split": _dict(plan.get("privacy_split")),
        "hybrid_rules": _dict(plan.get("hybrid_rules")),
        "hybrid_policy": _dict(plan.get("hybrid_policy")),
        "sync_enforcement": _dict(plan.get("sync_enforcement")),
        "placement_enforcement": _dict(plan.get("placement_enforcement")),
        "degraded_mode": _dict(plan.get("degraded_mode")),
        "selection_error": None,
    }


def _orchestration_channels_payload(
    install: Dict[str, Any],
    *,
    timeline_payload: Dict[str, Any],
    runtime_policy: Dict[str, Any],
) -> Dict[str, Any]:
    items = _list(timeline_payload.get("items"))
    recent_summaries = [
        {
            "id": _token(item.get("id")),
            "ts": _token(item.get("ts")),
            "event_class": _token(item.get("event_class")),
            "action": _token(item.get("action")),
            "title": _token(item.get("title")),
            "summary": _token(item.get("summary")),
            "review_required": bool(item.get("review_required")),
        }
        for item in items[:6]
        if isinstance(item, dict)
    ]
    artifacts = _recent_artifacts_payload(timeline_payload)
    return {
        "summary_channel": {
            "source": "activity_ledger",
            "item_count": len(items),
            "recent_items": recent_summaries,
            "sage_visible": True,
            "raw_memory_visible": False,
        },
        "status_channel": {
            "install_status": _token(install.get("status")) or "active",
            "enabled": bool(install.get("enabled", True)),
            "runtime_mode": _token(install.get("runtime_mode")) or "hosted_secure",
            "selection_status": _token(runtime_policy.get("selection_status")) or "unknown",
            "execution_target_selected": _token(runtime_policy.get("execution_target_selected")),
            "degraded_mode": _dict(runtime_policy.get("degraded_mode")),
        },
        "artifact_channel": {
            **artifacts,
            "sage_visible": True,
        },
    }


def _projection_summary(contract: Dict[str, Any]) -> Dict[str, Any]:
    identity = _dict(contract.get("identity"))
    runtime_policy = _dict(contract.get("runtime_policy"))
    memory_scope = _dict(contract.get("memory_scope"))
    operating_mode = _dict(contract.get("operating_mode"))
    skill_scope = _dict(contract.get("skill_scope"))
    mcp_scope = _dict(contract.get("mcp_scope"))
    connector_scope = _dict(contract.get("connector_scope"))
    tool_scope = _dict(contract.get("tool_scope"))
    orchestration = _dict(contract.get("sage_orchestration"))
    artifact_channel = _dict(orchestration.get("artifact_channel"))
    return {
        "install_id": _token(contract.get("install_id")),
        "label": _token(contract.get("label")),
        "service_kind": _token(contract.get("service_kind")) or "business_specialist",
        "specialist_mode": _token(operating_mode.get("mode")),
        "identity": identity,
        "runtime_policy": {
            "runtime_mode": _token(runtime_policy.get("runtime_mode")),
            "selection_status": _token(runtime_policy.get("selection_status")),
            "execution_target_selected": _token(runtime_policy.get("execution_target_selected")),
            "degraded_mode": _dict(runtime_policy.get("degraded_mode")),
        },
        "scope_summary": {
            "memory_layers": _list(memory_scope.get("accessible_layers")),
            "enabled_skills": _list(skill_scope.get("enabled_skills")),
            "mcp_servers": _list(mcp_scope.get("bound_servers")),
            "enabled_connectors": _list(connector_scope.get("enabled_connectors")),
            "enabled_tools": _list(tool_scope.get("enabled_tools")),
        },
        "mode_contract": {
            "prompt_editable": bool(operating_mode.get("prompt_editable")),
            "config_editable": bool(operating_mode.get("config_editable")),
            "response_audience": _token(operating_mode.get("response_audience")),
            "approval_behavior": _token(operating_mode.get("approval_behavior")),
        },
        "orchestration_channels": {
            "summary_item_count": int(_dict(orchestration.get("summary_channel")).get("item_count") or 0),
            "review_required_count": int(artifact_channel.get("review_required_count") or 0),
            "recent_artifacts": _list(artifact_channel.get("recent_artifacts")),
        },
    }


async def build_specialist_service_contract(
    *,
    tenant_id: str,
    workspace_id: str,
    install: Dict[str, Any],
) -> Dict[str, Any]:
    payload = dict(install or {})
    install_id = _token(payload.get("id"))
    if not install_id:
        raise ValueError("install.id is required.")

    memory_payload = await unified_memory_service.build_specialist_memory_payload(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_install_id=install_id,
    )

    runtime_plan: Optional[Dict[str, Any]] = None
    runtime_error: Optional[runtime_attachment_service.RuntimeAttachmentSelectionError] = None
    try:
        runtime_plan = await runtime_attachment_service.resolve_install_runtime_plan(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            install=payload,
        )
    except runtime_attachment_service.RuntimeAttachmentSelectionError as error:
        runtime_error = error

    timeline_payload = await activity_ledger_service.list_activity_timeline_payload(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        install_id=install_id,
        limit=12,
        detail_level="timeline_detail",
    )

    runtime_policy = _runtime_policy_payload(
        payload,
        runtime_plan=runtime_plan,
        runtime_error=runtime_error,
    )
    identity = _manifest_identity_payload(payload)

    contract = {
        "service_kind": "business_specialist",
        "install_id": install_id,
        "label": _token(payload.get("label")) or identity.get("name"),
        "operating_mode": _specialist_mode_payload(payload),
        "identity": identity,
        "separation_contract": {
            "is_specialist_service": True,
            "is_application": False,
            "is_personal_captain": False,
            "inherits_sage_memory_by_default": False,
            "inherits_other_specialist_memory_by_default": False,
            "same_runtime_capability_as_platform": True,
            "power_model": "runtime_and_policy_bound",
        },
        "memory_scope": _memory_scope_payload(memory_payload),
        "skill_scope": _skill_scope_payload(payload, workspace_id=workspace_id),
        "mcp_scope": _mcp_scope_payload(payload, workspace_id=workspace_id),
        "tool_scope": _tool_scope_payload(payload),
        "connector_scope": _connector_scope_payload(payload),
        "artifact_scope": {
            "root_folder_uri": _token(payload.get("root_folder_uri")),
            "folder_grants": _list(_tool_scope_payload(payload).get("folder_grants")),
            **_recent_artifacts_payload(timeline_payload),
        },
        "runtime_policy": runtime_policy,
        "sage_orchestration": _orchestration_channels_payload(
            payload,
            timeline_payload=timeline_payload,
            runtime_policy=runtime_policy,
        ),
    }
    return contract


async def list_workspace_specialist_service_summaries(
    *,
    tenant_id: str,
    workspace_id: str,
    installs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for install in list(installs or []):
        if not isinstance(install, dict):
            continue
        contract = await build_specialist_service_contract(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            install=install,
        )
        summaries.append(_projection_summary(contract))
    return summaries
