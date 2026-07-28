"""Labels: a per-WORKSPACE vocabulary ("bug", "customer-reported") plus the
many-to-many that attaches them to tasks.

The sibling of project_tasks_service.py and the second half of what
migrations/add_project_tasks.sql deliberately left out of v1 ("no labels,
cycles, or priorities"). See migrations/add_task_labels.sql for the schema
and the full reasoning; the short version of the two decisions that shape
this module:

WORKSPACE-SCOPED, NOT PROJECT-SCOPED. A label describes a KIND of work, and
that kind does not change when the work moves to another client. Per-project
labels would mean re-inventing "bug" in every project, five near-identical
rows no cross-project view could group, and a chip that quietly means
something different depending on which board you are looking at.

COLOUR IS A TOKEN NAME, NOT A HEX. frontend/lib/workspace/fleet/task-status.
tsx already states this convention outright for task statuses -- "Colour
comes from the --task-* custom properties in lib/ui/theme-tokens.css
(defined per theme), never from a hex literal here ... a colour is a theme
decision". Labels follow it: this module stores one of ten palette token
names and the frontend owns what each resolves to, per theme. A free-form
hex picked against the dark theme is routinely unreadable on the light one
(theme-tokens.css defines a separate, darker value for all seven task
statuses for exactly that reason) and there is no way to validate its
contrast.

Same direct-pool access pattern as project_tasks_service.py: plain
pool.fetch/fetchrow/execute with explicit tenant_id/workspace_id WHERE
filters (`workspace_labels` and `project_task_labels` carry no RLS policy,
scoped like `projects`/`project_tasks`). Postgres-first -- when Postgres is
unavailable reads return empty and writes raise, rather than falling back to
SQLite.

Imports project_tasks_service LAZILY, inside the one function that needs it,
so the dependency between the two modules stays a DAG: a task carries
labels, so project_tasks_service imports THIS module at module scope, and
this one must not import it back at module scope in return.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

# ── Colour vocabulary ─────────────────────────────────────────────────────
# Ten palette TOKENS, not hex values. The names are the contract between
# this table and the frontend, which resolves each to a real colour per
# theme via a `--label-<name>` custom property, exactly as it already does
# for `--task-blocked` and friends (see frontend/lib/ui/theme-tokens.css).
#
# Ordered as a colour wheel rather than alphabetically, because the one
# place this order is user-visible is a colour picker, and a wheel is what a
# person expects to see there. `grey` leads: it is the default, the
# "unclassified" reading, and the same neutral the backlog/todo statuses
# already use.
#
# Why exactly these ten: six are hues theme-tokens.css already defines for
# the task-status and priority ramps (grey, red, amber, green, blue, violet
# via --task-* and --priority-urgent's orange), and four are the neighbours
# a label set needs to stay mutually distinguishable at chip size. Extending
# the list is one migration -- the CHECK constraint in
# migrations/add_task_labels.sql is the single source of truth the database
# enforces, and this tuple must be kept in step with it.
LABEL_COLOR_ORDER = (
    "grey",
    "red",
    "orange",
    "amber",
    "green",
    "teal",
    "blue",
    "indigo",
    "violet",
    "pink",
)
VALID_LABEL_COLORS = set(LABEL_COLOR_ORDER)
DEFAULT_LABEL_COLOR = "grey"

# Spellings accepted on input beyond the canonical names -- the same
# forgiving-input posture project_tasks_service takes with
# TASK_STATUS_ALIASES / TASK_PRIORITY_ALIASES. "gray" is the American
# spelling of the default and would otherwise be the single most common way
# to get this wrong; the rest are the nearest neighbour of a colour a caller
# is likely to reach for that this palette does not carry.
LABEL_COLOR_ALIASES = {
    "gray": "grey",
    "silver": "grey",
    "neutral": "grey",
    "slate": "grey",
    "yellow": "amber",
    "gold": "amber",
    "lime": "green",
    "emerald": "green",
    "cyan": "teal",
    "turquoise": "teal",
    "navy": "indigo",
    "purple": "violet",
    "magenta": "pink",
    "crimson": "red",
}

# A label name is a chip on a card, not a paragraph. 60 characters is
# generous for "customer-reported" and short enough that a chip can never
# push a card's layout around.
MAX_LABEL_NAME_LENGTH = 60


def _new_label_id() -> str:
    return f"label_{uuid.uuid4().hex[:16]}"


def _normalize_color(value: Any, *, default: Optional[str] = DEFAULT_LABEL_COLOR) -> Optional[str]:
    """Case/whitespace-insensitive, alias-aware. Returns `default` for
    anything unrecognized -- callers that must REJECT rather than coerce
    pass default=None and check for None, mirroring how
    project_tasks_service._normalize_priority does it."""
    token = re.sub(r"[^a-z]+", "", str(value or "").strip().lower())
    if not token:
        return default
    token = LABEL_COLOR_ALIASES.get(token, token)
    return token if token in VALID_LABEL_COLORS else default


def _invalid_color_message(value: Any) -> str:
    return (
        f"Invalid label colour {value!r}. Must be one of {list(LABEL_COLOR_ORDER)}. "
        f"Labels store a palette TOKEN NAME, not a hex code -- the theme decides what "
        f"each token looks like in light and dark mode."
    )


def _normalize_name(value: Any) -> str:
    """Collapse internal whitespace and trim. Case is PRESERVED (the label
    renders as its author typed it); it is only ignored for uniqueness, which
    the database enforces with a functional index on lower(name)."""
    return re.sub(r"\s+", " ", str(value or "").strip())[:MAX_LABEL_NAME_LENGTH]


def _row_to_label(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    return {
        "id": str(r.get("id") or "").strip(),
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "name": str(r.get("name") or "").strip(),
        "color": _normalize_color(r.get("color")),
        "created_by": str(r.get("created_by") or "").strip() or None,
        # Present on the list path only (a LATERAL count), absent on a
        # single-row read -- 0 rather than None so a caller can render it
        # unconditionally.
        "task_count": int(r.get("task_count") or 0),
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
    }


_LABEL_COLUMNS = "id, tenant_id, workspace_id, name, color, created_by, created_at, updated_at"


# ── Label CRUD ────────────────────────────────────────────────────────────


async def create_label(
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    color: Optional[str] = None,
    created_by: Optional[str] = None,
    label_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a label in this workspace's vocabulary.

    Name uniqueness is case-insensitive and enforced BY THE DATABASE
    (uq_workspace_labels_name, a functional UNIQUE index on lower(name)) --
    this function checks first only so the caller gets "A label named 'Bug'
    already exists in this workspace" instead of a raw Postgres unique
    violation. The index is what makes the guarantee real under a race
    between two concurrent creates; the check is what makes the error
    readable. Both are needed, and neither is redundant with the other.
    """
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_name = _normalize_name(name)
    if not resolved_tenant_id or not resolved_workspace_id:
        raise ValueError("tenant_id and workspace_id are required to create a label.")
    if not resolved_name:
        raise ValueError("Label name is required.")
    resolved_color = DEFAULT_LABEL_COLOR
    if color is not None and str(color).strip():
        resolved_color = _normalize_color(color, default=None)
        if resolved_color is None:
            raise ValueError(_invalid_color_message(color))
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to create a label."
        )
    existing = await find_label_by_name(
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, name=resolved_name,
    )
    if existing is not None:
        raise ValueError(
            f"A label named '{existing['name']}' already exists in this workspace. "
            f"Label names are unique per workspace and compared case-insensitively, "
            f"so '{resolved_name}' and '{existing['name']}' are the same label."
        )
    row = await pool.fetchrow(
        f"""
        INSERT INTO workspace_labels (id, tenant_id, workspace_id, name, color, created_by)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING {_LABEL_COLUMNS}
        """,
        str(label_id or "").strip() or _new_label_id(),
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_name,
        resolved_color,
        str(created_by or "").strip() or None,
    )
    return _row_to_label(row)


