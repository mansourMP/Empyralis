-- Labels -> a workspace vocabulary plus a proper many-to-many onto tasks.
--
-- The other half of what migrations/add_project_tasks.sql deliberately left
-- out of v1 ("no labels, cycles, or priorities"), and the sibling of
-- migrations/add_task_parent.sql. Linear's card carries label chips; ours
-- could not, because there was nowhere to put them.
--
-- WHY WORKSPACE-SCOPED AND NOT PROJECT-SCOPED. A label like "bug",
-- "customer-reported" or "needs-design" describes a KIND of work, and that
-- kind does not change when the work moves to a different client. Scoping
-- the vocabulary per project would mean re-inventing "bug" in every project
-- a workspace owns, five near-identical rows that no cross-project view
-- could ever group together, and a label that silently stops meaning the
-- same thing depending on which board you are looking at. The workspace is
-- already this schema's vocabulary boundary for exactly this class of
-- thing: agent display names are unique per workspace
-- (uq_workspace_agent_installs_label), inventory items are per workspace,
-- and the tenant/workspace pair is the scope every single query in
-- project_tasks_service.py already filters on. Linear scopes labels to the
-- workspace (with an optional per-team narrowing) for the same reason.
--
-- The cost of this choice, stated honestly: two projects cannot have
-- different labels that share a name. That is the intended trade -- it is
-- the property that makes "show me every `bug` in this workspace" a real
-- question with a real answer.
--
-- WHY A JOIN TABLE AND NOT A jsonb ARRAY ON project_tasks. `metadata` JSONB
-- is this table's established seam for small append-mostly extras
-- (add_task_comment writes comments there, see project_tasks_service.py),
-- and labels were a candidate for the same treatment. They are not the same
-- shape: a label is a shared ENTITY with its own identity, name and colour
-- that many tasks point at, and renaming or recolouring it has to change
-- every task at once. In a jsonb array a rename means rewriting every task
-- row that carries it, "which tasks have this label" is an unindexed scan,
-- and nothing stops two tasks disagreeing about what colour "bug" is. A
-- join table makes the rename a one-row UPDATE and the lookup an index
-- seek. Comments have no such shared identity, which is exactly why they
-- stayed in jsonb.
--
-- COLOUR IS A NAME, NOT A HEX -- this is a deliberate reuse of the
-- convention frontend/lib/workspace/fleet/task-status.tsx already states
-- outright: "Colour comes from the --task-* custom properties in
-- lib/ui/theme-tokens.css (defined per theme), never from a hex literal
-- here ... a colour is a theme decision". Free-form hex would break that in
-- two concrete ways: a colour picked against the dark theme is frequently
-- unreadable on the light one (theme-tokens.css defines a SEPARATE, darker
-- value for all seven task statuses for precisely this reason), and a
-- user-supplied hex is unvalidatable contrast. So this column stores one of
-- ten palette TOKEN names and the frontend owns what each one resolves to,
-- per theme, exactly as it already does for `--task-blocked` and friends.
-- The vocabulary is pinned by a CHECK under a stable explicit name, the
-- same shape as project_tasks_status_check, so it is one migration to
-- extend and impossible to write around.
--
-- CASE-INSENSITIVE UNIQUENESS PER WORKSPACE. "Bug" and "bug" are the same
-- label and must not both exist -- otherwise the chips on a board read as
-- two different things and a filter on one silently misses the other. This
-- is a functional UNIQUE index on (tenant_id, workspace_id, lower(name)),
-- the identical idiom migrations/add_workspace_agent_installs_label_
-- uniqueness.sql already uses for agent display names. Unlike that file
-- this one needs NO dedupe pass first: the table is brand new, so there are
-- no rows that could conflict and the index cannot fail on existing data.
--
-- DELETION SEMANTICS -- note this is the OPPOSITE of the sub-task decision
-- in add_task_parent.sql, on purpose. Both join-table FKs CASCADE: deleting
-- a label detaches it from every task, and deleting a task drops its
-- attachments. That is not the same as the cascade that file rejects,
-- because a row in project_task_labels is not user content -- it is a LINK,
-- and a link to a thing that no longer exists is not data worth preserving,
-- it is a dangling pointer. The task itself and the label itself are the
-- content, and neither is touched by the other's removal.
--
-- PRODUCTION ROWS ARE SAFE. Purely additive in the strongest sense: two
-- brand-new tables and their indexes. `project_tasks` is not altered at
-- all, no existing row anywhere is read or rewritten, and a workspace that
-- never creates a label sees zero behaviour change.
--
-- Idempotent, in the CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
-- EXISTS style of every sibling migration here.
--
-- Mirrored into server_modules/control_plane_repository.py's
-- CONTROL_PLANE_SCHEMA_SQL (new-database DDL) and its ensure_control_plane_
-- schema() migration section (existing-database self-heal), following the
-- same "standalone migration file + mirror" convention as
-- migrations/add_task_status_vocabulary.sql and
-- migrations/add_task_priority.sql.

