-- Per-user notifications (MAN-146) -- a recipient-addressed record of the
-- three events a workspace member actually needs to know happened TO THEM:
-- someone @-mentioned them on a task, a task was assigned to them, or
-- someone commented on a task they own.
--
-- WHY THIS TABLE EXISTS AT ALL. outbox_service.emit_notification_event
-- already produces "notification" events, but OutboxEvent (outbox_
-- service.py) carries only tenant_id/workspace_id -- no recipient. The one
-- caller that tries to address a human (task_mention_service.py's
-- dispatch_resolved_mentions) can only stamp metadata.mentioned_user_id
-- into that broadcast payload; nothing downstream ever reads it, and
-- GET /notifications (runtime_events_api.py) filters purely by
-- allowed_workspace_ids, so every OTHER member of the workspace receives
-- every "you were mentioned" meant for someone else. This table is not a
-- fix to that broadcast (task_mention_service.py's own existing outbox
-- call stays exactly as it is -- server_modules/tests/test_task_mention_
-- service.py::MentionDispatchTests::test_mentioning_a_human_notifies_and_
-- never_calls_the_scheduler hard-asserts it still fires); it is the new,
-- correctly-scoped, ADDITIVE mechanism the three producers (mention,
-- assignment, comment-on-an-owned-task) write into instead, and the one a
-- future frontend pass should read from and eventually be the only one
-- left standing.
--
-- WHY POSTGRES AND NOT THE SQLITE runtime_state_store notification_reads/
-- CHANNEL_EVENTS machinery notification_service.py already has. That store
-- is per-machine, ephemeral-leaning runtime state (see runtime_state_store.
-- py's own module scope) -- it is not where the durable facts this table
-- must JOIN against (which task, which user, which workspace) already
-- live. project_tasks, users and workspace_agent_installs are all
-- Postgres-native control-plane tables; a notification that references
-- them belongs next to them, in control_plane_repository.py, following
-- exactly the same "Postgres for durable control-plane state" posture
-- project_tasks itself already established.
--
-- COLUMNS.
--   recipient_user_id -- WHO this notification is FOR. NOT NULL: a
--     notification with no recipient is not a per-user notification, it is
--     the workspace-broadcast this table exists to stop being the only
--     option. FK CASCADE (not SET NULL, unlike completed_by_user_id in
--     migrations/add_task_completion_attribution.sql) -- a deleted user's
--     inbox is meaningless to keep around; SET NULL would leave orphaned
--     rows with no recipient at all, which the NOT NULL constraint above
--     already says can never be a valid state.
--   source_event_type -- which of the three producers wrote this row.
--     Pinned by a CHECK, the same disciplined-vocabulary shape project_
--     tasks_status_check already uses, so the set is one migration to
--     extend and impossible to write around.
--   task_id -- the task this notification is about. Nullable (a future
--     producer might not be task-shaped) but FK CASCADE when present: a
--     notification pointing at a deleted task has nothing left to deep-
--     link to, so it goes with the task, mirroring project_task_labels'
--     own "a link to a thing that no longer exists is a dangling pointer,
--     not data worth preserving" reasoning.
--   comment_id -- which comment (task.metadata.comments[].id) triggered
--     this, when applicable. Comments are NOT a first-class table (see
--     project_tasks_service.add_task_comment's own docstring -- they live
--     in project_tasks.metadata as an append-mostly jsonb log), so this is
--     a plain unvalidated TEXT reference, not an FK -- there is no table to
--     point it at.
--   actor_type / actor_id -- who CAUSED this notification (the mentioner,
--     the assigner, the commenter). Same free-text, unvalidated shape
--     project_tasks.metadata comments already use for author_type/
--     author_id (a polymorphic identity across human/agent/system with no
--     single table to FK against) -- deliberately not constrained the way
--     completed_by_user_id/completed_by_agent_id are, because those two
--     are a single, storage-enforced mutual-exclusivity invariant this
--     column has no equivalent need for.
--   body / deep_link -- the rendered notification text and where clicking
--     it should go. Both come from the producer at write time (never
--     recomputed at read time), the same "render once, read many" posture
--     add_task_comment already takes for a stored comment body.
--   read_at -- THE unread mechanism this feature ships. Nullable,
--     NULL == unread. Three other unread mechanisms already exist in this
--     codebase (an in-memory viewedIds Set in frontend/app/(account)/w/
--     [workspaceId]/inbox/page.tsx, a localStorage "fleet:inbox-last-seen"
--     key in frontend/lib/workspace/fleet/fleet-data.ts driving the rail
--     badge, and the unused SQLite notification_reads table in
--     runtime_state_store.py) -- this column is the one a future UI-
--     consolidation pass should standardize on, not a fourth mechanism
--     alongside the other three.
--
-- RLS. Every query against this table is written from day one against
-- control_plane_repository.rls_fetch/rls_fetchrow/rls_execute (there is no
-- legacy unconverted call site to worry about the way the MAN-109 six-
-- tables pass had to sequence around) -- see migrations/enable_rls.sql's
-- own MAN-109 comment for why that ordering matters. Safe to enable RLS on
-- this table in the SAME migration that creates it, unlike that
-- retrofit.
--
-- RECIPIENT SCOPING IS APPLICATION-LEVEL, ON TOP OF RLS, NOT INSTEAD OF IT.
-- RLS enforces tenant_id/workspace_id isolation (the same guarantee every
-- other RLS-enabled control-plane table gets) -- it does NOT know about
-- recipient_user_id, and does not need to: task_notification_service.py's
-- list_notifications always adds `AND recipient_user_id = $N` explicitly,
-- the same defense-in-depth layering assignee_user_id/completed_by_user_id
-- already use (RLS keeps a query from ever crossing a tenant/workspace
-- boundary; the recipient filter is what keeps two members of the SAME
-- workspace from reading each other's inbox).
--
-- PRODUCTION ROWS ARE SAFE. Purely additive: one brand-new table, no
-- existing table is altered, no existing row anywhere is read or
-- rewritten. A workspace that never mentions/assigns/comments produces
-- zero rows here and sees zero behavior change.
--
-- Idempotent, in the CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
-- EXISTS style of every sibling migration here.
--
-- Mirrored into server_modules/control_plane_repository.py's CONTROL_
-- PLANE_SCHEMA_SQL (a brand-new table needs no separate self-heal ALTER
-- block -- CREATE TABLE IF NOT EXISTS is already a no-op-or-create on
-- every boot, on a fresh database and an existing one alike).

BEGIN;

CREATE TABLE IF NOT EXISTS task_notifications (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    recipient_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_event_type TEXT NOT NULL
        CONSTRAINT task_notifications_source_event_type_check CHECK (source_event_type IN (
            'task_mention', 'task_assigned', 'task_comment'
        )),
    task_id TEXT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
    comment_id TEXT NULL,
    actor_type TEXT NOT NULL DEFAULT 'system',
    actor_id TEXT NULL,
    body TEXT NOT NULL DEFAULT '',
    deep_link TEXT NULL,
    read_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The primary read pattern: "give me this recipient's feed, newest first,
-- inside this tenant/workspace" -- routes_fleet.py's GET notifications
-- route is exactly this query, so it leads on (tenant_id, workspace_id,
-- recipient_user_id) and trails on created_at DESC.
CREATE INDEX IF NOT EXISTS idx_task_notifications_recipient_feed
    ON task_notifications(tenant_id, workspace_id, recipient_user_id, created_at DESC);

-- The secondary read pattern: "how many unread does this recipient have"
-- (a rail badge count). Partial on read_at IS NULL so the index only ever
-- carries the minority of rows that matter for that question, the same
-- partial-index idiom idx_project_tasks_assignee_user already uses.
CREATE INDEX IF NOT EXISTS idx_task_notifications_recipient_unread
    ON task_notifications(tenant_id, workspace_id, recipient_user_id)
    WHERE read_at IS NULL;

COMMIT;
