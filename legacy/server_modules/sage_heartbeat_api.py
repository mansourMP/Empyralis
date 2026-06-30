from __future__ import annotations

from typing import Optional

from fastapi import Depends

from server_modules.auth import enforce_workspace_access, workspace_tenant_id
from server_modules.sage_heartbeat_service import build_sage_heartbeat_snapshot


def register_sage_heartbeat_routes(app) -> None:
    import server as _server

    module_globals = globals()
    for key, value in _server.__dict__.items():
        if key not in module_globals:
            module_globals[key] = value

    member_dependency = getattr(_server, "require_api_key")

    @app.get("/api/sage-heartbeat", dependencies=[Depends(member_dependency)])
    async def get_workspace_sage_heartbeat(
        workspace_id: Optional[str] = None,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = await build_sage_heartbeat_snapshot(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            account_seed={
                "display_name": str((current_user or {}).get("name") or (current_user or {}).get("display_name") or "").strip(),
                "email": str((current_user or {}).get("email") or "").strip(),
            },
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }
