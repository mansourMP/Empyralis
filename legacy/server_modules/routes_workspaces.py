from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server_modules import auth as auth_module
from server_modules import control_plane_repository
from server_modules import rust_runtime_kernel_client
from server_modules import session_service
from server_modules import workspace_admin_service
from server_modules.transparency_settings_service import (
    TransparencySettings,
    get_transparency_settings,
    put_transparency_settings,
)
from server_modules.workspace_ai_route_service import (
    build_workspace_ai_route_payload,
    update_workspace_default_ai_route,
)
from server_modules.workspace_channel_operations_service import (
    CHANNEL_PROVIDERS,
    build_workspace_channel_operations,
)
from server_modules.workspace_bootstrap_service import build_workspace_bootstrap


router = APIRouter()
get_current_user = auth_module.get_current_user

VALID_WORKSPACE_TYPES = {"personal", "professional", "team"}
VALID_SHELL_PROFILES = {
    "personal_shell",
    "document_workstation_shell",
    "operations_admin_shell",
}
COMPUTER_RUNTIME_BINDINGS = {
    "cloud_computer_agent",
    "my_computer_agent",
    "self_hosted_agent",
}


def _require_workspace_type(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token not in VALID_WORKSPACE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"workspace_type must be one of: {', '.join(sorted(VALID_WORKSPACE_TYPES))}.",
        )
    return token


def _require_shell_profile(value: Any) -> str:
    token = str(value or "").strip()
    if token not in VALID_SHELL_PROFILES:
        raise HTTPException(
            status_code=400,
            detail="preferred_shell_profile is invalid.",
        )
    return token


def _require_default_route(value: Any) -> str:
    token = str(value or "").strip()
    if not token.startswith("/") or token.startswith("//"):
        raise HTTPException(status_code=400, detail="default_route must be a valid route path.")
    return token


def _extract_workspace_id_from_route(route: str) -> Optional[str]:
    token = str(route or "").strip()
    if not token.startswith("/w/"):
        return None
    suffix = token[3:]
    workspace_token = suffix.split("/", 1)[0].strip()
    return workspace_token or None


def _require_workspace_default_route(value: Any, *, workspace_id: Optional[str] = None) -> str:
    token = _require_default_route(value)
    route_workspace_id = _extract_workspace_id_from_route(token)
    if token.startswith("/w/") and route_workspace_id is None:
        raise HTTPException(status_code=400, detail="default_route must target a valid workspace route.")
    if route_workspace_id is None:
        return token
    if not workspace_id:
        raise HTTPException(
            status_code=400,
            detail="default_route must be workspace-relative and may not target another workspace.",
        )
    if route_workspace_id != workspace_id:
        raise HTTPException(
            status_code=400,
            detail="default_route must resolve inside the current workspace.",
        )
    return token


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _workspace_settings_fields_set(body: BaseModel) -> set[str]:
    fields = getattr(body, "model_fields_set", None)
    if fields is None:
        fields = getattr(body, "__fields_set__", set())
    return {str(item) for item in (fields or set())}


