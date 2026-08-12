from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server_modules import auth as auth_module
from server_modules import control_plane_repository
from server_modules import rust_runtime_kernel_client
from server_modules import session_service
from server_modules import workspace_admin_service
from server_modules import workspace_invite_email_service
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
LOGGER = logging.getLogger(__name__)

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


def _require_invite_role(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token not in auth_module.RBAC_ROLE_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of: {', '.join(sorted(auth_module.RBAC_ROLE_ORDER))}.",
        )
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


async def _control_plane_tenant_id(current_user: Any, workspace_id: str, user: Optional[Dict[str, Any]] = None) -> str:
    """Authoritative, per-workspace tenant resolution -- the same shared
    helper routes_fleet.py's _resolve_tenant() uses. Deliberately does NOT
    read tenant_id off the user record: users.tenant_id is a stale legacy
    default (see CLAUDE.md) that does not track workspace membership and a
    prior version of this function trusted it first, which sent invites for
    a brand-new user's project lookup to the wrong tenant and produced
    "Project not found in this workspace." current_user/user are accepted
    only for call-site compatibility and are no longer consulted -- do not
    reintroduce a user-record read here."""
    return await control_plane_repository.resolve_tenant_id_for_workspace(
        str(workspace_id or "").strip(), default="default"
    )


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
        "workspace_routing_update": {"apply_control_plane_write"},
        "workspace_policy_update": {"apply_control_plane_write"},
        "sage_tool_policy_update": {"apply_control_plane_write"},
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


class WorkspaceInviteCreateRequest(BaseModel):
    email: str
    role: str = "member"
    # Optional: which project this invite grants access to (MAN-70 follow-up
    # -- previously accepting a workspace invite granted zero project
    # access; project_memberships rows were only ever created by project
    # creation itself or the UI-less /fleet/projects/{id}/members route, so
    # an invited teammate saw no projects until someone separately made them
    # a workspace owner). None means "no project" -- see
    # create_workspace_invite_route's docstring for why that stays the
    # explicit default rather than granting some implicit set.
    project_id: Optional[str] = None


class WorkspaceInviteAcceptRequest(BaseModel):
    token: str


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
        tenant_id=await _control_plane_tenant_id(current_user, resolved_workspace_id),
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
        tenant_id=await _control_plane_tenant_id(current_user, resolved_workspace_id),
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
        tenant_id=await _control_plane_tenant_id(current_user, resolved_workspace_id),
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
        tenant_id=await _control_plane_tenant_id(current_user, resolved_workspace_id),
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
        tenant_id=await _control_plane_tenant_id(current_user, resolved_workspace_id),
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
        tenant_id=await _control_plane_tenant_id(current_user, resolved_workspace_id),
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


