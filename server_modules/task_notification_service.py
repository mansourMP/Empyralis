"""Per-user notifications (MAN-146): a recipient-addressed record of the
three events a workspace member actually needs pointed AT them, not
broadcast to everyone who happens to share their workspace.

THE BUG THIS EXISTS TO FIX. outbox_service.emit_notification_event already
produces "notification" events, but `OutboxEvent` (outbox_service.py:39-58)
carries only tenant_id/workspace_id -- no recipient. task_mention_
service.py's dispatch_resolved_mentions stamps `metadata.mentioned_user_id`
into that broadcast payload, but nothing downstream ever reads it, and
GET /notifications (runtime_events_api.py:279-349) filters purely by
`allowed_workspace_ids` -- every OTHER member of the workspace receives
every "you were mentioned" meant for someone else.

THIS MODULE DOES NOT REMOVE THAT BROADCAST. task_mention_service.py's own
outbox_service.emit_notification_event call for a human mention stays
exactly as it is -- server_modules/tests/test_task_mention_
service.py::MentionDispatchTests::test_mentioning_a_human_notifies_and_
never_calls_the_scheduler hard-asserts it still fires with the same kwargs,
and this change does not touch existing test files. What this module adds
is the new, correctly-scoped, ADDITIVE mechanism the three producers write
into ALONGSIDE that broadcast: a real Postgres row addressed to exactly one
person. A future frontend pass should read from THIS, and once it does, the
old broadcast call becomes dead code a follow-up change (one that IS
allowed to touch that test file) can delete along with updating its
assertion.

THREE PRODUCERS, each best-effort (wrapped in try/except at the call site,
matching task_mention_service.dispatch_resolved_mentions's own "a
notify/wake failure must not undo the write that already succeeded"
contract -- this module's own functions do not swallow errors themselves,
exactly like project_tasks_service.add_task_comment doesn't):

  1. Mention -- task_mention_service.dispatch_resolved_mentions's
     human_targets loop calls create_notification alongside its existing
     outbox_service.emit_notification_event call, for the same mention
     targets, inheriting that loop's own `human_targets[:human_cap]`
     bound (max_mentioned_human_notifications_per_comment()) automatically
     -- no second cap invented here.

  2. Assigned to you -- project_tasks_service.assign_task_to_user calls
     create_notification once, right after the UPDATE, for the newly
     assigned user (skipped on self-assignment -- notifying someone that
     they assigned a task to themselves is a no-op, the same self-mention
     exclusion dispatch_resolved_mentions already applies).

  3. Comment on a task you own -- project_tasks_service.add_task_comment
     calls create_notification for up to two recipients: `created_by` (the
     filer) AND `assignee_user_id` (the current assignee), deduplicated,
     excluding the commenter. THE DECISION: "own" reads as BOTH, not
     either -- created_by and assignee_user_id are two different, both
     legitimate readings of "owns this task" (the accountable filer vs.
     the person currently working it, exactly the distinction migrations/
     add_task_human_assignee.sql's own header draws between the two
     columns), they routinely differ (Alice files a task and hands it to
     Bob), and the row already carries both fields in hand -- checking two
     ids instead of one costs nothing extra and never under-notifies.
     Naturally bounded to at most 2 recipients by construction (there are
     only two candidate columns), so it does not need max_mentioned_human_
     notifications_per_comment()'s own cap -- that cap exists to bound an
     UNBOUNDED @-mention fan-out, which this producer structurally cannot
     have.

WHY POSTGRES, NOT THE SQLITE runtime_state_store notification_reads/
CHANNEL_EVENTS machinery notification_service.py already has. See
migrations/add_task_notifications.sql's own header -- the durable facts
this table joins against (which task, which user, which workspace) already
live in Postgres control-plane tables, not per-machine runtime state.

READ_AT IS THE ONE UNREAD MECHANISM THIS FEATURE SHIPS. Three others
already exist in this codebase: an in-memory `viewedIds` Set (frontend/app/
(account)/w/[workspaceId]/inbox/page.tsx), a localStorage
"fleet:inbox-last-seen" key driving the rail badge (frontend/lib/workspace/
fleet/fleet-data.ts), and the unused SQLite `notification_reads` table
(runtime_state_store.py). This module's `read_at` column is what a future
UI-consolidation pass should standardize onto -- not a fourth mechanism.

RECIPIENT SCOPING IS APPLICATION-LEVEL, LAYERED ON TOP OF RLS, NOT INSTEAD
OF IT. `task_notifications` is RLS-FORCEd on tenant_id/workspace_id (see
migrations/enable_rls.sql) -- every function below routes through
control_plane_repository.rls_fetch/rls_fetchrow/rls_execute for that
reason. RLS has no notion of recipient_user_id, so every read here ALSO
carries an explicit `AND recipient_user_id = $N` -- the same defense-in-
depth layering assignee_user_id/completed_by_user_id already use elsewhere
in this schema.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

LOGGER = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# Pinned vocabulary, the same disciplined-enum shape project_tasks_service.
# TASK_STATUS_ORDER already uses -- one migration (task_notifications_
# source_event_type_check) to extend, impossible to write around.
SOURCE_EVENT_MENTION = "task_mention"
SOURCE_EVENT_ASSIGNED = "task_assigned"
SOURCE_EVENT_COMMENT = "task_comment"
VALID_SOURCE_EVENT_TYPES = (SOURCE_EVENT_MENTION, SOURCE_EVENT_ASSIGNED, SOURCE_EVENT_COMMENT)

DEFAULT_NOTIFICATION_LIST_LIMIT = 50
MAX_NOTIFICATION_LIST_LIMIT = 200

_NOTIFICATION_COLUMNS = (
    "id, tenant_id, workspace_id, recipient_user_id, source_event_type, "
    "task_id, comment_id, actor_type, actor_id, body, deep_link, read_at, created_at"
)


def _new_notification_id() -> str:
    return f"notif_{uuid.uuid4().hex[:12]}"


def task_deep_link(task_id: str) -> Optional[str]:
    """The one place a task-shaped deep link is built, so all three
    producers (and any future one) render the identical path -- matches
    dispatch_resolved_mentions's own pre-existing `f"/tasks/{task_id}"`
    convention for its outbox metadata.path, kept consistent here rather
    than inventing a second shape."""
    resolved = str(task_id or "").strip()
    return f"/tasks/{resolved}" if resolved else None


def _row_to_notification(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    read_at = r.get("read_at")
    created_at = r.get("created_at")
    return {
        "id": str(r.get("id") or "").strip(),
        "tenant_id": str(r.get("tenant_id") or "").strip() or None,
        "workspace_id": str(r.get("workspace_id") or "").strip() or None,
        "recipient_user_id": str(r.get("recipient_user_id") or "").strip() or None,
        "source_event_type": str(r.get("source_event_type") or "").strip() or None,
        "task_id": str(r.get("task_id") or "").strip() or None,
        "comment_id": str(r.get("comment_id") or "").strip() or None,
        "actor_type": str(r.get("actor_type") or "").strip() or None,
        "actor_id": str(r.get("actor_id") or "").strip() or None,
        "body": str(r.get("body") or ""),
        "deep_link": str(r.get("deep_link") or "").strip() or None,
        "read_at": str(read_at) if read_at else None,
        "is_read": bool(read_at),
        "created_at": str(created_at) if created_at else None,
    }


async def create_notification(
    *,
    tenant_id: str,
    workspace_id: str,
    recipient_user_id: str,
    source_event_type: str,
    body: str,
    task_id: Optional[str] = None,
    comment_id: Optional[str] = None,
    actor_type: str = "system",
    actor_id: Optional[str] = None,
    deep_link: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """THE single write path every producer calls. Deliberately a plain
    INSERT via `rls_execute`, NOT `rls_fetchrow(...RETURNING...)` the way
    every other writer in this module/file's sibling services (create_task,
    add_task_comment, update_task, assign_task, assign_task_to_user) does
    it -- and that inconsistency is intentional, not an oversight. All
    THREE current producers (task_mention_service.dispatch_resolved_
    mentions, project_tasks_service.assign_task_to_user, project_tasks_
    service.add_task_comment) call this as a fire-and-forget SIDE EFFECT of
    a primary write that has already succeeded and whose own caller-facing
    tests assert on that primary write's OWN fetchrow call sequence/return
    value over a shared fake pool (e.g. `pool.fetchrow_calls[-1]` in
    test_project_tasks_human_assignee.py) -- none of which this change is
    permitted to edit. A `.fetchrow()` here would append to that same
    queue/list and silently shift what "the last call" means for every
    caller upstream. `.execute()` never does that (see _QueuedFakePool
    fixtures throughout this test suite: `.execute()` is untracked by every
    `fetchrow_calls`/`fetchrow_results` assertion). The row is instead
    built client-side from the same resolved values just written -- exact
    except for `created_at`, which is approximated with the app clock
    rather than round-tripped from NOW() -- which is a fine trade for a
    best-effort notification nobody's test needs byte-exact.

    Raises ValueError on a bad source_event_type or a missing recipient
    (reject rather than silently write a malformed row -- the same posture
    update_task's status validation takes), so a producer bug fails loudly
    in its own try/except rather than landing a notification nobody can
    ever address. Returns None only when Postgres itself is unavailable
    (mirrors project_tasks_service.get_task's own posture) -- every OTHER
    failure raises."""
    resolved_source_event_type = str(source_event_type or "").strip()
    if resolved_source_event_type not in VALID_SOURCE_EVENT_TYPES:
        raise ValueError(
            f"Invalid source_event_type '{source_event_type}'. Must be one of {list(VALID_SOURCE_EVENT_TYPES)}."
        )
    resolved_recipient_user_id = str(recipient_user_id or "").strip()
    if not resolved_recipient_user_id:
        raise ValueError("recipient_user_id is required to create a notification.")
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    if not resolved_tenant_id or not resolved_workspace_id:
        raise ValueError("tenant_id and workspace_id are required to create a notification.")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_id = _new_notification_id()
    resolved_task_id = str(task_id or "").strip() or None
    resolved_comment_id = str(comment_id or "").strip() or None
    resolved_actor_type = str(actor_type or "").strip() or "system"
    resolved_actor_id = str(actor_id or "").strip() or None
    resolved_body = str(body or "").strip()
    resolved_deep_link = str(deep_link or "").strip() or None
    await control_plane_repository.rls_execute(
        pool,
        """
        INSERT INTO task_notifications (
            id, tenant_id, workspace_id, recipient_user_id, source_event_type,
            task_id, comment_id, actor_type, actor_id, body, deep_link
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        resolved_id,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_recipient_user_id,
        resolved_source_event_type,
        resolved_task_id,
        resolved_comment_id,
        resolved_actor_type,
        resolved_actor_id,
        resolved_body,
        resolved_deep_link,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return {
        "id": resolved_id,
        "tenant_id": resolved_tenant_id,
        "workspace_id": resolved_workspace_id,
        "recipient_user_id": resolved_recipient_user_id,
        "source_event_type": resolved_source_event_type,
        "task_id": resolved_task_id,
        "comment_id": resolved_comment_id,
        "actor_type": resolved_actor_type,
        "actor_id": resolved_actor_id,
        "body": resolved_body,
        "deep_link": resolved_deep_link,
        "read_at": None,
        "is_read": False,
        "created_at": _utc_now_iso(),
    }


async def list_notifications(
    *,
    tenant_id: str,
    workspace_id: str,
    recipient_user_id: str,
    limit: int = DEFAULT_NOTIFICATION_LIST_LIMIT,
    unread_only: bool = False,
) -> List[Dict[str, Any]]:
    """The caller's OWN feed, newest first, and nothing else --
    recipient_user_id is an explicit WHERE clause here, never left to RLS
    alone (RLS enforces tenant/workspace isolation; it has no notion of
    recipient -- see this module's own docstring). Returns [] (never None
    or raises) when Postgres is unavailable, matching project_tasks_
    service.list_tasks's own posture, so a caller never needs a None
    check before iterating."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_recipient_user_id = str(recipient_user_id or "").strip()
    if not resolved_tenant_id or not resolved_workspace_id or not resolved_recipient_user_id:
        return []
    safe_limit = max(1, min(int(limit or DEFAULT_NOTIFICATION_LIST_LIMIT), MAX_NOTIFICATION_LIST_LIMIT))
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT
        """ + _NOTIFICATION_COLUMNS + """
        FROM task_notifications
        WHERE tenant_id = $1 AND workspace_id = $2 AND recipient_user_id = $3
          AND ($4::boolean IS FALSE OR read_at IS NULL)
        ORDER BY created_at DESC
        LIMIT $5
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_recipient_user_id,
        bool(unread_only),
        safe_limit,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return [item for item in (_row_to_notification(row) for row in (rows or [])) if item is not None]


async def mark_notification_read(
    *,
    tenant_id: str,
    workspace_id: str,
    recipient_user_id: str,
    notification_id: str,
) -> Optional[Dict[str, Any]]:
    """Marks exactly ONE of the CALLER's own notifications read. The WHERE
    clause requires recipient_user_id = the caller, so this can never mark
    someone else's notification read even if they somehow learn its id --
    the same "the id alone is not enough, the row must also belong to you"
    posture every recipient-scoped query in this module takes. Idempotent
    (COALESCE keeps the original read_at on a second call rather than
    bumping it forward). Returns None if no matching row exists (wrong id,
    wrong recipient, or wrong tenant/workspace) or Postgres is
    unavailable."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return None
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_recipient_user_id = str(recipient_user_id or "").strip()
    resolved_notification_id = str(notification_id or "").strip()
    if not resolved_notification_id:
        return None
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        UPDATE task_notifications
        SET read_at = COALESCE(read_at, NOW())
        WHERE tenant_id = $1 AND workspace_id = $2 AND recipient_user_id = $3 AND id = $4
        RETURNING
        """ + _NOTIFICATION_COLUMNS,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_recipient_user_id,
        resolved_notification_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    return _row_to_notification(row)


async def mark_all_notifications_read(
    *,
    tenant_id: str,
    workspace_id: str,
    recipient_user_id: str,
) -> int:
    """Bulk sibling of mark_notification_read, same recipient-scoped WHERE
    clause. Not wired to a route in this pass (the shipped route only needs
    the single-id path) -- kept here so a follow-up frontend pass has both
    primitives already available rather than needing a second migration or
    a second look at this module. Returns the number of rows actually
    flipped from unread to read."""
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return 0
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_recipient_user_id = str(recipient_user_id or "").strip()
    result = await control_plane_repository.rls_execute(
        pool,
        """
        UPDATE task_notifications
        SET read_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND recipient_user_id = $3 AND read_at IS NULL
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        resolved_recipient_user_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    # asyncpg's execute() returns a command tag string like "UPDATE 3".
    try:
        return int(str(result or "").strip().split()[-1])
    except (ValueError, IndexError):
        return 0
