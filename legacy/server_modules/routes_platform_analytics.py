from __future__ import annotations

from fastapi import APIRouter, Depends

from server_modules import auth as auth_module
from server_modules import workspace_admin_service


router = APIRouter()
get_current_user = auth_module.require_admin_access


@router.get("/platform-analytics")
async def platform_analytics(
    current_user=Depends(get_current_user),
):
    return await workspace_admin_service.build_platform_analytics_payload(
        current_user=current_user,
    )
