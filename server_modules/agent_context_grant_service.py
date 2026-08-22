"""Which projects one agent may reach -- the CONTEXT GRANT, on one waist.

FOUNDER'S DECISION, 2026-08-20 (CLAUDE.md, "An agent belongs to the
WORKSPACE. Context is GRANTED, never inherited"): an agent lives in the
workspace, and the projects it may reach are GRANTED to it per agent,
defaulting to NONE. His own case, verbatim: *"even if I create this agent
on behalf of other businesses it wouldn't see my task or my context about
the platform, even though I created this agent for my father's business."*

```
BEFORE   agent --workspace_agent_installs.project_id (ONE home project)--> that project
         and, for an install with no project at all (the Operator), the
         document reader fell through to THE ASKING PERSON'S WHOLE REACH
         -- for an owner, every project in the workspace.
AFTER    agent --install_metadata.context_project_ids (a GRANT)--> exactly those
         default for a new install: []  (none)
```

STORAGE is ``workspace_agent_installs.metadata["context_project_ids"]``
(the JSONB column ``instructions``/``skills``/``capability_config``
already live in), and the choice is load-bearing rather than lazy: the
grant needs THREE distinguishable states and a JSON value expresses all
three without a second flag column, which a join table cannot --

```
key ABSENT   ->  LEGACY   this install predates the grant. Behave EXACTLY as
                          before (its one home project; the Operator's
                          per-user fallback). Never "nothing" -- silently
                          revoking every live agent's context is a worse
                          failure than the one this feature fixes.
[]           ->  NONE     granted nothing. A real answer, not an absence.
["p1","p2"]  ->  THOSE    and only those.
```

FAIL CLOSED on anything else. A read that raises resolves to ``unavailable``
-> no project reach at all, never "everything" and never a fall-back to
legacy: an install that HAS a grant we could not read must not quietly get
the wider pre-grant behaviour back. The one deliberate exception is "there
is no control plane to ask" (SQLite fallback, pool is None), which is
LEGACY rather than unavailable -- it is not an error, it is a deployment
with no grant information at all, and treating it as an error would take
documents away from the Operator on those boxes for no security gain.

THE MODEL CANNOT WIDEN ITS OWN GRANT. There is no tool argument, no prompt
field, and no ``_ALLOWED_CONFIGURE_KEYS`` entry for it -- deliberately, so
``fleet__configure_agent`` / ``empyralis_configure_agent`` (both callable BY
an agent) cannot reach it. The only writer is the owner-gated HTTP route
``PUT /fleet/agents/{id}/context-projects`` (routes_fleet.py), the same
server-side-resolution posture as ``agent_goals.attempt_count`` and
``memory_write_private``'s user id.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, NamedTuple, Optional

logger = logging.getLogger(__name__)

# The metadata key. One constant, referenced by the resolver, the route and
# the tests -- a second hand-typed copy of a storage key is the "channel
# list copied into a third place" shape CLAUDE.md already records.
GRANT_METADATA_KEY = "context_project_ids"

# A grant is a short, human-curated list of a workspace's own projects, not
# a bulk import. The cap exists so a malformed or hostile payload cannot
# turn one install row into an unbounded document -- it is not a product
# limit anyone should ever reach.
MAX_GRANTED_PROJECTS = 200

STATUS_GRANT = "grant"
STATUS_LEGACY = "legacy"
STATUS_UNAVAILABLE = "unavailable"


class AgentProjectGrant(NamedTuple):
    """What one agent may reach.

    THREE FIELDS BECAUSE THERE ARE THREE QUESTIONS, and the codebase
    already proved collapsing them is a bug (see DocumentScope's own note):

      project_ids        every project this agent may READ. Sorted, deduped.
      write_project_id   the ONE project it may CREATE in, or "" for none.
      status             grant | legacy | unavailable -- see the module
                         docstring. Callers branch on this, never on
                         ``project_ids`` being empty, because "granted
                         nothing" and "could not tell" are different facts
                         that must not share one signal (CLAUDE.md).
    """

    project_ids: List[str]
    write_project_id: str
    status: str

    @property
    def has_reach(self) -> bool:
        return bool(self.project_ids)

    @property
    def is_legacy(self) -> bool:
        return self.status == STATUS_LEGACY


UNAVAILABLE_GRANT = AgentProjectGrant([], "", STATUS_UNAVAILABLE)


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalize_granted_project_ids(raw: Any) -> Optional[List[str]]:
    """The stored value -> a clean id list, or None when NO grant is recorded.

    ``None`` (the key is absent, or holds JSON null) is LEGACY and is the
    only way to say "no grant recorded" -- an empty list is a real, explicit
    grant of nothing. Anything of the wrong SHAPE (a string, a number, a
    dict) is NOT silently treated as legacy: it resolves to the empty grant,
    because a corrupt value must never buy back the wider pre-grant reach.
    """
    if raw is None:
        return None
    if isinstance(raw, (str, bytes, bytearray)):
        # Stored JSON that came back as text (the SQLite fallback hands back
        # whole metadata blobs as strings). Parse, then re-apply this rule.
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        return [] if parsed is None else normalize_granted_project_ids(parsed)
    if not isinstance(raw, (list, tuple, set)):
        return []
    cleaned = {str(item or "").strip() for item in raw}
    cleaned.discard("")
    return sorted(cleaned)[:MAX_GRANTED_PROJECTS]


def grant_from_install_fields(
    *, metadata: Any, home_project_id: Any,
) -> AgentProjectGrant:
    """The pure core: decide a grant from an install row already in hand.

    Separate from the async resolver below so the turn path
    (agent_turn_runtime_service._resolve_specialist_toolset), which already
    holds the install bundle, spends ZERO extra queries -- the same reason
    DocumentScope resolves read and write scope from one lookup.
    """
    home = str(home_project_id or "").strip()
    granted = normalize_granted_project_ids(_as_dict(metadata).get(GRANT_METADATA_KEY))
    if granted is None:
        # LEGACY: exactly what this install could reach before the grant
        # existed. For a specialist that is its one home project; for the
        # workspace-level Operator (no project_id, and never had one) it is
        # nothing here, and its own per-user fallback still applies
        # downstream (agent_document_scope_service).
        return AgentProjectGrant([home] if home else [], home, STATUS_LEGACY)
    write = home if home and home in granted else (granted[0] if len(granted) == 1 else "")
    return AgentProjectGrant(list(granted), write, STATUS_GRANT)


async def resolve_agent_project_grant(
    *, tenant_id: str, workspace_id: str, agent_install_id: str,
) -> AgentProjectGrant:
    """Read this install's grant. ONE query, RLS-scoped, never raises.

    Deliberately reads ``metadata`` and ``project_id`` together -- the same
    row ``project_tasks_service.agent_project_id`` already reads -- so
    resolving a grant is not two lookups of one fact.
    """
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_install_id = str(agent_install_id or "").strip()
    if not resolved_tenant_id or not resolved_workspace_id or not resolved_install_id:
        return UNAVAILABLE_GRANT

    from server_modules import control_plane_repository

    try:
        pool = await control_plane_repository.ensure_control_plane_schema()
    except Exception:
        logger.exception(
            "context grant: control plane unavailable workspace=%s agent=%s",
            resolved_workspace_id, resolved_install_id,
        )
        return UNAVAILABLE_GRANT
    if pool is None:
        # No control plane to ask (SQLite fallback). Not an error -- there
        # is no grant information on this deployment at all, which is the
        # definition of legacy. See the module docstring.
        return AgentProjectGrant([], "", STATUS_LEGACY)

    try:
        row = await control_plane_repository.rls_fetchrow(
            pool,
            "SELECT project_id, metadata FROM workspace_agent_installs "
            "WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3",
            resolved_install_id,
            resolved_tenant_id,
            resolved_workspace_id,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
        )
    except Exception:
        logger.exception(
            "context grant: install read failed workspace=%s agent=%s",
            resolved_workspace_id, resolved_install_id,
        )
        return UNAVAILABLE_GRANT
    if row is None:
        # No such install in this workspace. Fail closed rather than legacy:
        # there is no row whose prior behaviour we could be preserving.
        return UNAVAILABLE_GRANT
    # Row-shape tolerant on purpose: asyncpg Records, SQLite dict rows and
    # test fakes all reach here, and a row that simply does not carry
    # `metadata` is "no grant recorded" (legacy), not a crash.
    row_fields = dict(row)
    return grant_from_install_fields(
        metadata=row_fields.get("metadata"), home_project_id=row_fields.get("project_id"),
    )


def ambiguous_write_target_message(grant: AgentProjectGrant, *, noun: str) -> str:
    """Why a create was refused, in the owner's own vocabulary.

    Never invent a winner. An agent granted several projects and no home
    project among them has no way to say WHICH one a new task/document
    belongs in, and silently picking is the shape CLAUDE.md records as the
    provisioning-clobber bug. Say so, and say what fixes it."""
    count = len(grant.project_ids)
    if count == 0:
        return (
            f"this agent has no project it may reach, so it has no {noun}. "
            "Give it access to a project in Configure \u2192 Context."
        )
    return (
        f"this agent can reach {count} projects, so there is no single place to put a new {noun}. "
        "Leave it access to one project, or ask a person to create it in the project they mean."
    )
