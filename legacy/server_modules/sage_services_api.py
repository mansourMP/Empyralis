from __future__ import annotations

from typing import Optional

from fastapi import Depends

from server_modules.auth import enforce_workspace_access, workspace_tenant_id
from server_modules.sage_services_service import (
    create_service_entry,
    delete_service_entry,
    list_sage_services,
    set_service_entry_pinned,
    update_service_entry,
    update_service_profile,
)
from server_modules.schemas import (
    SageServiceEntryCreateRequest,
    SageServiceEntryPinRequest,
    SageServiceEntryUpdateRequest,
    SageServiceProfileUpdateRequest,
)


def register_sage_services_routes(app) -> None:
    import server as _server

    module_globals = globals()
    for key, value in _server.__dict__.items():
        if key not in module_globals:
            module_globals[key] = value

    member_dependency = getattr(_server, "require_api_key")

    @app.get("/api/sage-services", dependencies=[Depends(member_dependency)])
    async def list_workspace_sage_services(
        workspace_id: Optional[str] = None,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="viewer",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = list_sage_services(workspace_id=resolved_workspace_id)
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

    @app.put("/api/sage-services/{service_id}/profile", dependencies=[Depends(member_dependency)])
    async def update_workspace_sage_service_profile(
        service_id: str,
        body: SageServiceProfileUpdateRequest,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = await update_service_profile(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            service_id=service_id,
            profile=dict(body.profile or {}),
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

    @app.post("/api/sage-services/{service_id}/entries", dependencies=[Depends(member_dependency)])
    async def create_workspace_sage_service_entry(
        service_id: str,
        body: SageServiceEntryCreateRequest,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = await create_service_entry(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            service_id=service_id,
            entry=dict(body.entry or {}),
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

    @app.patch("/api/sage-services/{service_id}/entries/{entry_id}", dependencies=[Depends(member_dependency)])
    async def patch_workspace_sage_service_entry(
        service_id: str,
        entry_id: str,
        body: SageServiceEntryUpdateRequest,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = await update_service_entry(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            service_id=service_id,
            entry_id=entry_id,
            entry=dict(body.entry or {}),
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

    @app.delete("/api/sage-services/{service_id}/entries/{entry_id}", dependencies=[Depends(member_dependency)])
    async def delete_workspace_sage_service_entry(
        service_id: str,
        entry_id: str,
        workspace_id: Optional[str] = None,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = await delete_service_entry(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            service_id=service_id,
            entry_id=entry_id,
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }

    @app.post("/api/sage-services/{service_id}/entries/{entry_id}/pin", dependencies=[Depends(member_dependency)])
    async def pin_workspace_sage_service_entry(
        service_id: str,
        entry_id: str,
        body: SageServiceEntryPinRequest,
        current_user=Depends(member_dependency),
    ):
        resolved_workspace_id = enforce_workspace_access(
            current_user,
            body.workspace_id,
            minimum_role="member",
        )
        tenant_id = workspace_tenant_id(current_user, resolved_workspace_id)
        payload = await set_service_entry_pinned(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            service_id=service_id,
            entry_id=entry_id,
            pinned=bool(body.pinned),
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {
            "workspace_id": resolved_workspace_id,
            "tenant_id": tenant_id,
            **payload,
        }
