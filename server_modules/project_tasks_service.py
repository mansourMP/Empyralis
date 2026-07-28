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

SUB-TASKS AND LABELS (the last two structural gaps between this board and
Linear's) live here too:

  * Sub-tasks are `parent_task_id`, a self-reference on this same table --
    a sub-task IS a task. Exactly ONE level is permitted, enforced by
    `_resolve_parent_task` (a CHECK cannot express it; it needs a lookup).
    Deleting a parent PROMOTES its children rather than destroying them
    (ON DELETE SET NULL). Every read returns `subtask_count` and
    `subtask_done_count` -- the "1/3" badge -- computed by a LATERAL join
    in the same query as the task itself, never a per-task follow-up read.
    See migrations/add_task_parent.sql.

  * Labels are a workspace-scoped vocabulary in a separate module,
    workspace_labels_service.py, joined on by project_task_labels. This
    module only READS them, inline in the same rollup, so a board load
    stays one query. See migrations/add_task_labels.sql.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository
# One-directional by design: a task CARRIES labels, so tasks may know about
# labels. workspace_labels_service never imports this module at module
# scope in return (it reaches for project_tasks_service lazily, inside the
# one function that needs to check a task exists), which keeps the
# dependency a DAG rather than a cycle.
from server_modules import workspace_labels_service

# ── Task status vocabulary (Linear-style kanban columns) ──────────────────
# Ordered board-left-to-board-right, which is also the order the error
# message below lists them in -- a sorted() set reads as noise to whoever
# (human or agent) is being told what they got wrong.
#
# `backlog`   -- triaged out of the working queue; not queued for work yet.
#                Previously only IMPLIED by "unassigned"; now a real status.
# `todo`      -- queued, nobody has started. Formerly named `open`.
# `in_review` -- THE reason this vocabulary exists: agent-completed work
#                parked for a human to approve before it leaves the board.
#                Without it an agent finishing work goes straight to `done`
#                and there is no seam for review.
#
# See migrations/add_task_status_vocabulary.sql for the forward migration
# and the reasoning on why existing rows became `todo` and not `backlog`.
TASK_STATUS_ORDER = (
    "backlog",
    "todo",
    "in_progress",
    "awaiting_input",
    "blocked",
    "in_review",
    "done",
)
VALID_TASK_STATUSES = set(TASK_STATUS_ORDER)
DEFAULT_TASK_STATUS = "todo"

# Legacy spellings accepted on input and silently mapped forward, never
# rejected: `open` was this table's original name for `todo` (see
# migrations/add_project_tasks.sql). Anything still sending it -- an older
# client, an agent holding a cached tool schema, a saved API script, or a
# row read back from a database the forward migration has not reached yet --
# keeps working and lands on `todo`. Aliases are input-only: `_row_to_task`
# runs reads through the same normalizer, so nothing ever hands `open` back
# out.
TASK_STATUS_ALIASES = {"open": "todo"}

# The statuses `assign_task` treats as "not started yet", and therefore
# flips to `in_progress` when an agent is put on the task. `open` stays in
# the set for the window between deploying this code and applying the
# forward migration, where rows can still literally hold `open`.
UNSTARTED_TASK_STATUSES = ("backlog", "todo", "open")

# ── Task priority (Linear's five-level scale) ─────────────────────────────
# Stored as a SMALLINT so ordering is a plain column comparison rather than
# a CASE over strings. The encoding is Linear's own, inversion and all:
#
#   0 = none (default, "nobody has triaged this")
#   1 = urgent   <- the MOST urgent
#   2 = high
#   3 = medium
#   4 = low      <- the LEAST urgent
#
# 1 being the top is not a bug: it is what Linear stores and what the Linear
# MCP API accepts, so matching it removes a translation layer for every
# agent and integration that already speaks Linear. See
# migrations/add_task_priority.sql.
TASK_PRIORITY_NONE = 0
TASK_PRIORITY_URGENT = 1
TASK_PRIORITY_HIGH = 2
TASK_PRIORITY_MEDIUM = 3
TASK_PRIORITY_LOW = 4
DEFAULT_TASK_PRIORITY = TASK_PRIORITY_NONE

# Ordered most-urgent-first (which is NOT numeric order, because 0/none
# belongs at the bottom) -- the order a board renders and the order the
# error message below lists, for the same "a sorted() set reads as noise"
# reason TASK_STATUS_ORDER is hand-ordered.
TASK_PRIORITY_ORDER = (
    TASK_PRIORITY_URGENT,
    TASK_PRIORITY_HIGH,
    TASK_PRIORITY_MEDIUM,
    TASK_PRIORITY_LOW,
    TASK_PRIORITY_NONE,
)
VALID_TASK_PRIORITIES = set(TASK_PRIORITY_ORDER)

# The integer is canonical; the name is what humans and agents actually
# think in. `_normalize_priority` accepts either on input and `_row_to_task`
# hands the name back out alongside the integer as `priority_label`, so
# nothing above this module has to memorize the inversion.
TASK_PRIORITY_LABELS = {
    TASK_PRIORITY_NONE: "none",
    TASK_PRIORITY_URGENT: "urgent",
    TASK_PRIORITY_HIGH: "high",
    TASK_PRIORITY_MEDIUM: "medium",
    TASK_PRIORITY_LOW: "low",
}
TASK_PRIORITY_BY_LABEL = {label: value for value, label in TASK_PRIORITY_LABELS.items()}

# Human spellings accepted on input beyond the canonical labels above --
# same forgiving-input posture as TASK_STATUS_ALIASES. "no_priority"/"unset"
# are what a caller reaching for "none" tends to write; "p0".."p4" is the
# other common shorthand and maps onto the same scale Linear does.
TASK_PRIORITY_ALIASES = {
    "no_priority": TASK_PRIORITY_NONE,
    "nopriority": TASK_PRIORITY_NONE,
    "unset": TASK_PRIORITY_NONE,
    "p0": TASK_PRIORITY_NONE,
    "p1": TASK_PRIORITY_URGENT,
    "p2": TASK_PRIORITY_HIGH,
    "p3": TASK_PRIORITY_MEDIUM,
    "p4": TASK_PRIORITY_LOW,
    "critical": TASK_PRIORITY_URGENT,
}

# The SQL fragment that means "urgent first, none last". Not a plain
# `ORDER BY priority`, because 0 means UNSET and has to sink to the bottom
# rather than float to the top: NULLIF turns 0 into NULL, and NULLS LAST
# puts it there. Kept as a constant so the two list queries below cannot
# drift apart on the one expression that is easy to get subtly backwards.
TASK_PRIORITY_SORT_SQL = "NULLIF(priority, 0) ASC NULLS LAST"

# list_my_tasks's historical primary ordering ("tasks I own before tasks
# nobody owns"), kept ahead of whatever secondary ordering is in force.
# Named rather than inlined so the f-string query below contains no nested
# double quotes.
_MINE_FIRST_SORT_SQL = "(assignee_agent_id IS NOT NULL) DESC"

# Accepted `sort=` values on the list paths. "created_at" is the historical
# default and stays the default -- adding priority as an ORDER BY option is
# not the same as changing what every existing caller already gets back.
TASK_SORT_PRIORITY = "priority"
TASK_SORT_CREATED_AT = "created_at"
VALID_TASK_SORTS = (TASK_SORT_CREATED_AT, TASK_SORT_PRIORITY)


# ── Sub-tasks (the "0/2" badge on a Linear card) ──────────────────────────
# A sub-task is a plain row in this same table with `parent_task_id` set --
# not a second table, because a sub-task IS a task (same statuses, same
# priority, same assignee, same comments, same agent tools). See
# migrations/add_task_parent.sql for the full reasoning.
#
# EXACTLY ONE LEVEL. A sub-task may not itself have sub-tasks. Linear allows
# arbitrary nesting; the founder has not asked for it, and depth turns every
# rollup into a recursive tree walk, makes "is this done" a non-local
# question, and admits cycles. The rule needs a LOOKUP ("does the proposed
# parent already have a parent?"), which no CHECK constraint can express, so
# it is enforced right here -- the same place the status and priority
# vocabularies are enforced beyond what their CHECKs can say. The database
# backstops only the degenerate self-parent case
# (project_tasks_parent_not_self_check).
#
# The three rules, all rejected with a clear, agent-facing message rather
# than silently coerced:
#   1. the parent must exist in the same workspace AND the same project
#      (the project is this product's collaboration boundary);
#   2. the parent must be top-level -- parenting to a sub-task is the depth
#      violation;
#   3. a task that already HAS sub-tasks cannot become one -- that is the
#      same violation approached from the other end.
MAX_SUBTASK_DEPTH = 1

# The task columns, written once so the six queries below cannot drift apart
# on which fields they name -- the exact failure mode that made an
# un-migrated `priority` column break every read.
_TASK_COLUMNS = (
    "id, tenant_id, workspace_id, project_id, title, description, status, priority, "
    "parent_task_id, assignee_agent_id, created_by, due_at, plan, metadata, created_at, updated_at"
)

# The rollup, computed IN THE SAME QUERY as the task itself -- never a
# per-task follow-up read. Two LATERAL joins: one counts children (total and
# done, which is the whole "1/3" badge), the other aggregates the task's
# labels into a jsonb array. Both ride an index (idx_project_tasks_parent,
# project_task_labels' composite PK) and both are correlated on the outer
# row, so a 200-card board is one round trip and one plan, not 201 of them.
#
# Both laterals re-assert (tenant_id, workspace_id) against the outer row
# even though the join key is already a globally unique primary key. That is
# redundant by construction and kept anyway: this repo has a live history of
# cross-tenant leaks, and a scoping clause that is impossible to get wrong
# costs one index column and removes the question entirely.
_TASK_ROLLUP_COLUMNS = (
    "COALESCE(rollup.subtask_count, 0) AS subtask_count, "
    "COALESCE(rollup.subtask_done_count, 0) AS subtask_done_count, "
    "COALESCE(lbl.labels, '[]'::jsonb) AS labels"
)

_TASK_ROLLUP_JOINS = """
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS subtask_count,
                   COUNT(*) FILTER (WHERE child.status = 'done') AS subtask_done_count
            FROM project_tasks child
            WHERE child.parent_task_id = project_tasks.id
              AND child.tenant_id = project_tasks.tenant_id
              AND child.workspace_id = project_tasks.workspace_id
        ) rollup ON TRUE
        LEFT JOIN LATERAL (
            SELECT COALESCE(
                       jsonb_agg(
                           jsonb_build_object('id', l.id, 'name', l.name, 'color', l.color)
                           ORDER BY lower(l.name)
                       ),
                       '[]'::jsonb
                   ) AS labels
            FROM project_task_labels tl
            JOIN workspace_labels l ON l.id = tl.label_id
            WHERE tl.task_id = project_tasks.id
              AND tl.tenant_id = project_tasks.tenant_id
              AND tl.workspace_id = project_tasks.workspace_id
        ) lbl ON TRUE
"""

# The same three rollup values for an INSERT/UPDATE ... RETURNING, where a
# LATERAL join is not available. Scalar subqueries instead -- same indexes,
# same single round trip, so a write hands back a fully-formed task rather
# than one the caller has to re-read to render.
_TASK_ROLLUP_RETURNING = """
                  (SELECT COUNT(*) FROM project_tasks child
                    WHERE child.parent_task_id = project_tasks.id
                      AND child.tenant_id = project_tasks.tenant_id
                      AND child.workspace_id = project_tasks.workspace_id) AS subtask_count,
                  (SELECT COUNT(*) FROM project_tasks child
                    WHERE child.parent_task_id = project_tasks.id
                      AND child.tenant_id = project_tasks.tenant_id
                      AND child.workspace_id = project_tasks.workspace_id
                      AND child.status = 'done') AS subtask_done_count,
                  (SELECT COALESCE(
                              jsonb_agg(
                                  jsonb_build_object('id', l.id, 'name', l.name, 'color', l.color)
                                  ORDER BY lower(l.name)
                              ),
                              '[]'::jsonb
                          )
                     FROM project_task_labels tl
                     JOIN workspace_labels l ON l.id = tl.label_id
                    WHERE tl.task_id = project_tasks.id
                      AND tl.tenant_id = project_tasks.tenant_id
                      AND tl.workspace_id = project_tasks.workspace_id) AS labels
"""

# The full RETURNING tail, assembled once. Appended by plain concatenation
# rather than interpolated into an f-string because several of these query
# bodies contain literal SQL braces (`'{}'::jsonb`, `'{comments}'`) that an
# f-string would try to evaluate as Python expressions.
_TASK_RETURNING_SQL = _TASK_COLUMNS + ",\n" + _TASK_ROLLUP_RETURNING


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


def _coerce_labels(value: Any) -> List[Dict[str, Any]]:
    """The rollup's jsonb label array, which -- like `plan` and `metadata`
    above -- arrives either already-decoded or as a raw JSON string
    depending on the pool's codec setup. Returns [] (never None) so every
    caller can render `task["labels"]` without a None-check, and so a
    database that has not yet had migrations/add_task_labels.sql applied
    (no `labels` key in the row at all) reads as "this task has no labels"
    rather than raising."""
    if isinstance(value, list):
        return [
            {
                "id": str(item.get("id") or "").strip(),
                "name": str(item.get("name") or "").strip(),
                "color": (
                    str(item.get("color") or "").strip()
                    or workspace_labels_service.DEFAULT_LABEL_COLOR
                ),
            }
            for item in value
            if isinstance(item, dict)
        ]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return _coerce_labels(parsed) if isinstance(parsed, list) else []
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
    """Case/whitespace-insensitive, alias-aware. Returns `default` for
    anything unrecognized -- callers that must REJECT rather than coerce
    (update_task) pass default="" and check for the empty string."""
    token = re.sub(r"[^a-z_]+", "", str(value or "").strip().lower())
    token = TASK_STATUS_ALIASES.get(token, token)
    return token if token in VALID_TASK_STATUSES else default


def _normalize_priority(value: Any, *, default: Optional[int] = DEFAULT_TASK_PRIORITY) -> Optional[int]:
    """Accepts the canonical integer (0-4), the human name ('urgent',
    'high', ...), a numeric string ('2'), or one of the aliases above.
    Returns `default` for anything unrecognized -- callers that must REJECT
    rather than coerce (update_task) pass default=None and check for None,
    mirroring how `_normalize_status` uses default="".

    `bool` is rejected explicitly rather than falling through Python's
    bool-is-an-int rule: `True` would otherwise silently become 1, i.e.
    priority='urgent', which is a genuinely bad thing to infer from a
    caller that passed a flag by mistake.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value in VALID_TASK_PRIORITIES else default
    if isinstance(value, float):
        return int(value) if float(value).is_integer() and int(value) in VALID_TASK_PRIORITIES else default
    token = str(value or "").strip().lower()
    if not token:
        return default
    if token.isdigit():
        parsed = int(token)
        return parsed if parsed in VALID_TASK_PRIORITIES else default
    token = re.sub(r"[^a-z0-9_]+", "_", token).strip("_")
    if token in TASK_PRIORITY_BY_LABEL:
        return TASK_PRIORITY_BY_LABEL[token]
    if token in TASK_PRIORITY_ALIASES:
        return TASK_PRIORITY_ALIASES[token]
    return default


def _priority_omitted(value: Any) -> bool:
    """True when a caller did not really supply a priority. `None` is the
    obvious case; an empty/whitespace string is the other one -- some model
    clients emit `""` for an optional parameter they chose to skip, and
    failing a whole agent turn over that would be a far worse outcome than
    reading it as "leave the priority alone". Note this is NOT the same as
    the integer 0, which is a genuine value ('none') and is applied."""
    return value is None or (isinstance(value, str) and not value.strip())


def _invalid_priority_message(value: Any) -> str:
    """One shared, agent-facing rejection message. Spells out BOTH halves of
    the contract -- the integer and its name -- because the inversion (1 is
    the most urgent, not the least) is the single thing a caller getting
    this error is most likely to have gotten wrong."""
    pairs = ", ".join(f"{value_} = {TASK_PRIORITY_LABELS[value_]}" for value_ in sorted(VALID_TASK_PRIORITIES))
    return (
        f"Invalid task priority {value!r}. Must be one of {pairs} "
        f"(Linear's scale: 1 is the MOST urgent, 4 the least, 0 means no priority set). "
        f"The names ({', '.join(TASK_PRIORITY_LABELS[value_] for value_ in TASK_PRIORITY_ORDER)}) are accepted too."
    )


def priority_label(value: Any) -> str:
    """The human name for a stored priority -- the read-side counterpart of
    `_normalize_priority`, exported because every surface that renders a
    task (the HTTP payload, an agent tool result) wants the name and none of
    them should be re-deriving the inversion themselves."""
    return TASK_PRIORITY_LABELS.get(
        _normalize_priority(value) or TASK_PRIORITY_NONE, "none"
    )


def _normalize_sort(value: Any) -> str:
    """Unknown/absent sort falls back to the historical `created_at DESC`
    rather than erroring: a bad sort key is a display preference, not a
    correctness question, and silently returning the default ordering beats
    failing a whole board load over it."""
    token = re.sub(r"[^a-z_]+", "", str(value or "").strip().lower())
    return token if token in VALID_TASK_SORTS else TASK_SORT_CREATED_AT


def _order_by_clause(sort: Any, *, prefix: str = "") -> str:
    """ORDER BY body for the two list queries. `prefix` lets list_my_tasks
    keep its existing "assigned to me before unassigned" primary key ahead
    of whichever secondary ordering is in force."""
    head = f"{prefix}, " if prefix else ""
    if _normalize_sort(sort) == TASK_SORT_PRIORITY:
        return f"{head}{TASK_PRIORITY_SORT_SQL}, created_at DESC"
    return f"{head}created_at DESC"


def _row_to_task(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    due_at = r.get("due_at")
    # A database that has not yet had migrations/add_task_priority.sql
    # applied returns no `priority` key at all; `_normalize_priority(None)`
    # lands that on 0/none rather than raising, so a read keeps working
    # through the deploy-before-migrate window.
    resolved_priority = _normalize_priority(r.get("priority"))
    return {
        "id": str(r.get("id") or "").strip(),
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "project_id": str(r.get("project_id") or "").strip() or None,
        "title": str(r.get("title") or "").strip(),
        "description": str(r.get("description") or "").strip(),
        "status": _normalize_status(r.get("status")),
        "priority": resolved_priority,
        "priority_label": TASK_PRIORITY_LABELS.get(resolved_priority, "none"),
        # Sub-tasks. `parent_task_id` is None on a top-level task, which is
        # every task that existed before migrations/add_task_parent.sql.
        # The two counts are the "1/3" badge, already computed by the query
        # that fetched this row -- a caller never has to go back for them.
        # A database that has not yet been migrated returns no key at all
        # for any of the three, and they read as 0/0/[] rather than raising,
        # so a read keeps working through the deploy-before-migrate window
        # (the same posture `priority` takes directly above).
        "parent_task_id": str(r.get("parent_task_id") or "").strip() or None,
        "subtask_count": int(r.get("subtask_count") or 0),
        "subtask_done_count": int(r.get("subtask_done_count") or 0),
        "labels": _coerce_labels(r.get("labels")),
        "assignee_agent_id": str(r.get("assignee_agent_id") or "").strip() or None,
        "created_by": str(r.get("created_by") or "").strip() or None,
        "due_at": str(due_at) if due_at else None,
        "plan": _coerce_plan(r.get("plan")),
        "metadata": _coerce_metadata(r.get("metadata")),
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }


async def _resolve_parent_task(
    pool: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    parent_task_id: str,
    project_id: Optional[str],
    child_task_id: Optional[str] = None,
) -> str:
    """Validate a proposed parent and return its id, or raise ValueError with
    a message a human OR a model can act on.

    THE SINGLE-LEVEL RULE LIVES HERE. It cannot live in a CHECK constraint
    because it needs a lookup ("does the proposed parent already have a
    parent?"), and it deliberately does not live in a trigger -- this table
    has none, and a trigger is a large invisible moving part to back a rule
    with exactly one writer. One SELECT does the whole check.

    Four ways this refuses, all of them explicit rather than coerced:
      * the parent does not exist in this workspace;
      * the parent is in a DIFFERENT project (the project is this product's
        collaboration boundary -- a parent reaching across it would make a
        task's rollup count work the reader is not allowed to see);
      * the parent is itself a sub-task (the depth violation);
      * the task being parented already HAS sub-tasks (the same violation
        from the other end -- it would become the middle of a 3-level tree).
    """
    resolved_parent_id = str(parent_task_id or "").strip()
    resolved_child_id = str(child_task_id or "").strip()
    if resolved_child_id and resolved_parent_id == resolved_child_id:
        raise ValueError("A task cannot be its own sub-task.")
    parent_row = await pool.fetchrow(
        """
        SELECT id, project_id, parent_task_id
        FROM project_tasks
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        resolved_parent_id,
    )
    if parent_row is None:
        raise ValueError(f"Parent task {resolved_parent_id} not found in this workspace.")
    parent = dict(parent_row)
    parent_project_id = str(parent.get("project_id") or "").strip()
    if project_id and parent_project_id and parent_project_id != str(project_id).strip():
        raise ValueError(
            f"Parent task {resolved_parent_id} belongs to a different project "
            f"({parent_project_id}). A sub-task must live in the same project as its parent."
        )
    if str(parent.get("parent_task_id") or "").strip():
        raise ValueError(
            f"Task {resolved_parent_id} is already a sub-task, and sub-tasks cannot have "
            f"sub-tasks of their own -- this board supports exactly one level of nesting. "
            f"Attach this to {resolved_parent_id}'s own parent instead, or leave it as a "
            f"top-level task."
        )
    if resolved_child_id:
        child_of_child = await pool.fetchrow(
            """
            SELECT id FROM project_tasks
            WHERE tenant_id = $1 AND workspace_id = $2 AND parent_task_id = $3
            LIMIT 1
            """,
            str(tenant_id or "").strip(),
            str(workspace_id or "").strip(),
            resolved_child_id,
        )
        if child_of_child is not None:
            raise ValueError(
                f"Task {resolved_child_id} already has sub-tasks of its own, so it cannot "
                f"become a sub-task -- this board supports exactly one level of nesting. "
                f"Detach its sub-tasks first if you want to move it under another task."
            )
    return resolved_parent_id


async def create_task(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    title: str,
    description: str = "",
    created_by: Optional[str] = None,
    due_at: Optional[Any] = None,
    priority: Optional[Any] = None,
    parent_task_id: Optional[str] = None,
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
    # An explicitly-supplied priority is validated and REJECTED if bad --
    # silently creating an 'urgent' card as 'none' because the caller typed
    # `priority=9` is exactly the kind of quiet data loss that makes a
    # triage field untrustworthy. Omitting it entirely is not an error: it
    # simply means "untriaged", which is what 0/none says.
    resolved_priority = DEFAULT_TASK_PRIORITY
    if _priority_omitted(priority):
        priority = None
    if priority is not None:
        resolved_priority = _normalize_priority(priority, default=None)
        if resolved_priority is None:
            raise ValueError(_invalid_priority_message(priority))
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to create a task."
        )
    # A sub-task is created by naming its parent here rather than by a
    # follow-up call, so the card never exists in a half-parented state and
    # a rejected parent means no row was written at all. Validation is the
    # shared _resolve_parent_task above -- the SAME function the re-parent
    # path uses, so "one level only" cannot mean two different things
    # depending on which door the caller came through.
    resolved_parent_id: Optional[str] = None
    if str(parent_task_id or "").strip():
        resolved_parent_id = await _resolve_parent_task(
            pool,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            parent_task_id=parent_task_id,
            project_id=project_id,
        )
    tid = str(task_id or "").strip() or _new_task_id()
    row = await pool.fetchrow(
        """
        INSERT INTO project_tasks (id, tenant_id, workspace_id, project_id, title, description, created_by, due_at, priority, parent_task_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::timestamptz, $9, $10)
        RETURNING
        """ + _TASK_RETURNING_SQL,
        tid,
        tenant_id,
        workspace_id,
        project_id,
        title,
        str(description or "").strip(),
        str(created_by or "").strip() or None,
        _coerce_due_at(due_at),
        resolved_priority,
        resolved_parent_id,
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
        f"""
        SELECT {_TASK_COLUMNS},
               {_TASK_ROLLUP_COLUMNS}
        FROM project_tasks
{_TASK_ROLLUP_JOINS}
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
    sort: Optional[str] = None,
    parent_task_id: Optional[str] = None,
    top_level_only: bool = False,
) -> List[Dict[str, Any]]:
    """`sort="priority"` returns the board triage-ordered -- urgent first,
    untriaged (`none`) last, ties broken by the same newest-first recency
    the default ordering uses. Anything else (including the absent default)
    keeps the historical `created_at DESC`, so no existing caller's ordering
    changes underneath it.

    `parent_task_id` narrows to one task's sub-tasks; `top_level_only=True`
    excludes sub-tasks entirely, which is what a KANBAN BOARD wants -- a
    sub-task belongs on its parent's card, not as a seventh card in the
    column. Both default off, so every existing caller keeps seeing exactly
    the rows it saw before (sub-tasks included, since before this change
    there were none).

    Every row carries its own `subtask_count`/`subtask_done_count` rollup
    and its `labels`, computed by this single query's LATERAL joins -- a
    200-card board is one round trip, not 201."""
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
    if parent_task_id:
        params.append(str(parent_task_id).strip())
        clauses.append(f"parent_task_id = ${len(params)}")
    elif top_level_only:
        clauses.append("parent_task_id IS NULL")
    query = f"""
        SELECT {_TASK_COLUMNS},
               {_TASK_ROLLUP_COLUMNS}
        FROM project_tasks
{_TASK_ROLLUP_JOINS}
        WHERE {' AND '.join(clauses)}
        ORDER BY {_order_by_clause(sort)}
    """
    rows = await pool.fetch(query, *params)
    return [t for t in (_row_to_task(r) for r in rows) if t]


async def list_subtasks(
    *,
    tenant_id: str,
    workspace_id: str,
    parent_task_id: str,
    sort: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The children of one task, oldest-relevant-ordering aside -- the list
    behind the "1/3" badge when somebody opens the parent card.

    A thin wrapper over `list_tasks` rather than its own query on purpose:
    the sub-task list must render with the same fields, the same normalizers
    and the same label rollup as any other task list, and a second hand-
    written query is exactly how those drift apart."""
    resolved_parent_id = str(parent_task_id or "").strip()
    if not resolved_parent_id:
        # Guarded explicitly: `list_tasks(parent_task_id="")` would fall
        # through to "no parent filter" and hand back the whole board, which
        # is the worst possible answer to "what are this task's sub-tasks".
        return []
    return await list_tasks(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        parent_task_id=resolved_parent_id,
        sort=sort,
    )


async def list_my_tasks(
    *,
    tenant_id: str,
    workspace_id: str,
    external_agent_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    sort: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The MCP `empyralis_list_my_tasks` tool's backing query: tasks assigned
    to the caller (an external agent's roster id, or a platform agent's
    install id) OR unassigned tasks -- still visible/actionable work the
    caller can pick up.

    "Unassigned" here means assignee_agent_id IS NULL, which is NOT the same
    thing as status='backlog'. Those two were the same fact back when the
    board only had five statuses and inferred "backlog" from a NULL
    assignee; since the seven-status vocabulary landed they are independent
    (a task can sit in `backlog` with an owner already named, or be
    unassigned while sitting in `blocked`). This query is about ownership,
    so it stays on the assignee column; callers wanting the backlog column
    pass status='backlog'.

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

    `sort="priority"` orders urgent-first / untriaged-last WITHIN each half
    of the existing "mine before unassigned" split rather than replacing it:
    an agent asking "what should I work on next" wants its own urgent work
    ahead of somebody else's urgent unclaimed work, not interleaved with it.
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
        SELECT {_TASK_COLUMNS},
               {_TASK_ROLLUP_COLUMNS}
        FROM project_tasks
{_TASK_ROLLUP_JOINS}
        WHERE {' AND '.join(clauses)}
        ORDER BY {_order_by_clause(sort, prefix=_MINE_FIRST_SORT_SQL)}
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
        RETURNING
        """ + _TASK_RETURNING_SQL,
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
    priority: Optional[Any] = None,
    due_at: Optional[Any] = None,
    clear_due_at: bool = False,
) -> Optional[Dict[str, Any]]:
    """Generic patch -- title/description/status/priority/due_at only.
    Assignment has its own dedicated entry point (assign_task, below) since
    it has side effects (the task_assigned wakeup) a plain field patch must
    not trigger implicitly.

    Priority is REJECTED rather than coerced when invalid, exactly like
    status: a triage field that silently swallows a bad value is worse than
    one that errors, because the caller walks away believing the card is
    urgent. Note that `priority=0` is a real, meaningful patch ("clear the
    priority") and is applied -- only `priority=None` means "leave it
    alone", which is why the parameter is None-defaulted rather than
    0-defaulted."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    if status is not None and _normalize_status(status, default="") == "":
        raise ValueError(
            f"Invalid task status '{status}'. Must be one of {list(TASK_STATUS_ORDER)}."
        )
    resolved_priority = None
    if _priority_omitted(priority):
        priority = None
    if priority is not None:
        resolved_priority = _normalize_priority(priority, default=None)
        if resolved_priority is None:
            raise ValueError(_invalid_priority_message(priority))
    resolved_due_at = None if clear_due_at else _coerce_due_at(due_at)
    row = await pool.fetchrow(
        """
        UPDATE project_tasks
        SET title = COALESCE(NULLIF($4, ''), title),
            description = COALESCE($5, description),
            status = COALESCE($6, status),
            due_at = CASE WHEN $7 THEN NULL WHEN $8::timestamptz IS NOT NULL THEN $8::timestamptz ELSE due_at END,
            priority = COALESCE($9::smallint, priority),
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING
        """ + _TASK_RETURNING_SQL,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(task_id or "").strip(),
        str(title or "").strip(),
        None if description is None else str(description).strip(),
        _normalize_status(status, default="") or None if status is not None else None,
        bool(clear_due_at),
        resolved_due_at,
        resolved_priority,
    )
    return _row_to_task(row)


async def set_task_parent(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
    parent_task_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Make a task a sub-task of another, or (with parent_task_id=None)
    promote it back to top-level.

    Its own entry point rather than a field on `update_task`, for the same
    reason `assign_task` is: this is a STRUCTURAL change with a validity
    question attached (does it violate the one-level rule?), not a plain
    field patch, and folding it into the generic patcher would mean every
    caller of that patcher has to think about parenting. It also keeps
    update_task's parameter list and every existing caller untouched.

    Passing None/"" is not an error -- it is the documented way to DETACH a
    sub-task, which is also the manual version of what the database does on
    its own when a parent is deleted (ON DELETE SET NULL: the child is
    promoted, never destroyed -- see migrations/add_task_parent.sql).

    Returns None if the task does not exist in this workspace; raises
    ValueError with an actionable message for every rejected parent.
    """
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_task_id = str(task_id or "").strip()
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    existing = await get_task(
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, task_id=resolved_task_id,
    )
    if existing is None:
        return None
    resolved_parent_id: Optional[str] = None
    if str(parent_task_id or "").strip():
        resolved_parent_id = await _resolve_parent_task(
            pool,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            parent_task_id=parent_task_id,
            project_id=existing.get("project_id"),
            child_task_id=resolved_task_id,
        )
    row = await pool.fetchrow(
        """
        UPDATE project_tasks
        SET parent_task_id = $4,
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING
        """ + _TASK_RETURNING_SQL,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_task_id,
        resolved_parent_id,
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
        RETURNING
        """ + _TASK_RETURNING_SQL,
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


async def agent_project_id(
    *, tenant_id: str, workspace_id: str, agent_id: str,
) -> Optional[str]:
    """The calling agent's own project — the boundary its in-turn
    project_task__* tools are scoped to (2026-07-25 ruling: a platform
    agent works its own project's board only; unlike an external MCP key,
    which is already workspace-wide by design, an agent's native tools
    must not let it read or edit another project's tasks just by knowing
    an id). Returns None if the agent has no project (not expected in
    practice post-46bda1f7e, but every new agent gets one) or doesn't
    exist in this workspace."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        "SELECT project_id FROM workspace_agent_installs WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3",
        str(agent_id or "").strip(),
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
    )
    if row is None:
        return None
    return str(row["project_id"] or "").strip() or None


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
            status = CASE WHEN status = ANY($5::text[]) THEN 'in_progress' ELSE status END,
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING
        """ + _TASK_RETURNING_SQL,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_task_id,
        resolved_agent_id,
        list(UNSTARTED_TASK_STATUSES),
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