# ── Members & Invites (Multiplayer Projects Phase 1) ─────────────────────────
# create_workspace_invite mints a signed, expiring token; this route then
# EMAILS it (workspace_invite_email_service) and reports whether that
# worked. It used to send nothing at all -- the token came back and the UI
# said "No email sender yet", so multiplayer never started for anyone who
# did not also hand-deliver a link. The email is best-effort by design: the
# row and its token exist before the send is attempted and are returned
# whatever the mailer does, with the outcome carried in `email_delivery` so
# the copy-link fallback can appear exactly when it is needed. The MAN-70
# placeholder ruling
# ("project member" == "workspace member", no per-project ACL) is superseded:
# MAN-115 added the real project_memberships table, and this route can now
# carry an optional project_id on the invite so acceptance (both paths --
# see accept_workspace_invite_route below and auth.accept_workspace_invites_
# for_user) can grant that specific project too, not just workspace access.
#
# project_id is intentionally optional with no implicit default. An invite
# with no project_id grants workspace membership only -- the invitee sees no
# projects until an owner explicitly grants one (via a future invite carrying
# project_id, or the existing /fleet/projects/{id}/members route). This is a
# deliberate choice, not a gap: silently defaulting to "every project" would
# be a bigger blast radius than a workspace owner asked for, and defaulting
# to "the workspace's default project only" is a policy call this repository
# layer shouldn't make unilaterally -- the caller (route/UI) can always pass
# project_id explicitly when it knows what it wants granted.
@router.post("/workspaces/{workspace_id}/invites")
async def create_workspace_invite_route(
    workspace_id: str,
    body: WorkspaceInviteCreateRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    auth_module.validate_csrf(request)
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="owner",
    )
    clean_email = str(body.email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        raise HTTPException(status_code=400, detail="A valid email is required.")
    requested_role = _require_invite_role(body.role)

    user = auth_module.get_authenticated_user_record(current_user)
    inviter_role = auth_module.normalize_rbac_role(
        auth_module.workspace_role(current_user, resolved_workspace_id),
        default="owner",
    )
    if auth_module.RBAC_ROLE_ORDER[requested_role] > auth_module.RBAC_ROLE_ORDER[inviter_role]:
        raise HTTPException(status_code=403, detail="Cannot invite a role above your own.")

    tenant_id = await _control_plane_tenant_id(current_user, resolved_workspace_id, user)

    clean_project_id = str(body.project_id or "").strip() or None
    if clean_project_id:
        # Validate up front, scoped to THIS workspace's own tenant_id -- an
        # owner can only tag an invite with a project that actually lives in
        # the workspace they're inviting into. get_project's WHERE clause is
        # tenant_id AND workspace_id AND id, so a cross-tenant/cross-workspace
        # project_id returns None here rather than silently getting stored.
        from server_modules import projects_repository

        target_project = await projects_repository.get_project(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            project_id=clean_project_id,
        )
        if target_project is None:
            raise HTTPException(status_code=400, detail="Project not found in this workspace.")

    try:
        invite = await control_plane_repository.create_workspace_invite(
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
            email=clean_email,
            role=requested_role,
            invited_by_user_id=_control_plane_actor_id(current_user, user),
            invited_by_role=inviter_role,
            project_id=clean_project_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not isinstance(invite, dict) or not invite.get("token"):
        raise HTTPException(status_code=500, detail="Invite could not be created.")

    # Send it -- but never at the cost of the invite. deliver_workspace_
    # invite_email catches every provider failure and reports one of
    # sent / not_configured / failed, so this route's contract is unchanged
    # for the token and additive for the delivery outcome.
    # The name lookup is part of the EMAIL, not part of the invite, so it
    # obeys the same rule: the invite is already written, and a control-plane
    # read that fails here must cost the owner nothing more than a generic
    # subject line. Without this guard the one un-caught await between
    # creating the row and returning it could 500 a real invite.
    try:
        workspace_record = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
    except Exception:  # noqa: BLE001 -- see above; the invite outlives this read
        workspace_record = None
    workspace_name = str((workspace_record or {}).get("name") or "").strip()
    email_delivery = await workspace_invite_email_service.deliver_workspace_invite_email(
        invitee_email=clean_email,
        workspace_name=workspace_name,
        inviter_label=workspace_invite_email_service.inviter_label_from_user(user),
        token=str(invite.get("token") or ""),
        expires_at_epoch=invite.get("expires_at"),
    )
    # Persist the outcome onto the invite itself -- otherwise "failed to
    # send" is visible only in this response's one-time toast, and becomes
    # indistinguishable from "pending" the moment it's dismissed. See
    # list_project_invite_status_route / list_workspace_invites_for_project,
    # which is what reads this back for the owner. Never at the cost of the
    # invite or this response -- same "a read failing here costs nothing the
    # caller already has" posture as the workspace-name lookup above.
    try:
        await control_plane_repository.record_workspace_invite_email_delivery(
            invite_id=str(invite.get("id") or ""),
            delivery_status=str((email_delivery or {}).get("status") or ""),
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception(
            "Failed to record email_delivery status for invite_id=%s",
            invite.get("id"),
        )

    return {
        "invite": {
            "id": invite.get("id"),
            "workspace_id": invite.get("workspace_id"),
            "email": invite.get("email"),
            "role": invite.get("role"),
            "status": invite.get("status"),
            "project_id": invite.get("project_id"),
            "created_at": invite.get("created_at"),
        },
        "token": invite.get("token"),
        "expires_at": invite.get("expires_at"),
        "email_delivery": email_delivery,
    }


@router.get("/workspaces/{workspace_id}/invites")
async def list_workspace_pending_invites_route(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    items = await control_plane_repository.list_pending_workspace_invites(resolved_workspace_id)
    return {
        "items": [
            {
                "id": item.get("id"),
                "workspace_id": item.get("workspace_id"),
                "email": item.get("email"),
                "role": item.get("role"),
                "status": item.get("status"),
                "invited_by_user_id": item.get("invited_by_user_id"),
                "created_at": item.get("created_at"),
            }
            for item in items
            if isinstance(item, dict)
        ]
    }


@router.get("/workspaces/{workspace_id}/projects/{project_id}/invites")
async def list_project_invite_status_route(
    workspace_id: str,
    project_id: str,
    current_user=Depends(get_current_user),
):
    """Owner-visible invite status for the panel where members are managed
    (ProjectMemberAdd.tsx) -- unlike GET /workspaces/{id}/invites above
    (workspace-wide, pending only), this is scoped to ONE project and
    reports every status: pending / accepted / declined / revoked, each
    carrying email_delivery_status so a failed send stays visible after the
    one-time creation toast is gone. See
    control_plane_repository.list_workspace_invites_for_project's docstring
    for why these are kept apart rather than collapsed to one state.

    Security review 2026-08-13 (sec/cross-tenant-authz): this route is
    scoped to ONE project exactly like routes_fleet.py's
    fleet_list_project_members ("Reads are viewer-gated through
    enforce_project_access (so a project member can see their own
    project's roster)") but, unlike that sibling, only checked
    workspace-viewer access -- any workspace member could read another
    project's invited emails, roles, and invited-by ids just by knowing
    its project_id, with no project_memberships row for it. Gated the
    same way fleet_list_project_members is.
    """
    resolved_workspace_id = await auth_module.enforce_project_access(
        current_user,
        workspace_id,
        project_id,
        minimum_role="viewer",
    )
    items = await control_plane_repository.list_workspace_invites_for_project(
        resolved_workspace_id,
        str(project_id or "").strip(),
    )
    return {
        "items": [
            {
                "id": item.get("id"),
                "workspace_id": item.get("workspace_id"),
                "email": item.get("email"),
                "role": item.get("role"),
                "status": item.get("status"),
                "email_delivery_status": item.get("email_delivery_status"),
                "invited_by_user_id": item.get("invited_by_user_id"),
                "created_at": item.get("created_at"),
            }
            for item in items
            if isinstance(item, dict)
        ]
    }


@router.get("/workspaces/{workspace_id}/members")
async def list_workspace_members_route(
    workspace_id: str,
    current_user=Depends(get_current_user),
):
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="viewer",
    )
    items = await control_plane_repository.list_workspace_members(resolved_workspace_id)
    return {
        "items": [
            {
                "user_id": item.get("user_id"),
                "email": item.get("email"),
                "display_name": item.get("display_name"),
                "role": item.get("role"),
                "joined_at": item.get("joined_at"),
            }
            for item in items
            if isinstance(item, dict)
        ]
    }


async def _finalize_workspace_invite_acceptance(invite: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Shared tail of every invite-acceptance path (the signed /join/{token}
    link below, and the in-app Join button at /workspaces/invites/{id}/join):
    grant workspace membership, grant the invite's project (if any), and mark
    the invite row accepted. A single call site is the only place this
    sequence can regress -- see this file's docstrings elsewhere on guards
    living on a narrow waist rather than being copied per branch.
    """
    invite_id = str(invite.get("id") or "").strip()
    invite_workspace_id = str(invite.get("workspace_id") or "").strip()
    invite_metadata = invite.get("metadata") if isinstance(invite.get("metadata"), dict) else {}
    invite_role = auth_module.normalize_rbac_role(invite.get("role"), default="viewer")
    auth_module.upsert_workspace_membership(user_id, invite_workspace_id, invite_role)

    # MAN-70/MAN-114 follow-up: grant the project this invite carries, if
    # any -- see projects_repository.grant_invite_project_access's docstring
    # for why this call is shared with auth.accept_workspace_invites_for_user
    # rather than duplicated. Uses the invite's own tenant_id (falling back
    # to workspace_id, matching _control_plane_tenant_id's convention) --
    # never the accepting caller's -- so this always resolves to the project
    # in the workspace the invite actually belongs to.
    invite_tenant_id = str(invite.get("tenant_id") or "").strip() or invite_workspace_id
    from server_modules import projects_repository

    try:
        await projects_repository.grant_invite_project_access(
            tenant_id=invite_tenant_id,
            workspace_id=invite_workspace_id,
            user_id=user_id,
            metadata=invite_metadata,
            added_by=str(invite.get("invited_by_user_id") or "").strip() or None,
        )
    except Exception:
        LOGGER.exception(
            "Failed to grant invite project access for invite_id=%s user_id=%s",
            invite_id,
            user_id,
        )

    accepted = await control_plane_repository.accept_workspace_invite(
        invite_id=invite_id,
        accepted_by_user_id=user_id,
        metadata_patch={"auto_accepted_at_login": False},
    )
    return {
        "workspace_id": invite_workspace_id,
        "role": invite_role,
        "status": str((accepted or {}).get("status") or "accepted"),
    }


@router.post("/workspaces/invites/accept")
async def accept_workspace_invite_route(
    body: WorkspaceInviteAcceptRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Accept a workspace invite link. Any authenticated user may call this --
    there is no workspace-role gate (the caller isn't a member of the target
    workspace yet, by definition). The token must verify (signature + not
    expired), the underlying invite row must still be 'pending' (or already
    accepted by this exact caller as an auto-accept-at-login side effect --
    see below), and the caller's authenticated email must match the invite's
    email exactly.
    """
    auth_module.validate_csrf(request)
    user = auth_module.get_authenticated_user_record(current_user)
    user_id = str(user.get("id") or "").strip()
    user_email = str(user.get("email") or "").strip().lower()
    if not user_id or not user_email:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        claims = control_plane_repository.verify_workspace_invite_token(body.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    invite_id = str(claims.get("invite_id") or "").strip()
    invite = await control_plane_repository.get_workspace_member_invite(invite_id)
    if not isinstance(invite, dict):
        raise HTTPException(status_code=404, detail="Invite is no longer valid.")

    invite_status = str(invite.get("status") or "").strip()
    invite_metadata = invite.get("metadata") if isinstance(invite.get("metadata"), dict) else {}
    accepted_by_user_id = str(invite.get("accepted_by_user_id") or "").strip()

    # A user's own login/registration auto-accepts every pending invite that
    # matches their email (auth.accept_workspace_invites_for_user, called
    # from auth.login_user/register_user) -- entirely independent of this
    # token-based flow. That means by the time someone who just signed up
    # via a /join/{token} link lands back here, the invite this exact token
    # points at may *already* be 'accepted' -- accepted by them, via their
    # own login, moments ago. That is a real success, not an invalid invite,
    # so it must not 404. auto_accepted_at_login is the marker
    # accept_workspace_invites_for_user stamps for exactly this case; it is
    # cleared below once this route has acknowledged it, so a genuine repeat
    # call with the same token (replay) still 404s like any other
    # already-consumed invite.
    already_confirmed_via_login = (
        invite_status == "accepted"
        and bool(invite_metadata.get("auto_accepted_at_login"))
        and accepted_by_user_id == user_id
    )

    if invite_status != "pending" and not already_confirmed_via_login:
        raise HTTPException(status_code=404, detail="Invite is no longer valid.")

    invite_workspace_id = str(invite.get("workspace_id") or "").strip()
    invite_email = str(invite.get("email") or "").strip().lower()
    if invite_workspace_id != str(claims.get("workspace_id") or "").strip():
        raise HTTPException(status_code=400, detail="Invite token does not match the invite record.")
    if invite_email != user_email:
        raise HTTPException(status_code=403, detail="This invite was issued to a different email address.")

    return await _finalize_workspace_invite_acceptance(invite, user_id)


@router.get("/workspaces/invites/pending")
async def list_my_pending_workspace_invites_route(
    current_user=Depends(get_current_user),
):
    """The invitee-facing counterpart to GET /workspaces/{id}/invites (which
    only an existing member of the target workspace can call, and therefore
    can never be how the person being invited learns about it). A signed-in
    user with a pending invite to their own email gets nothing anywhere else
    -- this is that signal, enriched with the inviting workspace's name so
    the UI never has to show a bare workspace_id.
    """
    user = auth_module.get_authenticated_user_record(current_user)
    user_email = str(user.get("email") or "").strip().lower()
    if not user_email:
        return {"items": []}

    invites = await control_plane_repository.list_pending_workspace_invites_for_email(user_email)
    items: list[Dict[str, Any]] = []
    for invite in invites:
        if not isinstance(invite, dict):
            continue
        invite_workspace_id = str(invite.get("workspace_id") or "").strip()
        # A workspace-name lookup failure must not hide a real pending invite
        # -- same "the read failing costs nothing the caller already has"
        # posture as create_workspace_invite_route's own workspace-name
        # lookup above.
        try:
            workspace_record = await control_plane_repository.get_workspace_by_id(invite_workspace_id)
        except Exception:  # noqa: BLE001
            workspace_record = None
        workspace_name = str((workspace_record or {}).get("name") or "").strip() or invite_workspace_id
        items.append(
            {
                "id": invite.get("id"),
                "workspace_id": invite_workspace_id,
                "workspace_name": workspace_name,
                "role": invite.get("role"),
                "invited_by_user_id": invite.get("invited_by_user_id"),
                "created_at": invite.get("created_at"),
            }
        )
    return {"items": items}


def _require_own_pending_invite(invite: Optional[Dict[str, Any]], user_email: str) -> Dict[str, Any]:
    if not isinstance(invite, dict):
        raise HTTPException(status_code=404, detail="Invite is no longer valid.")
    if str(invite.get("status") or "").strip() != "pending":
        raise HTTPException(status_code=404, detail="Invite is no longer valid.")
    invite_email = str(invite.get("email") or "").strip().lower()
    if invite_email != user_email:
        raise HTTPException(status_code=403, detail="This invite was issued to a different email address.")
    return invite


@router.post("/workspaces/invites/{invite_id}/join")
async def join_pending_workspace_invite_route(
    invite_id: str,
    request: Request,
    current_user=Depends(get_current_user),
):
    """The in-app counterpart to POST /workspaces/invites/accept: no signed
    token, because there is no email link here -- the caller reached this
    invite through their own authenticated session (the pending-invites list
    above), so the same email-match check that guards the token path is the
    whole authorization story, matching auth.accept_workspace_invites_for_user's
    (login-triggered auto-accept) security model exactly.
    """
    auth_module.validate_csrf(request)
    user = auth_module.get_authenticated_user_record(current_user)
    user_id = str(user.get("id") or "").strip()
    user_email = str(user.get("email") or "").strip().lower()
    if not user_id or not user_email:
        raise HTTPException(status_code=401, detail="Authentication required.")

    invite = await control_plane_repository.get_workspace_member_invite(invite_id)
    invite = _require_own_pending_invite(invite, user_email)
    return await _finalize_workspace_invite_acceptance(invite, user_id)


@router.post("/workspaces/invites/{invite_id}/decline")
async def decline_pending_workspace_invite_route(
    invite_id: str,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Decline is a real, recorded state -- not a silent dismissal off a
    list. See control_plane_repository.decline_workspace_invite's docstring
    for why 'declined' is kept distinct from 'revoked' (owner-initiated) and
    'pending' (unanswered).
    """
    auth_module.validate_csrf(request)
    user = auth_module.get_authenticated_user_record(current_user)
    user_id = str(user.get("id") or "").strip()
    user_email = str(user.get("email") or "").strip().lower()
    if not user_id or not user_email:
        raise HTTPException(status_code=401, detail="Authentication required.")

    invite = await control_plane_repository.get_workspace_member_invite(invite_id)
    invite = _require_own_pending_invite(invite, user_email)

    declined = await control_plane_repository.decline_workspace_invite(
        invite_id=invite_id,
        declined_by_user_id=user_id,
    )
    return {
        "workspace_id": invite.get("workspace_id"),
        "status": str((declined or {}).get("status") or "declined"),
    }

