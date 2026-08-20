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

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from server_modules import auth as auth_module

LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["fleet"])


async def _resolve_tenant(workspace_id: str) -> str:
    """Phase 3A: derive the tenant for a fleet request from its workspace
    instead of hardcoding "default". Falls back to "default" only for a
    workspace with no tenant on record (workspaces / registry / installs)."""
    from server_modules import control_plane_repository

    return await control_plane_repository.resolve_tenant_id_for_workspace(workspace_id, default="default")


def _resolve_gateway_display_name(gateway_id: Optional[str]) -> Optional[str]:
    """Human-readable label for a project's default_gateway_id, resolved the
    same way GatewayBoxPicker's own gatewayLabel() does client-side
    (display_name, falling back to hostname/platform/the raw id) — so a
    project's saved default reads the same name here as it would in the
    picker itself. Kept out of projects_repository.py (repo layer stays free
    of the gateway-registry dependency, same reasoning fleet_tools.
    gateway_resolves_in_workspace's own docstring gives for living there
    instead of in this file). None when the id is empty or doesn't resolve
    to a live registration (deleted/never existed) — the frontend renders an
    honest "no longer available" state for that rather than a raw id."""
    gid = str(gateway_id or "").strip()
    if not gid:
        return None
    try:
        from server_modules import gateway_registry_service, gateway_state_repository

        registration = gateway_state_repository.get_gateway_registration(gid)
        if not registration:
            return None
        payload = gateway_registry_service.gateway_registration_public_payload(registration)
        label = str(
            payload.get("display_name")
            or (registration.get("metadata") or {}).get("hostname")
            or payload.get("platform")
            or gid
        ).strip()
        return label or None
    except Exception:
        return None


def _default_gateway_shared(gateway_id: Optional[str]) -> bool:
    """Whether a project's saved default_gateway_id is actually LIVE right
    now — its owner has opted this machine into project sharing (CLAUDE.md:
    "Hardware attaches to its owner, never to the project"). A stored value
    can be inert: it predates the opt-in check, or its owner has since
    revoked consent, and set_project_default_gateway/
    resolve_specialist_runtime_context will never hand it to an agent
    either way. Surfaced separately from default_gateway_label so the
    frontend can show an honest "not currently shared" state instead of a
    label that reads as active when it silently isn't — a control whose own
    state lies about what it does is exactly the "no dead controls" failure
    mode CLAUDE.md calls out. False (never a guess) on any empty id or
    lookup failure."""
    gid = str(gateway_id or "").strip()
    if not gid:
        return False
    try:
        from server_modules import gateway_state_repository

        return gateway_state_repository.gateway_project_sharing_opted_in(gid)
    except Exception:
        return False


# ── MAN-115: real per-project ACL wiring ─────────────────────────────────
# The MAN-70 placeholder ("project member" == "workspace member", no
# per-project table) is replaced by project_memberships
# (server_modules/projects_repository.py, auth_module.enforce_project_access).
# Two helpers below wire it into this router: one for routes that already
# have a project_id in hand, one for agent-scoped routes that only have an
# agent_id and must resolve its owning project first (an agent belongs to
# exactly one project — see project_tasks_service.agent_project_id).

async def _visible_project_ids(
    current_user: Dict[str, Any], resolved_workspace_id: str, tenant_id: str,
) -> Optional[set]:
    """None means "no filter needed" (the caller is a workspace owner and
    sees every project). A concrete set means "only these project ids" —
    the explicit project_memberships rows for a non-owner caller. Used to
    filter list endpoints (projects, agents, tasks) that have no single
    project_id to gate on via enforce_project_access."""
    actual_role = auth_module.normalize_rbac_role(
        auth_module.workspace_role(current_user, resolved_workspace_id)
        or auth_module.current_user_role(current_user, default="viewer"),
        default="viewer",
    )
    if auth_module.RBAC_ROLE_ORDER[actual_role] >= auth_module.RBAC_ROLE_ORDER["owner"]:
        return None
    from server_modules import projects_repository as projects

    user_id = str((current_user or {}).get("user_id") or "").strip()
    ids = await projects.list_member_project_ids(
        tenant_id=tenant_id, workspace_id=resolved_workspace_id, user_id=user_id,
    )
    if not ids:
        # MAN-335: a workspace invite accepted before this fix granted
        # membership and NOTHING ELSE — this member's own project list has
        # been permanently empty since the day they accepted, with no
        # control anywhere that could fix it (nothing re-runs invite
        # acceptance for someone already accepted). Self-heals here, the
        # exact place the empty result is computed, on this member's own
        # next request — see backfill_default_project_access_if_never_
        # granted's docstring for why a durable per-member marker (not
        # "currently zero") is what keeps this from ever undoing a
        # deliberate later removal from every one of their projects.
        healed = await projects.backfill_default_project_access_if_never_granted(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id, user_id=user_id,
        )
        if healed:
            ids = await projects.list_member_project_ids(
                tenant_id=tenant_id, workspace_id=resolved_workspace_id, user_id=user_id,
            )
    return set(ids)


def _is_workspace_scoped_agent(agent: Dict[str, Any]) -> bool:
    """Is this the workspace-level system agent (Sage / the Operator)?

    MAN-201. The founder's decision (2026-08-01) is that Ask AI is a PERSONAL
    surface every workspace member gets, with each person's conversations
    their own — not owner-only and not shared. It was unreachable for a
    member, and the cause was NOT an `audience` gate (no such filter exists
    anywhere in the codebase; `audience` is emitted as an informational
    field and read by nothing). It was collateral damage from the MAN-115
    per-project ACL filter: this install is workspace-scoped by design, so
    it carries no project_id, and `"" in visible_ids` is False for every
    non-owner — the filter has no concept for an agent that belongs to the
    workspace rather than to a project.

    This is the SAME exemption `_enforce_agent_project_access` (right below)
    has always applied on the per-agent routes — `if not project_id: return`
    — so the list endpoint and the detail endpoints agreed on everything
    except this one agent, and only the list dropped it.

    Deliberately keyed on `agent_kind == "master"`, the producer's own
    authoritative field, and NOT on an empty project_id: a plain specialist
    that somehow ends up project-less must stay hidden from people who were
    never added to its project, so "has no project" alone must never be
    what grants visibility. The frontend already excludes this agent from
    every fleet count and list (fleet-presentation.ts's isSageAgent, and
    agent-count-shape.ts's contract that Sage never counts), so letting it
    through here re-enables the Ask AI console without adding a card.
    """
    return str(agent.get("agent_kind") or "").strip().lower() == "master"


async def _enforce_agent_project_access(
    current_user: Dict[str, Any],
    resolved_workspace_id: str,
    tenant_id: str,
    agent_id: str,
    *,
    minimum_role: str = "viewer",
) -> None:
    """Resolve the project this agent belongs to and enforce MAN-115 access
    to it, using the SAME grant rule `agent_reachability_service.
    enforce_resolved_agent_access` already enforces on the turn path — one
    decision, shared, rather than two independently-drifting opinions.

    CORRECTION (2026-08-19): this used to be `if not project_id: return` —
    an unconditional fail-open on ANY project-less agent. That was live and
    exploitable: `project_id` is nullable BY SCHEMA (`ON DELETE SET NULL`),
    and a real production row (a specialist, not the workspace master) was
    project-less and enabled, so any workspace member could reach it through
    every fleet DETAIL route (activity, memory, channels, connectors, tools,
    capabilities, usage) with no project membership at all. Fixed to the same
    rule `enforce_agent_reachable` uses: `agent_kind == "master"` is always
    exempt (MAN-201 — Ask AI must stay reachable by every member), a
    project-having agent is gated by that project's own ACL (unchanged), and
    a project-less SPECIALIST is now workspace-OWNER-only, never an ordinary
    member.

    The one thing this helper still does differently from
    `enforce_agent_reachable`, on purpose: if the agent bundle cannot be
    resolved at all (doesn't exist here, or the lookup failed), this still
    does NOT raise — there is nothing to leak for an agent that isn't there,
    and the underlying service call a route makes right after this will
    itself return a normal not-found/empty result. `enforce_agent_reachable`
    is the one that must fail closed on an unresolvable bundle (it is the
    ONLY gate on the turn-execution path); this one is a defense-in-depth
    check ahead of a call that already degrades safely on its own."""
    from server_modules import agent_reachability_service as reachability

    clean_agent_id = str(agent_id or "").strip()
    if not clean_agent_id:
        return
    bundle = await reachability.lookup_agent_install_bundle(
        clean_agent_id, tenant_id=tenant_id, workspace_id=resolved_workspace_id,
    )
    if bundle is None:
        return
    await reachability.enforce_resolved_agent_access(
        current_user, resolved_workspace_id, bundle, minimum_role=minimum_role,
    )


