"""Review attribution (MAN-145 follow-up) -- migrations/add_task_
completion_attribution.sql, project_tasks_service.update_task's transition
detection, and the schema mirror in control_plane_repository.py.

`project_tasks` had no record of WHO completed a task -- an agent closing
its own work, another agent closing a teammate's, and a human dragging the
card to Done were indistinguishable afterward (ProjectOverview.tsx:77-90).
This file proves, in order:

1. UNIT (fake pool, no database needed): update_task rejects a call that
   names both a human and an agent actor, and the SQL it builds threads
   the actor ids into the completed_by_* CASE expressions (the wiring
   Postgres itself then evaluates -- see layer 3 for the real semantic
   proof, which a fake pool can only pretend to have, exactly like
   test_project_tasks_human_assignee.py's own layering says about the
   CHECK constraint). Also proves _row_to_task's read side.

2. SCHEMA / DDL guards (no database needed): the migration and the
   control_plane_repository.py mirror agree on the columns, the FKs (SET
   NULL, never CASCADE), the CHECK, and the "purely additive, no rewrite,
   no backfill" shape every sibling project_tasks migration promises.

3. REAL POSTGRES, skipped when no database is reachable (same opt-in
   convention as test_project_tasks_human_assignee.py -- DATABASE_URL must
   already be exported by the caller). This is the only layer that can
   actually prove (a) the CHECK constraint rejects a row with both
   completed_by columns set, and (b) the transition-detection semantics
   themselves: a real done-transition stamps the actor, a no-op re-patch
   of an already-done task does not re-stamp, a title-only patch touches
   nothing, and leaving 'done' clears all three columns.
"""

from __future__ import annotations

import inspect
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import control_plane_repository
from server_modules import project_tasks_service

REPO_ROOT = Path(__file__).resolve().parents[2]
ATTRIBUTION_MIGRATION = REPO_ROOT / "migrations" / "add_task_completion_attribution.sql"


# ── Fake pool plumbing -- identical shape to test_project_tasks_human_
# assignee.py's own copy (project_tasks is FORCE RLS, so update_task's call
# goes through control_plane_repository.rls_fetchrow, which opens a scoped
# connection via pool.acquire() rather than calling pool.fetchrow
# directly). Kept as a local copy, not an import, matching every sibling
# test file's "self-contained" convention. ─────────────────────────────────


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, pool: "_QueuedFakePool") -> None:
        self._pool = pool

    async def fetchrow(self, query, *args):
        return await self._pool.fetchrow(query, *args)

    async def fetch(self, query, *args):
        return await self._pool.fetch(query, *args)

    async def execute(self, query, *args):
        if "set_config(" in query:
            return "SELECT 1"
        return await self._pool.execute(query, *args)

    def transaction(self):
        return _FakeTransaction()


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _QueuedFakePool:
    def __init__(self, *, fetchrow_results=None, fetch_results=None):
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetch_results = list(fetch_results or [])
        self.fetchrow_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if not self._fetchrow_results:
            return None
        return self._fetchrow_results.pop(0)

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        if not self._fetch_results:
            return []
        return self._fetch_results.pop(0)

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "UPDATE 1"

    def acquire(self):
        return _FakeAcquire(_FakeConnection(self))


class _RaisingThenSucceedingFakePool(_QueuedFakePool):
    """A _QueuedFakePool whose first real (non-set_config) fetchrow call
    raises `first_call_error`, then behaves exactly like the base class
    (serving `fetchrow_results` in order) on every subsequent call --
    stands in for update_task's UPDATE call raising a Postgres FK
    violation on its first attempt, so the retry-with-nulled-actors path
    can be proven without a real database."""

    def __init__(self, *, first_call_error: Exception, fetchrow_results=None):
        super().__init__(fetchrow_results=fetchrow_results)
        self._first_call_error = first_call_error
        self.fetchrow_attempt_count = 0

    async def fetchrow(self, query, *args):
        self.fetchrow_attempt_count += 1
        if self.fetchrow_attempt_count == 1:
            self.fetchrow_calls.append((query, args))
            raise self._first_call_error
        return await super().fetchrow(query, *args)