async def list_labels(
    *,
    tenant_id: str,
    workspace_id: str,
) -> List[Dict[str, Any]]:
    """Every label in the workspace, each with the number of tasks currently
    carrying it -- computed by one LATERAL join in this same query, never a
    per-label follow-up read. Ordered case-insensitively by name, which is
    the order a chip picker and a filter sidebar both want."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await pool.fetch(
        f"""
        SELECT {_LABEL_COLUMNS},
               COALESCE(usage.task_count, 0) AS task_count
        FROM workspace_labels
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS task_count
            FROM project_task_labels tl
            WHERE tl.label_id = workspace_labels.id
              AND tl.tenant_id = workspace_labels.tenant_id
              AND tl.workspace_id = workspace_labels.workspace_id
        ) usage ON TRUE
        WHERE tenant_id = $1 AND workspace_id = $2
        ORDER BY lower(name) ASC
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
    )
    return [label for label in (_row_to_label(r) for r in rows) if label]


async def get_label(
    *,
    tenant_id: str,
    workspace_id: str,
    label_id: str,
) -> Optional[Dict[str, Any]]:
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        f"""
        SELECT {_LABEL_COLUMNS}
        FROM workspace_labels
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(label_id or "").strip(),
    )
    return _row_to_label(row)


async def find_label_by_name(
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
) -> Optional[Dict[str, Any]]:
    """Case-insensitive name lookup -- the read side of the same
    lower(name) uniqueness the index enforces. This is what lets an agent
    say `label="bug"` and hit the label a human created as "Bug"."""
    resolved_name = _normalize_name(name)
    if not resolved_name:
        return None
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    row = await pool.fetchrow(
        f"""
        SELECT {_LABEL_COLUMNS}
        FROM workspace_labels
        WHERE tenant_id = $1 AND workspace_id = $2 AND lower(name) = lower($3)
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        resolved_name,
    )
    return _row_to_label(row)


