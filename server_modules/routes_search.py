"""Workspace search route: GET /api/w/{workspace_id}/search?q=

The one endpoint behind ⌘K-style "find that task" and the iOS Search tab.
Everything about WHAT matches lives in `workspace_search_service`; this file
owns only WHO may see it, which is the half that has to be right.


THE ACL IS IMPORTED, NOT RE-DERIVED
───────────────────────────────────
`routes_fleet._visible_project_ids` is called directly rather than copied.
A second same-language implementation of "which projects may this person
see" is the defect CLAUDE.md names repeatedly (the channel list that grew a
third copy, the two hand-written maps that agreed with each other and were
both wrong) -- and an ACL is the worst possible thing to hold two opinions
about, because the copies drift toward whichever one nobody re-reads.

`agent_document_scope_service._project_ids_visible_to_user` is a deliberate
twin of that helper, and its own docstring says exactly why it could not
import: it runs on the TURN path, which has no request `current_user` dict,
and importing a route module there would drag FastAPI into the runtime.
Neither reason applies here -- this IS a FastAPI route with a `current_user`
in hand -- so the import is the correct call and a twin would not be.


SCOPE IS ALWAYS A CONCRETE LIST, INCLUDING FOR AN OWNER
───────────────────────────────────────────────────────
`_visible_project_ids` answers `None` for a workspace owner, meaning "no
filter needed". This route does NOT pass that through as "search
everything": it expands it into the workspace's own project ids and binds
that list, so owner and member take the IDENTICAL code path into SQL.

    member  →  {p1, p3}          →  project_id = ANY($3)
    owner   →  every project id  →  project_id = ANY($3)   ← same predicate

The alternative -- an `if visible_ids is None: skip the filter` branch -- is
a scope predicate that exists on one path and not the other, i.e. the
fail-open shape one `if` away from `WHERE ($1 = '' OR ...)`. There is no
branch here in which the search runs unscoped, so a future edit cannot
forget the filter on the half that is harder to test.

A member of NO project gets an empty list, which
`workspace_search_service.search_workspace` answers with zero rows before
touching the database. That is the correct answer, not a degraded one.


THREE OUTCOMES, THREE ANSWERS
─────────────────────────────
CLAUDE.md's outcome-honesty law. A search box is where collapsing these
hurts most, because a person reads "no results" as "my task is gone".

    ok: true,  results: [...]   matches
    ok: true,  results: []      the query RAN and matched nothing
    ok: false, error: "..."     the query did not run. NOT "no results".

`{"ok": False, ...}` is returned with HTTP 200 and a real `error`, matching
every sibling route in `routes_fleet` (the clients already branch on `ok`,
not on the status code). `enforce_workspace_access` still raises its own
403/404 -- authorization is not a business-logic outcome.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query, Request

from server_modules import auth as auth_module

LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["search"])


async def _resolve_tenant(workspace_id: str) -> str:
    """Same derivation `routes_fleet._resolve_tenant` uses. Never
    `users.tenant_id` -- CLAUDE.md: that column is written once at signup and
    goes stale the moment somebody joins a second workspace."""
    from server_modules import control_plane_repository

    return await control_plane_repository.resolve_tenant_id_for_workspace(workspace_id, default="default")


async def _searchable_project_ids(
    current_user: Dict[str, Any], resolved_workspace_id: str, tenant_id: str,
) -> List[str]:
    """The concrete project ids this caller may search, never `None`.

    Owner (`_visible_project_ids` → None) is expanded to the workspace's own
    projects here so SQL sees one shape for everybody. An owner with no
    projects, and a member with no memberships, both correctly resolve to
    `[]` -- "may see no project", which searches nothing.
    """
    from server_modules import routes_fleet

    visible_ids = await routes_fleet._visible_project_ids(
        current_user, resolved_workspace_id, tenant_id,
    )
    if visible_ids is not None:
        return sorted(str(p or "").strip() for p in visible_ids if str(p or "").strip())

    from server_modules import projects_repository as projects

    rows = await projects.list_projects(
        tenant_id=tenant_id, workspace_id=resolved_workspace_id,
    ) or []
    return sorted(
        {
            str(row.get("id") or "").strip()
            for row in rows
            if str(row.get("id") or "").strip()
        }
    )


@router.get("/api/w/{workspace_id}/search")
async def workspace_search(
    request: Request,
    workspace_id: str,
    q: str = Query("", description="What the person typed. Empty returns nothing, never everything."),
    limit: int = Query(20, ge=1, le=50, description="Max hits PER KIND, so tasks never crowd out documents"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Search this workspace's tasks and documents.

    `viewer` is the gate: reading is exactly what this does, and a viewer
    can already open every task and document the results point at -- search
    must never be a narrower door onto content the person could reach by
    scrolling, nor a wider one.
    """
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user, workspace_id, minimum_role="viewer",
    )
    from server_modules import workspace_search_service as search

    query = search.normalize_query(q)
    empty_counts = {"tasks": 0, "documents": 0}
    if not query:
        # Answered without resolving a tenant, a project scope, or a pool.
        # An empty box is not a failure and not a zero-result search -- it
        # is the idle state, and the clients render it as such.
        return {"ok": True, "query": "", "results": [], "counts": dict(empty_counts)}

    try:
        tenant_id = await _resolve_tenant(resolved_workspace_id)
        project_ids = await _searchable_project_ids(
            current_user, resolved_workspace_id, tenant_id,
        )
        results = await search.search_workspace(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            project_ids=project_ids,
            query=query,
            limit=limit,
        )
    except search.WorkspaceSearchUnavailable as exc:
        LOGGER.warning("workspace_search unavailable for %s: %s", resolved_workspace_id, exc)
        return {
            "ok": False,
            "query": query,
            "error": "Search is temporarily unavailable. Nothing was searched.",
            "results": [],
            "counts": dict(empty_counts),
        }
    except Exception as exc:  # noqa: BLE001 - reported, never rendered as "no results"
        LOGGER.exception("workspace_search failed for %s", resolved_workspace_id)
        return {
            "ok": False,
            "query": query,
            "error": str(exc) or "Search failed.",
            "results": [],
            "counts": dict(empty_counts),
        }

    counts = {
        "tasks": sum(1 for hit in results if hit.get("kind") == search.KIND_TASK),
        "documents": sum(1 for hit in results if hit.get("kind") == search.KIND_DOCUMENT),
    }
    return {"ok": True, "query": query, "results": results, "counts": counts}
