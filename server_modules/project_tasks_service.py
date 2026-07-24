"""Tasks -> Agents backend foundation (docs/design/tasks-to-agents-research.md
Section 4.6, steps 1-3): a first-class task object living inside a Project
that can be assigned to an agent, which then wakes up and works it.

Follows the same direct-pool access pattern as projects_repository.py: plain
pool.fetch/fetchrow/execute with explicit tenant_id/workspace_id WHERE
filters -- `project_tasks` carries no RLS policy, exactly like `projects`
itself (see migrations/add_project_tasks.sql). Postgres-first -- when
Postgres is unavailable these functions return empty/None rather than
falling back to SQLite (tasks are a control-plane concept, same era as
Projects).

`assign_task` is the ONE code path for setting a task's assignee, used by
both the HTTP API (routes_fleet.py) and, later, an @-mention resolver --
per the research doc's pitfall #2, assignment and mention must never fork
into two different code paths with different ownership semantics.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

VALID_TASK_STATUSES = {"open", "in_progress", "blocked", "awaiting_input", "done"}
DEFAULT_TASK_STATUS = "open"


def _new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:16]}"


def _coerce_metadata(value: Any) -> Dict[str, Any]:
    """Postgres JSONB sometimes arrives already-decoded (dict) and sometimes
    as a raw JSON string, depending on the pool's codec setup -- see
    projects_repository._coerce_metadata for the same footgun."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _coerce_plan(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [dict(item) for item in parsed if isinstance(item, dict)]
    return []


def _coerce_due_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _normalize_status(value: Any, *, default: str = DEFAULT_TASK_STATUS) -> str:
    token = re.sub(r"[^a-z_]+", "", str(value or "").strip().lower())
    return token if token in VALID_TASK_STATUSES else default


