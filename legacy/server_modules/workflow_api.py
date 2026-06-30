from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from server_modules.auth import enforce_workspace_access, workspace_tenant_id
from server_modules.runtime_common import require_api_key
from server_modules import workflow_service


router = APIRouter()


class WorkflowCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None


def _owner_user_id(current_user: Any) -> Optional[str]:
    return str((current_user or {}).get("user_id") or "").strip() or None


def register_workflow_routes(app: APIRouter) -> None:
    @app.get("/workflows", dependencies=[Depends(require_api_key)])
    async def list_workflows(
        workspaceId: Optional[str] = Query(default=None),
        current_user=Depends(require_api_key),
    ):
        workspace_id = enforce_workspace_access(current_user, workspaceId)
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        items = await workflow_service.list_workflows(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        return {"items": items}

    @app.post("/workflows", dependencies=[Depends(require_api_key)])
    async def create_workflow(
        body: WorkflowCreateRequest,
        workspaceId: Optional[str] = Query(default=None),
        current_user=Depends(require_api_key),
    ):
        workspace_id = enforce_workspace_access(current_user, workspaceId)
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        return await workflow_service.create_workflow(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=body.name,
            description=body.description,
            definition=body.definition,
            created_by_user_id=_owner_user_id(current_user),
            metadata={"source": "workflow_api"},
        )

    @app.get("/workflows/{workflow_id}", dependencies=[Depends(require_api_key)])
    async def get_workflow(workflow_id: str, current_user=Depends(require_api_key)):
        item = await workflow_service.get_workflow(workflow_id)
        if not item:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        enforce_workspace_access(current_user, str(item.get("workspaceId") or ""))
        return item

    async def _update_workflow(workflow_id: str, body: WorkflowUpdateRequest, current_user=Depends(require_api_key)):
        existing = await workflow_service.get_workflow(workflow_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        workspace_id = enforce_workspace_access(current_user, str(existing.get("workspaceId") or ""))
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        updated = await workflow_service.update_workflow(
            workflow_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=body.name,
            description=body.description,
            definition=body.definition,
            created_by_user_id=_owner_user_id(current_user),
            metadata={"source": "workflow_api"},
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return updated

    @app.put("/workflows/{workflow_id}", dependencies=[Depends(require_api_key)])
    async def replace_workflow(workflow_id: str, body: WorkflowUpdateRequest, current_user=Depends(require_api_key)):
        return await _update_workflow(workflow_id, body, current_user)

    @app.patch("/workflows/{workflow_id}", dependencies=[Depends(require_api_key)])
    async def update_workflow(workflow_id: str, body: WorkflowUpdateRequest, current_user=Depends(require_api_key)):
        return await _update_workflow(workflow_id, body, current_user)

    @app.delete("/workflows/{workflow_id}", dependencies=[Depends(require_api_key)])
    async def delete_workflow(workflow_id: str, current_user=Depends(require_api_key)):
        existing = await workflow_service.get_workflow(workflow_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        workspace_id = enforce_workspace_access(current_user, str(existing.get("workspaceId") or ""))
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        deleted = await workflow_service.delete_workflow(
            workflow_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return {"ok": True, "id": str(workflow_id or "").strip()}

    @app.post("/workflows/{workflow_id}/publish", dependencies=[Depends(require_api_key)])
    async def publish_workflow(workflow_id: str, current_user=Depends(require_api_key)):
        existing = await workflow_service.get_workflow(workflow_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        workspace_id = enforce_workspace_access(current_user, str(existing.get("workspaceId") or ""))
        tenant_id = workspace_tenant_id(current_user, workspace_id)
        published = await workflow_service.publish_workflow(
            workflow_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            created_by_user_id=_owner_user_id(current_user),
            metadata={"source": "workflow_api"},
        )
        if not published:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return published
