from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from server_modules import control_plane_repository
from server_modules import deployed_agent_service
from server_modules.schemas import DeployedAgentAdminDashboardResponse


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _actor_role(current_user: Optional[Dict[str, Any]], workspace_id: str) -> str:
    user = dict(current_user or {}) if isinstance(current_user, dict) else {}
    workspace_access = user.get("workspace_access") if isinstance(user.get("workspace_access"), dict) else {}
    scoped = workspace_access.get(workspace_id) if isinstance(workspace_access.get(workspace_id), dict) else {}
    role = _normalize_text(scoped.get("role") or user.get("role")).lower()
    if role in {"owner", "admin", "member"}:
        return "owner" if role == "owner" else "admin"
    return "admin"


class DeployedAgentAdminDashboardService:
    async def get_dashboard(
        self,
        *,
        agent_id: str,
        workspace_id: str,
        current_user: Optional[Dict[str, Any]],
        limit: int = 50,
        offset: int = 0,
        cursor_last_message_at: Optional[str] = None,
        cursor_external_user_id: Optional[str] = None,
    ) -> DeployedAgentAdminDashboardResponse:
        resolved_workspace_id = deployed_agent_service.require_deployed_agent_admin_access(
            current_user=current_user,
            workspace_id=workspace_id,
        )
        workspace = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
        if not isinstance(workspace, dict):
            raise HTTPException(status_code=400, detail="Workspace is unavailable.")
        tenant_id = _normalize_text(workspace.get("tenant_id"))
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Workspace is missing a tenant binding.")
        deployed_agent = await control_plane_repository.get_deployed_agent_by_id(
            agent_id,
            tenant_id=tenant_id,
            owner_workspace_id=resolved_workspace_id,
        )
        if not isinstance(deployed_agent, dict):
            raise HTTPException(status_code=404, detail="Deployed agent not found.")
        deployed_agent_service._enforce_deployed_agent_service_decision(
            "admin_dashboard",
            deployed_agent=deployed_agent,
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            current_user=current_user,
        )
        payload = await control_plane_repository.get_deployed_agent_admin_dashboard(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            deployed_agent_id=agent_id,
            limit=limit,
            offset=offset,
            cursor_last_message_at=cursor_last_message_at,
            cursor_external_user_id=cursor_external_user_id,
        )
        if not isinstance(payload, dict):
            raise HTTPException(status_code=404, detail="Deployed agent not found.")
        readiness_payload: Dict[str, Any] = {}
        try:
            readiness_payload = await deployed_agent_service.get_deployed_agent_telegram_readiness(
                current_user=current_user,
                owner_workspace_id=resolved_workspace_id,
                deployed_agent_id=agent_id,
            )
        except HTTPException:
            readiness_payload = {}
        configured_binding = dict(readiness_payload.get("configured_binding") or {})
        projected_agent = deployed_agent_service.project_deployed_agent(
            deployed_agent,
            include_internal=False,
        ) or {}
        payload["customer_entry"] = deployed_agent_service.build_deployed_agent_customer_entry(
            deployed_agent=deployed_agent,
            bot_username=_normalize_text(configured_binding.get("bot_username")) or None,
            endpoint_key=_normalize_text(configured_binding.get("endpoint_key")) or None,
        )
        payload["specialist_profile"] = dict(
            (projected_agent.get("config") or {}).get("specialist_profile") or {}
        )
        return DeployedAgentAdminDashboardResponse.model_validate(payload)


_SERVICE = DeployedAgentAdminDashboardService()


def get_deployed_agent_admin_dashboard_service() -> DeployedAgentAdminDashboardService:
    return _SERVICE
