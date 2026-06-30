"""FastAPI router exposing the Sage Doctor diagnostic endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from server_modules import auth as auth_module
from server_modules.sage_doctor_service import SageDoctorService

router = APIRouter(prefix="/api")
get_current_user = auth_module.get_current_user


def _user_id(current_user: Any) -> str:
    if not isinstance(current_user, dict):
        return ""
    return (
        str(current_user.get("user_id") or "").strip()
        or str(current_user.get("id") or "").strip()
        or str(current_user.get("sub") or "").strip()
    )


@router.get("/sage/doctor/check")
async def sage_doctor_check(
    workspace_id: str = Query(""),
    current_user=Depends(get_current_user),
):
    """Run the full Sage Doctor diagnostics suite.

    Returns a JSON object with a ``checks`` list and a ``summary`` dict.
    """
    ws = str(workspace_id or "").strip()
    if not ws:
        raise HTTPException(status_code=400, detail="workspace_id is required.")

    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user,
        ws,
        minimum_role="viewer",
    )
    tenant_id = auth_module.workspace_tenant_id(current_user, resolved_workspace_id)
    result = SageDoctorService.check_all(
        workspace_id=resolved_workspace_id,
        tenant_id=tenant_id,
        user_id=_user_id(current_user),
    )
    return result
