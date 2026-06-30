from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules import auth as auth_module
from server_modules import control_plane_repository
from server_modules import entitlements_service
from server_modules import runtime_attachment_service

_WORKSPACE_BOOTSTRAP_CACHE: Dict[str, Dict[str, Any]] = {}
_WORKSPACE_BOOTSTRAP_CACHE_LIMIT = 128


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    return bool(default)


def _version_component(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _entitlement_flags(entitlements: Dict[str, Any]) -> Dict[str, bool]:
    return {
        str(key): value
        for key, value in entitlements.items()
        if isinstance(value, bool)
    }


def _entitlement_limits(entitlements: Dict[str, Any]) -> Dict[str, int | float | None]:
    out: Dict[str, int | float | None] = {}
    for key, value in entitlements.items():
        if isinstance(value, bool):
            continue
        if value is None or isinstance(value, (int, float)):
            out[str(key)] = value
    return out


def _normalized_deployment_mode(raw_mode: Any) -> str:
    token = str(raw_mode or "").strip().lower()
    if token == "local_only":
        return "local_companion"
    if token == "self_hosted_business":
        return "self_host_runtime"
    if token == "hybrid":
        return "hybrid"
    return "cloud_default"


def _runtime_target_kind(target_id: str) -> str:
    if target_id == "local_companion":
        return "local_companion"
    if target_id == "sage_cloud_computer":
        return "cloud_computer"
    if target_id == "self_host_runtime":
        return "self_host_runtime"
    return "cloud_default"


def _runtime_target_trust_profile(target: Dict[str, Any]) -> Dict[str, Any]:
    target_id = str(target.get("target_id") or "").strip()
    supported_modes = {
        str(mode or "").strip()
        for mode in list(target.get("supports_runtime_modes") or [])
        if str(mode or "").strip()
    }
    if target_id == "local_companion":
        if "privileged_device" in supported_modes:
            return dict(runtime_attachment_service.TRUST_MODEL_MAP.get("privileged_device") or {})
        return dict(runtime_attachment_service.TRUST_MODEL_MAP.get("local_secure") or {})
    if target_id == "sage_cloud_computer":
        return dict(runtime_attachment_service.TRUST_MODEL_MAP.get("cloud_computer_secure") or {})
    if target_id == "self_host_runtime":
        return dict(runtime_attachment_service.TRUST_MODEL_MAP.get("self_hosted_business_node") or {})
    return dict(runtime_attachment_service.TRUST_MODEL_MAP.get("hosted_secure") or {})


def _runtime_target_status_label(target: Dict[str, Any]) -> str:
    target_id = str(target.get("target_id") or "").strip()
    available = bool(target.get("available"))
    online = bool(target.get("online"))
    healthy = bool(target.get("healthy"))
    if not available:
        if target_id == "local_companion":
            return "Needs pairing"
        if target_id == "sage_cloud_computer":
            return "Not enabled"
        if target_id == "self_host_runtime":
            return "Not configured"
        return "Unavailable"
    if not online:
        return "Offline"
    if not healthy:
        return "Needs attention"
    return "Ready"


def _runtime_target_status_reason(target: Dict[str, Any]) -> str:
    target_id = str(target.get("target_id") or "").strip()
    label = str(
        target.get("public_label")
        or target.get("canonical_label")
        or target.get("label")
        or runtime_attachment_service.public_runtime_target_label(target_id)
        or target_id
        or "Runtime"
    ).strip() or "Runtime"
    available = bool(target.get("available"))
    online = bool(target.get("online"))
    healthy = bool(target.get("healthy"))
    if target_id == "local_companion":
        if not available:
            return "No device is paired to this workspace yet, so Sage stays in cloud mode."
        if not online:
            return "This Device is paired but offline, so Sage will not start device work until it reconnects."
        if not healthy:
            return "This Device is connected but not healthy enough for device work yet."
        return "This Device is ready. Sensitive device actions follow the selected access mode."
    if target_id == "self_host_runtime":
        if not available:
            return "No Server/VPS runtime is configured for this workspace yet."
        if not online:
            return "The Server/VPS runtime is configured but currently offline."
        if not healthy:
            return "The Server/VPS runtime is reachable but needs attention before Sage can trust it."
        return "The Server/VPS runtime is ready under the workspace policy boundary."
    if target_id == "sage_cloud_computer":
        if not available:
            return "Cloud Computer is not enabled for this workspace. Sage will not allocate a hosted computer automatically."
        if not online:
            return "Cloud Computer is configured but no hosted session is currently available."
        if not healthy:
            return "Cloud Computer is reachable but needs attention before hosted computer work can start."
        return "Cloud Computer is ready as an optional metered computer. Personal device work still requires a paired device."
    if not available:
        return f"{label} is not available for this workspace right now."
    if not online or not healthy:
        return f"{label} is currently degraded, so Sage may not be able to start hosted execution."
    return "Cloud execution is ready and remains the default path for ordinary turns."


def _runtime_target_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    target_id = str(item.get("target_id") or "").strip()
    canonical_id = str(item.get("canonical_target_id") or runtime_attachment_service.canonical_runtime_target_id(target_id)).strip()
    public_label = str(
        item.get("public_label")
        or item.get("canonical_label")
        or runtime_attachment_service.public_runtime_target_label(target_id)
    ).strip() or target_id
    trust_profile = _runtime_target_trust_profile(item)
    execution_modes = [
        {
            "id": str(mode.get("id") or "").strip(),
            "label": str(mode.get("label") or "").strip() or str(mode.get("id") or "").strip(),
            "description": str(mode.get("description") or "").strip() or None,
            "available": bool(mode.get("available")),
            "runtimeAccessMode": str(mode.get("runtime_access_mode") or "").strip() or None,
            "requiresExplicitSelection": bool(mode.get("requires_explicit_selection")),
            "requiresOwnerApproval": bool(mode.get("requires_owner_approval")),
            "setupWarning": str(mode.get("setup_warning") or "").strip() or None,
        }
        for mode in list(item.get("execution_modes") or [])
        if isinstance(mode, dict) and str(mode.get("id") or "").strip()
    ]
    return {
        "id": target_id,
        "canonicalId": canonical_id,
        "runtimeFabricTarget": canonical_id,
        "legacyId": str(item.get("legacy_target_id") or "").strip() or None,
        "publicLabel": public_label,
        "label": public_label,
        "kind": _runtime_target_kind(target_id),
        "online": bool(item.get("online")),
        "preferred": bool(item.get("default_for_workspace")),
        "available": bool(item.get("available")),
        "healthy": bool(item.get("healthy")),
        "status": str(item.get("status") or "").strip() or "unavailable",
        "statusLabel": _runtime_target_status_label(item),
        "statusReason": _runtime_target_status_reason(item),
        "description": str(item.get("description") or "").strip() or None,
        "connectionMode": str(item.get("connection_mode") or "").strip() or None,
        "executionTarget": str(item.get("execution_target") or "").strip() or None,
        "trustTier": str(trust_profile.get("trust_tier") or "").strip() or None,
        "approvalMode": str(trust_profile.get("approval_mode") or "").strip() or None,
        "defaultRuntimeAccessMode": str(item.get("default_runtime_access_mode") or "").strip() or None,
        "supportsFullAccess": bool(item.get("supports_full_access")),
        "executionModes": execution_modes,
        "sampleAttachmentLabel": str(item.get("sample_attachment_label") or "").strip() or None,
    }


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)


