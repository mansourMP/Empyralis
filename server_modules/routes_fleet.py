"""Phase U4: Fleet API routes — agent listing with placement + real ledger events.

Serves the Fleet Home UI with:
- GET /api/w/{workspace_id}/fleet/agents — agent cards with runtime_target + hardware_status
- GET /api/w/{workspace_id}/fleet/agent-activity — real ledger events for detail panel
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request

router = APIRouter(tags=["fleet"])


@router.get("/api/w/{workspace_id}/fleet/agents")
async def fleet_agents(
    request: Request,
    workspace_id: str,
) -> Dict[str, Any]:
    """List workspace agents with placement visibility (Phase U3 fields)."""
    from server_modules.fleet_tools import fleet_list_agents

    try:
        result = await fleet_list_agents(
            actor_id="fleet_ui",
            workspace_id=workspace_id,
            tenant_id="system",
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "agents": []}


@router.get("/api/w/{workspace_id}/fleet/agent-activity")
async def fleet_agent_activity(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    since: Optional[str] = Query(None, description="ISO timestamp filter"),
) -> Dict[str, Any]:
    """Get REAL ledger events for an agent (Phase U4 detail panel Activity tab)."""
    from server_modules.fleet_tools import fleet_get_agent_activity

    try:
        result = await fleet_get_agent_activity(
            actor_id="fleet_ui",
            workspace_id=workspace_id,
            agent_id=agent_id,
            since=since,
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "events": []}