def _task_row(**overrides) -> dict:
    row = {
        "id": "task-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "project_id": "proj-1",
        "title": "Ship the widget",
        "description": "Build and ship it.",
        "status": "todo",
        "priority": 0,
        "parent_task_id": None,
        "assignee_agent_id": None,
        "assignee_user_id": None,
        "created_by": "user-1",
        "completed_by_user_id": None,
        "completed_by_agent_id": None,
        "completed_at": None,
        "due_at": None,
        "plan": [],
        "metadata": {},
        "created_at": "2026-07-30T00:00:00Z",
        "updated_at": "2026-07-30T00:00:00Z",
    }
    row.update(overrides)
    return row


# ── Layer 1: unit tests against the fake pool ─────────────────────────────


class RowToTaskCompletionAttributionFieldsTests(unittest.TestCase):
    """The read side -- _row_to_task must surface the three completed_by_*
    fields, and degrade to None (not raise) when a database predating this
    migration returns no key for them at all."""

    def test_stamped_agent_completion_round_trips(self):
        task = project_tasks_service._row_to_task(
            _task_row(status="done", completed_by_agent_id="agent-1", completed_at="2026-07-31T00:00:00Z")
        )
        self.assertEqual(task["completed_by_agent_id"], "agent-1")
        self.assertIsNone(task["completed_by_user_id"])
        self.assertEqual(task["completed_at"], "2026-07-31T00:00:00Z")

    def test_stamped_user_completion_round_trips(self):
        task = project_tasks_service._row_to_task(
            _task_row(status="done", completed_by_user_id="user-9", completed_at="2026-07-31T00:00:00Z")
        )
        self.assertEqual(task["completed_by_user_id"], "user-9")
        self.assertIsNone(task["completed_by_agent_id"])

    def test_unstamped_task_reports_none_for_all_three(self):
        task = project_tasks_service._row_to_task(_task_row())
        self.assertIsNone(task["completed_by_user_id"])
        self.assertIsNone(task["completed_by_agent_id"])
        self.assertIsNone(task["completed_at"])

    def test_missing_columns_degrade_to_none_rather_than_raising(self):
        """A database that has not yet had migrations/add_task_completion_
        attribution.sql applied returns no completed_by_* keys at all --
        this must read as "nothing recorded" rather than raising, the same
        deploy-before-migrate posture every sibling column already takes."""
        row = _task_row()
        del row["completed_by_user_id"]
        del row["completed_by_agent_id"]
        del row["completed_at"]
        task = project_tasks_service._row_to_task(row)
        self.assertIsNone(task["completed_by_user_id"])
        self.assertIsNone(task["completed_by_agent_id"])
        self.assertIsNone(task["completed_at"])


class UpdateTaskActorValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_both_actor_kinds_at_once(self):
        """Mirrors assign_task/assign_task_to_user's own mutual-exclusivity
        posture at the Python layer, backstopped by the real CHECK
        constraint proven in layer 3 below."""
        with self.assertRaises(ValueError):
            await project_tasks_service.update_task(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                task_id="task-1",
                status="done",
                actor_user_id="user-9",
                actor_agent_id="agent-1",
            )

    async def test_no_database_returns_none_without_touching_a_pool(self):
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            result = await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="done", actor_user_id="user-9",
            )
        self.assertIsNone(result)