def _cache_store(cache: Dict[str, Dict[str, Any]], key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if len(cache) >= _WORKSPACE_BOOTSTRAP_CACHE_LIMIT and key not in cache:
        cache.clear()
    cache[key] = copy.deepcopy(payload)
    return copy.deepcopy(payload)


def _workspace_record_version(workspace: Optional[Dict[str, Any]]) -> str:
    record = _coerce_dict(workspace)
    return _stable_json(
        {
            "updated_at": record.get("updated_at"),
            "workspace_id": str(record.get("workspace_id") or "").strip(),
            "tenant_id": str(record.get("tenant_id") or "").strip(),
            "name": str(record.get("name") or "").strip(),
            "workspace_type": str(record.get("workspace_type") or record.get("kind") or "").strip(),
            "metadata": _coerce_dict(record.get("metadata")),
        }
    )


def _resolve_bootstrap_tenant_id(
    *,
    current_user: Optional[Dict[str, Any]],
    membership_row: Optional[Dict[str, Any]],
    workspace_id: str,
    workspace_record: Optional[Dict[str, Any]],
) -> str:
    membership_tenant_id = str((membership_row or {}).get("tenant_id") or "").strip()
    workspace_tenant_id = str(_coerce_dict(workspace_record).get("tenant_id") or "").strip()
    if membership_tenant_id:
        return membership_tenant_id
    if workspace_tenant_id:
        return workspace_tenant_id
    return auth_module.workspace_tenant_id(current_user, workspace_id)


def _entitlement_state_version(entitlement_state: Any) -> str:
    return _stable_json(
        {
            "plan": str(getattr(entitlement_state, "plan_id", "") or "").strip(),
            "label": str(getattr(entitlement_state, "plan_label", "") or "").strip(),
            "source": str(getattr(entitlement_state, "source", "") or "").strip(),
            "entitlements": _coerce_dict(getattr(entitlement_state, "entitlements", {})),
        }
    )


def _build_workspace_bootstrap_payload(
    *,
    current_user: Optional[Dict[str, Any]],
    user: Dict[str, Any],
    user_id: str,
    membership_row: Optional[Dict[str, Any]],
    role: str,
    resolved_workspace_id: str,
    tenant_id: str,
    workspace_record: Optional[Dict[str, Any]],
    entitlement_state: Any,
    runtime_targets: Dict[str, Any],
) -> Dict[str, Any]:
    workspace_payload = _workspace_payload(
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        workspace=workspace_record,
        workspace_name=str((membership_row or {}).get("workspace_name") or "").strip() or None,
    )
    capability_flags = entitlements_service.workspace_capability_flags(state=entitlement_state)
    workspace_traits = _workspace_traits(
        workspace=_coerce_dict(workspace_record),
        role=role,
        capabilities=capability_flags,
    )
    shell_hints = _shell_hints(
        workspace_id=resolved_workspace_id,
        role=role,
        workspace=workspace_record,
        traits=workspace_traits,
    )
    identity_versions = _coerce_dict((current_user or {}).get("identity_versions"))
    membership_version = (
        f"{_version_component(identity_versions.get('membership_version') or 1)}"
        f":{_version_component((membership_row or {}).get('updated_at'))}"
    )
    capabilities = {
        **capability_flags,
        "workspace_admin_enabled": role in {"owner", "admin"},
        "platform_admin_enabled": bool((current_user or {}).get("auth_admin") or (current_user or {}).get("is_admin")),
        "billing_read_enabled": role in {"owner", "admin"},
        "billing_write_enabled": role in {"owner", "admin"},
        "routing_read_enabled": role in {"owner", "admin"},
        "routing_write_enabled": role in {"owner", "admin"},
        "document_workstation_enabled": bool(workspace_traits.get("documentHeavy")),
        "channel_pairing_enabled": (
            role in {"member", "owner", "admin"}
            and (
                bool(capability_flags.get("telegram_channel_enabled"))
                or bool(capability_flags.get("whatsapp_channel_enabled"))
            )
        ),
    }
    permissions = _membership_permissions(
        role=role,
        capabilities=capabilities,
        traits=workspace_traits,
    )

    return {
        "account": {
            "id": str(user.get("id") or user_id).strip(),
            "email": str(user.get("email") or (current_user or {}).get("email") or "").strip().lower(),
            "displayName": str(user.get("name") or user.get("display_name") or "").strip() or None,
        },
        "workspace": workspace_payload,
        "membership": {
            "role": role,
            "permissions": permissions,
            "version": membership_version,
        },
        "capabilities": capabilities,
        "entitlements": {
            "plan": entitlement_state.plan_id,
            "label": entitlement_state.plan_label,
            "source": entitlement_state.source,
            "flags": _entitlement_flags(entitlement_state.entitlements),
            "limits": _entitlement_limits(entitlement_state.entitlements),
        },
        "billing": {
            "currentPlan": entitlement_state.plan_id,
            "planLabel": entitlement_state.plan_label,
            "source": entitlement_state.source,
        },
        "workspaceTraits": workspace_traits,
        "runtime": {
            "deploymentMode": _normalized_deployment_mode(runtime_targets.get("deployment_mode")),
            "runtimeTargets": [
                _runtime_target_payload(item)
                for item in list(runtime_targets.get("targets") or [])
                if str(item.get("target_id") or "").strip()
            ],
        },
        "shellHints": {
            "defaultRoute": shell_hints["defaultRoute"],
            "preferredProfile": shell_hints["preferredProfile"],
            "setupCompleted": shell_hints["setupCompleted"],
            "requiresOnboarding": shell_hints["requiresOnboarding"],
        },
    }


def _workspace_traits(
    *,
    workspace: Dict[str, Any],
    role: str,
    capabilities: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = _coerce_dict(workspace.get("metadata"))
    shell_meta = {
        **_coerce_dict(metadata.get("shell")),
        **_coerce_dict(metadata.get("ui")),
        **_coerce_dict(metadata.get("workspace_traits")),
    }
    workspace_kind = str(workspace.get("workspace_type") or workspace.get("kind") or "personal").strip() or "personal"
    compliance_mode = (
        str(
            shell_meta.get("complianceMode")
            or shell_meta.get("compliance_mode")
            or metadata.get("compliance_mode")
            or "standard"
        ).strip()
        or "standard"
    )
    document_heavy = _coerce_bool(
        shell_meta.get("documentHeavy"),
        default=workspace_kind == "enterprise" or compliance_mode in {"legal", "financial"},
    )
    admin_heavy = _coerce_bool(
        shell_meta.get("adminHeavy"),
        default=workspace_kind in {"side_business", "team"} and role in {"owner", "admin"},
    )
    operating_mode = str(
        shell_meta.get("operatingMode")
        or (
            "document_workstation"
            if document_heavy and role in {"viewer", "member"}
            else "operations"
            if admin_heavy and role in {"owner", "admin"}
            else "personal"
        )
    ).strip() or "personal"
    default_surface = str(
        shell_meta.get("defaultSurface")
        or (
            "workstation"
            if operating_mode == "document_workstation"
            else "admin"
            if operating_mode == "operations"
            else "chat"
        )
    ).strip() or "chat"
    return {
        "operatingMode": operating_mode,
        "defaultSurface": default_surface,
        "documentHeavy": bool(document_heavy),
        "adminHeavy": bool(admin_heavy),
        "complianceMode": compliance_mode,
        "mobileEnabled": bool(capabilities.get("mobile_app_enabled")),
    }


def _preferred_shell_profile(*, role: str, traits: Dict[str, Any]) -> str:
    operating_mode = str(traits.get("operatingMode") or "").strip().lower()
    if operating_mode == "document_workstation":
        return "document_workstation_shell"
    if operating_mode == "operations" and role in {"owner", "admin"}:
        return "operations_admin_shell"
    return "personal_shell"


def _default_route(*, workspace_id: str, traits: Dict[str, Any]) -> str:
    default_surface = str(traits.get("defaultSurface") or "chat").strip().lower()
    if default_surface in {"dashboard", "admin"}:
        return f"/w/{workspace_id}/admin"
    if default_surface == "workstation":
        return f"/w/{workspace_id}/workstation"
    return f"/w/{workspace_id}/chat"


def _extract_workspace_id_from_route(route: Any) -> str:
    token = str(route or "").strip()
    if not token.startswith("/w/"):
        return ""
    suffix = token[3:]
    return suffix.split("/", 1)[0].strip()


def _normalize_shell_default_route(workspace_id: str, route: Any) -> str:
    token = str(route or "").strip()
    if not token or not token.startswith("/") or token.startswith("//"):
        return ""
    route_workspace_id = _extract_workspace_id_from_route(token)
    if token.startswith("/w/") and not route_workspace_id:
        return ""
    if route_workspace_id and route_workspace_id != str(workspace_id or "").strip():
        return ""
    normalized = token if route_workspace_id else f"/w/{workspace_id}{token}"
    if normalized == f"/w/{workspace_id}/dashboard":
        return f"/w/{workspace_id}/admin"
    if normalized.endswith("/dashboard") and _extract_workspace_id_from_route(normalized) == str(workspace_id or "").strip():
        return normalized[:-10] + "/admin"
    return normalized


def _shell_hints(
    *,
    workspace_id: str,
    role: str,
    workspace: Optional[Dict[str, Any]],
    traits: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = _coerce_dict(_coerce_dict(workspace).get("metadata"))
    shell_meta = _coerce_dict(metadata.get("shell"))
    preferred_profile = (
        str(shell_meta.get("preferredProfile") or "").strip()
        or _preferred_shell_profile(role=role, traits=traits)
    )
    default_route = (
        _normalize_shell_default_route(workspace_id, shell_meta.get("defaultRoute"))
        or _default_route(workspace_id=workspace_id, traits=traits)
    )
    explicit_preferred_profile = str(shell_meta.get("preferredProfile") or "").strip()
    workspace_name = str(_coerce_dict(workspace).get("name") or "").strip()
    setup_completed = (
        _coerce_bool(shell_meta.get("setupCompleted"), default=False)
        and bool(workspace_name)
        and bool(explicit_preferred_profile)
    )
    return {
        "defaultRoute": default_route,
        "preferredProfile": preferred_profile,
        "setupCompleted": bool(setup_completed),
        "requiresOnboarding": not bool(setup_completed),
    }


def _membership_permissions(*, role: str, capabilities: Dict[str, Any], traits: Dict[str, Any]) -> List[str]:
    permissions = {
        "workspace.read",
        "chat.read",
        "runs.read",
        "notifications.read",
    }
    if capabilities.get("artifacts_enabled"):
        permissions.add("artifacts.read")
    if role in {"member", "owner", "admin"}:
        permissions.update({"chat.write", "runs.write"})
    if capabilities.get("approvals_enabled"):
        permissions.add("approvals.read")
        if role in {"member", "owner", "admin"}:
            permissions.add("approvals.review")
    if capabilities.get("mobile_app_enabled"):
        permissions.add("mobile.use")
    if capabilities.get("telegram_channel_enabled"):
        permissions.add("channels.telegram.use")
    if capabilities.get("whatsapp_channel_enabled"):
        permissions.add("channels.whatsapp.use")
    if capabilities.get("channel_pairing_enabled"):
        permissions.update({"channels.pair", "channels.link.revoke"})
    if str(traits.get("operatingMode") or "").strip().lower() == "document_workstation":
        permissions.add("documents.workstation.use")
    if role in {"owner", "admin"}:
        permissions.update(
            {
                "workspace.admin",
                "members.manage",
                "billing.read",
                "billing.write",
                "routing.read",
                "routing.write",
            }
        )
    return sorted(permissions)


def _workspace_payload(
    *,
    workspace_id: str,
    tenant_id: str,
    workspace: Optional[Dict[str, Any]],
    workspace_name: Optional[str],
) -> Dict[str, Any]:
    record = _coerce_dict(workspace)
    kind = str(record.get("workspace_type") or record.get("kind") or "personal").strip() or "personal"
    label = (
        str(record.get("name") or "").strip()
        or str(workspace_name or "").strip()
        or workspace_id
    )
    return {
        "id": workspace_id,
        "tenantId": tenant_id,
        "label": label,
        "kind": kind,
    }


async def build_workspace_bootstrap(
    *,
    current_user: Optional[Dict[str, Any]],
    workspace_id: str,
) -> Dict[str, Any]:
    requested_workspace_id = str(workspace_id or "").strip()
    workspace_access = auth_module.workspace_access_map(current_user)
    if (
        requested_workspace_id
        and requested_workspace_id != "default"
        and workspace_access
        and requested_workspace_id not in workspace_access
    ):
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")

    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    user_id = str((current_user or {}).get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authenticated user is required.")

    user_bundle = await control_plane_repository.get_user_bundle_by_id(user_id)
    if not isinstance(user_bundle, dict):
        raise HTTPException(status_code=404, detail="User not found.")

    user = _coerce_dict(user_bundle.get("user"))
    memberships = [
        dict(item)
        for item in list(user_bundle.get("memberships") or [])
        if isinstance(item, dict)
    ]
    membership_row = next(
        (item for item in memberships if str(item.get("workspace_id") or "").strip() == resolved_workspace_id),
        None,
    )
    workspace_record = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    tenant_id = _resolve_bootstrap_tenant_id(
        current_user=current_user,
        membership_row=membership_row,
        workspace_id=resolved_workspace_id,
        workspace_record=workspace_record,
    )
    role = auth_module.workspace_role(current_user, resolved_workspace_id) or "viewer"

    entitlement_state = entitlements_service.resolve_workspace_entitlement_state_for_workspace_id(
        workspace_id=resolved_workspace_id,
        workspace=workspace_record,
    )
    runtime_targets = await runtime_attachment_service.list_workspace_runtime_targets(
        tenant_id=tenant_id,
        workspace_id=resolved_workspace_id,
        include_snapshot_version=True,
    )
    identity_versions = _coerce_dict((current_user or {}).get("identity_versions"))
    membership_version = (
        f"{_version_component(identity_versions.get('membership_version') or 1)}"
        f":{_version_component((membership_row or {}).get('updated_at'))}"
    )
    cache_key = _stable_json(
        {
            "account_id": str(user.get("id") or user_id).strip(),
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            "membership_version": membership_version,
            "workspace_version": _workspace_record_version(workspace_record),
            "entitlements_version": _entitlement_state_version(entitlement_state),
            "runtime_targets_version": str(runtime_targets.get("_snapshot_version") or "").strip(),
        }
    )
    cached_payload = _WORKSPACE_BOOTSTRAP_CACHE.get(cache_key)
    if cached_payload is not None:
        return copy.deepcopy(cached_payload)
    payload = _build_workspace_bootstrap_payload(
        current_user=current_user,
        user=user,
        user_id=user_id,
        membership_row=membership_row,
        role=role,
        resolved_workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        workspace_record=workspace_record,
        entitlement_state=entitlement_state,
        runtime_targets=runtime_targets,
    )
    return _cache_store(_WORKSPACE_BOOTSTRAP_CACHE, cache_key, payload)