BEGIN;

-- 1. The workspace's label vocabulary.
--
-- Named `workspace_labels` rather than a bare `labels` because the scope IS
-- the design decision here and this schema names that scope in the table:
-- workspace_agent_installs, workspace_inventory_items, workspace_labels. A
-- bare `labels` in a shared public schema also collides with the ordinary
-- English meaning of half a dozen unrelated things already in this database
-- (workspace_agent_installs.label is an agent's DISPLAY NAME, and
-- agent_channel_bindings has its own notion of one).
--
-- No RLS -- scoped exactly like `projects`/`project_tasks`: every query in
-- workspace_labels_service.py filters by (tenant_id, workspace_id)
-- explicitly. See migrations/add_project_tasks.sql for the same reasoning.
CREATE TABLE IF NOT EXISTS workspace_labels (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    -- A palette TOKEN name, never a hex. The frontend resolves each of these
    -- to a real colour per theme, the same contract task-status.tsx already
    -- has with the --task-* custom properties. Ten tokens, matching the hues
    -- theme-tokens.css already defines for the task/priority ramps plus the
    -- handful of neighbours a label set needs to stay distinguishable.
    color TEXT NOT NULL DEFAULT 'grey'
        CONSTRAINT workspace_labels_color_check CHECK (color IN (
            'grey', 'red', 'orange', 'amber', 'green',
            'teal', 'blue', 'indigo', 'violet', 'pink'
        )),
    created_by TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Case-insensitive name uniqueness per workspace. Functional UNIQUE index
-- rather than a UNIQUE constraint because a constraint cannot be declared
-- over an expression -- same idiom as
-- uq_workspace_agent_installs_label.
CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_labels_name
    ON workspace_labels(tenant_id, workspace_id, lower(name));

CREATE INDEX IF NOT EXISTS idx_workspace_labels_workspace
    ON workspace_labels(tenant_id, workspace_id);

-- 2. The many-to-many. Composite PRIMARY KEY on (task_id, label_id) rather
-- than a surrogate id: the pair IS the identity, and it gives "is this
-- label on this task" and "detach it" for free while making a duplicate
-- attach impossible at the storage layer rather than only in the service.
--
-- tenant_id/workspace_id are denormalized onto this row so a label rollup
-- can be scoped without joining back through project_tasks -- the same
-- reason project_memberships carries them alongside its project_id FK.
CREATE TABLE IF NOT EXISTS project_task_labels (
    task_id TEXT NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
    label_id TEXT NOT NULL REFERENCES workspace_labels(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    added_by TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (task_id, label_id)
);

-- "Which tasks carry this label" -- the reverse of the PK's own
-- "which labels are on this task".
CREATE INDEX IF NOT EXISTS idx_project_task_labels_label
    ON project_task_labels(tenant_id, workspace_id, label_id);

COMMIT;