def _row_to_task(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    due_at = r.get("due_at")
    return {
        "id": str(r.get("id") or "").strip(),
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "project_id": str(r.get("project_id") or "").strip() or None,
        "title": str(r.get("title") or "").strip(),
        "description": str(r.get("description") or "").strip(),
        "status": _normalize_status(r.get("status")),
        "assignee_agent_id": str(r.get("assignee_agent_id") or "").strip() or None,
        "created_by": str(r.get("created_by") or "").strip() or None,
        "due_at": str(due_at) if due_at else None,
        "plan": _coerce_plan(r.get("plan")),
        "metadata": _coerce_metadata(r.get("metadata")),
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }


async def create_task(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    title: str,
    description: str = "",
    created_by: Optional[str] = None,
    due_at: Optional[Any] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    tenant_id = str(tenant_id or "").strip()
    workspace_id = str(workspace_id or "").strip()
    project_id = str(project_id or "").strip()
    title = str(title or "").strip()
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required to create a task.")
    if not project_id:
        raise ValueError("project_id is required to create a task.")
    if not title:
        raise ValueError("Task title is required.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to create a task."
        )
    tid = str(task_id or "").strip() or _new_task_id()
    row = await pool.fetchrow(
        """
        INSERT INTO project_tasks (id, tenant_id, workspace_id, project_id, title, description, created_by, due_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::timestamptz)
        RETURNING id, tenant_id, workspace_id, project_id, title, description, status,
                  assignee_agent_id, created_by, due_at, plan, metadata, created_at, updated_at
        """,
        tid,
        tenant_id,
        workspace_id,
        project_id,
        title,
        str(description or "").strip(),
        str(created_by or "").strip() or None,
        _coerce_due_at(due_at),
    )
    return _row_to_task(row)


async def get_task(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        """
        SELECT id, tenant_id, workspace_id, project_id, title, description, status,
               assignee_agent_id, created_by, due_at, plan, metadata, created_at, updated_at
        FROM project_tasks
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(task_id or "").strip(),
    )
    return _row_to_task(row)


async def list_tasks(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: Optional[str] = None,
    assignee_agent_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    params: List[Any] = [str(tenant_id or "").strip(), str(workspace_id or "").strip()]
    clauses = ["tenant_id = $1", "workspace_id = $2"]
    if project_id:
        params.append(str(project_id).strip())
        clauses.append(f"project_id = ${len(params)}")
    if assignee_agent_id:
        params.append(str(assignee_agent_id).strip())
        clauses.append(f"assignee_agent_id = ${len(params)}")
    if status:
        params.append(_normalize_status(status))
        clauses.append(f"status = ${len(params)}")
    query = f"""
        SELECT id, tenant_id, workspace_id, project_id, title, description, status,
               assignee_agent_id, created_by, due_at, plan, metadata, created_at, updated_at
        FROM project_tasks
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC
    """
    rows = await pool.fetch(query, *params)
    return [t for t in (_row_to_task(r) for r in rows) if t]


async def list_my_tasks(
    *,
    tenant_id: str,
    workspace_id: str,
    external_agent_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The MCP `empyralis_list_my_tasks` tool's backing query: tasks assigned
    to the caller (an external agent's roster id, or a platform agent's
    install id) OR unassigned (backlog) tasks -- still visible/actionable
    work the caller can pick up.

    IMPORTANT scoping note: `assignee_agent_id` is FK'd to
    `workspace_agent_installs` only today (see
    migrations/add_project_tasks.sql) -- external agents cannot yet BE the
    assignee of a task; that needs the @-mention resolver's schema change,
    explicitly deferred to the next wave (docs/design/tasks-to-agents-
    research.md Section 4.6 step 4). `external_agent_id` is accepted and
    matched here anyway so this function already returns the right rows the
    moment that lands, with zero changes to this query -- until then it
    simply never matches anything (no task can carry an external id yet) and
    the caller falls back to seeing unassigned/backlog tasks only, which is
    honest: today an external agent genuinely has no assigned tasks.
    """
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    caller_id = str(external_agent_id or agent_id or "").strip()
    params: List[Any] = [str(tenant_id or "").strip(), str(workspace_id or "").strip()]
    clauses = ["tenant_id = $1", "workspace_id = $2"]
    if caller_id:
        params.append(caller_id)
        mine_clause = f"assignee_agent_id = ${len(params)}"
    else:
        mine_clause = "FALSE"
    clauses.append(f"({mine_clause} OR assignee_agent_id IS NULL)")
    if project_id:
        params.append(str(project_id).strip())
        clauses.append(f"project_id = ${len(params)}")
    if status:
        params.append(_normalize_status(status))
        clauses.append(f"status = ${len(params)}")
    query = f"""
        SELECT id, tenant_id, workspace_id, project_id, title, description, status,
               assignee_agent_id, created_by, due_at, plan, metadata, created_at, updated_at
        FROM project_tasks
        WHERE {' AND '.join(clauses)}
        ORDER BY (assignee_agent_id IS NOT NULL) DESC, created_at DESC
    """
    rows = await pool.fetch(query, *params)
    return [t for t in (_row_to_task(r) for r in rows) if t]


async def add_task_comment(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
    author_type: str,
    author_id: str,
    body: str,
) -> Optional[Dict[str, Any]]:
    """Append a comment to `task.metadata.comments` -- the MCP
    `empyralis_comment_on_task` tool's backing write.

    Deliberately NOT a new `task_comments` table: comments are a small,
    append-mostly, read-mostly log, exactly the "metadata JSONB: free-form
    extensibility" seam every sibling table already carries (see
    migrations/add_project_tasks.sql's own rationale for that column). A
    first-class threaded comment table + UI feed is real future work
    (docs/design/tasks-to-agents-research.md Section 4.5) -- this unblocks
    the pull -> work -> report-back loop today without it.

    The append is one atomic UPDATE (jsonb_set + `||` computed server-side in
    a single statement), not a read-modify-write in application code, so two
    concurrent commenters (the owner dashboard and an agent, or two agents)
    can never clobber each other's comment under a race.
    """
    body_text = str(body or "").strip()
    if not body_text:
        raise ValueError("Comment body is required.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to comment on a task."
        )
    comment = {
        "id": f"comment_{uuid.uuid4().hex[:12]}",
        "author_type": str(author_type or "").strip() or "unknown",
        "author_id": str(author_id or "").strip() or "unknown",
        "body": body_text[:4000],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row = await pool.fetchrow(
        """
        UPDATE project_tasks
        SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{comments}',
                COALESCE(metadata->'comments', '[]'::jsonb) || $4::jsonb,
                true
            ),
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id, tenant_id, workspace_id, project_id, title, description, status,
                  assignee_agent_id, created_by, due_at, plan, metadata, created_at, updated_at
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(task_id or "").strip(),
        json.dumps([comment]),
    )
    return _row_to_task(row)


async def update_task(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    due_at: Optional[Any] = None,
    clear_due_at: bool = False,
) -> Optional[Dict[str, Any]]:
    """Generic patch -- title/description/status/due_at only. Assignment has
    its own dedicated entry point (assign_task, below) since it has side
    effects (the task_assigned wakeup) a plain field patch must not trigger
    implicitly."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    if status is not None and _normalize_status(status, default="") == "":
        raise ValueError(f"Invalid task status '{status}'. Must be one of {sorted(VALID_TASK_STATUSES)}.")
    resolved_due_at = None if clear_due_at else _coerce_due_at(due_at)
    row = await pool.fetchrow(
        """
        UPDATE project_tasks
        SET title = COALESCE(NULLIF($4, ''), title),
            description = COALESCE($5, description),
            status = COALESCE($6, status),
            due_at = CASE WHEN $7 THEN NULL WHEN $8::timestamptz IS NOT NULL THEN $8::timestamptz ELSE due_at END,
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id, tenant_id, workspace_id, project_id, title, description, status,
                  assignee_agent_id, created_by, due_at, plan, metadata, created_at, updated_at
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(task_id or "").strip(),
        str(title or "").strip(),
        None if description is None else str(description).strip(),
        _normalize_status(status, default="") or None if status is not None else None,
        bool(clear_due_at),
        resolved_due_at,
    )
    return _row_to_task(row)


async def get_task_plan(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
) -> List[Dict[str, Any]]:
    """Section 4.4's seed side: the durable plan a wakeup turn should restore
    current_plan from at turn start. Returns [] (never None) so a caller can
    always assign it straight into current_plan without a None-check."""
    task = await get_task(tenant_id=tenant_id, workspace_id=workspace_id, task_id=task_id)
    return list(task.get("plan") or []) if task else []


async def set_task_plan(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
    plan: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Section 4.4's persist side: called at turn end (direct_chat_generation_
    service._persist_assigned_task_plan) so update_plan's current_plan
    survives the wakeup -> turn -> wakeup gap instead of dying with the
    turn, same full-replace semantics update_plan itself already uses."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    normalized_plan = [dict(item) for item in (plan or []) if isinstance(item, dict)]
    row = await pool.fetchrow(
        """
        UPDATE project_tasks
        SET plan = $4::jsonb,
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id, tenant_id, workspace_id, project_id, title, description, status,
                  assignee_agent_id, created_by, due_at, plan, metadata, created_at, updated_at
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(task_id or "").strip(),
        json.dumps(normalized_plan),
    )
    return _row_to_task(row)


async def _agent_install_exists(
    *, tenant_id: str, workspace_id: str, agent_id: str,
) -> bool:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    row = await pool.fetchrow(
        "SELECT id FROM workspace_agent_installs WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3",
        str(agent_id or "").strip(),
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
    )
    return row is not None


async def assign_task(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
    agent_id: str,
    triggered_by: str = "owner",
) -> Dict[str, Any]:
    """THE single code path for setting a task's assignee -- called by the
    HTTP API today and, per the research doc's pitfall #2, must be the SAME
    function a future @-mention resolver calls. Ownership semantics stay
    identical regardless of trigger: this only ever sets assignee_agent_id
    and fires the task_assigned wakeup; it never changes created_by (the
    human stays the accountable owner of record, matching Linear's "the
    human teammate remains the primary assignee and owner" rule cited in
    the research doc Section 1.1/4.2).

    Raises ValueError if the task or the agent don't exist in this
    workspace. Best-effort on the wakeup: a scheduler failure is reported in
    the return payload rather than raised, since the assignment itself
    (the durable, addressable fact the owner cares about) already
    succeeded by that point."""
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_task_id = str(task_id or "").strip()
    resolved_agent_id = str(agent_id or "").strip()
    if not resolved_agent_id:
        raise ValueError("agent_id is required to assign a task.")
    task = await get_task(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, task_id=resolved_task_id)
    if task is None:
        raise ValueError(f"Task {resolved_task_id} not found in this workspace.")
    if not await _agent_install_exists(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, agent_id=resolved_agent_id):
        raise ValueError(f"Agent {resolved_agent_id} not found in this workspace.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to assign a task."
        )
    row = await pool.fetchrow(
        """
        UPDATE project_tasks
        SET assignee_agent_id = $4,
            status = CASE WHEN status = 'open' THEN 'in_progress' ELSE status END,
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id, tenant_id, workspace_id, project_id, title, description, status,
                  assignee_agent_id, created_by, due_at, plan, metadata, created_at, updated_at
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_task_id,
        resolved_agent_id,
    )
    updated = _row_to_task(row)
    if updated is None:
        raise ValueError(f"Task {resolved_task_id} not found in this workspace.")
    wake_request = None
    wake_error = None
    try:
        from server_modules import bounded_scheduler_service

        wake_result = await bounded_scheduler_service.schedule_task_assigned_wakeup(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            agent_id=resolved_agent_id,
            task_id=resolved_task_id,
            title=updated.get("title") or "",
            description=updated.get("description") or "",
            triggered_by=triggered_by,
        )
        wake_request = wake_result
    except Exception as exc:
        wake_error = str(exc)
    return {"task": updated, "wake_request": wake_request, "wake_error": wake_error}