def _require_notification_channel_id(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    prefix = token.split(":", 1)[0].strip()
    allowed_prefixes = {str(item or "").strip() for item in CHANNEL_PROVIDERS}
    if prefix not in allowed_prefixes:
        raise HTTPException(status_code=400, detail="notification_channel_id must start with a known connector or channel type.")
    return token


def _control_plane_actor_id(current_user: Any, user: Optional[Dict[str, Any]] = None) -> str:
    record = user if isinstance(user, dict) else {}
    if not record:
        try:
            record = auth_module.get_authenticated_user_record(current_user)
        except Exception:
            record = {}
    for key in ("id", "user_id", "sub", "email"):
        token = str(record.get(key) or "").strip()
        if token:
            return token
    if isinstance(current_user, dict):
        for key in ("user_id", "id", "sub", "email"):
            token = str(current_user.get(key) or "").strip()
            if token:
                return token
    return "workspace_route"


def _control_plane_tenant_id(current_user: Any, workspace_id: str, user: Optional[Dict[str, Any]] = None) -> str:
    record = user if isinstance(user, dict) else {}
    for source in (record, current_user if isinstance(current_user, dict) else {}):
        token = str(source.get("tenant_id") or "").strip()
        if token:
            return token
    try:
        token = auth_module.workspace_tenant_id(current_user, workspace_id)
    except Exception:
        token = ""
    return str(token or workspace_id or "default").strip()


def _enforce_control_plane_route_decision(**payload: Any) -> Dict[str, Any]:
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "control-plane-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        result = getattr(exc, "result", None)
        if not isinstance(result, dict):
            result = {}
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_control_plane_service_denied",
                "operation": result.get("operation") or payload.get("operation"),
                "reason": result.get("reason") or str(exc),
            },
        ) from exc
    mutation_plan = _coerce_dict(decision.get("mutation_plan"))
    operation = str(decision.get("operation") or payload.get("operation") or "").strip()
    expected_next_actions = {
        "workspace_create": {"apply_control_plane_write"},
        "workspace_update": {"apply_control_plane_write"},
        "transparency_settings_update": {"apply_control_plane_write"},
        "workspace_routing_update": {"apply_control_plane_write"},
        "workspace_policy_update": {"apply_control_plane_write"},
        "sage_tool_policy_update": {"apply_control_plane_write"},
        "invite_create": {"apply_control_plane_write"},
        "invite_revoke": {
            "apply_control_plane_write",
            "return_existing_control_plane_record",
        },
        "secret_reference_write": {"apply_control_plane_write"},
        "provider_models_refresh": {"apply_control_plane_write"},
    }.get(operation, {"apply_control_plane_write"})
    next_action = str(
        mutation_plan.get("next_action") or decision.get("next_action") or ""
    ).strip()
    allow_idempotent_return = operation == "invite_revoke" and next_action == "return_existing_control_plane_record"
    if mutation_plan.get("apply") is not True and not allow_idempotent_return:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_control_plane_service_denied",
                "operation": decision.get("operation") or payload.get("operation"),
                "reason": decision.get("reason") or "missing_rust_mutation_plan",
            },
        )
    if next_action not in expected_next_actions:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "rust_control_plane_service_invalid_next_action",
                "operation": operation or payload.get("operation"),
                "reason": (
                    "Rust control-plane service returned unexpected next_action for "
                    f"{operation or payload.get('operation')}: {next_action or 'missing'}"
                ),
            },
        )
    return decision


