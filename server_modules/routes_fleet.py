"""Phase U6b/S2: Fleet API routes — agent listing, activity, + per-agent
Memory, Channels, Connectors, and Tools endpoints for the agent detail modal.

Serves the Fleet Home UI and the per-agent modal tabs with:
- GET /api/w/{workspace_id}/fleet/agents
- GET /api/w/{workspace_id}/fleet/agent-activity?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-memory?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-channels?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-connectors?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-tools?agent_id=
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
            tenant_id="default",
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


@router.get("/api/w/{workspace_id}/fleet/agent-memory")
async def fleet_agent_memory(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Per-agent memory file listing (agent detail modal → Memory tab)."""
    from server_modules.agent_memory_tools import memory_list

    try:
        result = await memory_list(
            workspace_id=workspace_id,
            agent_install_id=agent_id,
            agent_id=agent_id,
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "files": [], "count": 0}


@router.get("/api/w/{workspace_id}/fleet/agent-channels")
async def fleet_agent_channels(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Per-agent channel/pairing status (agent detail modal → Channels tab).
    Returns workspace channel state — per-agent channels are not yet provisioned
    (see docs/ROADMAP.md: per-agent hosted bots). Until then, the workspace
    channels are the agent's channels."""
    from server_modules.connection_catalog_service import (
        catalog_items,
        _vault_connector_ids,
    )
    from server_modules.sage_telegram_hosted_service import is_configured as hosted_configured

    try:
        # Channel-scoped catalog items (surface="channels" or connection kind
        # that involves messaging). The catalog's surface filter already
        # separates channels from apps/connectors.
        channel_items = [
            item for item in catalog_items(surface="channels")
            if item.get("id") not in ("telegram_bot",)  # skip BYO-bot path
        ]

        # Enrich with live pairing status
        vault_ids = _vault_connector_ids(workspace_id)
        enriched: List[Dict[str, Any]] = []
        for item in channel_items:
            cid = str(item.get("id") or "")
            connected = cid in vault_ids
            enriched.append({
                "id": cid,
                "label": item.get("label") or cid,
                "summary": item.get("summary") or "",
                "image": item.get("image") or None,
                "connected": connected,
                "setupHint": item.get("setupHint") or "",
            })

        return {
            "ok": True,
            "channels": enriched,
            "hosted_telegram_configured": hosted_configured(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "channels": []}


@router.get("/api/w/{workspace_id}/fleet/agent-connectors")
async def fleet_agent_connectors(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Per-agent connectors (agent detail modal → Connectors tab).
    Returns MCP/OAuth connectors available to this agent based on its role."""
    from server_modules.connection_catalog_service import (
        catalog_items,
        _vault_connector_ids,
    )

    try:
        # Apps/connectors surface (not channels)
        app_items = catalog_items(surface="apps")
        vault_ids = _vault_connector_ids(workspace_id)

        enriched: List[Dict[str, Any]] = []
        for item in app_items:
            cid = str(item.get("id") or "")
            connected = cid in vault_ids
            enriched.append({
                "id": cid,
                "label": item.get("label") or cid,
                "summary": item.get("summary") or "",
                "image": item.get("image") or None,
                "connected": connected,
                "kind": item.get("setupKind") or item.get("kind") or "oauth",
                "oauthUrl": None,  # populated client-side on connect click
            })

        return {"ok": True, "connectors": enriched}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "connectors": []}


@router.get("/api/w/{workspace_id}/fleet/agent-tools")
async def fleet_agent_tools(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Per-agent tool catalog (agent detail modal → Tools tab).
    Returns the tool manifest for this agent based on its hardware_status
    and role."""
    from server_modules.fleet_tools import fleet_get_agent_tools

    try:
        manifest = await fleet_get_agent_tools(
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        return manifest
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tools": []}
