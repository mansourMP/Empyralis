"""Phase U6b/S2: Fleet API routes — agent listing, activity, + per-agent
Memory, Channels, Connectors, and Tools endpoints for the agent detail modal.

Serves the Fleet Home UI and the per-agent modal tabs with:
- GET  /api/w/{workspace_id}/fleet/agents
- POST /api/w/{workspace_id}/fleet/agents
- PATCH /api/w/{workspace_id}/fleet/agents/{agent_id}
- GET /api/w/{workspace_id}/fleet/agent-activity?agent_id=
- GET /api/w/{workspace_id}/fleet/project-activity?project_id=
- GET /api/w/{workspace_id}/fleet/agent-memory?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-channels?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-connectors?agent_id=
- GET /api/w/{workspace_id}/fleet/agent-tools?agent_id=
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from server_modules import auth as auth_module

router = APIRouter(tags=["fleet"])


async def _resolve_tenant(workspace_id: str) -> str:
    """Phase 3A: derive the tenant for a fleet request from its workspace
    instead of hardcoding "default". Falls back to "default" only for a
    workspace with no tenant on record (workspaces / registry / installs)."""
    from server_modules import control_plane_repository

    return await control_plane_repository.resolve_tenant_id_for_workspace(workspace_id, default="default")


async def _channel_already_owned_message(
    subject: str, conflict: Dict[str, Any], *, tenant_id: str, workspace_id: str,
) -> str:
    """Build a specific, human-readable channel-ownership-conflict message,
    naming WHICH agent already owns the channel when a label is cheaply
    resolvable -- falling back to a still-specific "another agent" copy
    when it isn't (e.g. the owning agent was deleted, or the label lookup
    fails). `subject` is the channel description, e.g. "This Slack channel"
    or "Discord bot @sagebot"."""
    from server_modules import agent_bindings_repository as bindings

    owner_id = str((conflict or {}).get("agent_install_id") or "").strip()
    label = None
    if owner_id:
        label = await bindings.get_agent_install_label(owner_id, tenant_id=tenant_id, workspace_id=workspace_id)
    owner_desc = f'"{label}"' if label else "another agent"
    return (
        f"{subject} is already connected to {owner_desc}. "
        "A channel can only be owned by one agent at a time."
    )


@router.get("/api/w/{workspace_id}/fleet/usage")
async def fleet_usage(
    request: Request,
    workspace_id: str,
    scope: str = Query("workspace", description="workspace | agent | project"),
    id: Optional[str] = Query(None, description="agent_install_id or project_id when scope != workspace"),
    period: str = Query("day", description="day | week | month"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Phase 5A: normalized usage rollup — per-agent / per-project / per-workspace,
    bucketed by day/week/month, with usd_cost totals."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import usage_events_repository as usage_repo

    try:
        return await usage_repo.summarize_usage(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            scope=scope,
            scope_id=id,
            period=period,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/workspace")
async def fleet_workspace(
    request: Request,
    workspace_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """The workspace's own display name — the fleet shell's breadcrumb root
    (not the platform brand, not "Home"; the actual workspace) — plus the
    workspace-wide stop state (Settings' "Stop all agents")."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import control_plane_repository

    try:
        ws = await control_plane_repository.get_workspace_by_id(resolved_workspace_id)
        name = str((ws or {}).get("name") or "").strip() or resolved_workspace_id
        meta = (ws or {}).get("metadata") if isinstance((ws or {}).get("metadata"), dict) else {}
        kill_switch = dict(meta.get("kill_switch") or {}) if isinstance(meta.get("kill_switch"), dict) else {}
        stopped = kill_switch if kill_switch.get("active") else {"active": False}
        return {"ok": True, "workspace": {"id": resolved_workspace_id, "name": name, "stopped": stopped}}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "workspace": {"id": workspace_id, "name": workspace_id, "stopped": {"active": False}}}