def _workspace_summary_payload(workspace_record: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _coerce_dict(workspace_record.get("metadata"))
    shell = _coerce_dict(metadata.get("shell"))
    workspace_id = str(workspace_record.get("workspace_id") or "").strip()
    label = str(workspace_record.get("name") or "").strip() or workspace_id
    preferred_shell_profile_id = str(shell.get("preferredProfile") or "").strip() or None
    default_route = control_plane_repository._normalize_workspace_default_route(
        workspace_id,
        shell.get("defaultRoute") or "/chat",
    )
    setup_completed = bool(shell.get("setupCompleted")) and bool(label) and bool(preferred_shell_profile_id)

    return {
        "workspace": {
            "id": workspace_id,
            "tenantId": str(workspace_record.get("tenant_id") or "").strip(),
            "label": label,
            "kind": str(
                workspace_record.get("workspace_type")
                or workspace_record.get("kind")
                or "personal"
            ).strip()
            or "personal",
        },
        "defaultRoute": default_route,
        "preferredShellProfileId": preferred_shell_profile_id,
        "setupCompleted": setup_completed,
        "requiresOnboarding": not setup_completed,
    }


class WorkspaceCreateRequest(BaseModel):
    name: str
    workspace_type: str
    preferred_shell_profile: str
    default_route: str
    tenant_id: Optional[str] = None


class WorkspaceUpdateRequest(BaseModel):
    name: Optional[str] = None
    workspace_type: Optional[str] = None
    preferred_shell_profile: Optional[str] = None
    default_route: Optional[str] = None
    setup_completed: Optional[bool] = None


class WorkspaceSettingsUpdateRequest(BaseModel):
    notification_channel_id: Optional[str] = None


class WorkspaceInviteRequest(BaseModel):
    email: str
    role: str = "member"


class WorkspaceMemberUpdateRequest(BaseModel):
    role: str


class WorkspacePoliciesUpdateRequest(BaseModel):
    capabilities: Optional[Dict[str, list[str]]] = None
    dangerous_action_classes: Optional[Dict[str, list[str]]] = None
    connectors: Optional[Dict[str, list[str]]] = None
    machine_enrollment_scope: Optional[str] = None
    trusted_owner_machine_ids: Optional[list[str]] = None


class WorkspaceSageToolPolicyUpdateRequest(BaseModel):
    tool: str
    enabled: bool


class WorkspaceProviderCredentialUpsertRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class WorkspaceProviderCredentialDeleteRequest(BaseModel):
    provider: str


class WorkspaceRoutingUpdateRequest(BaseModel):
    admin_defaults: Optional[Dict[str, Any]] = None


class WorkspaceAiRouteDefaultUpdateRequest(BaseModel):
    route_id: Optional[str] = None
    routeId: Optional[str] = None
    kind: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    model_preset: Optional[str] = None
    modelPreset: Optional[str] = None


class WorkspaceTransparencySettingsUpdateRequest(BaseModel):
    default_mode: Optional[str] = None
    sage_mode: Optional[str] = None
    studio_test_mode: Optional[str] = None
    customer_mode: Optional[str] = None
    show_trace_ids: Optional[bool] = None
    show_tool_names: Optional[bool] = None
    show_memory_usage: Optional[bool] = None
    show_policy_blocks: Optional[bool] = None
    show_sources: Optional[bool] = None


@router.get("/workspaces")
async def list_workspaces(
    current_user=Depends(get_current_user),
):
    user = auth_module.get_authenticated_user_record(current_user)
    memberships = auth_module.list_authenticated_workspace_memberships(current_user)
    membership_index = {
        str(item.get("workspace_id") or "").strip(): dict(item)
        for item in memberships
        if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
    }
    workspaces = await control_plane_repository.list_workspaces_for_user(str(user.get("id") or "").strip())
    return {
        "items": [
            {
                **_workspace_summary_payload(workspace_record),
                "role": auth_module.normalize_rbac_role(
                    membership_index.get(str(workspace_record.get("workspace_id") or "").strip(), {}).get("role"),
                    default="viewer",
                ),
            }
            for workspace_record in workspaces
        ]
    }


@router.post("/workspaces")
async def create_workspace(
    body: WorkspaceCreateRequest,
    current_user=Depends(get_current_user),
):
    user = auth_module.get_authenticated_user_record(current_user)
    clean_name = str(body.name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="name is required.")

    _enforce_control_plane_route_decision(
        operation="workspace_create",
        record_type="workspace",
        tenant_id=str(body.tenant_id or user.get("tenant_id") or user.get("id") or "default").strip(),
        workspace_id=f"pending:{str(user.get('id') or '').strip() or clean_name}",
        actor_id=_control_plane_actor_id(current_user, user),
        actor_role="owner",
        target_status=_require_workspace_type(body.workspace_type),
        idempotency_key=f"workspace_create:{str(user.get('id') or '').strip()}:{clean_name}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )

    workspace_record = await control_plane_repository.create_workspace_for_user(
        user_id=str(user.get("id") or "").strip(),
        tenant_id=None,
        name=clean_name,
        workspace_type=_require_workspace_type(body.workspace_type),
        preferred_shell_profile=_require_shell_profile(body.preferred_shell_profile),
        default_route=_require_workspace_default_route(body.default_route),
    )
    if not isinstance(workspace_record, dict):
        raise HTTPException(status_code=500, detail="Workspace could not be created.")
    return _workspace_summary_payload(workspace_record)


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdateRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
    )

    updates: Dict[str, Any] = {}
    if body.name is not None:
        clean_name = str(body.name or "").strip()
        if not clean_name:
            raise HTTPException(status_code=400, detail="name cannot be empty.")
        updates["name"] = clean_name
    if body.workspace_type is not None:
        updates["workspace_type"] = _require_workspace_type(body.workspace_type)
    if body.preferred_shell_profile is not None:
        updates["preferred_shell_profile"] = _require_shell_profile(body.preferred_shell_profile)
    if body.default_route is not None:
        updates["default_route"] = _require_workspace_default_route(
            body.default_route,
            workspace_id=resolved_workspace_id,
        )
    if body.setup_completed is not None:
        updates["setup_completed"] = bool(body.setup_completed)
    if not updates:
        raise HTTPException(status_code=400, detail="At least one workspace profile field must be supplied.")

    _enforce_control_plane_route_decision(
        operation="workspace_update",
        record_type="workspace",
        tenant_id=_control_plane_tenant_id(current_user, resolved_workspace_id),
        workspace_id=resolved_workspace_id,
        actor_id=_control_plane_actor_id(current_user),
        actor_role="owner",
        target_status=str(updates.get("workspace_type") or "active"),
        idempotency_key=f"workspace_update:{resolved_workspace_id}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )

    workspace_record = await control_plane_repository.update_workspace_profile(
        resolved_workspace_id,
        updates,
    )
    if not isinstance(workspace_record, dict):
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return _workspace_summary_payload(workspace_record)


@router.patch("/workspaces/{workspace_id}/settings")
async def update_workspace_settings(
    workspace_id: str,
    body: WorkspaceSettingsUpdateRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
    )
    supplied_fields = _workspace_settings_fields_set(body)
    if "notification_channel_id" not in supplied_fields:
        raise HTTPException(status_code=400, detail="At least one workspace settings field must be supplied.")
    workspace_record = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    if not isinstance(workspace_record, dict):
        raise HTTPException(status_code=404, detail="Workspace not found.")
    metadata = _coerce_dict(workspace_record.get("metadata"))
    settings = _coerce_dict(metadata.get("settings"))
    notification_channel_id = _require_notification_channel_id(body.notification_channel_id)
    if notification_channel_id:
        settings["notification_channel_id"] = notification_channel_id
        metadata["notification_channel_id"] = notification_channel_id
    else:
        settings.pop("notification_channel_id", None)
        metadata.pop("notification_channel_id", None)
    metadata["settings"] = settings
    updated = await control_plane_repository.update_workspace_profile(
        resolved_workspace_id,
        {
            "metadata": metadata,
            "actor_id": _control_plane_actor_id(current_user),
            "actor_role": "owner",
        },
    )
    if not isinstance(updated, dict):
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {
        "workspace_id": resolved_workspace_id,
        "settings": {
            "notification_channel_id": notification_channel_id or None,
        },
    }


@router.get("/workspaces/{workspace_id}/bootstrap")
async def workspace_bootstrap(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    return await build_workspace_bootstrap(
        current_user=current_user,
        workspace_id=workspace_id,
    )


@router.get("/workspaces/{workspace_id}/channel-operations")
async def workspace_channel_operations(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    return await build_workspace_channel_operations(
        current_user=current_user,
        workspace_id=workspace_id,
    )


@router.get("/workspaces/{workspace_id}/runtime-sessions")
async def workspace_runtime_sessions(
    workspace_id: str,
    limit: int = 50,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    records = await session_service.list_workspace_runtime_sessions(
        resolved_workspace_id,
        limit=limit,
        active_only=True,
    )
    items = []
    for record in records:
        metadata = _coerce_dict(record.get("metadata"))
        runtime_binding = str(metadata.get("runtime_session_binding") or "").strip().lower()
        if runtime_binding not in COMPUTER_RUNTIME_BINDINGS:
            continue
        items.append({
            "session_id": str(record.get("session_id") or "").strip(),
            "thread_id": str(metadata.get("thread_id") or record.get("session_id") or "").strip(),
            "channel": str(record.get("channel") or "web").strip() or "web",
            "status": str(record.get("status") or "active").strip() or "active",
            "created_at": str(record.get("created_at") or "").strip(),
            "expires_at": str(record.get("expires_at") or "").strip(),
            "actor": _coerce_dict(record.get("actor")),
            "deployed_agent_id": str(metadata.get("deployed_agent_id") or "").strip() or None,
            "runtime_session_id": str(metadata.get("runtime_session_id") or record.get("session_id") or "").strip(),
            "runtime_binding": runtime_binding,
            "runtime_choice": str(metadata.get("runtime_choice") or "").strip() or None,
            "runtime_provider_id": str(metadata.get("runtime_provider_id") or "").strip() or None,
            "runtime_provider_kind": str(metadata.get("runtime_provider_kind") or "").strip() or None,
            "runtime_attachment_id": str(metadata.get("runtime_attachment_id") or "").strip() or None,
            "runtime_profile_id": str(metadata.get("runtime_profile_id") or "").strip() or None,
            "runtime_node_id": str(metadata.get("runtime_node_id") or "").strip() or None,
            "gateway_id": str(metadata.get("gateway_id") or "").strip() or None,
        })
    return {"items": items}


@router.get("/workspaces/{workspace_id}/routing")
async def workspace_routing(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    return await workspace_admin_service.build_workspace_routing_payload(
        workspace_id=workspace_id,
        current_user=current_user,
    )


@router.get("/workspaces/{workspace_id}/ai-route")
async def workspace_ai_route(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    return await build_workspace_ai_route_payload(resolved_workspace_id)


@router.patch("/workspaces/{workspace_id}/ai-route/default")
async def workspace_ai_route_default_update(
    workspace_id: str,
    body: WorkspaceAiRouteDefaultUpdateRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    return await update_workspace_default_ai_route(
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        route_id=body.route_id or body.routeId,
        kind=body.kind,
        provider=body.provider,
        model=body.model,
        model_preset=body.model_preset or body.modelPreset,
    )


@router.get("/workspaces/{workspace_id}/transparency-settings")
async def workspace_transparency_settings(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    return get_transparency_settings(resolved_workspace_id).to_dict()


@router.patch("/workspaces/{workspace_id}/transparency-settings")
async def workspace_transparency_settings_update(
    workspace_id: str,
    body: WorkspaceTransparencySettingsUpdateRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
    )
    current = get_transparency_settings(resolved_workspace_id).to_dict()
    updates = (
        body.model_dump(exclude_none=True)
        if hasattr(body, "model_dump")
        else body.dict(exclude_none=True)
    )
    next_settings = TransparencySettings.from_dict({
        **current,
        **updates,
        "workspace_id": resolved_workspace_id,
    })
    _enforce_control_plane_route_decision(
        operation="transparency_settings_update",
        record_type="workspace_transparency_settings",
        tenant_id=_control_plane_tenant_id(current_user, resolved_workspace_id),
        workspace_id=resolved_workspace_id,
        actor_id=_control_plane_actor_id(current_user),
        actor_role="owner",
        target_status="active",
        idempotency_key=f"transparency_settings_update:{resolved_workspace_id}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )
    try:
        return put_transparency_settings(next_settings).to_dict()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/workspaces/{workspace_id}/routing")
async def workspace_routing_update(
    workspace_id: str,
    body: WorkspaceRoutingUpdateRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
    )
    _enforce_control_plane_route_decision(
        operation="workspace_routing_update",
        record_type="workspace_routing",
        tenant_id=_control_plane_tenant_id(current_user, resolved_workspace_id),
        workspace_id=resolved_workspace_id,
        actor_id=_control_plane_actor_id(current_user),
        actor_role="owner",
        target_status="active",
        idempotency_key=f"workspace_routing_update:{resolved_workspace_id}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )
    return await workspace_admin_service.update_workspace_routing_payload(
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        payload=(
            body.model_dump(exclude_none=True)
            if hasattr(body, "model_dump")
            else body.dict(exclude_none=True)
        ),
    )


@router.get("/workspaces/{workspace_id}/members")
async def workspace_members(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    return await workspace_admin_service.build_workspace_members_payload(
        workspace_id=workspace_id,
        current_user=current_user,
    )


@router.post("/workspaces/{workspace_id}/members/invites")
async def workspace_member_invites(
    workspace_id: str,
    body: WorkspaceInviteRequest,
    current_user=Depends(get_current_user),
):
    if str(os.getenv("EMPYRALIS_ENABLE_WORKSPACE_INVITES", "0")).strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404, detail="Not available")
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
    )
    _enforce_control_plane_route_decision(
        operation="invite_create",
        record_type="workspace_invite",
        tenant_id=_control_plane_tenant_id(current_user, resolved_workspace_id),
        workspace_id=resolved_workspace_id,
        actor_id=_control_plane_actor_id(current_user),
        actor_role="owner",
        target_actor_id=str(body.email or "").strip(),
        target_status=str(body.role or "member").strip(),
        idempotency_key=f"invite_create:{resolved_workspace_id}:{str(body.email or '').strip().lower()}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )
    return await workspace_admin_service.invite_workspace_member(
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        email=body.email,
        role=body.role,
    )


@router.delete("/workspaces/{workspace_id}/members/invites/{invite_id}")
async def workspace_member_invite_revoke(
    workspace_id: str,
    invite_id: str,
    current_user=Depends(get_current_user),
):
    if str(os.getenv("EMPYRALIS_ENABLE_WORKSPACE_INVITES", "0")).strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404, detail="Not available")
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
    )
    _enforce_control_plane_route_decision(
        operation="invite_revoke",
        record_type="workspace_invite",
        tenant_id=_control_plane_tenant_id(current_user, resolved_workspace_id),
        workspace_id=resolved_workspace_id,
        actor_id=_control_plane_actor_id(current_user),
        actor_role="owner",
        target_status="revoked",
        idempotency_key=f"invite_revoke:{resolved_workspace_id}:{str(invite_id or '').strip()}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )
    return await workspace_admin_service.revoke_workspace_invite(
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        invite_id=invite_id,
    )


@router.patch("/workspaces/{workspace_id}/members/{user_id}")
async def workspace_member_update(
    workspace_id: str,
    user_id: str,
    body: WorkspaceMemberUpdateRequest,
    current_user=Depends(get_current_user),
):
    if str(os.getenv("EMPYRALIS_ENABLE_WORKSPACE_INVITES", "0")).strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404, detail="Not available")
    return await workspace_admin_service.update_workspace_member_role(
        workspace_id=workspace_id,
        current_user=current_user,
        user_id=user_id,
        role=body.role,
    )


@router.delete("/workspaces/{workspace_id}/members/{user_id}")
async def workspace_member_remove(
    workspace_id: str,
    user_id: str,
    current_user=Depends(get_current_user),
):
    if str(os.getenv("EMPYRALIS_ENABLE_WORKSPACE_INVITES", "0")).strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404, detail="Not available")
    return await workspace_admin_service.remove_workspace_member(
        workspace_id=workspace_id,
        current_user=current_user,
        user_id=user_id,
    )


@router.get("/workspaces/{workspace_id}/policies")
async def workspace_policies(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    return await workspace_admin_service.build_workspace_policies_payload(
        workspace_id=workspace_id,
        current_user=current_user,
    )


@router.patch("/workspaces/{workspace_id}/policies")
async def workspace_policies_update(
    workspace_id: str,
    body: WorkspacePoliciesUpdateRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
    )
    _enforce_control_plane_route_decision(
        operation="workspace_policy_update",
        record_type="workspace_policy",
        tenant_id=_control_plane_tenant_id(current_user, resolved_workspace_id),
        workspace_id=resolved_workspace_id,
        actor_id=_control_plane_actor_id(current_user),
        actor_role="owner",
        target_status="active",
        idempotency_key=f"workspace_policy_update:{resolved_workspace_id}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )
    return await workspace_admin_service.update_workspace_policies_payload(
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        payload=(
            body.model_dump(exclude_none=True)
            if hasattr(body, "model_dump")
            else body.dict(exclude_none=True)
        ),
    )


@router.get("/workspaces/{workspace_id}/sage/tool-policy")
async def workspace_sage_tool_policy(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    return await workspace_admin_service.build_workspace_sage_tool_policy_payload(
        workspace_id=workspace_id,
        current_user=current_user,
    )


@router.patch("/workspaces/{workspace_id}/sage/tool-policy")
async def workspace_sage_tool_policy_update(
    workspace_id: str,
    body: WorkspaceSageToolPolicyUpdateRequest,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
    )
    _enforce_control_plane_route_decision(
        operation="sage_tool_policy_update",
        record_type="sage_tool_policy",
        tenant_id=_control_plane_tenant_id(current_user, resolved_workspace_id),
        workspace_id=resolved_workspace_id,
        actor_id=_control_plane_actor_id(current_user),
        actor_role="owner",
        target_status="enabled" if bool(body.enabled) else "disabled",
        idempotency_key=f"sage_tool_policy_update:{resolved_workspace_id}:{str(body.tool or '').strip()}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )
    return await workspace_admin_service.update_workspace_sage_tool_policy_payload(
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        tool_key=body.tool,
        enabled=bool(body.enabled),
    )


@router.post("/workspaces/{workspace_id}/providers/credentials")
async def workspace_provider_credential_create(
    workspace_id: str,
    body: WorkspaceProviderCredentialUpsertRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    return await workspace_admin_service.upsert_workspace_provider_credential(
        workspace_id=workspace_id,
        current_user=current_user,
        provider=body.provider,
        api_key=body.api_key,
        base_url=body.base_url,
        model=body.model,
    )


@router.delete("/workspaces/{workspace_id}/providers/credentials")
async def workspace_provider_credential_delete(
    workspace_id: str,
    body: WorkspaceProviderCredentialDeleteRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    provider_id = str(body.provider or "").strip().lower()
    _enforce_control_plane_route_decision(
        operation="secret_reference_write",
        record_type="provider_credential",
        tenant_id=_control_plane_tenant_id(current_user, resolved_workspace_id),
        workspace_id=resolved_workspace_id,
        actor_id=_control_plane_actor_id(current_user),
        actor_role="owner",
        target_status="delete",
        idempotency_key=f"provider_credential_delete:{resolved_workspace_id}:{provider_id}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )
    return await workspace_admin_service.delete_workspace_provider_credential(
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        provider=body.provider,
    )


@router.post("/workspaces/{workspace_id}/providers/{provider_id}/models/refresh")
async def workspace_provider_models_refresh(
    workspace_id: str,
    provider_id: str,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
        capability_id="connectors.manage",
    )
    clean_provider_id = str(provider_id or "").strip().lower()
    _enforce_control_plane_route_decision(
        operation="provider_models_refresh",
        record_type="provider_models",
        tenant_id=_control_plane_tenant_id(current_user, resolved_workspace_id),
        workspace_id=resolved_workspace_id,
        actor_id=_control_plane_actor_id(current_user),
        actor_role="owner",
        target_status=clean_provider_id,
        idempotency_key=f"provider_models_refresh:{resolved_workspace_id}:{clean_provider_id}",
        owner_access=True,
        admin_access=True,
        workspace_access=True,
        billing_entitled=True,
        quota_ok=True,
        approval_provided=True,
        owner_approval_provided=True,
    )
    return await workspace_admin_service.refresh_workspace_provider_models(
        workspace_id=resolved_workspace_id,
        current_user=current_user,
        provider=provider_id,
    )


# ── Identity Links ───────────────────────────────────────────────

class IdentityLinkEntry(BaseModel):
    name: str
    channel_ids: list[str]


class IdentityLinksResponse(BaseModel):
    identity_links: dict[str, list[str]]


@router.get("/workspaces/{workspace_id}/identity-links")
async def get_workspace_identity_links(
    workspace_id: str,
    current_user=Depends(get_current_user),
) -> IdentityLinksResponse:
    """Return the workspace identity_links mapping."""
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user=current_user,
        workspace_id=workspace_id,
        minimum_role="member",
    )
    ws_record = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    raw_links = (ws_record or {}).get("identity_links")
    if isinstance(raw_links, dict):
        return IdentityLinksResponse(identity_links=raw_links)
    return IdentityLinksResponse(identity_links={})


@router.post("/workspaces/{workspace_id}/identity-links")
async def upsert_workspace_identity_link(
    workspace_id: str,
    body: IdentityLinkEntry,
    current_user=Depends(get_current_user),
) -> IdentityLinksResponse:
    """Upsert an identity link entry for a canonical name."""
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user=current_user,
        workspace_id=workspace_id,
        minimum_role="owner",
    )
    name = str(body.name or "").strip()
    channel_ids = [str(cid).strip() for cid in (body.channel_ids or []) if str(cid).strip()]
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not channel_ids:
        raise HTTPException(status_code=400, detail="at least one channel_id is required")

    # Load current links, upsert entry, save back
    ws_record = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    raw_links = (ws_record or {}).get("identity_links")
    current_links: dict[str, list[str]] = dict(raw_links) if isinstance(raw_links, dict) else {}
    current_links[name] = channel_ids

    await control_plane_repository.update_workspace_identity_links(
        workspace_id=resolved_workspace_id,
        identity_links=current_links,
    )
    return IdentityLinksResponse(identity_links=current_links)