async def resolve_label(
    *,
    tenant_id: str,
    workspace_id: str,
    label: str,
) -> Optional[Dict[str, Any]]:
    """Accept EITHER a label id or a label name and return the label.

    Exists for the agent tool surfaces. A model asked to tag something as a
    bug will reach for the word "bug", not `label_9f2c...`; requiring the id
    would mean every attach is preceded by a list call and a fuzzy match the
    model does itself, badly. Id is tried first (it is unambiguous), then the
    case-insensitive name.
    """
    token = str(label or "").strip()
    if not token:
        return None
    by_id = await get_label(tenant_id=tenant_id, workspace_id=workspace_id, label_id=token)
    if by_id is not None:
        return by_id
    return await find_label_by_name(tenant_id=tenant_id, workspace_id=workspace_id, name=token)


async def update_label(
    *,
    tenant_id: str,
    workspace_id: str,
    label_id: str,
    name: Optional[str] = None,
    color: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Rename and/or recolour a label. Because this is a shared entity, one
    row change here updates every task carrying it -- which is the entire
    reason labels are a join table rather than a jsonb array on the task.

    A rename that collides with another label (case-insensitively) is
    REJECTED, not coerced or silently merged: merging two labels is a real,
    destructive operation with its own semantics ("what happens to tasks
    carrying both?") and must never be something a typo can trigger.
    """
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_label_id = str(label_id or "").strip()
    resolved_name = _normalize_name(name) if name is not None else None
    if name is not None and not resolved_name:
        raise ValueError("Label name cannot be empty.")
    resolved_color = None
    if color is not None and str(color).strip():
        resolved_color = _normalize_color(color, default=None)
        if resolved_color is None:
            raise ValueError(_invalid_color_message(color))
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    if resolved_name:
        clash = await find_label_by_name(
            tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, name=resolved_name,
        )
        if clash is not None and clash["id"] != resolved_label_id:
            raise ValueError(
                f"A label named '{clash['name']}' already exists in this workspace. "
                f"Label names are unique per workspace and compared case-insensitively."
            )
    row = await pool.fetchrow(
        f"""
        UPDATE workspace_labels
        SET name = COALESCE($4, name),
            color = COALESCE($5, color),
            updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING {_LABEL_COLUMNS}
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_label_id,
        resolved_name,
        resolved_color,
    )
    return _row_to_label(row)


async def delete_label(
    *,
    tenant_id: str,
    workspace_id: str,
    label_id: str,
) -> bool:
    """Delete a label from the workspace vocabulary.

    Its attachments go with it (project_task_labels FKs both CASCADE). That
    IS a cascade, and it is deliberately the opposite of the choice made for
    sub-tasks in migrations/add_task_parent.sql -- because a row in
    project_task_labels is a LINK, not content. No task is deleted, no task
    field is changed; the chip simply stops being on the card. Returns False
    when no such label exists in this workspace.
    """
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    row = await pool.fetchrow(
        """
        DELETE FROM workspace_labels
        WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3
        RETURNING id
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(label_id or "").strip(),
    )
    return row is not None


# ── Attach / detach ───────────────────────────────────────────────────────


async def _task_exists(*, tenant_id: str, workspace_id: str, task_id: str) -> bool:
    # Lazy import: see this module's docstring -- project_tasks_service
    # imports THIS module at module scope, so the reverse edge has to stay
    # inside a function to keep the dependency a DAG.
    from server_modules import project_tasks_service

    task = await project_tasks_service.get_task(
        tenant_id=tenant_id, workspace_id=workspace_id, task_id=task_id,
    )
    return task is not None


async def attach_label(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
    label: str,
    added_by: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Put a label on a task and return the task's resulting label list.

    `label` is an id OR a name (see resolve_label). Idempotent: attaching a
    label that is already on the task is a no-op that still succeeds, via
    ON CONFLICT DO NOTHING against the (task_id, label_id) primary key --
    an agent retrying a step it already completed should not see an error
    for a state that is exactly what it wanted.

    Deliberately does NOT create the label when the name is unknown. The
    workspace's label vocabulary is a small, curated, human-owned thing;
    letting any attach call mint a new one turns it into a junk drawer of
    near-duplicates ("bug", "Bugs", "bugfix") the moment a model guesses a
    word. The error names the labels that DO exist so the caller can pick
    one.
    """
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_task_id = str(task_id or "").strip()
    if not resolved_task_id:
        raise ValueError("task_id is required to attach a label.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to attach a label."
        )
    if not await _task_exists(
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, task_id=resolved_task_id,
    ):
        raise ValueError(f"Task {resolved_task_id} not found in this workspace.")
    resolved_label = await resolve_label(
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, label=label,
    )
    if resolved_label is None:
        available = await list_labels(
            tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id,
        )
        names = ", ".join(f"'{item['name']}'" for item in available) or "(none yet)"
        raise ValueError(
            f"No label '{label}' in this workspace. Existing labels: {names}. "
            f"Attaching does not create labels -- ask an owner to add it first."
        )
    await pool.execute(
        """
        INSERT INTO project_task_labels (task_id, label_id, tenant_id, workspace_id, added_by)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (task_id, label_id) DO NOTHING
        """,
        resolved_task_id,
        resolved_label["id"],
        resolved_tenant_id,
        resolved_workspace_id,
        str(added_by or "").strip() or None,
    )
    return await list_task_labels(
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, task_id=resolved_task_id,
    )


async def detach_label(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
    label: str,
) -> List[Dict[str, Any]]:
    """Take a label off a task and return the task's resulting label list.
    Idempotent in the same way `attach_label` is: detaching a label that is
    not on the task succeeds and changes nothing. The label itself survives
    -- this removes the link, never the vocabulary entry."""
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_task_id = str(task_id or "").strip()
    if not resolved_task_id:
        raise ValueError("task_id is required to detach a label.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to detach a label."
        )
    resolved_label = await resolve_label(
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, label=label,
    )
    if resolved_label is None:
        raise ValueError(f"No label '{label}' in this workspace.")
    await pool.execute(
        """
        DELETE FROM project_task_labels
        WHERE tenant_id = $1 AND workspace_id = $2 AND task_id = $3 AND label_id = $4
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_task_id,
        resolved_label["id"],
    )
    return await list_task_labels(
        tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id, task_id=resolved_task_id,
    )


async def list_task_labels(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
) -> List[Dict[str, Any]]:
    """One task's labels. Note that the normal read path does NOT go through
    here: project_tasks_service's queries aggregate labels inline via a
    LATERAL join, so a board load is one query rather than one per card.
    This exists for the write paths, which want to hand back the resulting
    list without re-reading the whole task."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await pool.fetch(
        """
        SELECT l.id, l.tenant_id, l.workspace_id, l.name, l.color,
               l.created_by, l.created_at, l.updated_at
        FROM project_task_labels tl
        JOIN workspace_labels l ON l.id = tl.label_id
        WHERE tl.tenant_id = $1 AND tl.workspace_id = $2 AND tl.task_id = $3
        ORDER BY lower(l.name) ASC
        """,
        str(tenant_id or "").strip(),
        str(workspace_id or "").strip(),
        str(task_id or "").strip(),
    )
    return [label for label in (_row_to_label(r) for r in rows) if label]