async def _enforce_task_project_access(
    current_user: Dict[str, Any],
    resolved_workspace_id: str,
    tenant_id: str,
    task_id: str,
    *,
    minimum_role: str = "viewer",
) -> None:
    """MAN-64/MAN-70 member-write rollout: the task-scoped counterpart of
    _enforce_agent_project_access just above. Resolve the task this route is
    about to read/mutate to its project and enforce MAN-115 access to it.

    WHY THIS EXISTS NOW AND DIDN'T BEFORE: every task-mutation route in this
    file used to be owner-only, and a workspace owner already bypasses the
    per-project ACL entirely (auth_module.enforce_project_access's own
    2026-07-28 ruling: an owner sees every project in their own workspace,
    full stop). That made an explicit project check on these routes
    redundant -- the workspace-level owner gate WAS the project gate, by
    construction. Loosening those routes to `member` breaks that equivalence:
    a member's workspace role no longer implies they can see every project,
    so without this check a member with project_memberships access to
    Project A could create/edit/assign/comment/label a task in Project B
    purely by knowing its id -- an actual privilege escalation past MAN-115's
    per-project ACL, not just a permissions nicety. This closes exactly that
    gap for every task-scoped route this rollout moves to `member`.

    If the task can't be resolved (doesn't exist in this workspace), this
    deliberately does NOT raise -- same posture as _enforce_agent_project_
    access: there is nothing to leak for a task that isn't there, and the
    mutation the route makes right after this will itself return its own
    normal "Task not found" result instead of a 403/404 that would reveal
    whether the id exists at all."""
    from server_modules import project_tasks_service as tasks

    task = await tasks.get_task(tenant_id=tenant_id, workspace_id=resolved_workspace_id, task_id=task_id)
    project_id = str((task or {}).get("project_id") or "").strip()
    if not project_id:
        return
    await auth_module.enforce_project_access(
        current_user, resolved_workspace_id, project_id, minimum_role=minimum_role,
    )


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
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    # MAN-115: scope=project leaks a project's own cost/token totals to
    # whoever knows its id unless gated the same as every other
    # project-scoped read below.
    if scope == "project" and id:
        await auth_module.enforce_project_access(current_user, resolved_workspace_id, id, minimum_role="viewer")
    # MAN-115 (closed the same day it was found missing here, 2026-08-13):
    # scope=agent had no equivalent check -- usage_events_repository.
    # summarize_usage filters purely by tenant_id+workspace_id+
    # agent_install_id, with no project predicate at all, so a workspace
    # member with project_memberships access to Project A only could read
    # Project B's agent's full cost/token rollup (model name, tokens,
    # usd_cost) by id alone. Every other agent-scoped read in this file
    # (fleet_agent_activity, fleet_agent_memory, fleet_agent_channels,
    # fleet_agent_connectors, fleet_agent_tools, fleet_agent_capabilities)
    # already calls _enforce_agent_project_access first -- this route did it
    # for scope=project two lines up and not for scope=agent, inconsistently,
    # in the same function. Confirmed live: a project-A-only member reading
    # scope=agent&id=<agent-in-project-B> got the full usd_cost/token
    # breakdown while the equivalent scope=project request correctly 404'd.
    if scope == "agent" and id:
        await _enforce_agent_project_access(current_user, resolved_workspace_id, tenant_id, id, minimum_role="viewer")
    from server_modules import usage_events_repository as usage_repo

    try:
        return await usage_repo.summarize_usage(
            tenant_id=tenant_id,
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
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    # MAN-115: a specific ?project_id= is a direct access check; with none,
    # this returns every agent in the workspace, so a non-owner caller gets
    # silently filtered down to agents in projects they actually belong to
    # rather than seeing (and being able to open) agents from a project they
    # were never added to.
    if project_id:
        await auth_module.enforce_project_access(current_user, resolved_workspace_id, project_id, minimum_role="viewer")
        visible_ids = None
    else:
        visible_ids = await _visible_project_ids(current_user, resolved_workspace_id, tenant_id)
    from server_modules.fleet_tools import fleet_list_agents

    try:
        result = await fleet_list_agents(
            actor_id="fleet_ui",
            workspace_id=resolved_workspace_id,
            tenant_id=tenant_id,
        )
        if result.get("ok") and isinstance(result.get("agents"), list):
            if project_id:
                wanted = str(project_id).strip()
                result["agents"] = [
                    a for a in result["agents"] if str(a.get("project_id") or "").strip() == wanted
                ]
            elif visible_ids is not None:
                result["agents"] = [
                    a
                    for a in result["agents"]
                    if str(a.get("project_id") or "").strip() in visible_ids
                    or _is_workspace_scoped_agent(a)
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
    """List projects in the workspace, each with its agent count. MAN-115:
    a non-owner only sees projects they were explicitly added to — this is
    the list a Sidebar/Projects page renders from, so filtering here (not
    just on the detail route) is what actually keeps Project B invisible to
    someone who was never added to it, not just unopenable."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import projects_repository as projects

    try:
        tenant_id = await _resolve_tenant(resolved_workspace_id)
        # Guarantee a default project exists so ungrouped agents have a home.
        await projects.ensure_default_project(tenant_id=tenant_id, workspace_id=resolved_workspace_id)
        rows = await projects.list_projects(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            include_archived=include_archived,
        )
        visible_ids = await _visible_project_ids(current_user, resolved_workspace_id, tenant_id)
        if visible_ids is not None:
            rows = [p for p in rows if str(p.get("id") or "").strip() in visible_ids]
        counts = await projects.count_agents_by_project(tenant_id=tenant_id, workspace_id=resolved_workspace_id)
        # Workspace-home cards lead with WORK, not agent headcount (CLAUDE.md
        # positioning: "the workspace is the product"). Same one-query-per-
        # workspace shape as count_agents_by_project, added alongside it in
        # project_tasks_service/project_documents_repository rather than a
        # third counting path.
        from server_modules import project_tasks_service, project_documents_repository

        task_counts = await project_tasks_service.count_tasks_by_project(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id
        )
        document_counts = await project_documents_repository.count_documents_by_project(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id
        )
        for p in rows:
            p["agent_count"] = int(counts.get(p["id"], 0))
            p["task_count"] = int(task_counts.get(p["id"], 0))
            p["document_count"] = int(document_counts.get(p["id"], 0))
            # U3-K: resolved here (once per list call) rather than making the
            # frontend re-derive it from a separate /gateway/registrations
            # fetch — the project settings control and any other reader can
            # show "what's set" from this one response.
            p["default_gateway_label"] = _resolve_gateway_display_name(p.get("default_gateway_id"))
            p["default_gateway_shared"] = _default_gateway_shared(p.get("default_gateway_id"))
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
    """Create a project.

    MAN-64/MAN-70 permission review: LEFT AT `owner`, DELIBERATELY, flagged
    rather than loosened. The brief's member-write list is explicit about
    the TASK surface (create/edit/status/priority/assign/comment/labels/
    sub-tasks) and says nothing about creating the PROJECT container itself.
    A real argument exists either way -- Linear itself lets ordinary
    members create projects -- but that argument was not made in the brief,
    and a new project is also a new unit of MAN-115 access control (its
    creator becomes its first implicit member; every other member needs an
    explicit grant via /members below, which stays owner-only). Guessing
    permissive here is exactly the failure mode the brief warned against;
    left at the pre-existing tier instead."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import projects_repository as projects

    try:
        project = await projects.create_project(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            name=body.name,
            description=body.description,
            # MAN-115: give the creator an explicit membership row too, even
            # though creation is owner-gated (owners already bypass the
            # project check) — keeps the project's own member roster honest
            # rather than showing an empty list for a project someone just
            # made.
            created_by_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {"ok": True, "project": project}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetPatchProjectRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    archived: Optional[bool] = None
    # U3-K: the project's default Gateway — see projects_repository.
    # set_project_default_gateway. `None` (the default) leaves it untouched;
    # `""` explicitly clears it back to unset; any other string is validated
    # against this workspace's registrations before saving.
    default_gateway_id: Optional[str] = None


@router.patch("/api/w/{workspace_id}/fleet/projects/{project_id}")
async def fleet_patch_project(
    request: Request,
    workspace_id: str,
    project_id: str,
    body: FleetPatchProjectRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Rename, edit, archive/unarchive, or set the default Gateway of a
    project.

    MAN-64/MAN-70 permission review: LEFT AT `owner`, DELIBERATELY, flagged
    rather than loosened -- same reasoning as fleet_create_project just
    above (out of the brief's explicit member list, and archiving in
    particular hides a project from every non-owner member's board at
    once, which is a workspace-shaping decision closer to project-level
    settings than to routine task upkeep). default_gateway_id is gated the
    same way, if anything for a stronger reason: assigning the project's
    shared compute is a workspace-shaping decision too, not routine task
    upkeep -- see set_project_default_gateway's own docstring."""
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
        if body.default_gateway_id is not None:
            project = await projects.set_project_default_gateway(
                tenant_id=await _resolve_tenant(resolved_workspace_id),
                workspace_id=resolved_workspace_id,
                project_id=project_id,
                gateway_id=body.default_gateway_id,
            )
        if project is None:
            return {"ok": False, "error": "Project not found."}
        project["default_gateway_label"] = _resolve_gateway_display_name(project.get("default_gateway_id"))
        project["default_gateway_shared"] = _default_gateway_shared(project.get("default_gateway_id"))
        return {"ok": True, "project": project}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/projects/{project_id}")
async def fleet_delete_project(
    request: Request,
    workspace_id: str,
    project_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only, irreversible: permanently delete a project. Its tasks,
    documents, goals and member grants go with it; its agents and its
    project-scoped connector credentials are rehomed to the workspace's
    default project rather than left with a NULL project_id — see
    projects_repository.delete_project's own docstring for why that
    distinction is load-bearing (a NULL there silently revokes an agent's
    project_task__*/document__*/goal__* tools) and for exactly what is
    deliberately left untouched.

    ARCHIVING (PATCH .../projects/{id} with archived=true) is the reversible
    everyday action and stays what the UI offers first; this exists because
    "hidden forever, removable never" is its own kind of trap.

    Permission is owner-on-the-workspace, matching fleet_create_project and
    fleet_patch_project exactly — a destructive operation is never gated
    more loosely than the edit it supersedes. The tenant is resolved PER
    WORKSPACE via _resolve_tenant (control_plane_repository.
    resolve_tenant_id_for_workspace), never off the stale users.tenant_id
    column; see CLAUDE.md.
    """
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import projects_repository as projects

    try:
        tenant_id = await _resolve_tenant(resolved_workspace_id)
        removed = await projects.delete_project(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            project_id=project_id,
        )
    except ValueError as exc:
        # Business-logic refusal (the default project) — a real, explainable
        # answer, not a crash. Same {ok:false, error} shape every other
        # failure in this file returns.
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if removed is None:
        return {"ok": False, "error": "Project not found."}

    # Audit trail, mirroring fleet_tools.fleet_delete_agent's own ledger
    # write. The project id is NOT written to a foreign-keyed column — the
    # row it would point at is already gone — it lives in metadata.
    try:
        from server_modules import activity_ledger_service
        from server_modules.control_plane_repository import get_workspace_by_id

        ws = await get_workspace_by_id(resolved_workspace_id)
        await activity_ledger_service.append_activity_event(
            tenant_id=str((ws or {}).get("tenant_id") or "").strip() or "system",
            workspace_id=resolved_workspace_id,
            actor_type="user",
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            event_class="fleet_control",
            detail_level="audit_reference",
            action="project_deleted",
            title=f"{removed.get('name') or 'Project'} deleted",
            summary=(
                f"{_actor_label(current_user)} deleted this project — "
                f"{removed.get('tasks_deleted', 0)} task(s), "
                f"{removed.get('documents_deleted', 0)} document(s) removed; "
                f"{removed.get('agents_moved', 0)} agent(s) moved to "
                f"{removed.get('moved_to_project_name') or 'the default project'}."
            ),
            status="executed",
            metadata=removed,
        )
    except Exception:
        # The project IS gone; failing to journal that must not turn a
        # successful delete into a reported failure the caller retries.
        LOGGER.warning("project_deleted ledger write failed for %s", project_id, exc_info=True)

    return {"ok": True, "deleted": removed}


# ── MAN-115: project member management ───────────────────────────────────
# The UI for the real per-project ACL: who, specifically, can see this
# project. Reuses the workspace's own member list as the pool to add
# from — you can only add someone to a project if they're already a
# workspace member (checked below via control_plane_repository.
# list_workspace_members, the exact same call the Settings members section
# and MemberAvatarStack already use). Reads are viewer-gated through
# enforce_project_access (so a project member can see their own project's
# roster); grant/revoke are owner-gated, matching every other roster
# mutation in this file (invites, bug-report list, etc.).

@router.get("/api/w/{workspace_id}/fleet/projects/{project_id}/members")
async def fleet_list_project_members(
    request: Request,
    workspace_id: str,
    project_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Explicit project_memberships rows only — does NOT include workspace
    owners, who see this project via the bypass in enforce_project_access
    rather than a row here. The frontend renders owners separately (from
    the workspace member list it already fetches) captioned as "always has
    access", so the two lists together are the true roster."""
    resolved_workspace_id = await auth_module.enforce_project_access(
        current_user, workspace_id, project_id, minimum_role="viewer",
    )
    from server_modules import projects_repository as projects

    try:
        members = await projects.list_project_members(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            project_id=project_id,
        )
        return {"ok": True, "members": members}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "members": []}


class FleetAddProjectMemberRequest(BaseModel):
    user_id: str = Field(min_length=1)


@router.post("/api/w/{workspace_id}/fleet/projects/{project_id}/members")
async def fleet_add_project_member(
    request: Request,
    workspace_id: str,
    project_id: str,
    body: FleetAddProjectMemberRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Grant a workspace member access to this project. Owner-only — same
    tier as creating/archiving a project itself. The target must already be
    a workspace member: this is a visibility grant within a workspace the
    person is already in, never a back door for adding a stranger."""
    # Workspace-owner check first (not enforce_project_access's viewer
    # floor) -- granting project access is a privileged action regardless
    # of the caller's own project membership.
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import control_plane_repository, projects_repository as projects

    try:
        target_user_id = str(body.user_id or "").strip()
        workspace_members = await control_plane_repository.list_workspace_members(resolved_workspace_id)
        if not any(str(m.get("user_id") or "").strip() == target_user_id for m in workspace_members):
            return {"ok": False, "error": "That person isn't a member of this workspace yet."}
        project = await projects.get_project(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            project_id=project_id,
        )
        if project is None:
            return {"ok": False, "error": "Project not found."}
        member = await projects.add_project_member(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            project_id=project_id,
            user_id=target_user_id,
            added_by=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {"ok": True, "member": member}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/projects/{project_id}/members/{user_id}")
async def fleet_remove_project_member(
    request: Request,
    workspace_id: str,
    project_id: str,
    user_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Revoke a member's access to this project. Owner-only. Removing a row
    here never touches workspace membership itself — it only narrows which
    projects this person can see, matching the "add" side's own scope."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import projects_repository as projects

    try:
        removed = await projects.remove_project_member(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            project_id=project_id,
            user_id=user_id,
        )
        return {"ok": True, "removed": removed}
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
    sort: Optional[str] = Query(None, description="created_at (default, newest first) | priority (urgent first, untriaged last)"),
    parent_task_id: Optional[str] = Query(None, description="Filter to one task's sub-tasks"),
    top_level_only: bool = Query(False, description="Exclude sub-tasks (what a kanban board wants — a sub-task belongs on its parent's card)"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """List tasks in the workspace, optionally filtered to a project,
    assignee, and/or status. MAN-115: a task is exactly as visible as the
    project it lives in — same enforce-when-scoped / filter-when-not
    pattern as fleet_agents above.

    `sort=priority` returns the board triage-ordered (urgent first,
    untriaged last); anything else keeps the historical newest-first
    ordering, so an existing client that never passes `sort` sees no
    change."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    if project_id:
        await auth_module.enforce_project_access(current_user, resolved_workspace_id, project_id, minimum_role="viewer")
        visible_ids = None
    else:
        visible_ids = await _visible_project_ids(current_user, resolved_workspace_id, tenant_id)
    from server_modules import project_tasks_service as tasks

    try:
        rows = await tasks.list_tasks(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            project_id=project_id,
            assignee_agent_id=assignee_agent_id,
            status=status,
            sort=sort,
            parent_task_id=parent_task_id,
            top_level_only=top_level_only,
        )
        if visible_ids is not None:
            rows = [t for t in rows if str(t.get("project_id") or "").strip() in visible_ids]
        return {"ok": True, "tasks": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tasks": []}


class FleetCreateTaskRequest(BaseModel):
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    due_at: Optional[str] = None
    # Sub-tasks: name a parent here to create this as its sub-task. Exactly
    # ONE level of nesting is allowed and the rule is enforced in
    # project_tasks_service (it needs a lookup no CHECK can express), so the
    # HTTP API and the agent tool surfaces reject exactly the same shapes
    # with exactly the same message.
    parent_task_id: Optional[str] = None
    # Linear's scale: 0 = none (default/untriaged), 1 = urgent, 2 = high,
    # 3 = medium, 4 = low -- 1 is the MOST urgent. Range-validated in
    # project_tasks_service, not here, so the HTTP API and the agent tool
    # surfaces reject exactly the same set with exactly the same message.
    priority: Optional[int] = None


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
    §2 surveyed (an issue can exist before anyone owns it).

    MAN-64/MAN-70: `member` is enough -- filing a task is exactly the kind
    of ordinary teammate action the member tier exists for. Gated on the
    NAMED project (body.project_id is already in hand, no task to resolve
    it from) rather than just the workspace, so a member cannot create a
    task in a project they have no project_memberships row for -- the same
    MAN-115 boundary fleet_list_tasks already enforces on the read side."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    await auth_module.enforce_project_access(current_user, resolved_workspace_id, body.project_id, minimum_role="member")
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
            priority=body.priority,
            parent_task_id=body.parent_task_id,
        )
        return {"ok": True, "task": task}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetPatchTaskRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    # See FleetCreateTaskRequest.priority. `0` is a real patch here ("clear
    # the priority"); only omitting the field leaves it untouched.
    priority: Optional[int] = None
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
    """Edit a task's title/description/status/priority/due_at. Status transitions are
    plain field updates here -- open/in_progress/blocked/awaiting_input/done
    are all reachable through this one endpoint; a "done" review gate is a
    later step (docs/design/tasks-to-agents-research.md §4.6 step 6), not
    this one. Assignment is NOT patchable here -- see /assign below, the one
    shared code path for setting assignee_agent_id.

    MAN-64/MAN-70: `member` is enough -- editing a task's own fields is
    ordinary teammate work, the same tier Linear itself requires. Gated on
    the task's OWN project via _enforce_task_project_access, mirroring
    fleet_list_subtasks's "a task is exactly as visible as the project it
    lives in" rule -- a member cannot edit a task in a project they have no
    project_memberships row for just by knowing its task_id."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_task_project_access(current_user, resolved_workspace_id, tenant_id, task_id, minimum_role="member")
    from server_modules import project_tasks_service as tasks

    try:
        task = await tasks.update_task(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            task_id=task_id,
            title=body.title,
            description=body.description,
            status=body.status,
            priority=body.priority,
            due_at=body.due_at,
            clear_due_at=body.clear_due_at,
            # Review attribution (pure stamp, never a gate): this route is
            # the board-drag / detail-view status row, always a human
            # acting through the authenticated session -- never an agent,
            # which reaches update_task through the project_task__update
            # tool in skills_service.py instead.
            actor_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        if task is None:
            return {"ok": False, "error": "Task not found."}
        return {"ok": True, "task": task}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetAssignTaskRequest(BaseModel):
    # MAN-64/MAN-70: a task's assignee is either an agent or a human, never
    # both -- exactly ONE of these two must be set. Two optional fields
    # rather than one polymorphic {type, id} pair, mirroring the schema
    # decision in migrations/add_task_human_assignee.sql: it keeps this
    # request body symmetric with the existing agent-only shape (an old
    # client sending {"agent_id": ...} keeps working unchanged) instead of
    # forcing every caller to learn a new envelope. Validated in the handler
    # body (exactly one of the two, matching this file's established
    # "raise ValueError -> {ok:false,error}" convention) rather than a
    # pydantic validator, for the same reason the priority/status vocabularies
    # are validated in project_tasks_service and not here.
    agent_id: Optional[str] = None
    user_id: Optional[str] = None


@router.post("/api/w/{workspace_id}/fleet/tasks/{task_id}/assign")
async def fleet_assign_task(
    request: Request,
    workspace_id: str,
    task_id: str,
    body: FleetAssignTaskRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Assign a task to an agent OR a human -- calls project_tasks_service.
    assign_task (agent) or assign_task_to_user (human), the two sibling
    single-purpose entry points (see assign_task_to_user's own docstring for
    why they are deliberately not one function with an internal branch).

    MAN-66 update: the @-mention resolver this docstring used to describe as
    future work is now live (task_mention_service.py), and it deliberately
    does NOT call assign_task -- per docs/design/tasks-to-agents-research.md
    §2 pitfall #2, assignment and mention must differ ONLY in whether
    `assignee_agent_id` changes, so a mention never reassigns the task. What
    IS shared between the two entry points, per that same pitfall, is the
    WAKE mechanism: assign_task calls schedule_task_assigned_wakeup,
    mentions call schedule_task_commented_wakeup -- same scheduler, same
    quiet-hours/battery/network policy gates, same per-task daily ceiling,
    just a different trigger_kind. "One code path" refers to that shared
    wake plumbing, not to this endpoint.

    Assigning to an AGENT schedules the task_assigned wakeup as a side
    effect; a scheduler failure is reported in the response without undoing
    the assignment itself (see assign_task's own docstring). Assigning to a
    HUMAN never does -- people are not woken by schedulers -- so
    wake_request/wake_error always come back None/None on that path.

    MAN-64/MAN-70: `member` is enough -- assigning/reassigning a task
    (to yourself, another human, or an agent) is ordinary teammate work,
    the same tier Linear itself requires. Gated on the task's OWN project,
    same as /patch above -- a member cannot assign a task in a project they
    have no project_memberships row for just by knowing its task_id."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_task_project_access(current_user, resolved_workspace_id, tenant_id, task_id, minimum_role="member")
    agent_id = str(body.agent_id or "").strip()
    user_id = str(body.user_id or "").strip()
    from server_modules import project_tasks_service as tasks

    try:
        if agent_id and user_id:
            return {"ok": False, "error": "Provide either agent_id or user_id, not both."}
        if agent_id:
            result = await tasks.assign_task(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                task_id=task_id,
                agent_id=agent_id,
                triggered_by=str((current_user or {}).get("user_id") or "").strip() or "owner",
                # authority_tier deliberately omitted (defaults to None):
                # this call has no turn to inherit a tier from, only an
                # authenticated human who already cleared this route's own
                # member+ gate above -- schedule_task_assigned_wakeup
                # resolves that to TIER_OWNER (see its docstring, 2026-08-13).
            )
        elif user_id:
            result = await tasks.assign_task_to_user(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                task_id=task_id,
                user_id=user_id,
                triggered_by=str((current_user or {}).get("user_id") or "").strip() or "owner",
            )
        else:
            return {"ok": False, "error": "agent_id or user_id is required to assign a task."}
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetCommentTaskRequest(BaseModel):
    # 4000 mirrors add_task_comment's own truncation (body_text[:4000]) --
    # rejecting an over-length comment loudly here is more honest than
    # silently accepting it and truncating it later without telling anyone.
    body: str = Field(min_length=1, max_length=4000)


@router.post("/api/w/{workspace_id}/fleet/tasks/{task_id}/comments")
async def fleet_comment_task(
    request: Request,
    workspace_id: str,
    task_id: str,
    body: FleetCommentTaskRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """The human->agent comment channel (docs/design/tasks-to-agents-
    research.md §4.5): the first HTTP route that lets a PERSON post into
    task.metadata.comments -- agents have had this since project_task__comment/
    empyralis_comment_on_task, a human never has. Calls
    project_tasks_service.add_human_task_comment, the ONE code path that
    both writes the comment AND (best-effort, only when the task has an
    assignee) schedules the task_commented wakeup -- exactly assign_task's
    shape above, not a fork.

    No approval gate: per this feature's hard constraint, a comment is an
    inline message the agent picks up on its next turn, never something
    that blocks or requires sign-off. A scheduler failure (including the
    debounce/ceiling backstops in schedule_task_commented_wakeup correctly
    declining to wake) is reported via wake_error without undoing the
    comment itself, matching /assign's own contract.

    MAN-64/MAN-70: `member` is enough -- a teammate being unable to comment
    on a task they can otherwise see and edit is the exact gap this whole
    feature exists to close. Gated on the task's OWN project, same as
    /patch and /assign above."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_task_project_access(current_user, resolved_workspace_id, tenant_id, task_id, minimum_role="member")
    from server_modules import project_tasks_service as tasks

    try:
        result = await tasks.add_human_task_comment(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            task_id=task_id,
            author_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            body=body.body,
        )
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Per-user notifications (MAN-146): a NEW, additive surface -- NOT the
# same thing as GET /notifications (runtime_events_api.py), which is a
# SQLite-backed, workspace-wide activity feed spanning many unrelated event
# types (machine events, channel deliveries, etc.) with its own SSE
# streaming and read-state machinery. Retrofitting recipient scoping onto
# that endpoint would mean understanding and safely modifying a much
# larger, differently-architected, currently-live subsystem outside a
# single safe pass. This route is scoped from day one -- every query
# carries `recipient_user_id = current_user` IN ADDITION TO the workspace
# scoping every other route in this file already enforces (see
# task_notification_service.py's own module docstring for why that
# doubling matters: RLS alone only proves tenant/workspace isolation, not
# that member A can't read member B's inbox). A future frontend
# consolidation pass is the right place to decide whether the legacy feed
# is retired in favor of this one -- see task_notification_service.py's
# docstring for the three existing unread mechanisms it should pick one
# of.
@router.get("/api/w/{workspace_id}/fleet/notifications")
async def fleet_list_notifications(
    request: Request,
    workspace_id: str,
    limit: int = Query(50, description="Max notifications to return (newest first)"),
    unread_only: bool = Query(False, description="Only notifications with no read_at"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """The caller's OWN notification feed -- mentions, assignments, and
    comments on tasks they own, newest first. `viewer` is enough: reading
    your own inbox is not a privileged action, the same tier fleet_list_
    tasks itself uses for reads."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    from server_modules import task_notification_service

    try:
        items = await task_notification_service.list_notifications(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            recipient_user_id=str((current_user or {}).get("user_id") or "").strip(),
            limit=limit,
            unread_only=unread_only,
        )
        return {"ok": True, "notifications": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "notifications": []}


@router.post("/api/w/{workspace_id}/fleet/notifications/{notification_id}/read")
async def fleet_mark_notification_read(
    request: Request,
    workspace_id: str,
    notification_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Marks exactly one of the CALLER's own notifications read.
    task_notification_service.mark_notification_read's own WHERE clause
    requires recipient_user_id = the caller, so this route cannot be used
    to mark someone else's notification read even by guessing/reusing an
    id -- there is no separate ownership check needed here beyond passing
    the authenticated caller's own user_id through."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    from server_modules import task_notification_service

    try:
        notification = await task_notification_service.mark_notification_read(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            recipient_user_id=str((current_user or {}).get("user_id") or "").strip(),
            notification_id=notification_id,
        )
        if notification is None:
            return {"ok": False, "error": "Notification not found."}
        return {"ok": True, "notification": notification}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Sub-tasks: a parent link on the task itself, not a new object. Exactly
# ONE level of nesting -- a sub-task cannot have sub-tasks of its own. That
# rule is enforced in project_tasks_service (it needs a lookup no CHECK can
# express) so this endpoint and every agent tool reject the same shapes with
# the same message. Deleting a parent PROMOTES its sub-tasks to top-level
# rather than deleting them (ON DELETE SET NULL, see
# migrations/add_task_parent.sql) -- the rollup counts every task read
# already returns (subtask_count / subtask_done_count) are the "1/3" badge.
# ─────────────────────────────────────────────────────────────────────────

@router.get("/api/w/{workspace_id}/fleet/tasks/{task_id}/subtasks")
async def fleet_list_subtasks(
    request: Request,
    workspace_id: str,
    task_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """One task's sub-tasks. Same shape as any other task list -- a sub-task
    is a full task, so it carries its own status, priority, assignee and
    labels."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import project_tasks_service as tasks

    try:
        tenant_id = await _resolve_tenant(resolved_workspace_id)
        parent = await tasks.get_task(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id, task_id=task_id,
        )
        if parent is None:
            return {"ok": False, "error": "Task not found.", "tasks": []}
        # A sub-task is exactly as visible as the project its parent lives
        # in -- same enforce-when-scoped rule fleet_list_tasks uses, applied
        # to the parent's project since parent and child always share one.
        await auth_module.enforce_project_access(
            current_user, resolved_workspace_id, str(parent.get("project_id") or ""), minimum_role="viewer",
        )
        rows = await tasks.list_subtasks(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id, parent_task_id=task_id,
        )
        return {"ok": True, "tasks": rows, "parent_task_id": task_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tasks": []}


class FleetSetTaskParentRequest(BaseModel):
    # None/"" DETACHES -- promoting a sub-task back to a top-level task. That
    # is the deliberate manual counterpart of what the database does on its
    # own when a parent row is deleted.
    parent_task_id: Optional[str] = None


@router.post("/api/w/{workspace_id}/fleet/tasks/{task_id}/parent")
async def fleet_set_task_parent(
    request: Request,
    workspace_id: str,
    task_id: str,
    body: FleetSetTaskParentRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Make a task a sub-task of another, or detach it back to top-level.

    Its own endpoint rather than a field on the PATCH above, for the same
    reason /assign is: this is a structural change with a validity question
    attached (does it break the one-level rule?), not a plain field edit.

    MAN-64/MAN-70: `member` is enough -- creating/managing sub-tasks is
    ordinary teammate work. Gated on the task's OWN project, same as /patch
    and /assign above; project_tasks_service.set_task_parent separately
    enforces that a proposed PARENT lives in that same project, so this one
    check covers both ends of the relationship."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_task_project_access(current_user, resolved_workspace_id, tenant_id, task_id, minimum_role="member")
    from server_modules import project_tasks_service as tasks

    try:
        task = await tasks.set_task_parent(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            task_id=task_id,
            parent_task_id=body.parent_task_id,
        )
        if task is None:
            return {"ok": False, "error": "Task not found."}
        return {"ok": True, "task": task}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Project documents: a project's owned, flat markdown knowledge -- the
# API half of the "owned-context layer for a team" positioning (CLAUDE.md).
# No RAG endpoint here and none planned for this table: a caller finds a
# document by listing a project's set (GET below) and reading the one it
# wants, the same way project_documents_repository.py's own module
# docstring says an agent should. Documents are project-scoped exactly like
# tasks -- MAN-115's per-project ACL gates every route below via
# _enforce_document_project_access, mirroring _enforce_task_project_access
# above it. See migrations/add_project_documents.sql for the schema.

async def _enforce_document_project_access(
    current_user: Dict[str, Any],
    resolved_workspace_id: str,
    tenant_id: str,
    document_id: str,
    *,
    minimum_role: str = "viewer",
) -> None:
    """The document-scoped counterpart of _enforce_task_project_access just
    above -- same reasoning, same non-raising posture when the document
    can't be resolved (nothing to leak for a document that isn't there; the
    route's own call right after this returns its normal not-found
    result)."""
    from server_modules import project_documents_repository as documents

    document = await documents.get_document(
        tenant_id=tenant_id, workspace_id=resolved_workspace_id, document_id=document_id,
    )
    project_id = str((document or {}).get("project_id") or "").strip()
    if not project_id:
        return
    await auth_module.enforce_project_access(
        current_user, resolved_workspace_id, project_id, minimum_role=minimum_role,
    )


@router.get("/api/w/{workspace_id}/fleet/documents")
async def fleet_list_documents(
    request: Request,
    workspace_id: str,
    project_id: Optional[str] = Query(None, description="Filter to one project; omitted = every project this caller can see"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """List documents, ordered by path -- a repository tree, not a feed.
    `viewer` is enough: reading a project's shared knowledge is the same
    tier that reads its tasks and its member roster.

    TWO MODES, ONE ACL, and it is the SAME enforce-when-scoped /
    filter-when-not shape fleet_list_tasks above already uses -- copied
    deliberately rather than invented, because a second ACL shape on the
    surface that holds a team's accumulated knowledge is exactly the risk
    CLAUDE.md keeps recording ("a filtered item list beside an unfiltered
    summary is still a disclosure"):

      project_id given    -> enforce_project_access on THAT project, so a
                             caller with no membership row gets a 404 and
                             not an empty list that would still confirm the
                             project exists.
      project_id omitted  -> _visible_project_ids decides. None means
                             "workspace owner, every project"; a concrete
                             set is this member's own project_memberships,
                             passed to the repository as the scope. An owner
                             is resolved to their real project id list
                             rather than an unscoped read, so there is no
                             code path here that reads documents without a
                             project scope bound to the query.

    This is what the workspace-level Context view reads. Bodies are omitted
    (see project_documents_repository.list_documents's include_body=False
    default) -- fetch a single document via GET .../documents/{id}."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    from server_modules import project_documents_repository as documents

    scope_project_ids: Optional[List[str]] = None
    if project_id:
        await auth_module.enforce_project_access(
            current_user, resolved_workspace_id, project_id, minimum_role="viewer",
        )
    else:
        visible_ids = await _visible_project_ids(current_user, resolved_workspace_id, tenant_id)
        if visible_ids is None:
            # Owner. Resolve to the real id list rather than reading
            # unscoped -- list_documents has no "everything" mode by
            # design, and giving it one for the owner case would be the
            # loaded gun this codebase already disarmed elsewhere.
            from server_modules import projects_repository as _projects

            scope_project_ids = [
                str(row.get("id") or "").strip()
                for row in (await _projects.list_projects(
                    tenant_id=tenant_id, workspace_id=resolved_workspace_id,
                ) or [])
                if str(row.get("id") or "").strip()
            ]
        else:
            scope_project_ids = sorted(visible_ids)

    try:
        if project_id:
            rows = await documents.list_documents(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                project_id=project_id,
            )
        else:
            rows = await documents.list_documents(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                project_ids=scope_project_ids or [],
            )
        return {"ok": True, "documents": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "documents": []}


@router.get("/api/w/{workspace_id}/fleet/documents/{document_id}")
async def fleet_get_document(
    request: Request,
    workspace_id: str,
    document_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Fetch one document, including its full body. `viewer` -- same tier
    the list route requires."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_document_project_access(
        current_user, resolved_workspace_id, tenant_id, document_id, minimum_role="viewer",
    )
    from server_modules import project_documents_repository as documents

    try:
        document = await documents.get_document(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id, document_id=document_id,
        )
        if document is None:
            return {"ok": False, "error": "Document not found."}
        return {"ok": True, "document": document}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/documents/{document_id}/revisions")
async def fleet_list_document_revisions(
    request: Request,
    workspace_id: str,
    document_id: str,
    limit: int = Query(50, ge=1, le=200, description="Max revisions to return, newest first."),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """List a document's revision history, newest first -- the human-facing
    counterpart of mcp_server.py's empyralis_list_document_revisions, which
    was the ONLY caller of project_documents_repository.list_document_
    revisions until now (CLAUDE.md: "built, tested, and never wired" -- an
    agent editing a project document could already see who changed it and
    when; a human looking at the same document over the same table could
    not). `viewer` -- same tier fleet_get_document requires, and gated on
    the document's own project via _enforce_document_project_access, same
    as that route: reading who touched a document and when is not a more
    privileged act than reading its current body.

    Read-only. There is no restore/rollback route here on purpose --
    CLAUDE.md's "a surface must earn its place," and list_document_
    revisions's own docstring already makes this same call for the
    repository layer it wraps. `include_body` is never set to True: the
    `diff` column (a unified diff against the immediately-prior revision)
    is what a history read is for, and a past revision's full body is not
    exposed over HTTP anywhere today -- consistent with there being no
    restore action that would need it."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_document_project_access(
        current_user, resolved_workspace_id, tenant_id, document_id, minimum_role="viewer",
    )
    from server_modules import project_documents_repository as documents

    try:
        document = await documents.get_document(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id, document_id=document_id,
        )
        if document is None:
            return {"ok": False, "error": "Document not found."}
        revisions = await documents.list_document_revisions(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            document_id=document_id,
            limit=limit,
        )
        return {"ok": True, "revisions": revisions}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/document-activity")
async def fleet_document_activity(
    request: Request,
    workspace_id: str,
    project_id: Optional[str] = Query(None, description="Filter to one project; omitted = every project this caller can see"),
    limit: int = Query(50, ge=1, le=200),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Every document revision in scope, newest first -- the repository's
    commit log to fleet_list_document_revisions' single-file history.

    READ ONLY, AND DELIBERATELY SO. There is no restore/revert here and none
    is planned from this surface: restoring a revision is a WRITE with real
    consequences (it would overwrite whatever is current, which is the very
    silent-loss class MAN-354 was filed for), and a feed that quietly grows
    a destructive control is the shape this codebase keeps getting bitten
    by. The feed answers "who changed what, and when"; changing anything is
    done on the document itself, where the stale-write precondition applies.

    SCOPE: identical enforce-when-scoped / filter-when-not shape as
    fleet_list_documents and fleet_list_tasks -- a named project is checked
    with enforce_project_access, an omitted one resolves to this caller's
    own visible projects. The project ids reaching SQL are always ones this
    request resolved, never a caller-supplied string trusted through."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    from server_modules import project_documents_repository as documents

    try:
        if project_id:
            await auth_module.enforce_project_access(
                current_user, resolved_workspace_id, project_id, minimum_role="viewer",
            )
            rows = await documents.list_project_document_activity(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                project_id=project_id,
                limit=limit,
            )
        else:
            visible_ids = await _visible_project_ids(current_user, resolved_workspace_id, tenant_id)
            if visible_ids is None:
                from server_modules import projects_repository as _projects

                scope = [
                    str(row.get("id") or "").strip()
                    for row in (await _projects.list_projects(
                        tenant_id=tenant_id, workspace_id=resolved_workspace_id,
                    ) or [])
                    if str(row.get("id") or "").strip()
                ]
            else:
                scope = sorted(visible_ids)
            rows = await documents.list_project_document_activity(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                project_ids=scope,
                limit=limit,
            )
        return {"ok": True, "activity": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "activity": []}


class FleetCreateDocumentRequest(BaseModel):
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body: str = ""
    # Where the document lives in the project's tree ("specs/api/auth.md").
    # Optional: omitted, create_document derives it from the title, which is
    # what the "New document" button wants. Folders are inferred from the
    # slashes -- there is nothing to create first, exactly as in git.
    path: Optional[str] = None


@router.post("/api/w/{workspace_id}/fleet/documents")
async def fleet_create_document(
    request: Request,
    workspace_id: str,
    body: FleetCreateDocumentRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Create a document inside a project.

    WHO CAN WRITE: `member` -- project membership is the natural grant for
    this surface, the same boundary project_task__* tools were given
    earlier (a project-member specialist gets those tools unconditionally
    off its own project_id, see sage_agent_runtime_service.py's
    _direct_tool_bundle). A project's documents are shared, ordinary-work
    content its members maintain together -- filing one is not a more
    privileged act than filing a task, so it is gated at the same `member`
    tier fleet_create_task uses, not `owner`. Gated on the NAMED project
    (body.project_id is already in hand), matching fleet_create_task's own
    reasoning: a member cannot create a document in a project they have no
    project_memberships row for."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    await auth_module.enforce_project_access(current_user, resolved_workspace_id, body.project_id, minimum_role="member")
    from server_modules import project_documents_repository as documents

    try:
        document = await documents.create_document(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            project_id=body.project_id,
            title=body.title,
            body=body.body,
            path=body.path,
            created_by=str((current_user or {}).get("user_id") or "").strip() or None,
            # A dashboard session is always a human -- see project_documents_
            # repository's changed_by_type vocabulary (human/agent/external_agent).
            changed_by_type="human",
        )
        return {"ok": True, "document": document}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetPatchDocumentRequest(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    # A move/rename inside the project's tree -- `git mv`. Omitted or empty
    # leaves the document exactly where it is; a title change never moves it
    # on its own (see update_document's docstring). Covered by the same
    # base_sha256 precondition as title and body, so a stale save can no
    # more silently un-move a document than it can revert its text.
    path: Optional[str] = None
    # The stale-write precondition -- the `state_sha256` of the document
    # state this edit was composed on top of (every body-bearing document
    # read carries one; see project_documents_repository.
    # document_state_sha256). Optional on the WIRE and required in spirit:
    # a client that omits it gets the old unconditional-overwrite behaviour,
    # which is what an existing integration or a curl call will do, and the
    # ONE client that matters (the document page's autosave) always sends
    # it. Making it wire-required would 422 those callers rather than
    # protect anyone; the honest posture is that an omitted precondition is
    # an unguarded write and the response says so.
    base_sha256: Optional[str] = None


@router.patch("/api/w/{workspace_id}/fleet/documents/{document_id}")
async def fleet_patch_document(
    request: Request,
    workspace_id: str,
    document_id: str,
    body: FleetPatchDocumentRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Edit a document's title and/or body. `member` -- same tier as
    creation and as fleet_patch_task's own edit route. Gated on the
    document's OWN project via _enforce_document_project_access, mirroring
    fleet_patch_task's _enforce_task_project_access: a member cannot edit a
    document in a project they have no project_memberships row for, just
    by knowing its document_id.

    CONCURRENCY. This route sends the WHOLE title and body from a draft the
    browser snapshotted when the page opened, and the page does not poll --
    so without a precondition every autosave is a blind full overwrite of
    whatever an agent may have written in the meantime, recorded in history
    as if the person had typed the reversion themselves. `base_sha256`
    closes that: the write lands only if the document is still the state the
    person was editing.

    THREE OUTCOMES, THREE RESPONSES -- never one shape with a different
    sentence in it (CLAUDE.md's "failed / couldn't confirm / succeeded are
    three different facts" law):
      200 {"ok": true, "document": ...}     saved
      200 {"ok": false, "error": ...}       ordinary business failure
      409 {"ok": false, "conflict": true,   REFUSED, nothing written, and
           "document": <current state>}     the current state is attached so
                                            the person can be shown what
                                            they would have overwritten
    409 is a real HTTP status rather than another {"ok": false} because the
    client has to BRANCH here: every other failure means "try again", and
    this one means "stop, a human has to choose". A conflict wearing the
    same clothes as a network blip is a conflict the client will retry
    straight over the top of."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_document_project_access(
        current_user, resolved_workspace_id, tenant_id, document_id, minimum_role="member",
    )
    from server_modules import project_documents_repository as documents

    try:
        document = await documents.update_document(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            document_id=document_id,
            expected_sha256=(str(body.base_sha256 or "").strip() or None),
            title=body.title,
            body=body.body,
            path=body.path,
            updated_by=str((current_user or {}).get("user_id") or "").strip() or None,
            changed_by_type="human",
        )
    except documents.DocumentPreconditionFailed as exc:
        # Deliberately raised OUTSIDE the catch-all below, and deliberately
        # not folded into it: str(exc) inside a generic {"ok": false,
        # "error": ...} would reach the browser as an indistinguishable
        # error string, and the client would show "couldn't save" over a
        # document that is fine and a change that is safely still on screen.
        raise HTTPException(
            status_code=409,
            detail={
                # A STABLE CODE, not the generic 409 "conflict" the error
                # shaper would otherwise derive, and never the prose: this
                # codebase already lost five weeks to a bucket that matched
                # on a sentence somebody later reworded. The client branches
                # on `error.code === "document_conflict"`.
                "code": "document_conflict",
                "message": str(exc),
                "conflict": True,
                # The current server state, body included, so the person can
                # be SHOWN the version they would have overwritten. A
                # conflict message with no way to see the other side is a
                # dead end, not a choice. Reaches the client under
                # `error.details.document` (error_response_service.
                # _http_error_details keeps every key except code/message).
                "document": exc.current_document,
            },
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if document is None:
        return {"ok": False, "error": "Document not found."}
    return {"ok": True, "document": document}


@router.delete("/api/w/{workspace_id}/fleet/documents/{document_id}")
async def fleet_delete_document(
    request: Request,
    workspace_id: str,
    document_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Delete a document. `member`, not `owner` -- unlike fleet_delete_label
    (a WORKSPACE-wide vocabulary change every project's board depends on, so
    owner-gated) a document is scoped to one project and any member of that
    project already has full read/write on it; letting the same tier delete
    it is consistent rather than a surprise step up in privilege partway
    through the CRUD set. Gated on the document's own project, same as
    patch above."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_document_project_access(
        current_user, resolved_workspace_id, tenant_id, document_id, minimum_role="member",
    )
    from server_modules import project_documents_repository as documents

    try:
        deleted = await documents.delete_document(
            tenant_id=tenant_id, workspace_id=resolved_workspace_id, document_id=document_id,
        )
        return {"ok": bool(deleted)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Labels: a per-WORKSPACE vocabulary, not per-project -- a label like
# "bug" describes a KIND of work and stays the same label when the work
# moves to another client. `color` is a palette TOKEN NAME, never a hex: the
# theme owns what each token looks like in light and dark mode, exactly as
# it already does for task statuses. Name uniqueness is per workspace and
# case-insensitive, enforced by a functional UNIQUE index. Reads are viewer,
# mutations are owner, matching every other route in this router. See
# migrations/add_task_labels.sql. ─────────────────────────────────────────

# ── The workspace roster: every agent that can appear as an actor ─────────
# An EXTERNAL agent (a Claude Code / Codex session connected through our MCP
# server at /mcp) writes tasks and comments under an opaque
# `ext_agent_<hex16>` id. That id resolves against neither
# `workspace_agent_installs` (GET .../fleet/agents) nor the member list, so
# until this route existed the board had no way to name it and printed
# "Unknown" where a real participant had acted.
#
# mcp_external_agent_roster_service.list_unified_roster is the module's own
# documented "ONE roster view" for exactly this -- platform and external in
# one list, each tagged `kind` -- so this is its read route, not a new
# parallel listing. It returns BOTH kinds even though the frontend already
# has the platform half from .../fleet/agents: an attribution lookup that
# silently covers only one of the two kinds of agent is the bug this route
# is fixing, and the platform half costs one query the roster already makes.
#
# `viewer`, matching .../fleet/agents and .../fleet/tasks: anyone who can
# read the board that shows these names can read the names. Scoping is the
# same two-step every route in this router uses -- enforce_workspace_access
# returns the workspace the CALLER is actually allowed into, and only that
# resolved id (never the raw path parameter) is passed downstream, so a
# request naming someone else's workspace is rejected before the query
# rather than filtered after it.
@router.get("/api/w/{workspace_id}/fleet/roster")
async def fleet_roster(
    request: Request,
    workspace_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Every addressable agent in this workspace, platform and external,
    each tagged `kind` ("platform" | "external").

    Revoked external agents ARE included, tagged `enabled: false` /
    `status: "revoked"`. They are unreachable, but the work they already did
    is still on the board and naming its author is the entire point of this
    route -- see list_unified_roster's own docstring."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import mcp_external_agent_roster_service as roster

    try:
        entries = await roster.list_unified_roster(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            include_revoked=True,
        )
        return {"ok": True, "workspace_id": resolved_workspace_id, "roster": entries}
    except Exception as exc:
        # A roster that cannot be read must degrade to "no names resolved",
        # never to a failed board load -- the caller renders an honest
        # fallback label, which is strictly better than an error page.
        return {"ok": False, "error": str(exc), "roster": []}


@router.get("/api/w/{workspace_id}/fleet/labels")
async def fleet_list_labels(
    request: Request,
    workspace_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Every label in the workspace, each with how many tasks carry it. Not
    project-filtered: the vocabulary is workspace-wide by design, and a
    picker that hid labels because no task in THIS project uses them yet
    would make the first use of a label impossible."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import workspace_labels_service as labels

    try:
        rows = await labels.list_labels(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
        )
        return {"ok": True, "labels": rows, "colors": list(labels.LABEL_COLOR_ORDER)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "labels": []}


class FleetCreateLabelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    # A palette token name ('grey', 'red', ... see
    # workspace_labels_service.LABEL_COLOR_ORDER), NOT a hex code. Validated
    # in the service so this endpoint and the agent surfaces reject the same
    # set with the same message.
    color: Optional[str] = None


@router.post("/api/w/{workspace_id}/fleet/labels")
async def fleet_create_label(
    request: Request,
    workspace_id: str,
    body: FleetCreateLabelRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Add a label to the workspace vocabulary. A name that already exists
    (compared case-insensitively) is rejected with a message naming the
    existing label, never silently merged.

    MAN-64/MAN-70: `member` is enough -- minting a new label is low-risk,
    purely additive (nothing existing changes), and is part of "manage
    labels on tasks" actually being usable: a member who can attach labels
    to a task but can never mint a new one has a crippled version of that
    ability. No per-project check needed -- labels are workspace-wide by
    design (see the section header above), so there is no project boundary
    to enforce here the way there is on a task-scoped route."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    from server_modules import workspace_labels_service as labels

    try:
        label = await labels.create_label(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            name=body.name,
            color=body.color,
            created_by=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {"ok": True, "label": label}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetPatchLabelRequest(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None


@router.patch("/api/w/{workspace_id}/fleet/labels/{label_id}")
async def fleet_patch_label(
    request: Request,
    workspace_id: str,
    label_id: str,
    body: FleetPatchLabelRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Rename and/or recolour a label. One row changes and every task
    carrying it updates at once -- the property that made this a join table
    rather than an array on the task.

    MAN-64/MAN-70: `member` is enough -- a rename/recolour is a non-
    destructive metadata edit (nothing is removed, no attachment is lost),
    the same risk class as editing a task's own title. No per-project check
    -- see fleet_create_label just above; the vocabulary is workspace-wide."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    from server_modules import workspace_labels_service as labels

    try:
        label = await labels.update_label(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            label_id=label_id,
            name=body.name,
            color=body.color,
        )
        if label is None:
            return {"ok": False, "error": "Label not found."}
        return {"ok": True, "label": label}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/labels/{label_id}")
async def fleet_delete_label(
    request: Request,
    workspace_id: str,
    label_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Remove a label from the workspace vocabulary. Its attachments go with
    it -- no task is deleted and no task field changes, the chip simply
    stops being on the card.

    MAN-64/MAN-70 permission review: LEFT AT `owner`, DELIBERATELY, flagged
    rather than loosened. Labels are workspace-wide (see the section header
    above) but tasks are project-scoped under MAN-115's per-project ACL --
    a member only has project_memberships rows for the projects they can
    see. Deleting a label silently strips it off every task carrying it
    ACROSS THE WHOLE WORKSPACE in one call, including tasks in projects that
    member has no visibility into and no _enforce_task_project_access check
    could gate (there is no single task_id here to resolve a project from --
    the blast radius spans however many projects happen to use this label).
    That is a real cross-project side effect a member cannot see the extent
    of before triggering it, which reads closer to this rollout's
    "anything destructive" owner-only bucket than to routine task upkeep.
    create/patch (immediately above) do not have this problem -- creating is
    purely additive and renaming/recolouring loses no data -- which is why
    only delete stays owner-gated here."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import workspace_labels_service as labels

    try:
        removed = await labels.delete_label(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            label_id=label_id,
        )
        if not removed:
            return {"ok": False, "error": "Label not found."}
        return {"ok": True, "removed": True, "label_id": label_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class FleetAttachLabelRequest(BaseModel):
    # A label id OR a label name (resolved case-insensitively) -- the same
    # forgiving input the agent tools take, so a client that has the name
    # in hand does not need a lookup round trip first.
    label: str = Field(min_length=1)


@router.post("/api/w/{workspace_id}/fleet/tasks/{task_id}/labels")
async def fleet_attach_task_label(
    request: Request,
    workspace_id: str,
    task_id: str,
    body: FleetAttachLabelRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Put a label on a task. Idempotent -- re-attaching an existing label
    succeeds and changes nothing. Does NOT create unknown labels: the
    vocabulary is curated, and an attach that mints a label on a typo turns
    it into a junk drawer.

    MAN-64/MAN-70: `member` is enough -- "manage labels on tasks" is one of
    the explicit teammate abilities this rollout exists to grant. Gated on
    the task's OWN project (unlike the label-vocabulary routes above, this
    one touches a specific task, so the MAN-115 project boundary applies
    here exactly as it does on /patch, /assign, and /parent)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_task_project_access(current_user, resolved_workspace_id, tenant_id, task_id, minimum_role="member")
    from server_modules import workspace_labels_service as labels

    try:
        attached = await labels.attach_label(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            task_id=task_id,
            label=body.label,
            added_by=str((current_user or {}).get("user_id") or "").strip() or None,
        )
        return {"ok": True, "task_id": task_id, "labels": attached}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/tasks/{task_id}/labels/{label}")
async def fleet_detach_task_label(
    request: Request,
    workspace_id: str,
    task_id: str,
    label: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Take a label off a task. Idempotent, and the label itself survives --
    this removes the link, never the vocabulary entry.

    MAN-64/MAN-70: `member` is enough, same reasoning and same project gate
    as /labels POST just above -- this is the other half of "manage labels
    on tasks," scoped to one task in one project, not the shared vocabulary."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="member")
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_task_project_access(current_user, resolved_workspace_id, tenant_id, task_id, minimum_role="member")
    from server_modules import workspace_labels_service as labels

    try:
        remaining = await labels.detach_label(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            task_id=task_id,
            label=label,
        )
        return {"ok": True, "task_id": task_id, "labels": remaining}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Bug reports (MAN-106): the small "report an issue" entry point in the
# fleet rail (BugReportButton.tsx). Any workspace member can file one
# (viewer minimum -- reporting a bug you ran into isn't a privileged action);
# reading the list back is owner-gated, matching every other read of
# workspace-wide operational data in this router. ──────────────────────────

class FleetCreateBugReportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    page_path: str = ""


@router.post("/api/w/{workspace_id}/fleet/bug-reports")
async def fleet_create_bug_report(
    request: Request,
    workspace_id: str,
    body: FleetCreateBugReportRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Persist a bug report -- see bug_report_service.py. Deliberately never
    raises an HTTPException for a business-logic failure, matching every
    other mutation in this router: the dialog that submits this always gets
    a JSON body back to render, success or failure."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    from server_modules import bug_report_service

    try:
        report = await bug_report_service.create_report(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            title=body.title,
            description=body.description,
            reported_by_user_id=str((current_user or {}).get("user_id") or "").strip() or None,
            page_path=body.page_path,
            user_agent=request.headers.get("user-agent", ""),
        )
        return {"ok": True, "report": report}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/w/{workspace_id}/fleet/bug-reports")
async def fleet_list_bug_reports(
    request: Request,
    workspace_id: str,
    limit: int = Query(100, ge=1, le=500),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Read back submitted bug reports -- no dedicated ops UI ships with
    this yet; this is the durable, queryable surface an operator (or a
    future admin view) reads from."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import bug_report_service

    try:
        reports = await bug_report_service.list_reports(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
            limit=limit,
        )
        return {"ok": True, "reports": reports}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "reports": []}


@router.get("/api/w/{workspace_id}/fleet/agents/suggested-name")
async def fleet_suggested_agent_name_route(
    request: Request,
    workspace_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """A ready-to-use agent name for the create-agent wizard's Placement
    step's Name field, so it's never blank before the owner has typed
    anything. Same pool + dedup logic fleet_create_agent falls back to when
    no name is given (fleet_tools.suggest_agent_name) — this route lets the
    wizard SHOW that name before commit, rather than duplicating the pool
    client-side."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import suggest_agent_name

    try:
        name = await suggest_agent_name(
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            workspace_id=resolved_workspace_id,
        )
        return {"ok": True, "name": name}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "name": ""}


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


# ── Owner-only RECURRING schedule control ─────────────────────────────────
# "Every morning at 9am," not a single wake-up -- see bounded_scheduler_
# service.create_recurring_schedule/fleet_tools.fleet_*_agent_recurring_
# schedule for the underlying design. Same owner-gating and same split from
# the agent-callable fleet__schedule_recurring_task tool as the one-shot
# schedule routes just above.


class FleetCreateRecurringScheduleRequest(BaseModel):
    cron: str = ""
    instruction: str = ""
    max_occurrences: Optional[int] = None
    expires_at: Optional[str] = None


@router.get("/api/w/{workspace_id}/fleet/agents/{agent_id}/recurring-schedule")
async def fleet_list_agent_recurring_schedule_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only list of this agent's active recurring schedules."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_list_agent_recurring_schedules

    try:
        return await fleet_list_agent_recurring_schedules(
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "schedules": []}


@router.post("/api/w/{workspace_id}/fleet/agents/{agent_id}/recurring-schedule")
async def fleet_create_agent_recurring_schedule_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    body: FleetCreateRecurringScheduleRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only: create a recurring wake schedule for this agent. Always
    executes at owner tier — see fleet_tools.fleet_create_agent_recurring_schedule."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_create_agent_recurring_schedule

    try:
        return await fleet_create_agent_recurring_schedule(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            actor_label=_actor_label(current_user),
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
            cron=body.cron,
            instruction=body.instruction,
            max_occurrences=body.max_occurrences,
            expires_at=body.expires_at,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.delete("/api/w/{workspace_id}/fleet/agents/{agent_id}/recurring-schedule/{schedule_id}")
async def fleet_cancel_agent_recurring_schedule_route(
    request: Request,
    workspace_id: str,
    agent_id: str,
    schedule_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Owner-only cancel of one of this agent's recurring schedules."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules.fleet_tools import fleet_cancel_agent_recurring_schedule

    try:
        return await fleet_cancel_agent_recurring_schedule(
            actor_id=str((current_user or {}).get("user_id") or "").strip() or "owner",
            actor_label=_actor_label(current_user),
            workspace_id=resolved_workspace_id,
            tenant_id=await _resolve_tenant(resolved_workspace_id),
            agent_id=agent_id,
            schedule_id=schedule_id,
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
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_agent_project_access(current_user, resolved_workspace_id, tenant_id, agent_id, minimum_role="viewer")
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
    await auth_module.enforce_project_access(current_user, resolved_workspace_id, project_id, minimum_role="viewer")
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
    tenant_id = await _resolve_tenant(resolved_workspace_id)
    await _enforce_agent_project_access(current_user, resolved_workspace_id, tenant_id, agent_id, minimum_role="viewer")
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
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
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
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
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
    # Was missing on both mutations while the GETs beside them carried it --
    # a no-op for a workspace owner today (an owner already clears every
    # project ACL in their own workspace), but the asymmetry is exactly the
    # kind of latent gap CLAUDE.md records for _enforce_agent_project_access
    # itself: the READ was gated and the WRITE was not.
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="owner")
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
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="owner")
    from server_modules import agent_memory_tree_service as tree

    try:
        ns = await _resolve_memory_namespace(resolved_workspace_id, agent_id)
        return {"ok": True, "deleted": tree.delete_file(resolved_workspace_id, path, agent_install_id=ns), "path": path}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── What the agent WROTE ITSELF: facts + this person's private note ──────────
#
# The memory tree above (MEMORY.md + topic files) was the only memory a
# person could ever SEE. Two other stores were live and completely invisible
# from any screen:
#
#   memory_entries (SQLite, server-side, per workspace+install)
#       the agent's own key/value facts -- what `memory_write` writes, and
#       what direct-chat fact extraction writes when it runs. Carries the
#       attribution columns agent_memory._row_to_entry already returns
#       (source_platform/surface/sender_*, derive_trust_tier), so a fact
#       learned from a stranger on a channel is distinguishable from one the
#       owner stated directly. That distinction existed in the database and
#       reached nobody.
#
#   agent_private_memory_notes (Postgres, per workspace+install+USER)
#       the per-person half of the shared-vs-private split. Reachable ONLY
#       through the model's memory_get_private tool until now -- a person
#       could not read, correct, or even confirm the existence of the note
#       their own agent keeps about them.
#
# The private-note routes take NO user_id parameter, deliberately. The only
# source is the authenticated session (`current_user["user_id"]`), exactly
# as skills_service's tool dispatch resolves it from session_metadata --
# CLAUDE.md's "the FIRING CODE decides which layer a write lands in, never a
# model-supplied (or here, caller-supplied) flag". Adding a user_id query
# parameter would turn a workspace owner into a reader of every teammate's
# private note with one URL edit, which is the exact boundary
# agent_private_memory_repository's four-column WHERE clause exists to make
# structurally impossible.


@router.get("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/facts")
async def fleet_agent_memory_facts(
    request: Request, workspace_id: str, agent_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """The agent's own key/value facts (shared pool -- every project member
    sees the same list, same as the memory tree)."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
    from server_modules import memory_service

    try:
        ns = await _resolve_memory_namespace(resolved_workspace_id, agent_id)
        entries = memory_service.list_memory_entries(resolved_workspace_id, agent_install_id=ns)
        return {
            "ok": True,
            "agent_id": agent_id,
            "scope": "workspace" if ns is None else "install",
            "facts": entries,
            "count": len(entries),
        }
    except Exception as exc:
        # "no facts" and "could not read the facts" are different facts and
        # must not share one screen (CLAUDE.md's outcome-honesty law). The
        # caller distinguishes them on `ok`, never on an empty list.
        return {"ok": False, "error": str(exc), "facts": [], "count": 0}


@router.delete("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/facts")
async def fleet_agent_memory_fact_delete(
    request: Request, workspace_id: str, agent_id: str,
    key: str = Query(..., description="The fact's storage key, as returned by the facts listing"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="owner")
    from server_modules import memory_service

    try:
        ns = await _resolve_memory_namespace(resolved_workspace_id, agent_id)
        # `deleted` is the row count, not the request outcome: a key that was
        # already gone returns ok=True/deleted=False rather than an error.
        deleted = memory_service.delete_memory(resolved_workspace_id, key, agent_install_id=ns)
        return {"ok": True, "deleted": bool(deleted), "key": key}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "deleted": False, "key": key}


@router.get("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/daily")
async def fleet_agent_memory_daily(
    request: Request, workspace_id: str, agent_id: str,
    days: int = Query(14, ge=1, le=30, description="How many days back to read"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """The rolling day-log this agent's own turns append to.

    THIS IS THE FEEDBACK LOOP, not a log viewer: the same text is injected
    back into the next turn's prompt as the "Recent Daily Logs" section
    (workspace_context_memory_adapter.load_workspace_context_payload), so
    what shows here is literally what the agent will read about itself
    tomorrow. It is a THIRD store, separate from both the memory tree and
    the facts table, and it had no reader outside the prompt assembler --
    a person could not see the one part of memory that grows on its own.

    Note for anyone extending this: `memory_service.get_recent_logs` reads
    `.orion-stack/memory/<workspace>/<date>.md`, which is NOT the same file
    set as `memory_append_daily_note`'s `<context dir>/memory/<date>.md`.
    Two daily-note stores exist; `consolidate_daily_memory_notes` reads only
    the second. See CLAUDE.md's memory-storage entry."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
    from server_modules import memory_service

    try:
        ns = await _resolve_memory_namespace(resolved_workspace_id, agent_id)
        text = memory_service.get_recent_logs(resolved_workspace_id, days=days, agent_install_id=ns)
        return {"ok": True, "days": days, "content": text or "", "has_content": bool(str(text or "").strip())}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "content": "", "has_content": False}


class FleetPrivateMemoryNoteWriteRequest(BaseModel):
    content: str = ""


@router.get("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/private-note")
async def fleet_agent_private_memory_note_read(
    request: Request, workspace_id: str, agent_id: str,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """THIS caller's own private note for this agent. Never anyone else's --
    see the block comment above."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
    from server_modules import agent_private_memory_service as private_memory

    user_id = str((current_user or {}).get("user_id") or "").strip()
    if not user_id:
        return {"ok": False, "error": "No resolved user identity for this session.", "note": None}
    try:
        note = await private_memory.aget_private_memory_note(
            resolved_workspace_id,
            agent_install_id=private_memory.resolve_agent_install_scope(agent_id),
            user_id=user_id,
        )
        # `note: None` with ok=True is the honest "you have not written one",
        # distinct from ok=False ("this could not be read") -- including the
        # no-Postgres-pool case, which the service also surfaces as None. The
        # `supported` flag says which of those two Nones this is.
        return {"ok": True, "note": note, "max_chars": private_memory.PRIVATE_MEMORY_NOTE_MAX_CHARS}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "note": None}


@router.put("/api/w/{workspace_id}/fleet/agents/{agent_id}/memory/private-note")
async def fleet_agent_private_memory_note_write(
    request: Request, workspace_id: str, agent_id: str,
    body: FleetPrivateMemoryNoteWriteRequest,
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Write THIS caller's own private note. `viewer` is the right floor: a
    person's private note is theirs, and gating it on `owner` would mean a
    teammate could be shown a note about themselves they are not allowed to
    correct."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="viewer")
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
    from server_modules import agent_private_memory_service as private_memory

    user_id = str((current_user or {}).get("user_id") or "").strip()
    if not user_id:
        return {"ok": False, "error": "No resolved user identity for this session."}
    try:
        saved = await private_memory.awrite_private_memory_note(
            resolved_workspace_id,
            agent_install_id=private_memory.resolve_agent_install_scope(agent_id),
            user_id=user_id,
            content=body.content or "",
            reason="owner_ui_edit",
        )
        return {"ok": True, **(saved or {})}
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
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
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
                # status_items() already computes all three; they were simply
                # not forwarded, so the Channels grid could say a channel was
                # unavailable but never WHY, and a channel that had genuinely
                # broken (Discord's live-socket check, an OAuth app the
                # deployment never configured, a local bridge reporting an
                # error) was indistinguishable from one merely not set up yet.
                # health_status/last_error are the only place that reason
                # exists; display_state is the backend's own already-resolved
                # verdict, forwarded so the client cannot invent a fifth one.
                "healthStatus": item.get("health_status") or "",
                "displayState": item.get("display_state") or "",
                "lastError": item.get("last_error") or None,
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
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
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
    await auth_module.enforce_project_access(current_user, resolved_workspace_id, project_id, minimum_role="viewer")
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

    # MAN-206: agent_install_id is caller-supplied and must be confirmed to
    # belong to THIS (tenant_id, workspace_id) before anything is written --
    # see agent_bindings_repository.agent_install_in_scope for why RLS alone
    # does not catch a cross-tenant id here (a fresh INSERT's WITH CHECK only
    # verifies the new row's OWN tenant_id/workspace_id match the caller's
    # session scope, never that agent_install_id itself belongs to that
    # scope). Every sibling channel-bind route (Telegram/Discord/WeChat/SMS)
    # already has this guard; this route was the one gap, confirmed live: a
    # caller who knows another tenant's agent_install_id could plant a Slack
    # binding against it AND permanently block that tenant's own legitimate
    # bind (the ON CONFLICT UPDATE afterwards hits a row RLS makes invisible
    # to them, raising rather than silently updating).
    if not await bindings.agent_install_in_scope(
        agent_id, tenant_id=tenant_id, workspace_id=resolved_workspace_id,
    ):
        return {"ok": False, "error": "This agent does not belong to your workspace."}

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
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
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
    await _enforce_agent_project_access(current_user, resolved_workspace_id, await _resolve_tenant(resolved_workspace_id), agent_id, minimum_role="viewer")
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
