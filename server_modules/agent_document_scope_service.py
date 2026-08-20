"""Which projects' documents a turn may read -- ONE function, on purpose.

THAT REWRITE HAPPENED (feat/agent-context-grant, 2026-08-20). This module
predicted it in its own header and it landed exactly here: the many-to-many
is `agent_context_grant_service` (install_metadata["context_project_ids"]),
and this resolver now asks it FIRST. Nothing else in the documents feature
changed, which is the entire reason this was a resolver instead of an
inlined `project_id` read at each call site.

The pre-grant behaviour is still below it, verbatim, and is still reached --
by every install that carries NO grant (the founder's own 13 live agents,
and the workspace-level Operator, whose per-user fallback is a separate
founder decision this must not quietly overturn). See
agent_context_grant_service's docstring for why "no grant recorded" and
"granted nothing" are different facts.

FAIL CLOSED. Every path that cannot establish a scope returns an EMPTY list,
never "everything". An empty list is a real answer -- "this turn may read no
project's documents" -- and list_documents treats it as exactly that
(returning nothing rather than widening), the posture CLAUDE.md records for
run_state_repository after the fail-open `WHERE ($1 = '' OR ...)` family.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, NamedTuple, Optional

logger = logging.getLogger(__name__)


class DocumentScope(NamedTuple):
    """What one turn may do with documents.

    TWO FIELDS BECAUSE THERE ARE TWO QUESTIONS, and collapsing them is what
    made the first version of this read `agent_project_id` twice per tool
    call -- once at the call site for the write target and once in here for
    the read scope. One resolution, both answers:

      project_ids     every project whose documents this turn may READ
      own_project_id  the ONE project it may WRITE to, or "" for none
    """

    project_ids: List[str]
    own_project_id: str


async def resolve_agent_document_project_scope(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: str = "",
    user_id: str = "",
) -> DocumentScope:
    """The project ids whose documents this turn may read, sorted.

    Two cases, and the second is the one that was previously impossible:

      A SPECIALIST (an install carrying its own project_id) reads exactly
      that project. Unchanged from the behaviour document__* already had --
      this is not a widening, it is the same grant expressed once.

      THE WORKSPACE-LEVEL OPERATOR (agent_kind "master") carries NO
      project_id and never has -- its seed INSERT omits the column -- so
      every document tool refused it outright ("this agent has no project,
      so it has no documents"), on every turn, for every workspace. It is
      also a PER-USER assistant (CLAUDE.md, 2026-08-18: Ask AI is personal,
      /threads is already scoped by owner_user_id), so the honest scope is
      THE ASKING PERSON'S OWN -- the identical set `_visible_project_ids`
      grants that same human in the UI, resolved here from ids because a
      turn has a user_id and not a request's `current_user` dict. The
      Operator therefore reads what the person it is answering could have
      opened themselves, and nothing else: it never becomes a way to reach
      past a project boundary the person does not already stand inside.

    An owner is resolved to their REAL project id list rather than to a
    "everything" sentinel, because there is no such sentinel downstream and
    there must not be one.
    """
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_install_id = str(agent_install_id or "").strip()
    resolved_user_id = str(user_id or "").strip()
    if not resolved_tenant_id or not resolved_workspace_id:
        return DocumentScope([], "")

    from server_modules import agent_context_grant_service as _grants

    if resolved_install_id:
        # STILL ONE QUERY. resolve_agent_project_grant reads `metadata` and
        # `project_id` off the same row agent_project_id used to read on its
        # own, so the grant did not cost this module a second lookup -- it
        # replaced the lookup it already had.
        grant = await _grants.resolve_agent_project_grant(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            agent_install_id=resolved_install_id,
        )
        if grant.status == _grants.STATUS_GRANT:
            # An explicit grant DECIDES, and it decides both answers. It is
            # never widened by the person asking: the founder's case is an
            # agent built for someone else's business, and "the owner is
            # standing here" must not be a way past that.
            return DocumentScope(list(grant.project_ids), grant.write_project_id)
        if grant.status == _grants.STATUS_UNAVAILABLE:
            # Could not tell. Fail closed -- never fall through to the wider
            # pre-grant path, which is exactly what a grant we could not
            # read would be silently undoing.
            return DocumentScope([], "")
        if grant.write_project_id:
            # LEGACY, and it is byte-for-byte the pre-grant behaviour: an
            # install with its own project reads and writes exactly that one.
            return DocumentScope([grant.write_project_id], grant.write_project_id)

    # No project of its own -> the asking person's own reach. Without a
    # resolved human there is nothing to inherit, so: nothing.
    if not resolved_user_id:
        return DocumentScope([], "")
    return DocumentScope(
        await _project_ids_visible_to_user(
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=resolved_user_id,
        ),
        "",
    )


async def _project_ids_visible_to_user(
    *, tenant_id: str, workspace_id: str, user_id: str,
) -> List[str]:
    """The id-only twin of routes_fleet._visible_project_ids: a workspace
    OWNER sees every project (owners need no project_memberships row, which
    is exactly why that helper returns None for them), anybody else sees
    their explicit membership rows. Kept beside the resolver rather than
    imported from routes_fleet because that one takes a request's
    `current_user` dict and a turn does not have one -- and importing a
    route module into the turn path would drag FastAPI in with it."""
    from server_modules import control_plane_repository as _cp
    from server_modules import projects_repository as _projects

    is_owner = False
    try:
        memberships = await _cp.list_workspace_memberships_for_user(user_id) or []
        for row in memberships:
            if str((row or {}).get("workspace_id") or "").strip() != workspace_id:
                continue
            if str((row or {}).get("role") or "").strip().lower() in ("owner", "admin"):
                is_owner = True
            break
    except Exception:
        logger.exception(
            "document scope: membership lookup failed workspace=%s user=%s", workspace_id, user_id,
        )
        return []

    try:
        if is_owner:
            rows = await _projects.list_projects(
                tenant_id=tenant_id, workspace_id=workspace_id,
            ) or []
            return sorted(
                {str(r.get("id") or "").strip() for r in rows if str(r.get("id") or "").strip()}
            )
        ids = await _projects.list_member_project_ids(
            tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id,
        ) or []
        return sorted({str(i or "").strip() for i in ids if str(i or "").strip()})
    except Exception:
        logger.exception(
            "document scope: project lookup failed workspace=%s user=%s", workspace_id, user_id,
        )
        return []


def document_tree_index(documents: List[Dict[str, Any]], *, max_entries: int = 200) -> str:
    """The context layer's INDEX -- paths only, never bodies.

    PUSH THE INDEX, PULL THE CONTENT. This is what gets injected into a
    turn so every agent knows the context layer exists and what is in it;
    the bodies stay behind document__read. Injecting bodies would rebuild
    the retrieval pipeline this codebase deliberately deleted (CLAUDE.md:
    the RAG/embeddings removal, and Claude Code's own finding that agentic
    search beats RAG) -- an agent that can see `ls -R` and grep is the
    model that was chosen. A path list is also cheap enough to be
    unconditional, which a body-bearing block never is.

    Truncated rather than unbounded, and it SAYS it was truncated: a silently
    cut index would have an agent conclude a document does not exist."""
    lines: List[str] = []
    for doc in documents or []:
        path = str((doc or {}).get("path") or "").strip()
        if not path:
            continue
        project_id = str((doc or {}).get("project_id") or "").strip()
        lines.append(f"{project_id}/{path}" if project_id else path)
    if not lines:
        return ""
    lines.sort()
    total = len(lines)
    shown = lines[:max_entries]
    body = "\n".join(f"- {line}" for line in shown)
    if total > len(shown):
        body += f"\n- ... and {total - len(shown)} more (use document__list to page through them)"
    return (
        "Context layer (documents you can read with document__read, by path):\n" + body
    )