class UpdateTaskSqlWiringTests(unittest.IsolatedAsyncioTestCase):
    """Proves the UPDATE statement update_task builds actually carries the
    transition-detection CASE expressions and threads the actor ids into
    them in the right parameter positions. This is a WIRING proof, not a
    semantic one -- a fake pool echoes back whatever row it is handed, it
    cannot evaluate a real CASE/IS DISTINCT FROM expression. The semantic
    proof (a real transition stamps, a no-op re-patch doesn't, leaving
    'done' clears) is layer 3, against real Postgres."""

    async def test_status_done_patch_sends_actor_user_id_in_the_query(self):
        pool = _QueuedFakePool(fetchrow_results=[_task_row(status="done", completed_by_user_id="user-9")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="done", actor_user_id="user-9",
            )
        self.assertEqual(task["completed_by_user_id"], "user-9")
        update_query, update_args = pool.fetchrow_calls[-1]
        self.assertIn("completed_by_user_id = CASE", update_query)
        self.assertIn("completed_by_agent_id = CASE", update_query)
        self.assertIn("completed_at = CASE", update_query)
        self.assertIn("IS DISTINCT FROM 'done'", update_query)
        # $10 is actor_user_id, $11 is actor_agent_id -- positions 9/10
        # (0-indexed) of the args tuple that follows tenant/workspace/task_id.
        self.assertEqual(update_args[9], "user-9")
        self.assertIsNone(update_args[10])

    async def test_status_done_patch_sends_actor_agent_id_in_the_query(self):
        pool = _QueuedFakePool(fetchrow_results=[_task_row(status="done", completed_by_agent_id="agent-1")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="done", actor_agent_id="agent-1",
            )
        update_query, update_args = pool.fetchrow_calls[-1]
        self.assertEqual(update_args[9], None)
        self.assertEqual(update_args[10], "agent-1")

    async def test_no_extra_round_trip_on_the_well_formed_happy_path(self):
        """The FK backstop (see the next test) must never cost the ordinary
        case a round trip -- a single-item fetchrow queue is enough for a
        successful done-transition with an actor, exactly the same call
        budget update_task always had. This is also what protects a fake-
        pool caller that queues an EXACT number of expected round trips
        (see test_project_task_native_tools.py's own project_task__update
        tests) from breaking because this feature quietly added one."""
        pool = _QueuedFakePool(fetchrow_results=[_task_row(status="done", completed_by_agent_id="agent-1")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="done", actor_agent_id="agent-1",
            )
        self.assertEqual(len(pool.fetchrow_calls), 1)

    async def test_fk_rejected_actor_id_degrades_to_no_stamp_rather_than_crashing(self):
        """THE backstop's whole point: a done-transition naming an actor id
        that Postgres's own FK rejects (stale, or a test/tool caller that
        never actually provisioned the agent row -- see
        test_task_subtasks_and_labels.py's AgentEndToEndReachabilityTests,
        which deliberately never inserts a workspace_agent_installs row)
        must still complete the task -- it just records no attribution,
        rather than the FK rejecting the write and the entire status
        change failing with it."""
        pool = _RaisingThenSucceedingFakePool(
            first_call_error=Exception(
                'insert or update on table "project_tasks" violates foreign key constraint '
                '"project_tasks_completed_by_agent_id_fkey"'
            ),
            fetchrow_results=[_task_row(status="done")],  # the RETRY's RETURNING -- no stamp
        )
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="done", actor_agent_id="ghost-agent",
            )
        self.assertEqual(task["status"], "done")
        self.assertIsNone(task["completed_by_agent_id"])
        # The retry's own args carry NULL for both actor slots.
        update_query, update_args = pool.fetchrow_calls[-1]
        self.assertIsNone(update_args[9])
        self.assertIsNone(update_args[10])
        self.assertEqual(pool.fetchrow_attempt_count, 2)

    async def test_unrelated_db_error_is_not_swallowed(self):
        """The backstop is narrow -- it only catches the two specific
        completed_by_* constraint names. Any other failure (a genuinely
        broken connection, an unrelated constraint) must still propagate,
        not be silently retried away."""
        pool = _RaisingThenSucceedingFakePool(
            first_call_error=Exception("connection to server was lost"),
            fetchrow_results=[_task_row(status="done")],
        )
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            with self.assertRaises(Exception) as ctx:
                await project_tasks_service.update_task(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="done", actor_agent_id="agent-1",
                )
        self.assertIn("connection to server was lost", str(ctx.exception))

    async def test_title_only_patch_sends_null_status_and_null_actors(self):
        """A plain field patch (no status in the call at all) must send
        $6=NULL -- the CASE expressions' own WHEN guards ($6 = 'done' /
        $6 IS NOT NULL) both read as false against a NULL, which is what
        makes a title-only patch a real no-op against every completed_by_*
        column at the SQL level, not just by convention."""
        pool = _QueuedFakePool(fetchrow_results=[_task_row(title="New title")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", title="New title",
            )
        update_query, update_args = pool.fetchrow_calls[-1]
        self.assertIsNone(update_args[5])  # $6: status
        self.assertIsNone(update_args[9])  # $10: actor_user_id
        self.assertIsNone(update_args[10])  # $11: actor_agent_id


# ── Layer 2: schema / DDL guards (no database needed) ──────────────────────


class CompletionAttributionSchemaContractTests(unittest.TestCase):
    def test_fks_set_null_and_never_cascade(self):
        """Deleting a user or agent must ORPHAN the historical attribution
        record back to "unknown", never cascade-delete the task -- the same
        orphaning decision assignee_user_id/assignee_agent_id's own FKs
        already make."""
        migration = ATTRIBUTION_MIGRATION.read_text()
        mirror = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        # Two different DDL shapes -- a standalone ALTER ... ADD CONSTRAINT
        # ... FOREIGN KEY in the migration, an inline column-level
        # REFERENCES in the CREATE TABLE mirror -- so each source gets its
        # own exact-shape assertion rather than one generic substring
        # (control_plane_repository.py also has an unrelated pre-existing
        # `completed_by_user_id` column on a totally different table --
        # external_user_privacy_requests -- so a loose substring check
        # would pass even if this migration's own FK line were wrong).
        self.assertIn(
            "FOREIGN KEY (completed_by_user_id) REFERENCES users(id) ON DELETE SET NULL", migration,
        )
        self.assertIn(
            "FOREIGN KEY (completed_by_agent_id) REFERENCES workspace_agent_installs(id)", migration,
        )
        self.assertIn(
            "completed_by_user_id TEXT NULL REFERENCES users(id) ON DELETE SET NULL", mirror,
        )
        self.assertIn(
            "completed_by_agent_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL", mirror,
        )
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            for line in sql.splitlines():
                if "completed_by_" in line and "REFERENCES" in line:
                    with self.subTest(source=name, line=line.strip()):
                        self.assertNotIn("CASCADE", line.upper())

    def test_mutual_exclusivity_check_is_in_both_sources(self):
        migration = ATTRIBUTION_MIGRATION.read_text()
        mirror = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            with self.subTest(source=name):
                self.assertIn(
                    "CHECK (completed_by_agent_id IS NULL OR completed_by_user_id IS NULL)", sql,
                )

    def test_migration_is_idempotent_purely_additive_and_touches_no_row(self):
        sql = ATTRIBUTION_MIGRATION.read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS completed_by_user_id", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS completed_by_agent_id", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS completed_at", sql)
        self.assertIn("DROP CONSTRAINT IF EXISTS project_tasks_completed_by_user_id_fkey", sql)
        self.assertIn("DROP CONSTRAINT IF EXISTS project_tasks_completed_by_agent_id_fkey", sql)
        self.assertIn("DROP CONSTRAINT IF EXISTS project_tasks_completed_by_single_actor_check", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("DROP COLUMN", upper)
        self.assertNotIn("DELETE FROM", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("UPDATE PROJECT_TASKS SET", upper)
        # No backfill: nothing in this migration may write a value into any
        # of the three new columns for an existing row.
        self.assertNotIn("SET completed_", upper)

    def test_self_heal_block_exists_and_is_guarded_against_crashing_bootstrap(self):
        source = (REPO_ROOT / "server_modules" / "control_plane_repository.py").read_text()
        marker = "ADD COLUMN IF NOT EXISTS completed_by_user_id TEXT NULL"
        self.assertIn(marker, source)
        idx = source.index(marker)
        window = source[max(0, idx - 400) : idx + 2200]
        self.assertIn("try:", window)
        self.assertIn("except Exception as exc:", window)
        self.assertIn("never let this crash bootstrap", window)
        self.assertIn("project_tasks_completed_by_single_actor_check", window)


# ── Layer 3: real Postgres, skipped when unreachable ───────────────────────


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


async def _pool_or_none():
    if not _database_url_available():
        return None
    try:
        return await control_plane_repository.ensure_control_plane_schema()
    except Exception:  # noqa: BLE001 — no database is a skip, not a failure
        return None


_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset or down)"


def _run(coro):
    from server_modules import sync_asyncio_bridge

    return sync_asyncio_bridge.run_coro_sync(coro)


class _BridgeAsyncTestCase(unittest.TestCase):
    def _maybe_await(self, value):
        return _run(value) if inspect.iscoroutine(value) else value

    def _callTestMethod(self, method):
        self._maybe_await(method())

    def setUp(self):
        self._maybe_await(self.async_setup())

    def tearDown(self):
        self._maybe_await(self.async_teardown())

    async def async_setup(self):
        return None

    async def async_teardown(self):
        return None


class CompletionAttributionDatabaseTests(_BridgeAsyncTestCase):
    """Against real SQL. Each test mints its own tenant/workspace/project/
    user/agent ids so runs never collide with other concurrent test runs
    against the same shared test database, and tearDown removes exactly
    what it made."""

    async def async_setup(self):
        self.pool = await _pool_or_none()
        if self.pool is None:
            self.skipTest(_NO_PG_REASON)
        suffix = uuid.uuid4().hex[:10]
        self.tenant_id = f"t_test_{suffix}"
        self.workspace_id = f"ws_test_{suffix}"
        self.project_id = f"proj_test_{suffix}"
        self.user_id = f"user_test_{suffix}"
        self.agent_def_id = f"agentdef_test_{suffix}"
        self.agent_def_version_id = f"agentdefver_test_{suffix}"
        self.agent_install_id = f"agent_test_{suffix}"
        await self.pool.execute(
            """
            INSERT INTO projects (id, tenant_id, workspace_id, name, slug)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO NOTHING
            """,
            self.project_id, self.tenant_id, self.workspace_id, "Test project", self.project_id,
        )
        await self.pool.execute(
            """
            INSERT INTO users (id, tenant_id, workspace_id, email)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO NOTHING
            """,
            self.user_id, self.tenant_id, self.workspace_id, f"{self.user_id}@example.com",
        )
        # update_task's defensive existence guard (mirrors assign_task_to_
        # user's own _workspace_user_exists check) requires an ACTIVE
        # workspace_memberships row, not just a bare `users` row -- see
        # test_project_tasks_human_assignee.py's identical setup.
        await self.pool.execute(
            """
            INSERT INTO workspace_memberships (id, tenant_id, workspace_id, user_id, role, status)
            VALUES ($1, $2, $3, $4, 'member', 'active')
            ON CONFLICT (id) DO NOTHING
            """,
            f"membership_{suffix}", self.tenant_id, self.workspace_id, self.user_id,
        )
        await self.pool.execute(
            """
            INSERT INTO agent_definitions (id, tenant_id, workspace_id, slug, name)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO NOTHING
            """,
            self.agent_def_id, self.tenant_id, self.workspace_id, self.agent_def_id, "Test agent",
        )
        await self.pool.execute(
            """
            INSERT INTO agent_definition_versions (id, tenant_id, workspace_id, agent_definition_id, version_number)
            VALUES ($1, $2, $3, $4, 1)
            ON CONFLICT (id) DO NOTHING
            """,
            self.agent_def_version_id, self.tenant_id, self.workspace_id, self.agent_def_id,
        )
        await self.pool.execute(
            """
            INSERT INTO workspace_agent_installs
                (id, tenant_id, workspace_id, agent_definition_id, agent_definition_version_id, project_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO NOTHING
            """,
            self.agent_install_id, self.tenant_id, self.workspace_id,
            self.agent_def_id, self.agent_def_version_id, self.project_id,
        )
        self.task = await project_tasks_service.create_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            project_id=self.project_id, title="Real DB task",
        )

    async def async_teardown(self):
        await self.pool.execute(
            "DELETE FROM projects WHERE tenant_id = $1 AND workspace_id = $2",
            self.tenant_id, self.workspace_id,
        )
        await self.pool.execute(
            "DELETE FROM agent_definitions WHERE tenant_id = $1 AND workspace_id = $2",
            self.tenant_id, self.workspace_id,
        )
        await self.pool.execute(
            "DELETE FROM users WHERE tenant_id = $1 AND workspace_id = $2",
            self.tenant_id, self.workspace_id,
        )

    async def test_human_close_stamps_user(self):
        task = await project_tasks_service.update_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            status="done", actor_user_id=self.user_id,
        )
        self.assertEqual(task["completed_by_user_id"], self.user_id)
        self.assertIsNone(task["completed_by_agent_id"])
        self.assertIsNotNone(task["completed_at"])

    async def test_agent_close_stamps_agent(self):
        task = await project_tasks_service.update_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            status="done", actor_agent_id=self.agent_install_id,
        )
        self.assertEqual(task["completed_by_agent_id"], self.agent_install_id)
        self.assertIsNone(task["completed_by_user_id"])
        self.assertIsNotNone(task["completed_at"])

    async def test_title_only_patch_stamps_nothing(self):
        # Sanity: a fresh task is not done and carries no attribution yet.
        before = await project_tasks_service.get_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
        )
        self.assertIsNone(before["completed_by_user_id"])
        self.assertIsNone(before["completed_at"])

        task = await project_tasks_service.update_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            title="A renamed task", actor_user_id=self.user_id,
        )
        self.assertEqual(task["title"], "A renamed task")
        self.assertIsNone(task["completed_by_user_id"])
        self.assertIsNone(task["completed_by_agent_id"])
        self.assertIsNone(task["completed_at"])

    async def test_redundant_done_to_done_repatch_does_not_restamp(self):
        """The first close wins -- a later done->done re-patch (even by a
        different actor) must not steal attribution from whoever actually
        completed it."""
        first = await project_tasks_service.update_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            status="done", actor_user_id=self.user_id,
        )
        self.assertEqual(first["completed_by_user_id"], self.user_id)
        first_completed_at = first["completed_at"]

        second = await project_tasks_service.update_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            status="done", actor_agent_id=self.agent_install_id,
        )
        self.assertEqual(second["completed_by_user_id"], self.user_id)
        self.assertIsNone(second["completed_by_agent_id"])
        self.assertEqual(second["completed_at"], first_completed_at)

    async def test_reopening_clears_it(self):
        done = await project_tasks_service.update_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            status="done", actor_user_id=self.user_id,
        )
        self.assertEqual(done["completed_by_user_id"], self.user_id)

        reopened = await project_tasks_service.update_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
            status="todo",
        )
        self.assertIsNone(reopened["completed_by_user_id"])
        self.assertIsNone(reopened["completed_by_agent_id"])
        self.assertIsNone(reopened["completed_at"])

    async def test_the_check_constraint_rejects_both_actors_at_once(self):
        """THE mandatory proof: the database itself, not just update_task's
        own ValueError guard, refuses a row with both a human and an agent
        completer. Bypasses project_tasks_service entirely -- a raw UPDATE
        straight at the table."""
        import asyncpg

        with self.assertRaises(asyncpg.PostgresError):
            await self.pool.execute(
                """
                UPDATE project_tasks
                SET completed_by_user_id = $3, completed_by_agent_id = $4
                WHERE tenant_id = $1 AND id = $2
                """,
                self.tenant_id, self.task["id"], self.user_id, self.agent_install_id,
            )
        after = await project_tasks_service.get_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
        )
        self.assertIsNone(after["completed_by_user_id"])
        self.assertIsNone(after["completed_by_agent_id"])


if __name__ == "__main__":
    unittest.main()
