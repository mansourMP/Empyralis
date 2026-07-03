from __future__ import annotations

import json
import re
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository, run_state_repository


CAPTAIN_AGENT_KIND = "master"
SPECIALIST_AGENT_KIND = "specialist"
PRIVATE_CAPTAIN_ROLE = "private_main_agent"
DEFAULT_SPECIALIST_MODE = "owner_edit"
SPECIALIST_ALLOWED_MODES = (
    "owner_edit",
    "owner_test",
    "customer_live",
)
_SPECIALIST_MODE_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "owner_edit": {
        "mode": "owner_edit",
        "prompt_editable": True,
        "config_editable": True,
        "response_audience": "owner_operator",
        "approval_behavior": "workspace_policy_enforced",
    },
    "owner_test": {
        "mode": "owner_test",
        "prompt_editable": False,
        "config_editable": False,
        "response_audience": "owner_operator_preview",
        "approval_behavior": "workspace_policy_enforced",
    },
    "customer_live": {
        "mode": "customer_live",
        "prompt_editable": False,
        "config_editable": False,
        "response_audience": "external_customer",
        "approval_behavior": "workspace_policy_enforced",
    },
}
SELF_HOSTED_NODE_KINDS = {"mac_mini", "mac", "linux_server", "docker_host"}
SELF_HOSTED_ENROLLMENT_MIN_TTL_SECONDS = 60
SELF_HOSTED_ENROLLMENT_MAX_TTL_SECONDS = 900
SELF_HOSTED_ENROLLMENT_DEFAULT_TTL_SECONDS = 600
SELF_HOSTED_NODE_COMMAND_QUEUE_LIMIT = 500
SELF_HOSTED_NODE_COMMAND_DEFAULT_TTL_SECONDS = 300
SELF_HOSTED_NODE_COMMAND_MIN_TTL_SECONDS = 30
SELF_HOSTED_NODE_COMMAND_MAX_TTL_SECONDS = 3600
SELF_HOSTED_NODE_COMMAND_DEFAULT_LEASE_SECONDS = 120
SELF_HOSTED_NODE_COMMAND_MIN_LEASE_SECONDS = 15
SELF_HOSTED_NODE_COMMAND_MAX_LEASE_SECONDS = 600


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_token(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    return token or None


def _to_json(value: Any, *, default: Any) -> str:
    payload = value if value is not None else default
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _dict_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return {}
        try:
            parsed = json.loads(token)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _list_json(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return []
        try:
            parsed = json.loads(token)
        except Exception:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value).strip() or None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _hmac_sha256(secret: str, message: str) -> str:
    return hmac.new(
        str(secret or "").encode("utf-8"),
        str(message or "").encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _iso_to_datetime(value: Any) -> Optional[datetime]:
    token = _iso(value)
    if not token:
        return None
    try:
        parsed = token[:-1] + "+00:00" if str(token).endswith("Z") else str(token)
        dt = datetime.fromisoformat(parsed)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_iso_after_seconds(seconds: int) -> str:
    return (_utc_now() + timedelta(seconds=int(seconds))).isoformat().replace("+00:00", "Z")


def _self_hosted_command_hash(command: Dict[str, Any]) -> str:
    canonical = {
        "id": _normalize_token(command.get("id")),
        "workspace_id": _normalize_token(command.get("workspace_id")),
        "runtime_node_id": _normalize_token(command.get("runtime_node_id")),
        "agent_id": _normalize_token(command.get("agent_id")),
        "command_type": _normalize_token(command.get("command_type")),
        "command_payload": _dict_json(command.get("command_payload")),
        "created_at": _iso(command.get("created_at")),
        "expires_at": _iso(command.get("expires_at")),
    }
    return _token_hash(_to_json(canonical, default={}))


def _normalize_self_hosted_node_kind(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token not in SELF_HOSTED_NODE_KINDS:
        raise ValueError("Unsupported node_kind. Allowed values: mac_mini, mac, linux_server, docker_host.")
    return token


def _normalize_self_hosted_capabilities(value: Any) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for item in list(value or []):
        token = str(item or "").strip()
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _slugify(value: Any, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or fallback


def normalize_specialist_mode(
    value: Any,
    *,
    default: Optional[str] = DEFAULT_SPECIALIST_MODE,
    strict: bool = True,
) -> Optional[str]:
    token = _normalize_token(value)
    if token is None:
        return default
    token = token.lower()
    if token in SPECIALIST_ALLOWED_MODES:
        return token
    if strict:
        raise ValueError(
            "Unsupported specialist_mode. Allowed values are owner_edit, owner_test, customer_live."
        )
    return default


def specialist_mode_contract(mode: Optional[str]) -> Optional[Dict[str, Any]]:
    token = normalize_specialist_mode(mode, default=None, strict=False)
    if token is None:
        return None
    return dict(_SPECIALIST_MODE_CONTRACTS[token])


def captain_identity_contract(
    *,
    install_id: Optional[str],
    label: Optional[str],
) -> Dict[str, Any]:
    display_name = _normalize_token(label) or "Captain"
    stable_id = _normalize_token(install_id)
    return {
        "install_id": stable_id,
        "stable_internal_id": stable_id,
        "role": PRIVATE_CAPTAIN_ROLE,
        "display_name": display_name,
        "editable_display_name": True,
        "mode_switching_supported": False,
    }


def normalize_install_contract_metadata(
    *,
    install_id: Optional[str],
    label: Optional[str],
    agent_kind: Any,
    metadata: Optional[Dict[str, Any]],
    strict: bool = True,
) -> Dict[str, Any]:
    token = str(agent_kind or "").strip().lower() or SPECIALIST_AGENT_KIND
    next_metadata = _dict_json(metadata)
    if token == CAPTAIN_AGENT_KIND:
        if strict and (
            "specialist_mode" in next_metadata or "specialist_mode_contract" in next_metadata
        ):
            raise ValueError("Captain installs do not support owner_edit, owner_test, or customer_live modes.")
        next_metadata.pop("specialist_mode", None)
        next_metadata.pop("specialist_mode_contract", None)
        next_metadata["captain_profile"] = {
            **_dict_json(next_metadata.get("captain_profile")),
            **captain_identity_contract(install_id=install_id, label=label),
        }
        return next_metadata

    if strict and "captain_profile" in next_metadata:
        raise ValueError("Specialist installs cannot persist captain_profile metadata.")

    next_metadata.pop("captain_profile", None)
    if token == SPECIALIST_AGENT_KIND:
        raw_mode = next_metadata.get("specialist_mode")
        if raw_mode is None:
            raw_mode = _dict_json(next_metadata.get("specialist_mode_contract")).get("mode")
        mode = normalize_specialist_mode(raw_mode, default=DEFAULT_SPECIALIST_MODE, strict=strict)
        next_metadata["specialist_mode"] = mode
        next_metadata["specialist_mode_contract"] = specialist_mode_contract(mode)
    return next_metadata


def project_install_contract_fields(
    *,
    install_id: Optional[str],
    label: Optional[str],
    agent_kind: Any,
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    token = str(agent_kind or "").strip().lower() or SPECIALIST_AGENT_KIND
    normalized_metadata = normalize_install_contract_metadata(
        install_id=install_id,
        label=label,
        agent_kind=token,
        metadata=metadata,
        strict=False,
    )
    if token == CAPTAIN_AGENT_KIND:
        return {
            "metadata": normalized_metadata,
            "captain_identity": captain_identity_contract(install_id=install_id, label=label),
            "specialist_mode": None,
            "specialist_mode_contract": None,
        }
    mode = normalize_specialist_mode(
        normalized_metadata.get("specialist_mode"),
        default=DEFAULT_SPECIALIST_MODE,
        strict=False,
    )
    return {
        "metadata": normalized_metadata,
        "captain_identity": None,
        "specialist_mode": mode,
        "specialist_mode_contract": specialist_mode_contract(mode),
    }


def _row_to_runtime_profile(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "slug": str(payload.get("slug") or "").strip() or None,
        "label": str(payload.get("label") or "").strip() or None,
        "runtime_class": str(payload.get("runtime_class") or "").strip() or "cloud_worker",
        "placement_mode": str(payload.get("placement_mode") or "").strip() or "auto",
        "runtime_id": _normalize_token(payload.get("runtime_id")),
        "machine_id": _normalize_token(payload.get("machine_id")),
        "default_execution_target": str(payload.get("default_execution_target") or "").strip() or "auto",
        "supported_capabilities": _list_json(payload.get("supported_capabilities")),
        "root_folder_uri": _normalize_token(payload.get("root_folder_uri")),
        "allowed_connector_scopes": _list_json(payload.get("allowed_connector_scopes")),
        "status": str(payload.get("status") or "").strip() or "active",
        "last_seen_at": _iso(payload.get("last_seen_at")),
        "metadata": _dict_json(payload.get("metadata")),
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _row_to_workflow_snapshot(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    definition = _dict_json(payload.get("definition"))
    validation = _dict_json(payload.get("validation"))
    metadata = _dict_json(payload.get("metadata"))
    return {
        "id": str(payload.get("workflow_id") or payload.get("id") or "").strip(),
        "tenantId": str(payload.get("tenant_id") or "").strip() or None,
        "workspaceId": str(payload.get("workspace_id") or "").strip() or None,
        "name": str(payload.get("name") or "").strip() or "Compiled Agent Artifact",
        "description": str(payload.get("description") or "").strip(),
        "status": str(payload.get("workflow_status") or payload.get("status") or "").strip() or "compiled",
        "definition": definition,
        "validation": validation,
        "workflowVersionId": _normalize_token(payload.get("workflow_version_id")),
        "versionNumber": payload.get("version_number"),
        "metadata": metadata,
        "createdAt": _iso(payload.get("created_at")),
        "updatedAt": _iso(payload.get("updated_at")),
    }


def _row_to_agent_definition(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "slug": str(payload.get("slug") or "").strip() or None,
        "name": str(payload.get("name") or "").strip() or "Agent Template",
        "description": str(payload.get("description") or "").strip(),
        "agent_kind": str(payload.get("agent_kind") or "").strip() or "specialist",
        "visibility": str(payload.get("visibility") or "").strip() or "workspace",
        "status": str(payload.get("status") or "").strip() or "draft",
        "category": _normalize_token(payload.get("category")),
        "icon": _normalize_token(payload.get("icon")),
        "created_by_user_id": _normalize_token(payload.get("created_by_user_id")),
        "current_version_id": _normalize_token(payload.get("current_version_id")),
        "published_version_id": _normalize_token(payload.get("published_version_id")),
        "source_workflow_definition_id": _normalize_token(payload.get("source_workflow_definition_id")),
        "metadata": _dict_json(payload.get("metadata")),
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
        "current_version": {
            "id": _normalize_token(payload.get("version_id")),
            "version_number": int(payload.get("version_number") or 1),
            "status": str(payload.get("version_status") or "").strip() or "draft",
            "manifest": _dict_json(payload.get("manifest")),
            "capability_manifest": _dict_json(payload.get("capability_manifest")),
            "memory_scope_manifest": _dict_json(payload.get("memory_scope_manifest")),
            "policy_manifest": _dict_json(payload.get("policy_manifest")),
            "placement_manifest": _dict_json(payload.get("placement_manifest")),
            "template_inputs_schema": _dict_json(payload.get("template_inputs_schema")),
            "metadata": _dict_json(payload.get("version_metadata")),
            "compiled_workflow_version_id": _normalize_token(payload.get("version_compiled_workflow_version_id")),
        },
    }


def _row_to_install_summary(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    agent_kind = str(payload.get("agent_kind") or "").strip() or SPECIALIST_AGENT_KIND
    label = _normalize_token(payload.get("label"))
    projected_contract = project_install_contract_fields(
        install_id=str(payload.get("id") or "").strip() or None,
        label=label or str(payload.get("agent_definition_name") or "").strip() or None,
        agent_kind=agent_kind,
        metadata=_dict_json(payload.get("metadata")),
    )
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "agent_definition_id": str(payload.get("agent_definition_id") or "").strip() or None,
        "agent_definition_version_id": str(payload.get("agent_definition_version_id") or "").strip() or None,
        "installed_by_user_id": _normalize_token(payload.get("installed_by_user_id")),
        "install_scope": str(payload.get("install_scope") or "").strip() or "workspace",
        "owner_user_id": _normalize_token(payload.get("owner_user_id")),
        "thread_id": _normalize_token(payload.get("thread_id")),
        "label": label,
        "status": str(payload.get("status") or "").strip() or "active",
        "enabled": bool(payload.get("enabled")),
        "runtime_profile_id": _normalize_token(payload.get("runtime_profile_id")),
        "runtime_mode": str(payload.get("runtime_mode") or "").strip() or "hosted_secure",
        "compiled_workflow_version_id": _normalize_token(payload.get("compiled_workflow_version_id")),
        "compiled_workflow_id": _normalize_token(payload.get("compiled_workflow_id")),
        "root_folder_uri": _normalize_token(payload.get("root_folder_uri")),
        "tool_toggles": _dict_json(payload.get("tool_toggles")),
        "folder_grants": _list_json(payload.get("folder_grants")),
        "connector_bindings": _dict_json(payload.get("connector_bindings")),
        "memory_scope_overrides": _dict_json(payload.get("memory_scope_overrides")),
        "policy_context_overrides": _dict_json(payload.get("policy_context_overrides")),
        "metadata": projected_contract["metadata"],
        "captain_identity": projected_contract["captain_identity"],
        "specialist_mode": projected_contract["specialist_mode"],
        "specialist_mode_contract": projected_contract["specialist_mode_contract"],
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
        "agent_definition": {
            "id": str(payload.get("agent_definition_id") or "").strip(),
            "slug": _normalize_token(payload.get("agent_definition_slug")),
            "name": str(payload.get("agent_definition_name") or "").strip() or "Installed Agent",
            "description": str(payload.get("agent_definition_description") or "").strip(),
            "category": _normalize_token(payload.get("agent_definition_category")),
            "icon": _normalize_token(payload.get("agent_definition_icon")),
            "agent_kind": agent_kind,
        },
        "agent_definition_version": {
            "id": _normalize_token(payload.get("agent_definition_version_id")),
            "version_number": int(payload.get("definition_version_number") or 1),
            "manifest": _dict_json(payload.get("manifest")),
            "capability_manifest": _dict_json(payload.get("capability_manifest")),
            "policy_manifest": _dict_json(payload.get("policy_manifest")),
            "placement_manifest": _dict_json(payload.get("placement_manifest")),
        },
        "runtime_profile": {
            "id": _normalize_token(payload.get("runtime_profile_id")),
            "slug": _normalize_token(payload.get("runtime_profile_slug")),
            "label": _normalize_token(payload.get("runtime_profile_label")),
            "runtime_class": str(payload.get("runtime_class") or "").strip() or "cloud_worker",
            "placement_mode": str(payload.get("placement_mode") or "").strip() or "auto",
            "runtime_id": _normalize_token(payload.get("runtime_id")),
            "machine_id": _normalize_token(payload.get("machine_id")),
            "default_execution_target": str(payload.get("default_execution_target") or "").strip() or "auto",
            "status": str(payload.get("runtime_profile_status") or "").strip() or "active",
        } if payload.get("runtime_profile_id") else None,
    }


DEFAULT_AGENT_DEFINITIONS: List[Dict[str, Any]] = [
    # ── Phase M: fleet-specialist — target for fleet_create_agent ────────
    {
        "slug": "fleet-specialist",
        "name": "Fleet Specialist",
        "display_name": None,  # null — agent will ask owner for a name
        "description": "A specialist agent created via the fleet management tools. Configurable by operators.",
        "category": "System",
        "icon": "bot",
        "agent_kind": "specialist",
        "visibility": "private",
        "manifest": {
            "template_kind": "specialist",
            "default_prompt": (
                "You are a new assistant in this workspace. Introduce yourself briefly and ask "
                "the owner what they would like to call you. Once they choose a name, use "
                "fleet_configure_agent to save your display_name."
            ),
        },
        "capability_manifest": {
            "summary": ["general_purpose"],
            "toggles": [
                {"id": "web_search", "label": "Web Search", "description": "Search the web for information.", "default_enabled": True},
                {"id": "http_request", "label": "HTTP Requests", "description": "Make HTTP requests to APIs.", "default_enabled": True},
                {"id": "memory_read", "label": "Memory Read", "description": "Read from agent memory.", "default_enabled": True},
                {"id": "memory_write", "label": "Memory Write", "description": "Write to agent memory.", "default_enabled": True},
            ],
        },
        "policy_manifest": {"trust_mode": "guarded", "interactive_approvals": True, "session_mode": "copilot"},
        "placement_manifest": {"preferred_runtime_slug": "empyralis-cloud", "allowed_runtime_classes": ["cloud_worker"]},
    },
]
DEFAULT_MASTER_AGENT_DEFINITION: Dict[str, Any] = {
    "slug": "sage",
    "name": "Sage",
    "display_name": "Sage",
    "description": "The central Life OS orchestrator for this workspace. Sage owns the primary relationship, planning loop, delegation graph, and universal memory.",
    "category": "System",
    "icon": "sparkles",
    "agent_kind": "master",
    "visibility": "private",
    "manifest": {
        "template_kind": "master_orchestrator",
        "default_prompt": "You are the master agent for this workspace. Own the user relationship, maintain universal context, plan carefully, delegate to installed specialists, and stay inside policy.",
    },
    "capability_manifest": {
        "summary": ["planning", "delegation", "universal_memory"],
        "toggles": [
            {"id": "planning", "label": "Planning", "description": "Break goals into execution plans and choose the right specialist.", "default_enabled": True},
            {"id": "delegation", "label": "Delegation", "description": "Spawn and supervise specialist child runs.", "default_enabled": True},
            {"id": "universal_memory", "label": "Universal Memory", "description": "Maintain cross-agent context for the workspace.", "default_enabled": True},
        ],
    },
    "policy_manifest": {"trust_mode": "guarded", "interactive_approvals": True, "session_mode": "copilot"},
    "placement_manifest": {"preferred_runtime_slug": "empyralis-cloud", "allowed_runtime_classes": ["cloud_worker", "desktop_companion"]},
}


DEFAULT_RUNTIME_PROFILES: List[Dict[str, Any]] = [
    {
        "slug": "empyralis-cloud",
        "label": "Empyralis Cloud",
        "runtime_class": "cloud_worker",
        "placement_mode": "preferred",
        "default_execution_target": "cloud",
        "supported_capabilities": ["web_search", "external_api", "file_access"],
        "status": "active",
        "metadata": {
            "base_image_id": "empyralis-hosted-secure-v1",
            "hosted_secure_limits": {
                "cpu_seconds": 20,
                "memory_mb": 512,
                "timeout_seconds": 25,
                "max_open_files": 64,
                "max_file_bytes": 16777216,
            },
            "network_policy": {
                "mode": "allowlist_hooks",
                "allow_outbound": True,
                "allow_private_hosts": False,
                "allowed_hosts": [],
                "allowed_providers": [
                    "openai",
                    "anthropic",
                    "google",
                    "groq",
                    "openrouter",
                    "xai",
                    "azure_openai",
                    "deepseek",
                ],
                "hooks_ready": True,
            },
        },
    },
    {
        "slug": "my-local-mac",
        "label": "My Local Mac",
        "runtime_class": "desktop_companion",
        "placement_mode": "preferred",
        "default_execution_target": "local_companion",
        "supported_capabilities": ["computer_control", "shell.execute", "file_access", "web_search"],
        "status": "active",
        "metadata": {
            "allowed_folders": [],
            "allowed_applications": [],
            "network_policy": {
                "mode": "allowlist_hooks",
                "allow_outbound": True,
                "allow_private_hosts": False,
                "allowed_hosts": [],
                "allowed_providers": [
                    "openai",
                    "anthropic",
                    "google",
                    "groq",
                    "openrouter",
                    "xai",
                    "azure_openai",
                    "deepseek",
                ],
                "hooks_ready": True,
            },
            "requires_privileged_approval": True,
        },
    },
]


def _default_tool_toggles_from_capabilities(capability_manifest: Dict[str, Any]) -> Dict[str, bool]:
    toggles = _list_json(capability_manifest.get("toggles"))
    out: Dict[str, bool] = {}
    for item in toggles:
        if not isinstance(item, dict):
            continue
        toggle_id = str(item.get("id") or "").strip()
        if not toggle_id:
            continue
        out[toggle_id] = bool(item.get("default_enabled"))
    return out


def build_master_thread_id(*, workspace_id: str, owner_user_id: Optional[str] = None) -> str:
    workspace_slug = _slugify(workspace_id, fallback="workspace")
    owner_slug = _slugify(owner_user_id, fallback="workspace-owner")
    return f"thread_sage_{workspace_slug}_{owner_slug}"


async def ensure_workspace_agent_registry_seeded(
    *,
    tenant_id: str,
    workspace_id: str,
    created_by_user_id: Optional[str] = None,
) -> None:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return
    tenant_token = str(tenant_id or "").strip() or "default"
    from server_modules import workspace_scope as _ws
    workspace_token = _ws.resolve_workspace(
        workspace_id, site="agent_registry_repository:seed_workspace"
    )
    workspace_slug = _slugify(workspace_token, fallback="workspace")

    async with pool.acquire() as connection:
        async with connection.transaction():
            for profile in DEFAULT_RUNTIME_PROFILES:
                existing = await connection.fetchrow(
                    """
                    SELECT id
                    FROM runtime_profiles
                    WHERE tenant_id = $1 AND workspace_id = $2 AND slug = $3
                    LIMIT 1
                    """,
                    tenant_token,
                    workspace_token,
                    profile["slug"],
                )
                if existing is not None:
                    continue
                profile_id = f"rprof_{workspace_slug}_{_slugify(profile['slug'], fallback='profile')}"
                await connection.execute(
                    """
                    INSERT INTO runtime_profiles (
                        id, tenant_id, workspace_id, slug, label, runtime_class, placement_mode, runtime_id, machine_id,
                        default_execution_target, supported_capabilities, root_folder_uri, allowed_connector_scopes, status,
                        last_seen_at, metadata, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, NULL, NULL, $8, $9::jsonb, NULL, '[]'::jsonb, $10, NULL, $11::jsonb, NOW(), NOW()
                    )
                    """,
                    profile_id,
                    tenant_token,
                    workspace_token,
                    profile["slug"],
                    profile["label"],
                    profile["runtime_class"],
                    profile["placement_mode"],
                    profile["default_execution_target"],
                    _to_json(profile.get("supported_capabilities"), default=[]),
                    profile["status"],
                    _to_json(profile.get("metadata"), default={}),
                )

            for definition in DEFAULT_AGENT_DEFINITIONS:
                existing = await connection.fetchrow(
                    """
                    SELECT id, current_version_id, published_version_id
                    FROM agent_definitions
                    WHERE tenant_id = $1 AND workspace_id = $2 AND slug = $3
                    LIMIT 1
                    """,
                    tenant_token,
                    workspace_token,
                    definition["slug"],
                )
                if existing is not None:
                    agent_definition_id = str(existing.get("id") or "").strip()
                else:
                    agent_definition_id = f"agentdef_{workspace_slug}_{_slugify(definition['slug'], fallback='agent')}"
                    await connection.execute(
                        """
                        INSERT INTO agent_definitions (
                            id, tenant_id, workspace_id, slug, name, description, agent_kind, visibility, status,
                            category, icon, created_by_user_id, current_version_id, published_version_id,
                            source_workflow_definition_id, metadata, created_at, updated_at
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, 'published', $9, $10, $11, NULL, NULL, NULL, '{}'::jsonb, NOW(), NOW()
                        )
                        """,
                        agent_definition_id,
                        tenant_token,
                        workspace_token,
                        definition["slug"],
                        definition["name"],
                        definition["description"],
                        definition["agent_kind"],
                        definition["visibility"],
                        definition["category"],
                        definition["icon"],
                        _normalize_token(created_by_user_id),
                    )
                version_existing = await connection.fetchrow(
                    """
                    SELECT id
                    FROM agent_definition_versions
                    WHERE tenant_id = $1 AND workspace_id = $2 AND agent_definition_id = $3 AND version_number = 1
                    LIMIT 1
                    """,
                    tenant_token,
                    workspace_token,
                    agent_definition_id,
                )
                version_id = str(version_existing.get("id") or "").strip() if version_existing is not None else f"{agent_definition_id}_v1"
                if version_existing is None:
                    await connection.execute(
                        """
                        INSERT INTO agent_definition_versions (
                            id, tenant_id, workspace_id, agent_definition_id, version_number, status, manifest,
                            compiled_workflow_version_id, capability_manifest, memory_scope_manifest, policy_manifest,
                            placement_manifest, template_inputs_schema, metadata, created_by_user_id, created_at
                        ) VALUES (
                            $1, $2, $3, $4, 1, 'published', $5::jsonb, NULL, $6::jsonb, '{}'::jsonb, $7::jsonb,
                            $8::jsonb, '{}'::jsonb, '{}'::jsonb, $9, NOW()
                        )
                        """,
                        version_id,
                        tenant_token,
                        workspace_token,
                        agent_definition_id,
                        _to_json(definition.get("manifest"), default={}),
                        _to_json(definition.get("capability_manifest"), default={}),
                        _to_json(definition.get("policy_manifest"), default={}),
                        _to_json(definition.get("placement_manifest"), default={}),
                        _normalize_token(created_by_user_id),
                    )
                await connection.execute(
                    """
                    UPDATE agent_definitions
                    SET
                        current_version_id = COALESCE(current_version_id, $4),
                        published_version_id = COALESCE(published_version_id, $4),
                        updated_at = NOW()
                    WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
                    """,
                    agent_definition_id,
                    tenant_token,
                    workspace_token,
                    version_id,
                )

            master_definition = DEFAULT_MASTER_AGENT_DEFINITION
            master_existing = await connection.fetchrow(
                """
                SELECT id, current_version_id, published_version_id
                FROM agent_definitions
                WHERE tenant_id = $1 AND workspace_id = $2 AND slug = $3
                LIMIT 1
                """,
                tenant_token,
                workspace_token,
                master_definition["slug"],
            )
            if master_existing is not None:
                master_definition_id = str(master_existing.get("id") or "").strip()
            else:
                master_definition_id = f"agentdef_{workspace_slug}_{_slugify(master_definition['slug'], fallback='sage')}"
                await connection.execute(
                    """
                    INSERT INTO agent_definitions (
                        id, tenant_id, workspace_id, slug, name, description, agent_kind, visibility, status,
                        category, icon, created_by_user_id, current_version_id, published_version_id,
                        source_workflow_definition_id, metadata, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, 'published', $9, $10, $11, NULL, NULL, NULL, '{"system_agent":true,"hidden_from_catalog":true}'::jsonb, NOW(), NOW()
                    )
                    """,
                    master_definition_id,
                    tenant_token,
                    workspace_token,
                    master_definition["slug"],
                    master_definition["name"],
                    master_definition["description"],
                    master_definition["agent_kind"],
                    master_definition["visibility"],
                    master_definition["category"],
                    master_definition["icon"],
                    _normalize_token(created_by_user_id),
                )
            master_version_existing = await connection.fetchrow(
                """
                SELECT id
                FROM agent_definition_versions
                WHERE tenant_id = $1 AND workspace_id = $2 AND agent_definition_id = $3 AND version_number = 1
                LIMIT 1
                """,
                tenant_token,
                workspace_token,
                master_definition_id,
            )
            master_version_id = str(master_version_existing.get("id") or "").strip() if master_version_existing is not None else f"{master_definition_id}_v1"
            if master_version_existing is None:
                await connection.execute(
                    """
                    INSERT INTO agent_definition_versions (
                        id, tenant_id, workspace_id, agent_definition_id, version_number, status, manifest,
                        compiled_workflow_version_id, capability_manifest, memory_scope_manifest, policy_manifest,
                        placement_manifest, template_inputs_schema, metadata, created_by_user_id, created_at
                    ) VALUES (
                        $1, $2, $3, $4, 1, 'published', $5::jsonb, NULL, $6::jsonb, '{}'::jsonb, $7::jsonb,
                        $8::jsonb, '{}'::jsonb, '{"system_agent":true,"hidden_from_catalog":true}'::jsonb, $9, NOW()
                    )
                    """,
                    master_version_id,
                    tenant_token,
                    workspace_token,
                    master_definition_id,
                    _to_json(master_definition.get("manifest"), default={}),
                    _to_json(master_definition.get("capability_manifest"), default={}),
                    _to_json(master_definition.get("policy_manifest"), default={}),
                    _to_json(master_definition.get("placement_manifest"), default={}),
                    _normalize_token(created_by_user_id),
                )
            await connection.execute(
                """
                UPDATE agent_definitions
                SET
                    current_version_id = COALESCE(current_version_id, $4),
                    published_version_id = COALESCE(published_version_id, $4),
                    updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
                """,
                master_definition_id,
                tenant_token,
                workspace_token,
                master_version_id,
            )

            master_install_existing = await connection.fetchrow(
                """
                SELECT wai.id
                FROM workspace_agent_installs wai
                INNER JOIN agent_definitions ad
                    ON ad.id = wai.agent_definition_id
                WHERE wai.tenant_id = $1
                  AND wai.workspace_id = $2
                  AND ad.agent_kind = 'master'
                LIMIT 1
                """,
                tenant_token,
                workspace_token,
            )
            if master_install_existing is None:
                await connection.execute(
                    """
                    INSERT INTO workspace_agent_installs (
                        id, tenant_id, workspace_id, agent_definition_id, agent_definition_version_id, installed_by_user_id,
                        install_scope, owner_user_id, thread_id, label, status, enabled, runtime_profile_id, compiled_workflow_version_id,
                        root_folder_uri, tool_toggles, folder_grants, connector_bindings, memory_scope_overrides,
                        policy_context_overrides, metadata, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6,
                        'workspace', NULL, NULL, $7, 'active', TRUE, $8, NULL,
                        NULL, '{}'::jsonb, '[]'::jsonb, '{}'::jsonb, '{}'::jsonb,
                        '{"trust_mode":"guarded","session_mode":"copilot"}'::jsonb, '{"system_agent":true,"hidden_from_agents_dashboard":true}'::jsonb, NOW(), NOW()
                    )
                    """,
                    f"ainstall_{workspace_slug}_sage",
                    tenant_token,
                    workspace_token,
                    master_definition_id,
                    master_version_id,
                    _normalize_token(created_by_user_id),
                    master_definition["name"],
                    f"rprof_{workspace_slug}_empyralis-cloud",
                )


async def list_runtime_profiles(
    *,
    tenant_id: str,
    workspace_id: str,
    seed_if_missing: bool = True,
) -> List[Dict[str, Any]]:
    if bool(seed_if_missing):
        await ensure_workspace_agent_registry_seeded(tenant_id=tenant_id, workspace_id=workspace_id)
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await pool.fetch(
        """
        SELECT *
        FROM runtime_profiles
        WHERE tenant_id = $1 AND workspace_id = $2
        ORDER BY
            CASE WHEN slug = 'empyralis-cloud' THEN 0 ELSE 1 END,
            label ASC
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
    )
    return [item for item in (_row_to_runtime_profile(row) for row in rows) if item]


async def create_self_hosted_runtime_profile_enrollment_intent(
    *,
    tenant_id: str,
    workspace_id: str,
    owner_user_id: Optional[str],
    label: Optional[str],
    node_kind: str,
    capabilities: Optional[List[str]] = None,
    max_concurrent_sessions: int = 1,
    root_policy: Optional[Dict[str, Any]] = None,
    token_ttl_seconds: int = SELF_HOSTED_ENROLLMENT_DEFAULT_TTL_SECONDS,
) -> Dict[str, Any]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise RuntimeError("Control-plane storage is unavailable.")
    tenant_token = str(tenant_id or "").strip() or "default"
    workspace_token = str(workspace_id or "").strip() or "default"
    owner_token = _normalize_token(owner_user_id)
    if owner_token is None:
        raise ValueError("owner_user_id is required for self-hosted enrollment.")
    normalized_node_kind = _normalize_self_hosted_node_kind(node_kind)
    normalized_caps = _normalize_self_hosted_capabilities(capabilities or [])
    max_sessions = max(1, int(max_concurrent_sessions or 1))
    ttl = int(token_ttl_seconds or SELF_HOSTED_ENROLLMENT_DEFAULT_TTL_SECONDS)
    ttl = max(SELF_HOSTED_ENROLLMENT_MIN_TTL_SECONDS, min(ttl, SELF_HOSTED_ENROLLMENT_MAX_TTL_SECONDS))
    now = _utc_now()
    expires_at = now + timedelta(seconds=ttl)
    suffix = uuid.uuid4().hex[:10]
    workspace_slug = _slugify(workspace_token, fallback="workspace")
    runtime_profile_id = f"rprof_{workspace_slug}_selfhost_{suffix}"
    runtime_node_id = f"node_{suffix}"
    profile_slug = f"self-hosted-{_slugify(label or normalized_node_kind, fallback='node')}-{suffix[:6]}"
    enrollment_token = secrets.token_urlsafe(32)
    enrollment_metadata = {
        "state": "pending",
        "token_hash": _token_hash(enrollment_token),
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "consumed_at": None,
        "issued_for_workspace_id": workspace_token,
        "issued_by_user_id": owner_token,
    }
    metadata = {
        "runtime_node_id": runtime_node_id,
        "owner_user_id": owner_token,
        "node_kind": normalized_node_kind,
        "allowed_agent_ids": [],
        "max_concurrent_sessions": max_sessions,
        "root_policy": _dict_json(root_policy),
        "public_key": None,
        "owner_approved": False,
        "owner_approved_at": None,
        "owner_approved_by_user_id": None,
        "enrollment": enrollment_metadata,
        "node_session_token_hash": None,
        "node_session_issued_at": None,
    }
    control_plane_repository._enforce_control_plane_service_decision(
        operation="self_hosted_enrollment_intent_create",
        tenant_id=tenant_token,
        workspace_id=workspace_token,
        actor_id=owner_token,
        actor_role="owner",
        record_type="runtime_profile",
        idempotency_key=runtime_profile_id,
        source="self_hosted_runtime",
    )
    await pool.execute(
        """
        INSERT INTO runtime_profiles (
            id, tenant_id, workspace_id, slug, label, runtime_class, placement_mode, runtime_id, machine_id,
            default_execution_target, supported_capabilities, root_folder_uri, allowed_connector_scopes, status,
            last_seen_at, metadata, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, 'self_hosted_business_node', 'preferred', $6, $6,
            'cloud', $7::jsonb, NULL, '[]'::jsonb, 'pending',
            NULL, $8::jsonb, NOW(), NOW()
        )
        """,
        runtime_profile_id,
        tenant_token,
        workspace_token,
        profile_slug,
        str(label or "Self-hosted Node").strip() or "Self-hosted Node",
        runtime_node_id,
        _to_json(normalized_caps, default=[]),
        _to_json(metadata, default={}),
    )
    profile = await get_runtime_profile(
        runtime_profile_id,
        tenant_id=tenant_token,
        workspace_id=workspace_token,
    )
    if not isinstance(profile, dict):
        raise RuntimeError("Self-hosted runtime profile creation failed.")
    return {
        "runtime_profile": profile,
        "runtime_profile_id": runtime_profile_id,
        "runtime_node_id": runtime_node_id,
        "enrollment_token": enrollment_token,
        "enrollment_expires_at": enrollment_metadata["expires_at"],
        "token_ttl_seconds": ttl,
    }


async def enroll_self_hosted_runtime_profile(
    *,
    runtime_profile_id: str,
    enrollment_token: str,
    public_key: str,
    node_kind: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    max_concurrent_sessions: Optional[int] = None,
    root_policy: Optional[Dict[str, Any]] = None,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    profile = await get_runtime_profile(runtime_profile_id)
    if not isinstance(profile, dict):
        raise ValueError("Self-hosted runtime profile not found.")
    if str(profile.get("runtime_class") or "").strip() != "self_hosted_business_node":
        raise ValueError("Runtime profile is not a self-hosted node.")
    enrollment = _dict_json(_dict_json(profile.get("metadata")).get("enrollment"))
    expected_hash = str(enrollment.get("token_hash") or "").strip()
    if not expected_hash:
        raise ValueError("Enrollment token is not pending.")
    provided = str(enrollment_token or "").strip()
    if not provided:
        raise ValueError("Enrollment token is required.")
    if _token_hash(provided) != expected_hash:
        raise ValueError("Enrollment token is invalid.")
    expires_at = _iso(enrollment.get("expires_at"))
    if expires_at:
        parsed = _iso(expires_at)
        expiry = None
        if parsed:
            try:
                token = parsed[:-1] + "+00:00" if str(parsed).endswith("Z") else str(parsed)
                expiry = datetime.fromisoformat(token)
            except Exception:
                expiry = None
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry is not None and _utc_now() > expiry:
            raise ValueError("Enrollment token expired.")
    workspace_id = str(profile.get("workspace_id") or "").strip() or "default"
    issued_workspace = str(enrollment.get("issued_for_workspace_id") or "").strip()
    if issued_workspace and issued_workspace != workspace_id:
        raise ValueError("Enrollment token workspace scope mismatch.")
    now_iso = _utc_now_iso()
    metadata = _dict_json(profile.get("metadata"))
    normalized_kind = _normalize_self_hosted_node_kind(node_kind or metadata.get("node_kind") or "linux_server")
    normalized_caps = _normalize_self_hosted_capabilities(capabilities or profile.get("supported_capabilities") or [])
    max_sessions = max(1, int(max_concurrent_sessions or metadata.get("max_concurrent_sessions") or 1))
    next_root_policy = _dict_json(root_policy) if root_policy is not None else _dict_json(metadata.get("root_policy"))
    session_token = secrets.token_urlsafe(32)
    next_enrollment = {
        **enrollment,
        "state": "enrolled_pending_owner_approval",
        "token_hash": None,
        "consumed_at": now_iso,
    }
    metadata.update(
        {
            "node_kind": normalized_kind,
            "public_key": str(public_key or "").strip(),
            "max_concurrent_sessions": max_sessions,
            "root_policy": next_root_policy,
            "enrollment": next_enrollment,
            "node_session_token_hash": _token_hash(session_token),
            "node_session_issued_at": now_iso,
            "heartbeat_at": now_iso,
            "runtime_node_id": str(profile.get("runtime_id") or metadata.get("runtime_node_id") or "").strip() or None,
        }
    )
    control_plane_repository._enforce_control_plane_service_decision(
        operation="self_hosted_runtime_enroll",
        tenant_id=str(profile.get("tenant_id") or "default").strip() or "default",
        workspace_id=workspace_id,
        actor_id=str(profile.get("runtime_id") or metadata.get("runtime_node_id") or runtime_profile_id).strip(),
        actor_role="system",
        record_type="runtime_profile",
        idempotency_key=str(profile.get("id") or runtime_profile_id).strip(),
        source="self_hosted_runtime",
    )
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise RuntimeError("Control-plane storage is unavailable.")
    await pool.execute(
        """
        UPDATE runtime_profiles
        SET
            label = $4,
            supported_capabilities = $5::jsonb,
            metadata = $6::jsonb,
            status = 'pending',
            last_seen_at = NOW(),
            updated_at = NOW()
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        str(profile.get("id") or runtime_profile_id).strip(),
        str(profile.get("tenant_id") or "default").strip() or "default",
        workspace_id,
        str(label or profile.get("label") or "Self-hosted Node").strip() or "Self-hosted Node",
        _to_json(normalized_caps, default=[]),
        _to_json(metadata, default={}),
    )
    enrolled_profile = await get_runtime_profile(
        str(profile.get("id") or runtime_profile_id).strip(),
        tenant_id=str(profile.get("tenant_id") or "default").strip() or "default",
        workspace_id=workspace_id,
    )
    return {
        "runtime_profile": enrolled_profile,
        "runtime_profile_id": str(profile.get("id") or runtime_profile_id).strip(),
        "workspace_id": workspace_id,
        "runtime_node_id": str(profile.get("runtime_id") or metadata.get("runtime_node_id") or "").strip() or None,
        "node_session_token": session_token,
        "owner_approval_required": True,
    }


async def approve_self_hosted_runtime_profile(
    *,
    runtime_profile_id: str,
    tenant_id: str,
    workspace_id: str,
    approved_by_user_id: str,
) -> Optional[Dict[str, Any]]:
    profile = await get_runtime_profile(
        runtime_profile_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not isinstance(profile, dict):
        return None
    if str(profile.get("runtime_class") or "").strip() != "self_hosted_business_node":
        raise ValueError("Runtime profile is not a self-hosted node.")
    metadata = _dict_json(profile.get("metadata"))
    now_iso = _utc_now_iso()
    metadata["owner_approved"] = True
    metadata["owner_approved_at"] = now_iso
    metadata["owner_approved_by_user_id"] = str(approved_by_user_id or "").strip() or None
    enrollment = _dict_json(metadata.get("enrollment"))
    if enrollment:
        enrollment["state"] = "approved"
        metadata["enrollment"] = enrollment
    control_plane_repository._enforce_control_plane_service_decision(
        operation="self_hosted_runtime_approve",
        tenant_id=str(tenant_id or "").strip() or "default",
        workspace_id=str(workspace_id or "").strip() or "default",
        actor_id=str(approved_by_user_id or "").strip(),
        actor_role="owner",
        record_type="runtime_profile",
        idempotency_key=str(runtime_profile_id or "").strip(),
        source="self_hosted_runtime",
    )
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise RuntimeError("Control-plane storage is unavailable.")
    await pool.execute(
        """
        UPDATE runtime_profiles
        SET
            metadata = $4::jsonb,
            status = 'active',
            updated_at = NOW()
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        str(runtime_profile_id or "").strip(),
        str(tenant_id or "").strip() or "default",
        str(workspace_id or "").strip() or "default",
        _to_json(metadata, default={}),
    )
    return await get_runtime_profile(
        runtime_profile_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def resolve_self_hosted_runtime_heartbeat(
    *,
    runtime_profile_id: str,
    node_session_token: str,
    status: str = "idle",
    note: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    health_state: Optional[str] = None,
) -> Dict[str, Any]:
    profile = await get_runtime_profile(runtime_profile_id)
    if not isinstance(profile, dict):
        raise ValueError("Self-hosted runtime profile not found.")
    if str(profile.get("runtime_class") or "").strip() != "self_hosted_business_node":
        raise ValueError("Runtime profile is not a self-hosted node.")
    metadata = _dict_json(profile.get("metadata"))
    expected = str(metadata.get("node_session_token_hash") or "").strip()
    provided = str(node_session_token or "").strip()
    if not provided or not expected or _token_hash(provided) != expected:
        raise ValueError("Node session token is invalid.")
    runtime_id = str(profile.get("runtime_id") or "").strip()
    if not runtime_id:
        raise ValueError("Self-hosted runtime profile is missing runtime_id.")
    now_iso = _utc_now_iso()
    metadata["heartbeat_at"] = now_iso
    control_plane_repository._enforce_control_plane_service_decision(
        operation="self_hosted_runtime_heartbeat",
        tenant_id=str(profile.get("tenant_id") or "default").strip() or "default",
        workspace_id=str(profile.get("workspace_id") or "default").strip() or "default",
        actor_id=runtime_id,
        actor_role="system",
        record_type="runtime_profile",
        idempotency_key=f"{str(profile.get('id') or runtime_profile_id).strip()}:{now_iso}",
        source="self_hosted_runtime",
    )
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise RuntimeError("Control-plane storage is unavailable.")
    await pool.execute(
        """
        UPDATE runtime_profiles
        SET
            metadata = $4::jsonb,
            last_seen_at = NOW(),
            updated_at = NOW()
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        str(profile.get("id") or runtime_profile_id).strip(),
        str(profile.get("tenant_id") or "default").strip() or "default",
        str(profile.get("workspace_id") or "default").strip() or "default",
        _to_json(metadata, default={}),
    )
    worker_caps = _normalize_self_hosted_capabilities(capabilities or profile.get("supported_capabilities") or [])
    await run_state_repository.upsert_fleet_worker(
        {
            "worker_id": runtime_id,
            "runtime_id": runtime_id,
            "machine_id": runtime_id,
            "tenant_id": str(profile.get("tenant_id") or "default").strip() or "default",
            "workspace_id": str(profile.get("workspace_id") or "default").strip() or "default",
            "runtime_type": "self_hosted_business_node",
            "display_name": str(profile.get("label") or runtime_id).strip() or runtime_id,
            "online": True,
            "status": str(status or "idle").strip().lower() or "idle",
            "control_state": "active",
            "execution_targets": ["cloud"],
            "capabilities": worker_caps,
            "last_heartbeat_at": now_iso,
            "last_seen_at": now_iso,
            "lease_seconds": 30,
            "note": str(note or "self_hosted_node_heartbeat").strip() or "self_hosted_node_heartbeat",
            "health_state": str(health_state or "healthy").strip() or "healthy",
            "metadata": {
                "node_kind": str(metadata.get("node_kind") or "").strip() or None,
                "public_key": str(metadata.get("public_key") or "").strip() or None,
                "max_concurrent_sessions": int(metadata.get("max_concurrent_sessions") or 1),
                "root_policy": _dict_json(metadata.get("root_policy")),
                "allowed_agent_ids": _list_json(metadata.get("allowed_agent_ids")),
                "owner_approved": bool(metadata.get("owner_approved")),
                "owner_approved_at": _iso(metadata.get("owner_approved_at")),
                "owner_approved_by_user_id": _normalize_token(metadata.get("owner_approved_by_user_id")),
            },
        },
        heartbeat_seen=True,
    )
    return {
        "ok": True,
        "runtime_profile_id": str(profile.get("id") or runtime_profile_id).strip(),
        "runtime_node_id": runtime_id,
        "workspace_id": str(profile.get("workspace_id") or "default").strip() or "default",
        "heartbeat_at": now_iso,
    }


# ── Vault blob backup storage ────────────────────────────────────────────────
# Encrypted vault blobs from self-hosted nodes are stored here so they
# survive node box loss.  The blobs are encrypted at rest (Fernet+PBKDF2)
# before transmission — safe to store on the cloud side.

import os as _os
from pathlib import Path as _Path


def _vault_blob_backup_dir() -> _Path:
    from server_modules.runtime_config import EMPYRALIS_STATE_HOME
    return _Path(EMPYRALIS_STATE_HOME) / "vault-backups"


def _vault_backup_blob_path(runtime_profile_id: str) -> _Path:
    return _vault_blob_backup_dir() / f"{runtime_profile_id}.json"


async def store_vault_backup_blob(
    *,
    runtime_profile_id: str,
    vault_blob_base64: str,
    environment: str,
    backup_version: str,
) -> Dict[str, Any]:
    """Store an encrypted vault blob from a self-hosted node."""
    import time as _time
    rp_id = str(runtime_profile_id or "").strip()
    if not rp_id:
        raise ValueError("runtime_profile_id is required")
    blob = str(vault_blob_base64 or "").strip()
    if not blob:
        raise ValueError("vault_blob_base64 is required")
    _vault_blob_backup_dir().mkdir(parents=True, exist_ok=True)
    payload = {
        "runtime_profile_id": rp_id,
        "vault_blob_base64": blob,
        "environment": str(environment or "dev").strip() or "dev",
        "backup_version": str(backup_version or "vault_blob_v1").strip() or "vault_blob_v1",
        "stored_at_iso": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "blob_size_bytes": len(blob),
    }
    path = _vault_backup_blob_path(rp_id)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        _os.chmod(path, 0o600)
    except Exception:
        pass
    return {
        "ok": True,
        "status": "stored",
        "runtime_profile_id": rp_id,
        "blob_size_bytes": len(blob),
        "backup_version": payload["backup_version"],
    }


async def retrieve_vault_backup_blob(
    *,
    runtime_profile_id: str,
) -> Dict[str, Any]:
    """Retrieve the encrypted vault blob for a self-hosted node."""
    rp_id = str(runtime_profile_id or "").strip()
    if not rp_id:
        raise ValueError("runtime_profile_id is required")
    path = _vault_backup_blob_path(rp_id)
    if not path.exists():
        return {
            "ok": False,
            "status": "not_found",
            "runtime_profile_id": rp_id,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "status": "read_error",
            "runtime_profile_id": rp_id,
            "error": str(exc),
        }
    return {
        "ok": True,
        "status": "found",
        "runtime_profile_id": rp_id,
        "vault_blob_base64": str(payload.get("vault_blob_base64") or ""),
        "environment": str(payload.get("environment") or ""),
        "backup_version": str(payload.get("backup_version") or ""),
        "stored_at_iso": str(payload.get("stored_at_iso") or ""),
        "blob_size_bytes": int(payload.get("blob_size_bytes") or 0),
    }


def _self_hosted_runtime_node_id(profile: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    return str(profile.get("runtime_id") or metadata.get("runtime_node_id") or "").strip()


def _is_terminal_self_hosted_command_state(state: Any) -> bool:
    return str(state or "").strip().lower() in {"completed", "failed", "canceled", "expired"}


def _normalized_self_hosted_command_queue(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    queue: List[Dict[str, Any]] = []
    for item in _list_json(metadata.get("node_command_queue")):
        if isinstance(item, dict):
            queue.append(dict(item))
    return queue


async def _load_self_hosted_runtime_profile_for_node_session(
    *,
    runtime_profile_id: str,
    node_session_token: str,
) -> tuple[Dict[str, Any], Dict[str, Any], str]:
    profile = await get_runtime_profile(runtime_profile_id)
    if not isinstance(profile, dict):
        raise ValueError("Self-hosted runtime profile not found.")
    if str(profile.get("runtime_class") or "").strip() != "self_hosted_business_node":
        raise ValueError("Runtime profile is not a self-hosted node.")
    metadata = _dict_json(profile.get("metadata"))
    expected = str(metadata.get("node_session_token_hash") or "").strip()
    provided = str(node_session_token or "").strip()
    if not provided or not expected or _token_hash(provided) != expected:
        raise ValueError("Node session token is invalid.")
    runtime_id = _self_hosted_runtime_node_id(profile, metadata)
    if not runtime_id:
        raise ValueError("Self-hosted runtime profile is missing runtime_id.")
    return profile, metadata, runtime_id


async def enqueue_self_hosted_runtime_command(
    *,
    runtime_profile_id: str,
    tenant_id: str,
    workspace_id: str,
    runtime_node_id: str,
    agent_id: str,
    command_type: str,
    command_payload: Dict[str, Any],
    requested_by_user_id: Optional[str] = None,
    ttl_seconds: int = SELF_HOSTED_NODE_COMMAND_DEFAULT_TTL_SECONDS,
) -> Dict[str, Any]:
    profile = await get_runtime_profile(
        runtime_profile_id,
        tenant_id=str(tenant_id or "").strip() or "default",
        workspace_id=str(workspace_id or "").strip() or "default",
    )
    if not isinstance(profile, dict):
        raise ValueError("Self-hosted runtime profile not found in workspace scope.")
    if str(profile.get("runtime_class") or "").strip() != "self_hosted_business_node":
        raise ValueError("Runtime profile is not a self-hosted node.")
    metadata = _dict_json(profile.get("metadata"))
    if not bool(metadata.get("owner_approved")):
        raise ValueError("Self-hosted node is not owner approved.")
    profile_runtime_node_id = _self_hosted_runtime_node_id(profile, metadata)
    if not profile_runtime_node_id:
        raise ValueError("Self-hosted node profile is missing its node id.")
    if str(runtime_node_id or "").strip() != profile_runtime_node_id:
        raise ValueError("runtime_node_id does not match runtime profile binding.")
    scoped_workspace_id = str(profile.get("workspace_id") or "").strip() or "default"
    if scoped_workspace_id != str(workspace_id or "").strip():
        raise ValueError("workspace_id scope mismatch for self-hosted command enqueue.")

    normalized_payload = _dict_json(command_payload)
    normalized_agent_id = str(agent_id or "").strip()
    normalized_type = str(command_type or "").strip().lower() or "runtime_action"
    if not normalized_agent_id:
        raise ValueError("agent_id is required for self-hosted command enqueue.")
    ttl = int(ttl_seconds or SELF_HOSTED_NODE_COMMAND_DEFAULT_TTL_SECONDS)
    ttl = max(SELF_HOSTED_NODE_COMMAND_MIN_TTL_SECONDS, min(ttl, SELF_HOSTED_NODE_COMMAND_MAX_TTL_SECONDS))
    now_iso = _utc_now_iso()
    expires_at = _utc_iso_after_seconds(ttl)

    command_id = f"shcmd_{uuid.uuid4().hex[:16]}"
    command_record: Dict[str, Any] = {
        "id": command_id,
        "workspace_id": scoped_workspace_id,
        "runtime_node_id": profile_runtime_node_id,
        "agent_id": normalized_agent_id,
        "command_type": normalized_type,
        "command_payload": normalized_payload,
        "state": "queued",
        "created_at": now_iso,
        "expires_at": expires_at,
        "issued_by_user_id": _normalize_token(requested_by_user_id),
    }
    command_record["command_hash"] = _self_hosted_command_hash(command_record)

    queue = _normalized_self_hosted_command_queue(metadata)
    queue.append(command_record)
    terminal_items: List[Dict[str, Any]] = [item for item in queue if _is_terminal_self_hosted_command_state(item.get("state"))]
    active_items: List[Dict[str, Any]] = [item for item in queue if not _is_terminal_self_hosted_command_state(item.get("state"))]
    if len(active_items) > SELF_HOSTED_NODE_COMMAND_QUEUE_LIMIT:
        raise ValueError("Self-hosted command queue limit reached for this node.")
    while len(active_items) + len(terminal_items) > SELF_HOSTED_NODE_COMMAND_QUEUE_LIMIT:
        terminal_items.pop(0)
    metadata["node_command_queue"] = active_items + terminal_items
    control_plane_repository._enforce_control_plane_service_decision(
        operation="self_hosted_command_enqueue",
        tenant_id=str(profile.get("tenant_id") or "default").strip() or "default",
        workspace_id=scoped_workspace_id,
        actor_id=_normalize_token(requested_by_user_id) or normalized_agent_id,
        actor_role="system",
        record_type="self_hosted_runtime_command",
        idempotency_key=command_id,
        agent_id=normalized_agent_id,
        source="self_hosted_runtime",
    )

    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise RuntimeError("Control-plane storage is unavailable.")

    await pool.execute(
        """
        UPDATE runtime_profiles
        SET
            metadata = $4::jsonb,
            updated_at = NOW()
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        str(profile.get("id") or runtime_profile_id).strip(),
        str(profile.get("tenant_id") or "default").strip() or "default",
        scoped_workspace_id,
        _to_json(metadata, default={}),
    )
    return {
        "ok": True,
        "runtime_profile_id": str(profile.get("id") or runtime_profile_id).strip(),
        "runtime_node_id": profile_runtime_node_id,
        "workspace_id": scoped_workspace_id,
        "command": dict(command_record),
    }


async def claim_self_hosted_runtime_commands(
    *,
    runtime_profile_id: str,
    node_session_token: str,
    max_commands: int = 1,
    lease_seconds: int = SELF_HOSTED_NODE_COMMAND_DEFAULT_LEASE_SECONDS,
) -> Dict[str, Any]:
    profile, metadata, runtime_id = await _load_self_hosted_runtime_profile_for_node_session(
        runtime_profile_id=runtime_profile_id,
        node_session_token=node_session_token,
    )
    workspace_id = str(profile.get("workspace_id") or "").strip() or "default"
    now = _utc_now()
    now_iso = now.isoformat().replace("+00:00", "Z")
    lease = int(lease_seconds or SELF_HOSTED_NODE_COMMAND_DEFAULT_LEASE_SECONDS)
    lease = max(SELF_HOSTED_NODE_COMMAND_MIN_LEASE_SECONDS, min(lease, SELF_HOSTED_NODE_COMMAND_MAX_LEASE_SECONDS))
    max_take = max(1, min(int(max_commands or 1), 50))

    queue = _normalized_self_hosted_command_queue(metadata)
    claimed_commands: List[Dict[str, Any]] = []
    mutated = False

    for item in queue:
        if len(claimed_commands) >= max_take:
            break
        state = str(item.get("state") or "").strip().lower() or "queued"
        expires_at_dt = _iso_to_datetime(item.get("expires_at"))
        if expires_at_dt is not None and expires_at_dt <= now:
            if state not in {"completed", "failed", "expired", "canceled"}:
                item["state"] = "expired"
                item["expired_at"] = now_iso
                mutated = True
            continue
        if state == "claimed":
            claim_expires_at = _iso_to_datetime(item.get("claim_expires_at"))
            if claim_expires_at is None or claim_expires_at > now:
                continue
        elif state != "queued":
            continue

        lease_expires_at = (now + timedelta(seconds=lease)).isoformat().replace("+00:00", "Z")
        claim_nonce = f"nonce_{uuid.uuid4().hex[:12]}"
        item["state"] = "claimed"
        item["claimed_at"] = now_iso
        item["claim_expires_at"] = lease_expires_at
        item["claimed_by_runtime_node_id"] = runtime_id
        item["claim_nonce"] = claim_nonce
        item["last_claimed_at"] = now_iso
        command_hash = str(item.get("command_hash") or "").strip() or _self_hosted_command_hash(item)
        item["command_hash"] = command_hash
        signature_payload = f"{runtime_profile_id}:{str(item.get('id') or '').strip()}:{command_hash}:{claim_nonce}"
        item["delivery_signature"] = _hmac_sha256(node_session_token, signature_payload)
        claimed_commands.append(
            {
                "id": str(item.get("id") or "").strip(),
                "workspace_id": str(item.get("workspace_id") or "").strip(),
                "runtime_node_id": str(item.get("runtime_node_id") or "").strip(),
                "agent_id": str(item.get("agent_id") or "").strip(),
                "command_type": str(item.get("command_type") or "").strip() or "runtime_action",
                "command_payload": _dict_json(item.get("command_payload")),
                "created_at": _iso(item.get("created_at")),
                "expires_at": _iso(item.get("expires_at")),
                "claimed_at": now_iso,
                "claim_expires_at": lease_expires_at,
                "claim_nonce": claim_nonce,
                "command_hash": command_hash,
                "command_signature": str(item.get("delivery_signature") or "").strip(),
            }
        )
        mutated = True

    if mutated:
        control_plane_repository._enforce_control_plane_service_decision(
            operation="self_hosted_command_claim",
            tenant_id=str(profile.get("tenant_id") or "default").strip() or "default",
            workspace_id=workspace_id,
            actor_id=runtime_id,
            actor_role="system",
            record_type="self_hosted_runtime_command",
            idempotency_key=f"{runtime_profile_id}:{now_iso}:{len(claimed_commands)}",
            source="self_hosted_runtime",
        )
        metadata["node_command_queue"] = queue
        metadata["heartbeat_at"] = now_iso
        pool = await control_plane_repository.ensure_control_plane_schema()
        if pool is None:
            raise RuntimeError("Control-plane storage is unavailable.")
        await pool.execute(
            """
            UPDATE runtime_profiles
            SET
                metadata = $4::jsonb,
                updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
            """,
            str(profile.get("id") or runtime_profile_id).strip(),
            str(profile.get("tenant_id") or "default").strip() or "default",
            workspace_id,
            _to_json(metadata, default={}),
        )

    return {
        "ok": True,
        "runtime_profile_id": str(profile.get("id") or runtime_profile_id).strip(),
        "runtime_node_id": runtime_id,
        "workspace_id": workspace_id,
        "commands": claimed_commands,
        "claimed_count": len(claimed_commands),
        "claimed_at": now_iso,
    }


async def complete_self_hosted_runtime_command(
    *,
    runtime_profile_id: str,
    node_session_token: str,
    command_id: str,
    status: str,
    result_payload: Optional[Dict[str, Any]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    audit_references: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    profile, metadata, runtime_id = await _load_self_hosted_runtime_profile_for_node_session(
        runtime_profile_id=runtime_profile_id,
        node_session_token=node_session_token,
    )
    workspace_id = str(profile.get("workspace_id") or "").strip() or "default"
    queue = _normalized_self_hosted_command_queue(metadata)
    now_iso = _utc_now_iso()
    target_id = str(command_id or "").strip()
    if not target_id:
        raise ValueError("command_id is required.")

    resolved: Optional[Dict[str, Any]] = None
    for item in queue:
        if str(item.get("id") or "").strip() != target_id:
            continue
        resolved = item
        break
    if resolved is None:
        raise ValueError("Self-hosted runtime command was not found.")
    if str(resolved.get("runtime_node_id") or "").strip() != runtime_id:
        raise ValueError("Self-hosted runtime command runtime_node_id scope mismatch.")
    if str(resolved.get("workspace_id") or "").strip() != workspace_id:
        raise ValueError("Self-hosted runtime command workspace scope mismatch.")

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"completed", "failed"}:
        raise ValueError("status must be completed or failed.")

    resolved["state"] = normalized_status
    resolved["completed_at"] = now_iso
    resolved["result_payload"] = _dict_json(result_payload)
    resolved["artifacts"] = [dict(item) for item in list(artifacts or []) if isinstance(item, dict)]
    resolved["audit_references"] = [dict(item) for item in list(audit_references or []) if isinstance(item, dict)]
    resolved["error"] = str(error or "").strip() or None
    resolved["claim_expires_at"] = None
    control_plane_repository._enforce_control_plane_service_decision(
        operation="self_hosted_command_complete",
        tenant_id=str(profile.get("tenant_id") or "default").strip() or "default",
        workspace_id=workspace_id,
        actor_id=runtime_id,
        actor_role="system",
        record_type="self_hosted_runtime_command",
        idempotency_key=target_id,
        source="self_hosted_runtime",
        target_status=normalized_status,
    )

    metadata["node_command_queue"] = queue
    metadata["heartbeat_at"] = now_iso
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise RuntimeError("Control-plane storage is unavailable.")
    await pool.execute(
        """
        UPDATE runtime_profiles
        SET
            metadata = $4::jsonb,
            updated_at = NOW()
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        str(profile.get("id") or runtime_profile_id).strip(),
        str(profile.get("tenant_id") or "default").strip() or "default",
        workspace_id,
        _to_json(metadata, default={}),
    )
    return {
        "ok": True,
        "runtime_profile_id": str(profile.get("id") or runtime_profile_id).strip(),
        "runtime_node_id": runtime_id,
        "workspace_id": workspace_id,
        "command_id": target_id,
        "status": normalized_status,
        "completed_at": now_iso,
        "command": dict(resolved),
        "result_payload": _dict_json(result_payload),
        "artifacts": [dict(item) for item in list(artifacts or []) if isinstance(item, dict)],
        "audit_references": [dict(item) for item in list(audit_references or []) if isinstance(item, dict)],
        "error": str(error or "").strip() or None,
    }


async def list_agent_definitions(
    *,
    tenant_id: str,
    workspace_id: str,
    include_private: bool = False,
) -> List[Dict[str, Any]]:
    await ensure_workspace_agent_registry_seeded(tenant_id=tenant_id, workspace_id=workspace_id)
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await pool.fetch(
        """
        SELECT
            ad.*,
            adv.id AS version_id,
            adv.version_number,
            adv.status AS version_status,
            adv.manifest,
            adv.capability_manifest,
            adv.memory_scope_manifest,
            adv.policy_manifest,
            adv.placement_manifest,
            adv.template_inputs_schema,
            adv.metadata AS version_metadata,
            adv.compiled_workflow_version_id AS version_compiled_workflow_version_id
        FROM agent_definitions ad
        LEFT JOIN agent_definition_versions adv
            ON adv.id = COALESCE(ad.published_version_id, ad.current_version_id)
        WHERE ad.tenant_id = $1
          AND ad.workspace_id = $2
          AND ($3::bool OR COALESCE(ad.visibility, 'workspace') <> 'private')
        ORDER BY ad.updated_at DESC, ad.name ASC
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        bool(include_private),
    )
    return [item for item in (_row_to_agent_definition(row) for row in rows) if item]


async def get_agent_definition(
    definition_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
) -> Optional[Dict[str, Any]]:
    await ensure_workspace_agent_registry_seeded(tenant_id=tenant_id, workspace_id=workspace_id)
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        """
        SELECT
            ad.*,
            adv.id AS version_id,
            adv.version_number,
            adv.status AS version_status,
            adv.manifest,
            adv.capability_manifest,
            adv.memory_scope_manifest,
            adv.policy_manifest,
            adv.placement_manifest,
            adv.template_inputs_schema,
            adv.metadata AS version_metadata,
            adv.compiled_workflow_version_id AS version_compiled_workflow_version_id
        FROM agent_definitions ad
        LEFT JOIN agent_definition_versions adv
            ON adv.id = COALESCE(ad.published_version_id, ad.current_version_id)
        WHERE ad.id = $1 AND ad.tenant_id = $2 AND ad.workspace_id = $3
        LIMIT 1
        """,
        str(definition_id or "").strip(),
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
    )
    return _row_to_agent_definition(row)


def _list_workspace_agent_installs_local(
    *,
    tenant_id: str,
    workspace_id: str,
    include_master: bool = False,
) -> List[Dict[str, Any]]:
    """SQLite fallback for list_workspace_agent_installs when Postgres is unavailable."""
    from server_modules.control_plane_repository import _connect_local_control_plane_db
    try:
        with _connect_local_control_plane_db() as connection:
            rows = connection.execute(
                """
                SELECT
                    wai.*,
                    ad.slug AS agent_definition_slug,
                    ad.name AS agent_definition_name,
                    ad.description AS agent_definition_description,
                    ad.category AS agent_definition_category,
                    ad.icon AS agent_definition_icon,
                    ad.agent_kind,
                    adv.version_number AS definition_version_number,
                    adv.manifest,
                    adv.capability_manifest,
                    adv.policy_manifest,
                    adv.placement_manifest,
                    rp.slug AS runtime_profile_slug,
                    rp.label AS runtime_profile_label,
                    rp.runtime_class,
                    rp.placement_mode,
                    rp.runtime_id,
                    rp.machine_id,
                    rp.default_execution_target,
                    rp.status AS runtime_profile_status
                FROM workspace_agent_installs wai
                INNER JOIN agent_definitions ad
                    ON ad.id = wai.agent_definition_id
                INNER JOIN agent_definition_versions adv
                    ON adv.id = wai.agent_definition_version_id
                LEFT JOIN runtime_profiles rp
                    ON rp.id = wai.runtime_profile_id
                WHERE wai.tenant_id = ?
                  AND wai.workspace_id = ?
                  AND (? OR COALESCE(ad.agent_kind, 'specialist') <> 'master')
                ORDER BY wai.updated_at DESC, wai.created_at DESC
                """,
                (
                    str(tenant_id or "").strip(),
                    str(workspace_id or "").strip(),
                    1 if include_master else 0,
                ),
            ).fetchall()
            return [item for item in (_row_to_install_summary(dict(r)) for r in rows) if item]
    except Exception:
        return []


async def list_workspace_agent_installs(
    *,
    tenant_id: str,
    workspace_id: str,
    include_master: bool = False,
) -> List[Dict[str, Any]]:
    await ensure_workspace_agent_registry_seeded(tenant_id=tenant_id, workspace_id=workspace_id)
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        # Phase U4: SQLite fallback — query local control-plane DB when
        # Postgres is unavailable, so the fleet UI renders real agent cards.
        return _list_workspace_agent_installs_local(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            include_master=include_master,
        )
    rows = await pool.fetch(
        """
        SELECT
            wai.*,
            ad.slug AS agent_definition_slug,
            ad.name AS agent_definition_name,
            ad.description AS agent_definition_description,
            ad.category AS agent_definition_category,
            ad.icon AS agent_definition_icon,
            ad.agent_kind,
            adv.version_number AS definition_version_number,
            adv.manifest,
            adv.capability_manifest,
            adv.policy_manifest,
            adv.placement_manifest,
            rp.slug AS runtime_profile_slug,
            rp.label AS runtime_profile_label,
            rp.runtime_class,
            rp.placement_mode,
            rp.runtime_id,
            rp.machine_id,
            rp.default_execution_target,
            rp.status AS runtime_profile_status,
            arp.runtime_mode,
            cwv.workflow_id AS compiled_workflow_id
        FROM workspace_agent_installs wai
        INNER JOIN agent_definitions ad
            ON ad.id = wai.agent_definition_id
        INNER JOIN agent_definition_versions adv
            ON adv.id = wai.agent_definition_version_id
        LEFT JOIN runtime_profiles rp
            ON rp.id = wai.runtime_profile_id
        LEFT JOIN agent_runtime_profiles arp
            ON arp.agent_install_id = wai.id
           AND arp.tenant_id = wai.tenant_id
           AND arp.workspace_id = wai.workspace_id
        LEFT JOIN workflow_versions cwv
            ON cwv.id = wai.compiled_workflow_version_id
        WHERE wai.tenant_id = $1
          AND wai.workspace_id = $2
          AND ($3::bool OR COALESCE(ad.agent_kind, 'specialist') <> 'master')
        ORDER BY wai.updated_at DESC, wai.created_at DESC
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        bool(include_master),
    )
    return [item for item in (_row_to_install_summary(row) for row in rows) if item]


async def get_workspace_master_agent_install(
    *,
    tenant_id: str,
    workspace_id: str,
    created_by_user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    await ensure_workspace_agent_registry_seeded(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        created_by_user_id=created_by_user_id,
    )
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        """
        SELECT wai.id
        FROM workspace_agent_installs wai
        INNER JOIN agent_definitions ad
            ON ad.id = wai.agent_definition_id
        WHERE wai.tenant_id = $1
          AND wai.workspace_id = $2
          AND ad.agent_kind = 'master'
        ORDER BY wai.updated_at DESC, wai.created_at DESC
        LIMIT 1
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
    )
    install_id = str(row.get("id") or "").strip() if row is not None else ""
    if not install_id:
        return None
    return await get_workspace_agent_install_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


def _pick_definition_version_id(definition: Dict[str, Any], requested_version_id: Optional[str]) -> Optional[str]:
    requested = _normalize_token(requested_version_id)
    if requested:
        return requested
    return _normalize_token(definition.get("published_version_id")) or _normalize_token(definition.get("current_version_id")) or _normalize_token(_dict_json(definition.get("current_version")).get("id"))


def _default_runtime_profile_id(definition: Dict[str, Any], runtime_profiles: List[Dict[str, Any]]) -> Optional[str]:
    current_version = _dict_json(definition.get("current_version"))
    placement_manifest = _dict_json(current_version.get("placement_manifest"))
    preferred_slug = _normalize_token(placement_manifest.get("preferred_runtime_slug"))
    for profile in runtime_profiles:
        if preferred_slug and str(profile.get("slug") or "").strip() == preferred_slug:
            return _normalize_token(profile.get("id"))
    return _normalize_token(runtime_profiles[0].get("id")) if runtime_profiles else None


async def create_workspace_agent_install(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_definition_id: str,
    agent_definition_version_id: Optional[str] = None,
    installed_by_user_id: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    label: Optional[str] = None,
    runtime_profile_id: Optional[str] = None,
    root_folder_uri: Optional[str] = None,
    tool_toggles: Optional[Dict[str, Any]] = None,
    folder_grants: Optional[List[Any]] = None,
    connector_bindings: Optional[Dict[str, Any]] = None,
    memory_scope_overrides: Optional[Dict[str, Any]] = None,
    policy_context_overrides: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    definition = await get_agent_definition(
        agent_definition_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not isinstance(definition, dict):
        return None
    agent_kind = str(definition.get("agent_kind") or "").strip().lower() or SPECIALIST_AGENT_KIND
    runtime_profiles = await list_runtime_profiles(tenant_id=tenant_id, workspace_id=workspace_id)
    resolved_runtime_profile_id = _normalize_token(runtime_profile_id) or _default_runtime_profile_id(definition, runtime_profiles)
    current_version = _dict_json(definition.get("current_version"))
    capability_manifest = _dict_json(current_version.get("capability_manifest"))
    default_toggles = _default_tool_toggles_from_capabilities(capability_manifest)
    merged_toggles = {**default_toggles, **_dict_json(tool_toggles)}
    merged_policy = {
        "trust_mode": str(_dict_json(current_version.get("policy_manifest")).get("trust_mode") or "guarded").strip() or "guarded",
        **_dict_json(policy_context_overrides),
    }
    install_id = f"ainstall_{uuid.uuid4().hex[:16]}"
    resolved_label = _normalize_token(label) or str(definition.get("name") or "").strip() or "Installed Agent"
    normalized_metadata = normalize_install_contract_metadata(
        install_id=install_id,
        label=resolved_label,
        agent_kind=agent_kind,
        metadata=metadata,
    )
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_version_id = _pick_definition_version_id(definition, agent_definition_version_id)
    await pool.execute(
        """
        INSERT INTO workspace_agent_installs (
            id, tenant_id, workspace_id, agent_definition_id, agent_definition_version_id, installed_by_user_id,
            install_scope, owner_user_id, thread_id, label, status, enabled, runtime_profile_id, compiled_workflow_version_id,
            root_folder_uri, tool_toggles, folder_grants, connector_bindings, memory_scope_overrides,
            policy_context_overrides, metadata, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6,
            'workspace', $7, $8, $9, 'active', TRUE, $10, NULL,
            $11, $12::jsonb, $13::jsonb, $14::jsonb, $15::jsonb,
            $16::jsonb, $17::jsonb, NOW(), NOW()
        )
        """,
        install_id,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(agent_definition_id or "").strip(),
        resolved_version_id,
        _normalize_token(installed_by_user_id),
        _normalize_token(owner_user_id),
        _normalize_token(thread_id),
        resolved_label,
        resolved_runtime_profile_id,
        _normalize_token(root_folder_uri),
        _to_json(merged_toggles, default={}),
        _to_json(folder_grants, default=[]),
        _to_json(connector_bindings, default={}),
        _to_json(memory_scope_overrides, default={}),
        _to_json(merged_policy, default={}),
        _to_json(normalized_metadata, default={}),
    )
    return await get_workspace_agent_install_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def update_workspace_agent_install(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    label: Optional[str] = None,
    runtime_profile_id: Optional[str] = None,
    root_folder_uri: Optional[str] = None,
    tool_toggles: Optional[Dict[str, Any]] = None,
    folder_grants: Optional[List[Any]] = None,
    connector_bindings: Optional[Dict[str, Any]] = None,
    memory_scope_overrides: Optional[Dict[str, Any]] = None,
    policy_context_overrides: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
    status: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    existing = await get_workspace_agent_install_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not isinstance(existing, dict):
        return None
    agent_kind = str(_dict_json(existing.get("agent_definition")).get("agent_kind") or "").strip().lower() or SPECIALIST_AGENT_KIND
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    next_tool_toggles = {**_dict_json(existing.get("tool_toggles")), **_dict_json(tool_toggles)}
    next_policy = {**_dict_json(existing.get("policy_context_overrides")), **_dict_json(policy_context_overrides)}
    next_metadata = {**_dict_json(existing.get("metadata")), **_dict_json(metadata)}
    next_label = _normalize_token(label) or _normalize_token(existing.get("label")) or _normalize_token(_dict_json(existing.get("agent_definition")).get("name"))
    normalized_metadata = normalize_install_contract_metadata(
        install_id=str(install_id or "").strip() or None,
        label=next_label,
        agent_kind=agent_kind,
        metadata=next_metadata,
    )
    await pool.execute(
        """
        UPDATE workspace_agent_installs
        SET
            label = $4,
            runtime_profile_id = $5,
            root_folder_uri = $6,
            tool_toggles = $7::jsonb,
            folder_grants = $8::jsonb,
            connector_bindings = $9::jsonb,
            memory_scope_overrides = $10::jsonb,
            policy_context_overrides = $11::jsonb,
            enabled = $12,
            status = $13,
            metadata = $14::jsonb,
            updated_at = NOW()
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        str(install_id or "").strip(),
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        next_label,
        _normalize_token(runtime_profile_id) if runtime_profile_id is not None else _normalize_token(existing.get("runtime_profile_id")),
        _normalize_token(root_folder_uri) if root_folder_uri is not None else _normalize_token(existing.get("root_folder_uri")),
        _to_json(next_tool_toggles, default={}),
        _to_json(folder_grants if folder_grants is not None else existing.get("folder_grants"), default=[]),
        _to_json(connector_bindings if connector_bindings is not None else existing.get("connector_bindings"), default={}),
        _to_json(memory_scope_overrides if memory_scope_overrides is not None else existing.get("memory_scope_overrides"), default={}),
        _to_json(next_policy, default={}),
        bool(enabled) if enabled is not None else bool(existing.get("enabled", True)),
        _normalize_token(status) or str(existing.get("status") or "active").strip() or "active",
        _to_json(normalized_metadata, default={}),
    )
    return await get_workspace_agent_install_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def get_runtime_profile(
    runtime_profile_id: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    clauses = ["id = $1"]
    params: List[Any] = [str(runtime_profile_id or "").strip()]
    if tenant_id:
        params.append(str(tenant_id or "").strip())
        clauses.append(f"tenant_id = ${len(params)}")
    if workspace_id:
        params.append(str(workspace_id or "").strip())
        clauses.append(f"workspace_id = ${len(params)}")
    row = await pool.fetchrow(
        f"""
        SELECT *
        FROM runtime_profiles
        WHERE {' AND '.join(clauses)}
        LIMIT 1
        """,
        *params,
    )
    return _row_to_runtime_profile(row)


async def get_workspace_agent_install_bundle(
    install_id: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    clauses = ["wai.id = $1"]
    params: List[Any] = [str(install_id or "").strip()]
    if tenant_id:
        params.append(str(tenant_id or "").strip())
        clauses.append(f"wai.tenant_id = ${len(params)}")
    if workspace_id:
        params.append(str(workspace_id or "").strip())
        clauses.append(f"wai.workspace_id = ${len(params)}")
    query = f"""
    SELECT
        wai.*,
        ad.slug AS agent_definition_slug,
        ad.name AS agent_definition_name,
        ad.description AS agent_definition_description,
        ad.agent_kind,
        ad.visibility AS agent_definition_visibility,
        ad.status AS agent_definition_status,
        ad.source_workflow_definition_id,
        ad.metadata AS agent_definition_metadata,
        adv.status AS agent_definition_version_status,
        adv.manifest,
        adv.capability_manifest,
        adv.memory_scope_manifest,
        adv.policy_manifest,
        adv.placement_manifest,
        adv.template_inputs_schema,
        adv.metadata AS agent_definition_version_metadata,
        adv.compiled_workflow_version_id AS definition_compiled_workflow_version_id,
        rp.slug AS runtime_profile_slug,
        rp.label AS runtime_profile_label,
        rp.runtime_class,
        rp.placement_mode,
        rp.runtime_id,
        rp.machine_id,
        rp.default_execution_target,
        rp.supported_capabilities,
        rp.root_folder_uri AS runtime_profile_root_folder_uri,
        rp.allowed_connector_scopes,
        rp.status AS runtime_profile_status,
        rp.last_seen_at AS runtime_profile_last_seen_at,
        rp.metadata AS runtime_profile_metadata,
        arp.runtime_mode,
        cwv.workflow_id AS compiled_workflow_id
    FROM workspace_agent_installs wai
    INNER JOIN agent_definitions ad
        ON ad.id = wai.agent_definition_id
    INNER JOIN agent_definition_versions adv
        ON adv.id = wai.agent_definition_version_id
    LEFT JOIN runtime_profiles rp
        ON rp.id = wai.runtime_profile_id
    LEFT JOIN agent_runtime_profiles arp
        ON arp.agent_install_id = wai.id
       AND arp.tenant_id = wai.tenant_id
       AND arp.workspace_id = wai.workspace_id
    LEFT JOIN workflow_versions cwv
        ON cwv.id = wai.compiled_workflow_version_id
    WHERE {' AND '.join(clauses)}
    LIMIT 1
    """
    if tenant_id and workspace_id:
        async with control_plane_repository._scoped_connection(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        ) as connection:
            if connection is None:
                return None
            row = await connection.fetchrow(query, *params)
    else:
        pool = await control_plane_repository.ensure_control_plane_schema()
        if pool is None:
            return None
        row = await pool.fetchrow(query, *params)
    if row is None:
        return None
    payload = dict(row)
    agent_kind = str(payload.get("agent_kind") or "").strip() or SPECIALIST_AGENT_KIND
    label = _normalize_token(payload.get("label"))
    projected_contract = project_install_contract_fields(
        install_id=str(payload.get("id") or "").strip() or None,
        label=label or str(payload.get("agent_definition_name") or "").strip() or None,
        agent_kind=agent_kind,
        metadata=_dict_json(payload.get("metadata")),
    )
    runtime_profile = None
    if payload.get("runtime_profile_id"):
        runtime_profile = {
            "id": str(payload.get("runtime_profile_id") or "").strip(),
            "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
            "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
            "slug": str(payload.get("runtime_profile_slug") or "").strip() or None,
            "label": str(payload.get("runtime_profile_label") or "").strip() or None,
            "runtime_class": str(payload.get("runtime_class") or "").strip() or "cloud_worker",
            "placement_mode": str(payload.get("placement_mode") or "").strip() or "auto",
            "runtime_id": _normalize_token(payload.get("runtime_id")),
            "machine_id": _normalize_token(payload.get("machine_id")),
            "default_execution_target": str(payload.get("default_execution_target") or "").strip() or "auto",
            "supported_capabilities": _list_json(payload.get("supported_capabilities")),
            "root_folder_uri": _normalize_token(payload.get("runtime_profile_root_folder_uri")),
            "allowed_connector_scopes": _list_json(payload.get("allowed_connector_scopes")),
            "status": str(payload.get("runtime_profile_status") or "").strip() or "active",
            "last_seen_at": _iso(payload.get("runtime_profile_last_seen_at")),
            "metadata": _dict_json(payload.get("runtime_profile_metadata")),
        }
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "agent_definition_id": str(payload.get("agent_definition_id") or "").strip() or None,
        "agent_definition_version_id": str(payload.get("agent_definition_version_id") or "").strip() or None,
        "installed_by_user_id": _normalize_token(payload.get("installed_by_user_id")),
        "install_scope": str(payload.get("install_scope") or "").strip() or "workspace",
        "owner_user_id": _normalize_token(payload.get("owner_user_id")),
        "thread_id": _normalize_token(payload.get("thread_id")),
        "label": label,
        "status": str(payload.get("status") or "").strip() or "active",
        "enabled": bool(payload.get("enabled")),
        "runtime_profile_id": _normalize_token(payload.get("runtime_profile_id")),
        "runtime_mode": str(payload.get("runtime_mode") or "").strip() or "hosted_secure",
        "compiled_workflow_version_id": _normalize_token(payload.get("compiled_workflow_version_id")),
        "compiled_workflow_id": _normalize_token(payload.get("compiled_workflow_id")),
        "root_folder_uri": _normalize_token(payload.get("root_folder_uri")),
        "tool_toggles": _dict_json(payload.get("tool_toggles")),
        "folder_grants": _list_json(payload.get("folder_grants")),
        "connector_bindings": _dict_json(payload.get("connector_bindings")),
        "memory_scope_overrides": _dict_json(payload.get("memory_scope_overrides")),
        "policy_context_overrides": _dict_json(payload.get("policy_context_overrides")),
        "metadata": projected_contract["metadata"],
        "captain_identity": projected_contract["captain_identity"],
        "specialist_mode": projected_contract["specialist_mode"],
        "specialist_mode_contract": projected_contract["specialist_mode_contract"],
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
        "agent_definition": {
            "id": str(payload.get("agent_definition_id") or "").strip(),
            "slug": str(payload.get("agent_definition_slug") or "").strip() or None,
            "name": str(payload.get("agent_definition_name") or "").strip() or "Installed Agent",
            "description": str(payload.get("agent_definition_description") or "").strip(),
            "agent_kind": agent_kind,
            "visibility": str(payload.get("agent_definition_visibility") or "").strip() or "workspace",
            "status": str(payload.get("agent_definition_status") or "").strip() or "draft",
            "source_workflow_definition_id": _normalize_token(payload.get("source_workflow_definition_id")),
            "metadata": _dict_json(payload.get("agent_definition_metadata")),
        },
        "agent_definition_version": {
            "id": str(payload.get("agent_definition_version_id") or "").strip(),
            "status": str(payload.get("agent_definition_version_status") or "").strip() or "draft",
            "manifest": _dict_json(payload.get("manifest")),
            "capability_manifest": _dict_json(payload.get("capability_manifest")),
            "memory_scope_manifest": _dict_json(payload.get("memory_scope_manifest")),
            "policy_manifest": _dict_json(payload.get("policy_manifest")),
            "placement_manifest": _dict_json(payload.get("placement_manifest")),
            "template_inputs_schema": _dict_json(payload.get("template_inputs_schema")),
            "metadata": _dict_json(payload.get("agent_definition_version_metadata")),
            "compiled_workflow_version_id": _normalize_token(payload.get("definition_compiled_workflow_version_id")),
        },
        "runtime_profile": runtime_profile,
    }


async def fetch_workflow_snapshot(
    workflow_id: str,
    *,
    workflow_version_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    params: List[Any] = [str(workflow_id or "").strip()]
    clauses = ["wd.id = $1"]
    version_join = "LEFT JOIN workflow_versions wv ON wv.id = wd.current_version_id"
    if workflow_version_id:
        params.append(str(workflow_version_id or "").strip())
        version_join = f"LEFT JOIN workflow_versions wv ON wv.workflow_id = wd.id AND wv.id = ${len(params)}"
    if tenant_id:
        params.append(str(tenant_id or "").strip())
        clauses.append(f"wd.tenant_id = ${len(params)}")
    if workspace_id:
        params.append(str(workspace_id or "").strip())
        clauses.append(f"wd.workspace_id = ${len(params)}")
    row = await pool.fetchrow(
        f"""
        SELECT
            wd.id AS workflow_id,
            wd.tenant_id,
            wd.workspace_id,
            wd.name,
            wd.description,
            wd.status AS workflow_status,
            wd.updated_at,
            wv.id AS workflow_version_id,
            wv.version_number,
            wv.definition,
            wv.validation,
            wv.metadata,
            wv.created_at
        FROM workflow_definitions wd
        {version_join}
        WHERE {' AND '.join(clauses)}
        LIMIT 1
        """,
        *params,
    )
    return _row_to_workflow_snapshot(row)


async def create_compiled_workflow_artifact(
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    description: str,
    definition: Dict[str, Any],
    validation: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    created_by_user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    version_id = f"wfver_{uuid.uuid4().hex[:16]}"
    control_plane_repository._enforce_control_plane_service_decision(
        operation="compiled_workflow_artifact_create",
        tenant_id=str(tenant_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        actor_id=_normalize_token(created_by_user_id) or "workflow-compiler",
        actor_role="system",
        record_type="compiled_workflow_artifact",
        idempotency_key=f"{workflow_id}:{version_id}",
        source="template_compiler",
    )
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    now = datetime.now(timezone.utc)
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO workflow_definitions (
                    id, tenant_id, workspace_id, name, description, status, current_version_id, published_version_id,
                    created_by_user_id, last_run_at, metadata, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'compiled', $6, $6, $7, NULL, $8::jsonb, $9::timestamptz, $9::timestamptz
                )
                """,
                workflow_id,
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
                str(name or "").strip() or "Compiled Agent Artifact",
                str(description or "").strip(),
                version_id,
                _normalize_token(created_by_user_id),
                _to_json(metadata, default={}),
                now,
            )
            await connection.execute(
                """
                INSERT INTO workflow_versions (
                    id, tenant_id, workspace_id, workflow_id, version_number, status, definition, validation, created_by_user_id, metadata, created_at
                ) VALUES (
                    $1, $2, $3, $4, 1, 'compiled', $5::jsonb, $6::jsonb, $7, $8::jsonb, $9::timestamptz
                )
                """,
                version_id,
                str(tenant_id or "").strip(),
                str(workspace_id or "").strip(),
                workflow_id,
                _to_json(definition, default={}),
                _to_json(validation, default={}),
                _normalize_token(created_by_user_id),
                _to_json(metadata, default={}),
                now,
            )
    return await fetch_workflow_snapshot(
        workflow_id,
        workflow_version_id=version_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def update_workspace_agent_install_compiled_artifact(
    install_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    compiled_workflow_version_id: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    control_plane_repository._enforce_control_plane_service_decision(
        operation="workspace_agent_install_compiled_artifact_update",
        tenant_id=str(tenant_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        actor_id=str(install_id or "").strip() or "workspace-agent-install",
        actor_role="system",
        record_type="workspace_agent_install",
        idempotency_key=f"{str(install_id or '').strip()}:{_normalize_token(compiled_workflow_version_id) or 'none'}",
        source="template_compiler",
    )
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    await pool.execute(
        """
        UPDATE workspace_agent_installs
        SET
            compiled_workflow_version_id = $4,
            metadata = COALESCE(metadata, '{}'::jsonb) || $5::jsonb,
            updated_at = NOW()
        WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
        """,
        str(install_id or "").strip(),
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        _normalize_token(compiled_workflow_version_id),
        _to_json(metadata, default={}),
    )
    return await get_workspace_agent_install_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
