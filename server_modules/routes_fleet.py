"""Phase U6b/S2: Fleet API routes — agent listing, activity, + per-agent
Memory, Channels, Connectors, and Tools endpoints for the agent detail modal.

Serves the Fleet Home UI and the per-agent modal tabs with:
- GET  /api/w/{workspace_id}/fleet/agents
- POST /api/w/{workspace_id}/fleet/agents
- PATCH /api/w/{workspace_id}/fleet/agents/{agent_id}
- GET /api/w/{workspace_id}/fleet/agent-activity?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-memory?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-channels?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-connectors?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-tools?agent_id=
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["fleet"])


async def _resolve_tenant(workspace_id: str) -> str:
    """Phase 3A: derive the tenant for a fleet request from its workspace
    instead of hardcoding "default". Falls back to "default" only for a
    workspace with no tenant on record (workspaces / registry / installs)."""
    from server_modules import control_plane_repository

    return await control_plane_repository.resolve_tenant_id_for_workspace(workspace_id, default="default")


@router.get("/api/w/{workspace_id}/fleet/usage")
async def fleet_usage(
    request: Request,
    workspace_id: str,
    scope: str = Query("workspace", description="workspace | agent | project"),
    id: Optional[str] = Query(None, description="agent_install_id or project_id when scope != workspace"),
    period: str = Query("day", description="day | week | month"),
) -> Dict[str, Any]:
    """Phase 5A: normalized usage rollup — per-agent / per-project / per-workspace,
    bucketed by day/week/month, with usd_cost totals."""
    from server_modules import usage_events_repository as usage_repo

    try:
        return await usage_repo.summarize_usage(
            tenant_id=await _resolve_tenant(workspace_id),
            workspace_id=workspace_id,
            scope=scope,
            scope_id=id,
            period=period,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/agents")
async def fleet_agents(
    request: Request,
    workspace_id: str,
    project_id: Optional[str] = Query(None, description="Filter to a single project"),
) -> Dict[str, Any]:
    """List workspace agents with placement visibility (Phase U3 fields).
    Each agent carries its project_id (Phase 2). Optionally filter to one
    project via ?project_id=."""
    from server_modules.fleet_tools import fleet_list_agents

    try:
        result = await fleet_list_agents(
            actor_id="fleet_ui",
            workspace_id=workspace_id,
            tenant_id=await _resolve_tenant(workspace_id),
        )
        if project_id and result.get("ok") and isinstance(result.get("agents"), list):
            wanted = str(project_id).strip()
            result["agents"] = [
                a for a in result["agents"] if str(a.get("project_id") or "").strip() == wanted
            ]
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "agents": []}


# ── Phase 2: Projects CRUD (Workspace > Projects > Agents) ──────────────────

@router.get("/api/w/{workspace_id}/fleet/projects")
async def fleet_projects(
    request: Request,
    workspace_id: str,
    include_archived: bool = Query(False),
) -> Dict[str, Any]:
    """List projects in the workspace, each with its agent count."""
    from server_modules import projects_repository as projects

    try:
        # Guarantee a default project exists so ungrouped agents have a home.
        await projects.ensure_default_project(tenant_id=await _resolve_tenant(workspace_id), workspace_id=workspace_id)
        rows = await projects.list_projects(
            tenant_id=await _resolve_tenant(workspace_id),
            workspace_id=workspace_id,
            include_archived=include_archived,
        )
        counts = await projects.count_agents_by_project(tenant_id=await _resolve_tenant(workspace_id), workspace_id=workspace_id)
        for p in rows:
            p["agent_count"] = int(counts.get(p["id"], 0))
        return {"ok": True, "projects": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "projects": []}


class FleetCreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


@router.post("/api/w/{workspace_id}/fleet/projects")
async def fleet_create_project(
    request: Request,
    workspace_id: str,
    body: FleetCreateProjectRequest,
) -> Dict[str, Any]:
    """Create a project."""
    from server_modules import projects_repository as projects

    try:
        project = await projects.create_project(
            tenant_id=await _resolve_tenant(workspace_id),
            workspace_id=workspace_id,
            name=body.name,
            description=body.description,
        )
        return {"ok": True, "project": project}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetPatchProjectRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    archived: Optional[bool] = None


@router.patch("/api/w/{workspace_id}/fleet/projects/{project_id}")
async def fleet_patch_project(
    request: Request,
    workspace_id: str,
    project_id: str,
    body: FleetPatchProjectRequest,
) -> Dict[str, Any]:
    """Rename, edit, or archive/unarchive a project."""
    from server_modules import projects_repository as projects

    try:
        project = None
        if body.name is not None or body.description is not None:
            project = await projects.rename_project(
                tenant_id=await _resolve_tenant(workspace_id),
                workspace_id=workspace_id,
                project_id=project_id,
                name=body.name,
                description=body.description,
            )
        if body.archived is not None:
            project = await projects.set_project_archived(
                tenant_id=await _resolve_tenant(workspace_id),
                workspace_id=workspace_id,
                project_id=project_id,
                archived=body.archived,
            )
        if project is None:
            return {"ok": False, "error": "Project not found."}
        return {"ok": True, "project": project}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetCreateAgentRequest(BaseModel):
    name: str = Field(min_length=1)
    instructions: str = ""
    purpose_preset: str = ""
    capability_preset: str = "standard"  # Phase 5B: knowledge | standard
    project_id: str = ""  # Phase 7B: assign to a project at creation


@router.post("/api/w/{workspace_id}/fleet/agents")
async def fleet_create_agent_route(
    request: Request,
    workspace_id: str,
    body: FleetCreateAgentRequest,
) -> Dict[str, Any]:
    """Create a specialist agent (create-agent wizard, steps 1-2)."""
    from server_modules.fleet_tools import fleet_create_agent

    try:
        result = await fleet_create_agent(
            actor_id="fleet_ui",
            workspace_id=workspace_id,
            tenant_id=await _resolve_tenant(workspace_id),
            name=body.name,
            instructions=body.instructions,
            purpose_preset=body.purpose_preset,
            capability_preset=body.capability_preset,
            project_id=body.project_id,
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "agent_id": ""}


class FleetConfigureAgentRequest(BaseModel):
    patch: Dict[str, Any] = Field(default_factory=dict)


@router.patch("/api/w/{workspace_id}/fleet/agents/{agent_id}")
async def fleet_configure_agent_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    body: FleetConfigureAgentRequest,
) -> Dict[str, Any]:
    """Patch an agent's fleet-managed config (create-agent wizard steps 2-5,
    and any future inline edits from the agent detail modal)."""
    from server_modules.fleet_tools import fleet_configure_agent

    try:
        result = await fleet_configure_agent(
            actor_id="fleet_ui",
            workspace_id=workspace_id,
            tenant_id=await _resolve_tenant(workspace_id),
            agent_id=agent_id,
            patch=body.patch,
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


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


# ── Phase 6: per-agent memory tree (owner API for the Phase 7B Memory tab) ────


async def _resolve_memory_namespace(workspace_id: str, agent_id: str) -> Optional[str]:
    """Sage (the workspace master install) owns the workspace-scoped memory tree
    (namespace = None, cross-project awareness); every other install owns its own
    isolated tree."""
    aid = str(agent_id or "").strip()
    if not aid:
        return None
    try:
        from server_modules import agent_registry_repository as reg
        tenant_id = await _resolve_tenant(workspace_id)
        master = await reg.get_workspace_master_agent_install(tenant_id=tenant_id, workspace_id=workspace_id)
        if str((master or {}).get("id") or "").strip() == aid:
            return None
    except Exception:
        pass
    return aid


class FleetMemoryFileWriteRequest(BaseModel):
    content: str = ""
    mode: str = "replace"


@router.get("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/tree")
async def fleet_agent_memory_tree(request: Request, workspace_id: str, agent_id: str) -> Dict[str, Any]:
    """The agent's memory tree: MEMORY.md index + topic files."""
    from server_modules import agent_memory_tree_service as tree

    try:
        ns = await _resolve_memory_namespace(workspace_id, agent_id)
        return {
            "ok": True, "agent_id": agent_id,
            "scope": "workspace" if ns is None else "install",
            **tree.list_tree(workspace_id, agent_install_id=ns),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/file")
async def fleet_agent_memory_file_read(
    request: Request, workspace_id: str, agent_id: str,
    path: str = Query(..., description="Tree path, e.g. MEMORY.md or customers/acme.md"),
) -> Dict[str, Any]:
    from server_modules import agent_memory_tree_service as tree

    try:
        ns = await _resolve_memory_namespace(workspace_id, agent_id)
        return {"ok": True, **tree.read_file(workspace_id, path, agent_install_id=ns)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.put("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/file")
async def fleet_agent_memory_file_write(
    request: Request, workspace_id: str, agent_id: str, body: FleetMemoryFileWriteRequest,
    path: str = Query(..., description="Tree path to write"),
) -> Dict[str, Any]:
    from server_modules import agent_memory_tree_service as tree

    try:
        ns = await _resolve_memory_namespace(workspace_id, agent_id)
        return {"ok": True, **tree.write_file(
            workspace_id, path, body.content or "", mode=body.mode or "replace",
            agent_install_id=ns, actor="owner",
        )}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/file")
async def fleet_agent_memory_file_delete(
    request: Request, workspace_id: str, agent_id: str,
    path: str = Query(..., description="Tree path to delete (topic files only)"),
) -> Dict[str, Any]:
    from server_modules import agent_memory_tree_service as tree

    try:
        ns = await _resolve_memory_namespace(workspace_id, agent_id)
        return {"ok": True, "deleted": tree.delete_file(workspace_id, path, agent_install_id=ns), "path": path}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/agent-channels")
async def fleet_agent_channels(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Per-agent channel/pairing status (agent detail modal → Channels tab).
    Returns workspace channel state — per-agent channels are not yet provisioned
    (see docs/ROADMAP.md: per-agent hosted bots). Until then, the workspace
    channels are the agent's channels.

    NOTE: previously called catalog_items(surface="channels"), but no catalog
    entry carries "channels" as a surface value (only agent_computer/
    applications/apps/sage/studio do) — that always returned an empty list.
    status_items(surface="sage") is what /api/connections/status (the
    working workspace-wide Channels page) actually uses; it also carries
    live connected/requires_gateway/gateway_count fields the old code had
    to hand-roll from a bare vault-id set.
    """
    from server_modules.connection_catalog_service import agent_status_items
    from server_modules.sage_telegram_hosted_service import is_configured as hosted_configured

    try:
        items = await agent_status_items(workspace_id=workspace_id, agent_id=agent_id, surface="sage")
        enriched: List[Dict[str, Any]] = []
        for item in items:
            if item.get("lane") not in ("sage_personal_channel", "studio_business_channel"):
                continue
            if item.get("id") == "telegram_bot":
                continue  # BYO-bot path, not part of the curated grid
            enriched.append({
                "id": item.get("id"),
                "label": item.get("display_name") or item.get("id"),
                "summary": item.get("description") or "",
                "connected": bool(item.get("connected")),
                "requiresGateway": bool(item.get("requires_gateway")),
                "gatewayCount": int(item.get("gateway_count") or 0),
                "onlineGatewayCount": int(item.get("online_gateway_count") or 0),
                "nextAction": item.get("next_action") or "connect",
                "runtimeUsable": bool(item.get("runtime_usable")),
                "setupAvailable": bool(item.get("setup_available")),
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
    Returns MCP/OAuth connectors available to this agent based on its role.

    NOTE: previously read item["label"]/item["image"]/item["setupKind"] —
    none of those keys exist on catalog items (the real keys are
    display_name/setup_kind, and there is no image key at all — icons are
    a client-side lookup by id). That's why labels rendered as raw ids like
    "google_workspace" instead of "Google Workspace".

    Phase 2: agent-scoped — `connected` is true iff a vault credential scoped to
    THIS agent exists AND an enabled connector binding row exists. Another
    agent's connection (or the unassigned legacy workspace credential) does not
    make it connected here.
    """
    from server_modules.connection_catalog_service import agent_status_items

    try:
        items = await agent_status_items(workspace_id=workspace_id, agent_id=agent_id, surface="apps")

        enriched: List[Dict[str, Any]] = []
        for item in items:
            if item.get("lane") != "work_app_connector":
                continue
            enriched.append({
                "id": item.get("id"),
                "label": item.get("display_name") or item.get("id"),
                "summary": item.get("description") or "",
                "connected": bool(item.get("connected")),
                "kind": item.get("setup_kind") or "oauth",
                "nextAction": item.get("next_action") or "connect",
                "healthStatus": item.get("health_status") or "unknown",
                "authRequiredFields": item.get("auth_required_fields") or [],
            })

        return {"ok": True, "connectors": enriched}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "connectors": []}


class FleetConnectAgentConnectorRequest(BaseModel):
    provider: str = Field(min_length=1)
    label: str = ""
    credentials: Dict[str, Any] = Field(default_factory=dict)
    connector_key: Optional[str] = None
    account_label: str = "default"
    credential_id: Optional[str] = Field(
        default=None,
        description="Reuse path: subscribe to this EXISTING project-scoped credential instead of connecting a new one. When set, provider/credentials/account_label are ignored.",
    )


@router.post("/api/w/{workspace_id}/fleet/agent-connectors")
async def fleet_connect_agent_connector(
    request: Request,
    workspace_id: str,
    body: FleetConnectAgentConnectorRequest,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Connect a connector inside an agent's Connectors tab (or the create-agent
    wizard's Connectors step). Two paths:

    - Reuse (body.credential_id set): subscribe this agent to an existing
      project-scoped credential — one click, no re-auth.
    - Connect new (body.credential_id absent): store a new credential at this
      agent's project scope and subscribe this agent to it. Use "Connect
      different" when the agent needs its own separate account for the same
      provider.

    Either way, the binding row is the isolation boundary: an agent only
    counts as connected if it holds an enabled binding pointing at the
    credential."""
    tenant_id = await _resolve_tenant(workspace_id)

    if body.credential_id:
        from server_modules.connectors_actions import subscribe_agent_to_project_credential
        try:
            result = await subscribe_agent_to_project_credential(
                workspace_id=workspace_id,
                agent_install_id=agent_id,
                credential_id=body.credential_id,
                tenant_id=tenant_id,
                connector_key=body.connector_key,
            )
            return {"ok": True, "connector": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    from server_modules.connectors_actions import store_agent_connector_credential
    try:
        result = await store_agent_connector_credential(
            workspace_id=workspace_id,
            agent_install_id=agent_id,
            tenant_id=tenant_id,
            provider=body.provider,
            label=body.label or body.provider,
            credentials=body.credentials,
            account_label=body.account_label,
            connector_key=body.connector_key,
        )
        return {"ok": True, "connector": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agent-connectors")
async def fleet_disconnect_agent_connector(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    connector_key: str = Query(..., description="Connector id/provider to disconnect"),
) -> Dict[str, Any]:
    """Unsubscribe this agent from a connector: removes ONLY its binding row.
    The project-scoped credential is untouched — other agents subscribed to it
    (or this one, again later) are unaffected."""
    from server_modules.connectors_actions import unsubscribe_agent_connector

    try:
        result = await unsubscribe_agent_connector(
            workspace_id=workspace_id,
            agent_install_id=agent_id,
            tenant_id=await _resolve_tenant(workspace_id),
            connector_key=connector_key,
        )
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/projects/{project_id}/connectors")
async def fleet_project_connectors(
    request: Request,
    workspace_id: str,
    project_id: str,
) -> Dict[str, Any]:
    """List a project's connector credentials (the reuse-or-separate picker's
    data source). Each item carries the agent_install_ids currently subscribed
    to it, so the picker can show "Use acme-support@gmail.com" for any
    provider the project already has."""
    from server_modules.connectors_actions import list_project_connectors

    try:
        items = await list_project_connectors(
            workspace_id=workspace_id,
            project_id=project_id,
            tenant_id=await _resolve_tenant(workspace_id),
        )
        return {"ok": True, "connectors": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "connectors": []}


@router.get("/api/w/{workspace_id}/fleet/connection-summary")
async def fleet_connection_summary(
    request: Request,
    workspace_id: str,
) -> Dict[str, Any]:
    """Honest workspace-aggregate counters for the Fleet Home strip:
    connectors/channels connected = sum of enabled bindings across the
    workspace's agents; total = catalog size."""
    from server_modules.connection_catalog_service import workspace_connection_summary

    try:
        summary = await workspace_connection_summary(workspace_id=workspace_id, tenant_id=await _resolve_tenant(workspace_id))
        return {"ok": True, **summary}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Phase 3B: per-agent Telegram bot provisioning ───────────────────────────

class FleetTelegramAssignRequest(BaseModel):
    source: str = Field(default="pool", description="'pool' (claim a platform bot) or 'byo'")
    token: Optional[str] = None


@router.post("/api/w/{workspace_id}/fleet/agent-channels/telegram")
async def fleet_assign_agent_telegram(
    request: Request,
    workspace_id: str,
    body: FleetTelegramAssignRequest,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Give this agent its OWN Telegram bot — either claimed from the hosted
    pool (source=pool) or the user's BotFather token (source=byo)."""
    from server_modules import hosted_bot_provisioning_service as prov

    tenant_id = await _resolve_tenant(workspace_id)
    source = str(body.source or "pool").strip().lower()
    try:
        if source == "byo":
            result = await prov.assign_byo_bot(
                agent_install_id=agent_id, workspace_id=workspace_id, tenant_id=tenant_id, token=body.token or "",
            )
        else:
            result = await prov.assign_pool_bot(
                agent_install_id=agent_id, workspace_id=workspace_id, tenant_id=tenant_id,
            )
        return {"ok": True, "channel": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agent-channels/telegram")
async def fleet_release_agent_telegram(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Release this agent's Telegram bot: delete webhook, clear binding, and
    return a pool bot to the pool (or delete a BYO credential)."""
    from server_modules import hosted_bot_provisioning_service as prov

    tenant_id = await _resolve_tenant(workspace_id)
    try:
        result = await prov.release_agent_telegram(
            agent_install_id=agent_id, workspace_id=workspace_id, tenant_id=tenant_id,
        )
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetDiscordAssignRequest(BaseModel):
    token: str = Field(..., description="The user's Discord bot token (BYO).")


@router.post("/api/w/{workspace_id}/fleet/agent-channels/discord")
async def fleet_assign_agent_discord(
    request: Request,
    workspace_id: str,
    body: FleetDiscordAssignRequest,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Give this agent its OWN Discord bot from the user's bot token (BYO only —
    there is no hosted Discord pool yet). One bot binds to exactly one agent."""
    from server_modules import discord_bot_provisioning_service as prov

    tenant_id = await _resolve_tenant(workspace_id)
    try:
        result = await prov.assign_agent_discord(
            agent_install_id=agent_id, workspace_id=workspace_id,
            tenant_id=tenant_id, token=body.token or "",
        )
        return {"ok": True, "channel": result}
    except prov.DiscordBotAlreadyBoundError as exc:
        return {"ok": False, "error": str(exc), "reason": "already_bound"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agent-channels/discord")
async def fleet_release_agent_discord(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
) -> Dict[str, Any]:
    """Release this agent's Discord bot: clear the binding and delete the
    agent-scoped credential."""
    from server_modules import discord_bot_provisioning_service as prov

    tenant_id = await _resolve_tenant(workspace_id)
    try:
        result = await prov.release_agent_discord(
            agent_install_id=agent_id, workspace_id=workspace_id, tenant_id=tenant_id,
        )
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


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
