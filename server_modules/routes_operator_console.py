"""Empyralis operator console routes -- the backend behind ops.empyralis.ai.

Every route here follows routes_health.py's own `/internal/platform-
activation` precedent exactly: `require_api_key` only proves someone is
logged in (CLAUDE.md is explicit that this is not an authorization check),
so every handler calls `has_platform_fleet_operator_access` FIRST and
refuses before `operator_console_service` runs a single query. A missing
control-plane pool is a 503 ("could not verify"), never a payload that looks
like an empty platform; a broken RLS-bypass canary from the service layer is
a 500, never a silently zeroed response. See operator_console_service.py's
module docstring for the RLS-bypass and no-customer-content rules every
query underneath these routes follows.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from server_modules.auth import has_platform_fleet_operator_access
from server_modules.runtime_common import require_api_key
from server_modules import control_plane_repository
from server_modules import operator_console_service

router = APIRouter()


def _require_operator_access(current_user) -> None:
    if not has_platform_fleet_operator_access(current_user):
        raise HTTPException(status_code=403, detail="Operator access required.")


async def _require_control_plane_pool():
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        # "Empty" and "could not load" are different facts (CLAUDE.md) -- a
        # control plane that never came up must never render as a platform
        # with 0 of everything.
        raise HTTPException(
            status_code=503,
            detail="Control-plane database is unavailable; operator console data cannot be verified.",
        )
    return pool


async def operator_overview(current_user=Depends(require_api_key)):
    _require_operator_access(current_user)
    pool = await _require_control_plane_pool()
    try:
        return await operator_console_service.build_overview(pool)
    except operator_console_service.OperatorConsoleScopeBroken as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def operator_accounts(
    current_user=Depends(require_api_key),
    search: Optional[str] = Query(None, description="Filter by workspace name substring"),
    min_members: Optional[int] = Query(None, ge=0, description="Only workspaces with at least this many active members"),
    has_agents: Optional[bool] = Query(None, description="Only workspaces with (true) or without (false) any agent installs"),
    active_within_days: Optional[int] = Query(
        None, ge=0, description="Only workspaces with a member active (auth_sessions.last_seen_at) in the last N days"
    ),
    sort_by: str = Query("name", description="One of: " + ", ".join(sorted(operator_console_service.ACCOUNT_SORT_COLUMNS))),
    sort_dir: str = Query("asc", description="asc | desc"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    _require_operator_access(current_user)
    pool = await _require_control_plane_pool()
    try:
        return await operator_console_service.list_accounts(
            pool,
            search=search,
            min_members=min_members,
            has_agents=has_agents,
            active_within_days=active_within_days,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except operator_console_service.OperatorConsoleScopeBroken as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def operator_account_detail(workspace_id: str, current_user=Depends(require_api_key)):
    _require_operator_access(current_user)
    pool = await _require_control_plane_pool()
    try:
        detail = await operator_console_service.get_account_detail(pool, workspace_id)
    except operator_console_service.OperatorConsoleScopeBroken as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return detail


async def operator_activation_funnel(current_user=Depends(require_api_key)):
    _require_operator_access(current_user)
    pool = await _require_control_plane_pool()
    try:
        return await operator_console_service.build_activation_funnel(pool)
    except operator_console_service.OperatorConsoleScopeBroken as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def operator_retention(current_user=Depends(require_api_key)):
    _require_operator_access(current_user)
    pool = await _require_control_plane_pool()
    try:
        return await operator_console_service.build_retention(pool)
    except operator_console_service.OperatorConsoleScopeBroken as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def operator_failures(
    current_user=Depends(require_api_key),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(200, ge=1, le=1000),
):
    _require_operator_access(current_user)
    pool = await _require_control_plane_pool()
    try:
        return await operator_console_service.list_failures(pool, days=days, limit=limit)
    except operator_console_service.OperatorConsoleScopeBroken as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def operator_spend(
    current_user=Depends(require_api_key),
    days: int = Query(30, ge=1, le=365),
):
    _require_operator_access(current_user)
    pool = await _require_control_plane_pool()
    try:
        return await operator_console_service.build_spend(pool, days=days)
    except operator_console_service.OperatorConsoleScopeBroken as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


router.add_api_route(
    "/internal/operator/overview", operator_overview, methods=["GET"], dependencies=[Depends(require_api_key)]
)
router.add_api_route(
    "/internal/operator/accounts", operator_accounts, methods=["GET"], dependencies=[Depends(require_api_key)]
)
router.add_api_route(
    "/internal/operator/accounts/{workspace_id}",
    operator_account_detail,
    methods=["GET"],
    dependencies=[Depends(require_api_key)],
)
router.add_api_route(
    "/internal/operator/activation-funnel",
    operator_activation_funnel,
    methods=["GET"],
    dependencies=[Depends(require_api_key)],
)
router.add_api_route(
    "/internal/operator/retention", operator_retention, methods=["GET"], dependencies=[Depends(require_api_key)]
)
router.add_api_route(
    "/internal/operator/failures", operator_failures, methods=["GET"], dependencies=[Depends(require_api_key)]
)
router.add_api_route(
    "/internal/operator/spend", operator_spend, methods=["GET"], dependencies=[Depends(require_api_key)]
)