@router.get("/api/w/{workspace_id}/fleet/agents")
async def fleet_agents(
    request: Request,
    workspace_id: str,
    project_id: Optional[str] = Query(None, description="Filter to a single project"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """List workspace agents with placement visibility (Phase U3 fields).
    Each agent carries its project_id (Phase 2). Optionally filter to one
    project via ?project_id=."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.fleet_tools import fleet_list_agents

    try:
        result = await fleet_list_agents(
            actor_id="fleet_ui",
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
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
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """List projects in the workspace, each with its agent count."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import projects_repository as projects

    try:
        # Guarantee a default project exists so ungrouped agents have a home.
        await projects.ensure_default_project(tenant_id=await _resolve_tenant(resolved_workspace_id), workspace_id=resolved_workspace_id)
        rows = await projects.list_projects(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            include_archived=include_archived,
        )
        counts = await projects.count_agents_by_project(tenant_id=await _resolve_tenant(resolved_workspace_id), workspace_id=resolved_workspace_id)
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
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Create a project."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import projects_repository as projects

    try:
        project = await projects.create_project(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
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
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Rename, edit, or archive/unarchive a project."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import projects_repository as projects

    try:
        project = None
        if body.name is not None or body.description is not None:
            project = await projects.rename_project(
                tenant_id=await _resolve_tenant(resolved_workspace_id),
                workspace_id=resolved_workspace_id,
                project_id=project_id,
                name=body.name,
                description=body.description,
            )
        if body.archived is not None:
            project = await projects.set_project_archived(
                tenant_id=await _resolve_tenant(resolved_workspace_id),
                workspace_id=resolved_workspace_id,
                project_id=project_id,
                archived=body.archived,
            )
        if project is None:
            return {"ok": False, "error": "Project not found."}
        return {"ok": True, "project": project}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Tasks -> Agents backend foundation (docs/design/tasks-to-agents-
# research.md Section 4.6, steps 1-3): a first-class task object inside a
# Project that can be assigned to an agent. Same auth/response conventions
# as the Projects CRUD directly above -- viewer for reads, owner for any
# mutation, {"ok": ..., ...} on every branch, never a raised HTTPException
# for a business-logic failure. ──────────────────────────────────────────

@router.get("/api/w/{workspace_id}/fleet/tasks")
async def fleet_list_tasks(
    request: Request,
    workspace_id: str,
    project_id: Optional[str] = Query(None, description="Filter to one project"),
    assignee_agent_id: Optional[str] = Query(None, description="Filter to one assignee"),
    status: Optional[str] = Query(None, description="open | in_progress | blocked | awaiting_input | done"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """List tasks in the workspace, optionally filtered to a project,
    assignee, and/or status."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import project_tasks_service as tasks

    try:
        rows = await tasks.list_tasks(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            project_id=project_id,
            assignee_agent_id=assignee_agent_id,
            status=status,
        )
        return {"ok": True, "tasks": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tasks": []}


class FleetCreateTaskRequest(BaseModel):
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    due_at: Optional[str] = None


@router.post("/api/w/{workspace_id}/fleet/tasks")
async def fleet_create_task(
    request: Request,
    workspace_id: str,
    body: FleetCreateTaskRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Create a task inside a project. Unassigned (backlog) until assign_task
    is called separately -- creation and assignment are deliberately two
    steps, matching every product docs/design/tasks-to-agents-research.md
    §2 surveyed (an issue can exist before anyone owns it)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import project_tasks_service as tasks

    try:
        task = await tasks.create_task(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            project_id=body.project_id,
            title=body.title,
            description=body.description,
            created_by=str((current_user or {}).get("user_id") or "").strip() or None,
            due_at=body.due_at,
        )
        return {"ok": True, "task": task}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetPatchTaskRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    due_at: Optional[str] = None
    clear_due_at: bool = False


@router.patch("/api/w/{workspace_id}/fleet/tasks/{task_id}")
async def fleet_patch_task(
    request: Request,
    workspace_id: str,
    task_id: str,
    body: FleetPatchTaskRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Edit a task's title/description/status/due_at. Status transitions are
    plain field updates here -- open/in_progress/blocked/awaiting_input/done
    are all reachable through this one endpoint; a "done" review gate is a
    later step (docs/design/tasks-to-agents-research.md §4.6 step 6), not
    this one. Assignment is NOT patchable here -- see /assign below, the one
    shared code path for setting assignee_agent_id."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import project_tasks_service as tasks

    try:
        task = await tasks.update_task(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            task_id=task_id,
            title=body.title,
            description=body.description,
            status=body.status,
            due_at=body.due_at,
            clear_due_at=body.clear_due_at,
        )
        if task is None:
            return {"ok": False, "error": "Task not found."}
        return {"ok": True, "task": task}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetAssignTaskRequest(BaseModel):
    agent_id: str = Field(min_length=1)


@router.post("/api/w/{workspace_id}/fleet/tasks/{task_id}/assign")
async def fleet_assign_task(
    request: Request,
    workspace_id: str,
    task_id: str,
    body: FleetAssignTaskRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Assign a task to an agent -- calls project_tasks_service.assign_task,
    the ONE code path a future @-mention resolver must also call (docs/
    design/tasks-to-agents-research.md §2 pitfall #2: assignment and mention
    must never fork into two different code paths). Schedules the
    task_assigned wakeup as a side effect; a scheduler failure is reported
    in the response without undoing the assignment itself (see assign_task's
    own docstring)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import project_tasks_service as tasks

    try:
        result = await tasks.assign_task(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            task_id=task_id,
            agent_id=body.agent_id,
            triggered_by=str((current_user or {}).get("user_id") or "").strip() or "owner",
        )
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetCreateAgentRequest(BaseModel):
    name: str = ""  # optional — server assigns a pool name when absent (see agent_name_pool.py)
    instructions: str = ""
    purpose_preset: str = ""
    audience: str = ""  # "owner" | "external" — facing flag for the create-agent wizard; derived from purpose_preset when omitted (see fleet_tools._AUDIENCE_BY_PURPOSE_PRESET)
    capability_preset: str = "standard"  # Phase 5B: knowledge | standard
    project_id: str = ""  # Phase 7B: assign to a project at creation


@router.post("/api/w/{workspace_id}/fleet/agents")
async def fleet_create_agent_route(
    request: Request,
    workspace_id: str,
    body: FleetCreateAgentRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Create a specialist agent (create-agent wizard, steps 1-2)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_create_agent

    try:
        result = await fleet_create_agent(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            name=body.name,
            instructions=body.instructions,
            purpose_preset=body.purpose_preset,
            audience=body.audience,
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
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Patch an agent's fleet-managed config (create-agent wizard steps 2-5,
    and any future inline edits from the agent detail modal)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_configure_agent

    try:
        result = await fleet_configure_agent(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
            patch=body.patch,
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Owner-only stop control ──────────────────────────────────────────────
# Unlike every other route in this file, these are owner-gated: stopping an
# agent (or the whole workspace) is a human/owner action, never something an
# agent can do via a tool call — enforce_workspace_access(minimum_role=
# "owner") raises 403 for anyone else, and fleet_tools.fleet_stop_agent/etc.
# are not wired into the LLM tool dispatcher at all.


class FleetStopAgentRequest(BaseModel):
    reason: str = ""


def _actor_label(current_user: Dict[str, Any]) -> str:
    return str((current_user or {}).get("email") or (current_user or {}).get("user_id") or "").strip()


@router.post("/api/w/{workspace_id}/fleet/agents/{agent_id}/stop")
async def fleet_stop_agent_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    body: FleetStopAgentRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only emergency stop for a single agent."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_stop_agent

    try:
        return await fleet_stop_agent(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            actor_label=_actor_label(current_user),
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
            reason=body.reason,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/api/w/{workspace_id}/fleet/agents/{agent_id}/resume")
async def fleet_resume_agent_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only resume for a single stopped agent."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_resume_agent

    try:
        return await fleet_resume_agent(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            actor_label=_actor_label(current_user),
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agents/{agent_id}")
async def fleet_delete_agent_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only, irreversible: permanently delete a single agent. Tears down
    its channel bindings (Discord/Telegram/Slack, incl. the agent-exclusive
    BYO bot credential + Telegram webhook), connector bindings, pending
    schedules, tool toggles, and on-disk memory, then hard-deletes the
    workspace_agent_installs row itself. See fleet_tools.fleet_delete_agent's
    own docstring for the exact teardown scope (and what it deliberately
    leaves untouched, like project-scoped connector credentials shared with
    other agents, and the workspace's own operator/Sage install, which this
    can never delete)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_delete_agent

    try:
        return await fleet_delete_agent(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            actor_label=_actor_label(current_user),
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/api/w/{workspace_id}/fleet/stop-all")
async def fleet_stop_workspace_route(
    request: Request,
    workspace_id: str,
    body: FleetStopAgentRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only emergency stop for every agent in the workspace."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_stop_workspace

    try:
        return await fleet_stop_workspace(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            actor_label=_actor_label(current_user),
            workspace_id=resolved_workspace_id,
            reason=body.reason,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/api/w/{workspace_id}/fleet/resume-all")
async def fleet_resume_workspace_route(
    request: Request,
    workspace_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only resume for every agent in the workspace."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_resume_workspace

    try:
        return await fleet_resume_workspace(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            actor_label=_actor_label(current_user),
            workspace_id=resolved_workspace_id,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Owner-only schedule control ──────────────────────────────────────────
# Part U2: when an agent wakes on its own. Same owner-gating as the stop
# control above — fleet_tools.fleet_*_agent_schedule are not wired into the
# LLM tool dispatcher; an agent proposes its own wake-ups only through the
# fleet__schedule_task tool (mandate-gated, see authority_mandate_service),
# never through these routes.


class FleetCreateScheduleRequest(BaseModel):
    when: str = ""
    instruction: str = ""


class FleetPreviewScheduleRequest(BaseModel):
    when: str = ""


@router.get("/api/w/{workspace_id}/fleet/agents/{agent_id}/schedule")
async def fleet_list_agent_schedule_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only list of this agent's scheduled wake-ups."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_list_agent_schedule

    try:
        return await fleet_list_agent_schedule(
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "schedule": []}


@router.post("/api/w/{workspace_id}/fleet/agents/{agent_id}/schedule/preview")
async def fleet_preview_agent_schedule_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    body: FleetPreviewScheduleRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only preview of what a 'when' expression resolves to, before
    confirming creation. Read-only — parses but never persists."""
    auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_preview_schedule_when

    try:
        return fleet_preview_schedule_when(when=body.when)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/api/w/{workspace_id}/fleet/agents/{agent_id}/schedule")
async def fleet_create_agent_schedule_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    body: FleetCreateScheduleRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only: schedule a future wake-up for this agent. Always executes
    at owner tier — see fleet_tools.fleet_create_agent_schedule."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_create_agent_schedule

    try:
        return await fleet_create_agent_schedule(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            actor_label=_actor_label(current_user),
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
            when=body.when,
            instruction=body.instruction,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agents/{agent_id}/schedule/{wake_request_id}")
async def fleet_cancel_agent_schedule_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    wake_request_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only cancel of one of this agent's pending wake-ups."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_cancel_agent_schedule

    try:
        return await fleet_cancel_agent_schedule(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            actor_label=_actor_label(current_user),
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
            wake_request_id=wake_request_id,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/agent-activity")
async def fleet_agent_activity(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    since: Optional[str] = Query(None, description="ISO timestamp filter"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Get REAL ledger events for an agent (Phase U4 detail panel Activity tab)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.fleet_tools import fleet_get_agent_activity

    try:
        result = await fleet_get_agent_activity(
            actor_id="fleet_ui",
            workspace_id=resolved_workspace_id,
            agent_id=agent_id,
            since=since,
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "events": []}


@router.get("/api/w/{workspace_id}/fleet/project-activity")
async def fleet_project_activity(
    request: Request,
    workspace_id: str,
    project_id: str = Query(..., description="Project ID"),
    limit: int = Query(20, description="Max events to return"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Recent ledger events across a project's agents (project detail right
    panel's Activity section — panel-only, no separate tab)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.fleet_tools import fleet_get_project_activity

    try:
        result = await fleet_get_project_activity(
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            project_id=project_id,
            limit=limit,
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "events": []}


@router.get("/api/w/{workspace_id}/fleet/agent-memory")
async def fleet_agent_memory(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Per-agent memory file listing (agent detail modal → Memory tab)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.agent_memory_tools import memory_list

    try:
        result = await memory_list(
            workspace_id=resolved_workspace_id,
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
async def fleet_agent_memory_tree(
    request: Request, workspace_id: str, agent_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """The agent's memory tree: MEMORY.md index + topic files."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import agent_memory_tree_service as tree

    try:
        ns = await _resolve_memory_namespace(resolved_workspace_id, agent_id)
        return {
            "ok": True, "agent_id": agent_id,
            "scope": "workspace" if ns is None else "install",
            **tree.list_tree(resolved_workspace_id, agent_install_id=ns),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/file")
async def fleet_agent_memory_file_read(
    request: Request, workspace_id: str, agent_id: str,
    path: str = Query(..., description="Tree path, e.g. MEMORY.md or customers/acme.md"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import agent_memory_tree_service as tree

    try:
        ns = await _resolve_memory_namespace(resolved_workspace_id, agent_id)
        return {"ok": True, **tree.read_file(resolved_workspace_id, path, agent_install_id=ns)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.put("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/file")
async def fleet_agent_memory_file_write(
    request: Request, workspace_id: str, agent_id: str, body: FleetMemoryFileWriteRequest,
    path: str = Query(..., description="Tree path to write"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import agent_memory_tree_service as tree

    try:
        ns = await _resolve_memory_namespace(resolved_workspace_id, agent_id)
        return {"ok": True, **tree.write_file(
            resolved_workspace_id, path, body.content or "", mode=body.mode or "replace",
            agent_install_id=ns, actor="owner",
        )}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/file")
async def fleet_agent_memory_file_delete(
    request: Request, workspace_id: str, agent_id: str,
    path: str = Query(..., description="Tree path to delete (topic files only)"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import agent_memory_tree_service as tree

    try:
        ns = await _resolve_memory_namespace(resolved_workspace_id, agent_id)
        return {"ok": True, "deleted": tree.delete_file(resolved_workspace_id, path, agent_install_id=ns), "path": path}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/agent-channels")
async def fleet_agent_channels(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
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

    selected_gateway_id is this agent's OWN preferred_gateway_id (same field
    PersonalChannelConnectPanel.tsx's agentGatewayId already uses) — without
    it, connection_catalog_service._selected_gateway() resolves nothing and
    every personal-channel item's `connected` stays hardcoded False
    regardless of real state.
    """
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.connection_catalog_service import agent_status_items
    from server_modules.sage_telegram_hosted_service import is_configured as hosted_configured

    try:
        tenant_id = await _resolve_tenant(resolved_workspace_id)
        from server_modules import agent_registry_repository as _reg
        bundle = await _reg.get_workspace_agent_install_bundle(agent_id, tenant_id=tenant_id, workspace_id=resolved_workspace_id)
        bundle_metadata = (bundle or {}).get("metadata") if isinstance((bundle or {}).get("metadata"), dict) else {}
        selected_gateway_id = str((bundle_metadata or {}).get("preferred_gateway_id") or "").strip() or None

        items = await agent_status_items(
            workspace_id=resolved_workspace_id, agent_id=agent_id, tenant_id=tenant_id,
            surface="sage", selected_gateway_id=selected_gateway_id,
        )
        enriched: List[Dict[str, Any]] = []
        telegram_bot_connected = False
        for item in items:
            if item.get("id") == "telegram_bot":
                # BYO-bot path, not part of the curated grid (it's a door
                # inside the "Telegram" card, not its own card) — but its
                # connected state is a distinct catalog item from
                # sage_telegram_hosted's (separate provider/vault_provider,
                # see connection_catalog_service.py), so the grid pill above
                # can't tell the ChannelsTab byo_bot door whether ITS token
                # is already saved. Surfaced below instead of silently lost.
                telegram_bot_connected = bool(item.get("connected"))
                continue
            if item.get("lane") not in ("sage_personal_channel", "studio_business_channel"):
                continue
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

        # Slack's per-agent binding is a specific channel id within the
        # workspace's shared OAuth connection (see fleet_assign_agent_slack's
        # own docstring) — "connected" above only says the OAuth app is
        # installed, not which channel THIS agent owns. Read the same table
        # POST /agent-channels/slack writes to, so the Slack-bind door can
        # show "already bound to X" instead of a blank field on reopen.
        slack_channel_binding: Optional[str] = None
        try:
            from server_modules import agent_bindings_repository as _bindings
            agent_bindings = await _bindings.list_agent_channel_bindings(
                tenant_id=tenant_id, workspace_id=resolved_workspace_id, agent_install_id=agent_id,
            )
            slack_binding = next((b for b in agent_bindings if b.get("channel_key") == "slack"), None)
            if slack_binding:
                endpoint_key = str((slack_binding.get("binding") or {}).get("endpoint_key") or "").strip()
                slack_channel_binding = endpoint_key or None
        except Exception:
            slack_channel_binding = None

        return {
            "ok": True,
            "channels": enriched,
            "hosted_telegram_configured": hosted_configured(),
            "telegram_bot_connected": telegram_bot_connected,
            "slack_channel_binding": slack_channel_binding,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "channels": []}


@router.get("/api/w/{workspace_id}/fleet/agent-connectors")
async def fleet_agent_connectors(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
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
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.connection_catalog_service import agent_status_items
    from server_modules.connection_oauth_service import OAUTH_PROVIDER_CONFIGS, oauth_provider_configured

    try:
        items = await agent_status_items(workspace_id=resolved_workspace_id, agent_id=agent_id, surface="apps")

        enriched: List[Dict[str, Any]] = []
        for item in items:
            if item.get("lane") != "work_app_connector":
                continue
            item_id = str(item.get("id") or "").strip().lower()
            # Not every connector goes through the OAuth env-var path (manual
            # credential entry doesn't need one) — only mark those as
            # configurable-or-not; anything else is always "configured" since
            # this check doesn't apply to it.
            configured = item_id not in OAUTH_PROVIDER_CONFIGS or oauth_provider_configured(item_id)
            enriched.append({
                "id": item.get("id"),
                "label": item.get("display_name") or item.get("id"),
                "summary": item.get("description") or "",
                "connected": bool(item.get("connected")),
                "kind": item.get("setup_kind") or "oauth",
                "nextAction": item.get("next_action") or "connect",
                "healthStatus": item.get("health_status") or "unknown",
                "authRequiredFields": item.get("auth_required_fields") or [],
                "configured": configured,
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
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
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
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    tenant_id = await _resolve_tenant(resolved_workspace_id)

    if body.credential_id:
        from server_modules.connectors_actions import subscribe_agent_to_project_credential
        try:
            result = await subscribe_agent_to_project_credential(
                workspace_id=resolved_workspace_id,
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
            workspace_id=resolved_workspace_id,
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
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Unsubscribe this agent from a connector: removes ONLY its binding row.
    The project-scoped credential is untouched — other agents subscribed to it
    (or this one, again later) are unaffected."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.connectors_actions import unsubscribe_agent_connector

    try:
        result = await unsubscribe_agent_connector(
            workspace_id=resolved_workspace_id,
            agent_install_id=agent_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
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
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """List a project's connector credentials (the reuse-or-separate picker's
    data source). Each item carries the agent_install_ids currently subscribed
    to it, so the picker can show "Use acme-support@gmail.com" for any
    provider the project already has."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.connectors_actions import list_project_connectors

    try:
        items = await list_project_connectors(
            workspace_id=resolved_workspace_id,
            project_id=project_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
        )
        return {"ok": True, "connectors": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "connectors": []}


@router.get("/api/w/{workspace_id}/fleet/connection-summary")
async def fleet_connection_summary(
    request: Request,
    workspace_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Honest workspace-aggregate counters for the Fleet Home strip:
    connectors/channels connected = sum of enabled bindings across the
    workspace's agents; total = catalog size."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.connection_catalog_service import workspace_connection_summary

    try:
        summary = await workspace_connection_summary(workspace_id=resolved_workspace_id, tenant_id=await _resolve_tenant(resolved_workspace_id))
        return {"ok": True, **summary}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Per-agent Telegram bot provisioning (BYO token only) ────────────────────
# The platform has no bot pool for specialist agents — only the user's own
# BotFather token. The one platform-owned Telegram bot is reserved for Sage
# (server_modules/sage_telegram_hosted_service.py), entirely separate from
# this per-agent path.

class FleetTelegramAssignRequest(BaseModel):
    token: str = Field(..., description="The user's BotFather token (BYO).")


@router.post("/api/w/{workspace_id}/fleet/agent-channels/telegram")
async def fleet_assign_agent_telegram(
    request: Request,
    workspace_id: str,
    body: FleetTelegramAssignRequest,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Give this agent its OWN Telegram bot from the user's BotFather token."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import hosted_bot_provisioning_service as prov

    tenant_id = await _resolve_tenant(resolved_workspace_id)
    try:
        result = await prov.assign_byo_bot(
            agent_install_id=agent_id, workspace_id=resolved_workspace_id, tenant_id=tenant_id, token=body.token,
        )
        return {"ok": True, "channel": result}
    except prov.TelegramBotAlreadyBoundError as exc:
        return {"ok": False, "error": str(exc), "reason": "already_bound"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agent-channels/telegram")
async def fleet_release_agent_telegram(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Release this agent's Telegram bot: delete webhook, clear binding, and
    delete its BYO credential."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import hosted_bot_provisioning_service as prov

    tenant_id = await _resolve_tenant(resolved_workspace_id)
    try:
        result = await prov.release_agent_telegram(
            agent_install_id=agent_id, workspace_id=resolved_workspace_id, tenant_id=tenant_id,
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
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Give this agent its OWN Discord bot from the user's bot token (BYO only —
    there is no hosted Discord pool yet). One bot binds to exactly one agent."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import discord_bot_provisioning_service as prov

    tenant_id = await _resolve_tenant(resolved_workspace_id)
    try:
        result = await prov.assign_agent_discord(
            agent_install_id=agent_id, workspace_id=resolved_workspace_id,
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
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Release this agent's Discord bot: clear the binding and delete the
    agent-scoped credential."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import discord_bot_provisioning_service as prov

    tenant_id = await _resolve_tenant(resolved_workspace_id)
    try:
        result = await prov.release_agent_discord(
            agent_install_id=agent_id, workspace_id=resolved_workspace_id, tenant_id=tenant_id,
        )
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetSlackChannelBindRequest(BaseModel):
    slack_channel_id: str = Field(..., description="The Slack channel id (e.g. C0123ABC456) this agent owns.")


@router.post("/api/w/{workspace_id}/fleet/agent-channels/slack")
async def fleet_assign_agent_slack(
    request: Request,
    workspace_id: str,
    body: FleetSlackChannelBindRequest,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Bind this agent to one Slack channel within the workspace's already-
    connected Slack app. Slack's OAuth connection is workspace-wide (one
    app install can serve many agents, unlike Discord's dedicated-bot-per-
    agent model), so per-agent ownership here is per-CHANNEL: inbound
    messages in that channel route to THIS agent instead of Sage
    (agent_channel_router._resolve_agent_for_inbound matches on this exact
    endpoint_key).

    'slack' is covered by uq_agent_channel_bindings_inbound_owner_v2
    (control_plane_repository.py, closed by commit b7f17d367) -- two agents
    in the same workspace cannot both claim the same Slack channel: the
    soft pre-check below gives a friendly, specific error immediately, and
    the unique index is the DB-level, race-free backstop if two binds land
    concurrently (translated to the same friendly copy, not left as a raw
    constraint-violation string)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import agent_bindings_repository as bindings

    channel_id = str(body.slack_channel_id or "").strip()
    if not channel_id:
        return {"ok": False, "error": "slack_channel_id is required."}

    tenant_id = await _resolve_tenant(resolved_workspace_id)

    conflict = await bindings.find_inbound_owner_conflict(
        tenant_id=tenant_id, workspace_id=resolved_workspace_id,
        channel_key="slack", endpoint_key=channel_id, exclude_agent_install_id=agent_id,
    )
    if conflict is not None:
        return {
            "ok": False,
            "error": await _channel_already_owned_message(
                "This Slack channel", conflict, tenant_id=tenant_id, workspace_id=resolved_workspace_id,
            ),
            "reason": "already_bound",
        }

    try:
        result = await bindings.upsert_channel_binding(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id, agent_install_id=agent_id,
            channel_key="slack", enabled=True,
            binding={
                "endpoint_key": channel_id,
                "is_inbound_owner": True,
                "source": "fleet_agent_channels",
            },
        )
        if result is None:
            return {"ok": False, "error": "Channel binding could not be saved."}
        return {"ok": True, "channel": {"channel_key": "slack", "endpoint_key": channel_id}}
    except Exception as exc:
        if bindings.is_inbound_owner_conflict(exc):
            return {
                "ok": False,
                "error": "This Slack channel is already connected to another agent. "
                         "A channel can only be owned by one agent at a time.",
                "reason": "already_bound",
            }
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agent-channels/slack")
async def fleet_release_agent_slack(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Release this agent's Slack channel binding."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import agent_bindings_repository as bindings

    tenant_id = await _resolve_tenant(resolved_workspace_id)
    try:
        deleted = await bindings.delete_channel_binding(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id, agent_install_id=agent_id,
            channel_key="slack",
        )
        return {"ok": True, "deleted": bool(deleted)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Per-agent WeChat Official Account / WeCom provisioning (BYO app credentials) ──
# Closes the gap connection_catalog_service.py's "wechat_official" catalog
# entry itself flags ("No UI/route exposes wechat_official_service.
# assign_wechat_official ... there is no way for a workspace owner to
# actually bind an agent to a WeChat/WeCom app yet"). Same BYO-per-agent
# shape as Telegram/Discord above -- WeChat/WeCom credentials are a
# workspace's own AppID+AppSecret (or CorpID+CorpSecret+AgentId) pair, not a
# first-party pool the platform owns (see wechat_official_service.py's
# module doc).

class FleetWeChatAssignRequest(BaseModel):
    account_kind: str = Field(..., description="'official_account' (WeChat Official Account) or 'wecom' (WeChat Work).")
    app_id: str = Field(..., description="WeChat AppID (official_account) or WeCom CorpID (wecom).")
    app_secret: str = Field(..., description="WeChat AppSecret (official_account) or WeCom CorpSecret (wecom).")
    verify_token: str = Field(..., description="The Token value configured in the WeChat/WeCom admin console's Server Configuration -- used to verify inbound callback signatures.")
    wecom_agent_id: Optional[str] = Field(
        default=None,
        description="WeCom app AgentId -- required when account_kind is 'wecom', unused for 'official_account'.",
    )


@router.post("/api/w/{workspace_id}/fleet/agent-channels/wechat")
async def fleet_assign_agent_wechat(
    request: Request,
    workspace_id: str,
    body: FleetWeChatAssignRequest,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Give this agent its own WeChat Official Account / WeCom bot from the
    user's own AppID+AppSecret (or CorpID+CorpSecret+AgentId) credentials.
    Validates the credentials for real (a live access_token fetch against
    Tencent) before storing anything, same as assign_byo_bot's get_me()
    check for Telegram. Returns the per-agent webhook URL
    (wechat_official_service.agent_wechat_webhook_url) the workspace owner
    must paste into the WeChat/WeCom admin console's Server Configuration."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import wechat_official_service as wechat

    tenant_id = await _resolve_tenant(resolved_workspace_id)
    try:
        result = await wechat.assign_wechat_official(
            agent_install_id=agent_id, workspace_id=resolved_workspace_id, tenant_id=tenant_id,
            account_kind=body.account_kind, app_id=body.app_id, app_secret=body.app_secret,
            verify_token=body.verify_token, agent_id=body.wecom_agent_id,
        )
        return {"ok": True, "channel": result}
    except wechat.WeChatAlreadyBoundError as exc:
        return {"ok": False, "error": str(exc), "reason": "already_bound"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agent-channels/wechat")
async def fleet_release_agent_wechat(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Release this agent's WeChat/WeCom binding: clear the binding and
    delete its BYO credential. No remote "delete webhook" call exists on
    Tencent's side -- the callback URL stays registered in the WeChat/WeCom
    admin console until the user manually clears it there."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import wechat_official_service as wechat

    tenant_id = await _resolve_tenant(resolved_workspace_id)
    try:
        result = await wechat.release_agent_wechat(
            agent_install_id=agent_id, workspace_id=resolved_workspace_id, tenant_id=tenant_id,
        )
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/agent-tools")
async def fleet_agent_tools(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Per-agent tool catalog (agent detail modal → Tools tab).
    Returns the tool manifest for this agent based on its hardware_status
    and role."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.fleet_tools import fleet_get_agent_tools

    try:
        manifest = await fleet_get_agent_tools(
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
        )
        return manifest
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tools": []}


# ── Per-agent Capabilities (image/video generation, TTS/STT) ───────────────
# See agent_capability_service.py for the resolver + storage model. Same
# platform_credits/byok_api spectrum fleet-provider-constants.ts's
# ProviderMode already defines for the chat model, one level down. Flat API
# keys only (no OAuth) — see docs/OpenClaw.md for why this deliberately
# skips connection_oauth_service.py / mcp_registry_service.py.

@router.get("/api/w/{workspace_id}/fleet/agent-capabilities")
async def fleet_agent_capabilities(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Per-agent capability catalog + resolved state (agent detail →
    Capabilities tab). Never returns key material."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules.fleet_tools import fleet_get_agent_capabilities

    try:
        return await fleet_get_agent_capabilities(
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "capabilities": []}


class FleetSetAgentCapabilityKeyRequest(BaseModel):
    capability: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    api_key: str = Field(min_length=1)


@router.post("/api/w/{workspace_id}/fleet/agent-capabilities/key")
async def fleet_set_agent_capability_key_route(
    request: Request,
    workspace_id: str,
    body: FleetSetAgentCapabilityKeyRequest,
    agent_id: str = Query(..., description="Agent install ID"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Save a BYOK API key for one capability, scoped to this agent only.
    Encrypts server-side before storing; the key is never echoed back."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_set_agent_capability_key

    try:
        result = await fleet_set_agent_capability_key(
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
            capability=body.capability,
            provider=body.provider,
            api_key=body.api_key,
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agent-capabilities/key")
async def fleet_clear_agent_capability_key_route(
    request: Request,
    workspace_id: str,
    agent_id: str = Query(..., description="Agent install ID"),
    capability: str = Query(..., description="Capability id, e.g. image_generation"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Remove a stored BYOK key for one capability and fall back to
    platform_credits."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_clear_agent_capability_key

    try:
        result = await fleet_clear_agent_capability_key(
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
            capability=capability,
        )
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
