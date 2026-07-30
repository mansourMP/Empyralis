"""Human task assignability (MAN-64/MAN-70) -- migrations/add_task_human_
assignee.sql, project_tasks_service.assign_task_to_user, and the schema
mirror in control_plane_repository.py.

A task must be assignable to a human user OR an agent, never both. This
file proves, in order:

1. UNIT (fake pool, no database needed): assign_task_to_user is assign_
   task's structural twin -- same validation shape, same {"task",
   "wake_request", "wake_error"} return contract -- with the ONE deliberate
   difference the whole feature turns on: it never calls
   bounded_scheduler_service, in any branch. People are not woken by
   schedulers. Also proves the mutual-exclusivity WRITE behavior on both
   sides: assigning a human clears any agent assignee and vice versa.

2. SCHEMA / DDL guards (no database needed): the migration and the
   control_plane_repository.py mirror agree on the column, the FK, the
   CHECK, and the "purely additive, no rewrite" shape every sibling
   project_tasks migration promises. Also the specific bootstrap-ordering
   hazard documented in control_plane_repository.py (an index on a
   column the schema blob does not create on an existing database must
   live in the guarded self-heal, not the top-level CREATE INDEX list) --
   test_task_subtasks_and_labels.py already proved this bug is real for
   parent_task_id; assignee_user_id is exactly the same shape of column.

3. REAL POSTGRES, skipped when no database is reachable (mirrors
   test_task_subtasks_and_labels.py's own opt-in convention: `DATABASE_URL`
   must already be exported by the caller, never read from .env or
   mutated into os.environ here -- see that file's _database_url_available
   docstring for exactly why). This is the only layer that can actually
   prove the CHECK constraint -- project_tasks_single_assignee_check --
   rejects a row with both assignee columns set; a fake pool can only
   pretend to have a CHECK constraint.
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
HUMAN_ASSIGNEE_MIGRATION = REPO_ROOT / "migrations" / "add_task_human_assignee.sql"


# ── Fake pool plumbing -- identical shape to test_project_tasks.py's own
# _QueuedFakePool/_task_row (kept as a local copy rather than an import: the
# two files are independent test suites and this keeps this file runnable on
# its own, matching test_task_subtasks_and_labels.py's own "self-contained"
# convention). ────────────────────────────────────────────────────────────


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
        "due_at": None,
        "plan": [],
        "metadata": {},
        "created_at": "2026-07-30T00:00:00Z",
        "updated_at": "2026-07-30T00:00:00Z",
    }
    row.update(overrides)
    return row


# ── Layer 1: unit tests against the fake pool ─────────────────────────────


class RowToTaskAssigneeFieldsTests(unittest.TestCase):
    """The read side -- _row_to_task must surface assignee_user_id and the
    derived assignee_type convenience field ("agent" | "user" | None),
    distinguishing a human assignee from an agent one for every reader
    downstream (the HTTP payload, the frontend) without each of them having
    to re-derive "which column is set" for itself."""

    def test_agent_assignee_reports_type_agent(self):
        task = project_tasks_service._row_to_task(_task_row(assignee_agent_id="agent-1"))
        self.assertEqual(task["assignee_agent_id"], "agent-1")
        self.assertIsNone(task["assignee_user_id"])
        self.assertEqual(task["assignee_type"], "agent")

    def test_human_assignee_reports_type_user(self):
        task = project_tasks_service._row_to_task(_task_row(assignee_user_id="user-9"))
        self.assertIsNone(task["assignee_agent_id"])
        self.assertEqual(task["assignee_user_id"], "user-9")
        self.assertEqual(task["assignee_type"], "user")

    def test_unassigned_reports_type_none(self):
        task = project_tasks_service._row_to_task(_task_row())
        self.assertIsNone(task["assignee_agent_id"])
        self.assertIsNone(task["assignee_user_id"])
        self.assertIsNone(task["assignee_type"])

    def test_missing_assignee_user_id_key_degrades_to_unassigned(self):
        """A database that has not yet had migrations/add_task_human_
        assignee.sql applied returns no `assignee_user_id` key at all --
        this must read as "no human assignee" rather than raising, the same
        deploy-before-migrate posture every sibling column
        (priority/parent_task_id) already takes."""
        row = _task_row()
        del row["assignee_user_id"]
        task = project_tasks_service._row_to_task(row)
        self.assertIsNone(task["assignee_user_id"])


class AssignTaskToUserTests(unittest.IsolatedAsyncioTestCase):
    """assign_task_to_user is assign_task's structural twin -- same
    validation shape, same return contract -- with exactly one deliberate
    behavioral difference: it must NEVER wake anyone."""

    async def test_raises_when_task_missing(self):
        pool = _QueuedFakePool(fetchrow_results=[None])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            with self.assertRaises(ValueError):
                await project_tasks_service.assign_task_to_user(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="ghost", user_id="user-1",
                )

    async def test_raises_when_user_missing(self):
        # get_task succeeds, _workspace_user_exists's lookup returns None.
        pool = _QueuedFakePool(fetchrow_results=[_task_row(), None])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            with self.assertRaises(ValueError):
                await project_tasks_service.assign_task_to_user(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", user_id="ghost-user",
                )

    async def test_requires_a_user_id(self):
        with self.assertRaises(ValueError):
            await project_tasks_service.assign_task_to_user(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", user_id="   ",
            )

    async def test_sets_human_assignee_never_schedules_a_wakeup_and_never_flips_status(self):
        """THE mandatory proof for this half of the feature: assigning a
        task to a human must not trigger an agent wakeup. Both scheduler
        functions are mocked to RAISE if called at all -- not just asserted
        not-called afterward -- so a future edit that adds a call inside a
        try/except (silently swallowing the "it was never supposed to be
        called" signal) still fails this test loudly."""
        pool = _QueuedFakePool(
            fetchrow_results=[
                _task_row(status="todo"),  # get_task
                {"user_id": "user-9"},  # _workspace_user_exists
                _task_row(status="todo", assignee_user_id="user-9"),  # UPDATE ... RETURNING
            ]
        )
        never_called = AsyncMock(side_effect=AssertionError("must never be called for a human assignment"))
        with (
            patch(
                "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=pool),
            ),
            patch("server_modules.bounded_scheduler_service.schedule_task_assigned_wakeup", new=never_called),
            patch("server_modules.bounded_scheduler_service.schedule_task_commented_wakeup", new=never_called),
        ):
            result = await project_tasks_service.assign_task_to_user(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                task_id="task-1",
                user_id="user-9",
                triggered_by="owner-user",
            )

        self.assertEqual(result["task"]["assignee_user_id"], "user-9")
        self.assertIsNone(result["task"]["assignee_agent_id"])
        # No auto-start: a human assignee has no immediate-start guarantee
        # the way an about-to-be-woken agent does, so the status is left
        # exactly where it was (see the docstring on assign_task_to_user).
        self.assertEqual(result["task"]["status"], "todo")
        self.assertIsNone(result["wake_request"])
        self.assertIsNone(result["wake_error"])
        never_called.assert_not_awaited()
        # The UPDATE went through the human assignee column and explicitly
        # cleared the agent one -- mutual exclusivity, enforced on write.
        update_query, update_args = pool.fetchrow_calls[-1]
        self.assertIn("SET assignee_user_id = $4", update_query)
        self.assertIn("assignee_agent_id = NULL", update_query)
        # No status CASE expression at all on this path (unlike assign_task).
        self.assertNotIn("CASE WHEN status", update_query)
        self.assertEqual(update_args[:4], ("tenant-1", "ws-1", "task-1", "user-9"))

    async def test_assigning_a_human_clears_a_previous_agent_assignee(self):
        """Re-assigning a task that an agent previously held over to a human
        hands it over cleanly -- the UPDATE always clears assignee_agent_id,
        not just when it happens to already be NULL."""
        pool = _QueuedFakePool(
            fetchrow_results=[
                _task_row(assignee_agent_id="agent-1"),  # get_task: was agent-assigned
                {"user_id": "user-9"},
                _task_row(assignee_user_id="user-9"),
            ]
        )
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            result = await project_tasks_service.assign_task_to_user(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", user_id="user-9",
            )
        self.assertEqual(result["task"]["assignee_user_id"], "user-9")
        self.assertIsNone(result["task"]["assignee_agent_id"])
        update_query, _ = pool.fetchrow_calls[-1]
        self.assertIn("assignee_agent_id = NULL", update_query)


class AssignTaskClearsHumanAssigneeTests(unittest.IsolatedAsyncioTestCase):
    """The other direction: assign_task (the pre-existing AGENT path) must
    keep firing the wakeup exactly as before (proven already by test_
    project_tasks.py's AssignTaskTests -- unchanged, still passing, byte-
    for-byte), AND must now also clear assignee_user_id so the mutual-
    exclusivity invariant holds no matter which direction a task is
    reassigned from."""

    async def test_assign_task_update_clears_assignee_user_id(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                _task_row(status="todo", assignee_user_id="user-9"),  # get_task: was human-assigned
                {"id": "agent-1"},  # _agent_install_exists
                _task_row(status="in_progress", assignee_agent_id="agent-1"),
            ]
        )
        with (
            patch(
                "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=pool),
            ),
            patch(
                "server_modules.bounded_scheduler_service.schedule_task_assigned_wakeup",
                new=AsyncMock(return_value={"id": "wake-1", "status": "pending"}),
            ) as wake_mock,
        ):
            result = await project_tasks_service.assign_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", agent_id="agent-1",
            )
        self.assertEqual(result["task"]["assignee_agent_id"], "agent-1")
        # Proof #2 of the mandatory pair: assigning to an AGENT still DOES
        # schedule a wakeup (proof #1, the human path never scheduling one,
        # is test_sets_human_assignee_never_schedules_a_wakeup_and_never_
        # flips_status above).
        wake_mock.assert_awaited_once()
        update_query, _ = pool.fetchrow_calls[-1]
        self.assertIn("SET assignee_agent_id = $4", update_query)
        self.assertIn("assignee_user_id = NULL", update_query)


# ── Layer 2: schema / DDL guards (no database needed) ─────────────────────


class HumanAssigneeSchemaContractTests(unittest.TestCase):
    def test_fk_sets_null_and_never_cascades(self):
        """Deleting a user must ORPHAN their task assignments back to
        "unassigned", never cascade-delete the task -- a task is real work,
        exactly the same orphaning decision assignee_agent_id's own FK
        already makes for a deleted agent."""
        migration = HUMAN_ASSIGNEE_MIGRATION.read_text()
        mirror = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            with self.subTest(source=name):
                self.assertIn("REFERENCES users(id) ON DELETE SET NULL", sql)
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            for line in sql.splitlines():
                if "assignee_user_id" in line and "REFERENCES" in line:
                    with self.subTest(source=name, line=line.strip()):
                        self.assertNotIn("CASCADE", line.upper())

    def test_mutual_exclusivity_check_is_in_both_sources(self):
        migration = HUMAN_ASSIGNEE_MIGRATION.read_text()
        mirror = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        for name, sql in (("migration", migration), ("schema mirror", mirror)):
            with self.subTest(source=name):
                self.assertIn(
                    "CHECK (assignee_agent_id IS NULL OR assignee_user_id IS NULL)", sql,
                )

    def test_assignee_user_index_is_not_in_the_bootstrap_schema_blob(self):
        """Same bootstrap-ordering hazard test_task_subtasks_and_labels.py
        already proved for idx_project_tasks_parent: CONTROL_PLANE_SCHEMA_
        SQL's `CREATE TABLE IF NOT EXISTS project_tasks` is a no-op on an
        already-provisioned database, so assignee_user_id only arrives
        later from the guarded self-heal ALTER. An index on that column
        placed in the top-level schema blob would run BEFORE the column
        exists and crash bootstrap with `column "assignee_user_id" does not
        exist` on every already-provisioned database."""
        self.assertNotIn(
            "CREATE INDEX IF NOT EXISTS idx_project_tasks_assignee_user",
            control_plane_repository.CONTROL_PLANE_SCHEMA_SQL,
            "idx_project_tasks_assignee_user must not be created in "
            "CONTROL_PLANE_SCHEMA_SQL -- it indexes a column that block "
            "does not create on an existing database.",
        )
        source = (REPO_ROOT / "server_modules" / "control_plane_repository.py").read_text()
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_project_tasks_assignee_user", source)

    def test_migration_is_idempotent_purely_additive_and_touches_no_row(self):
        sql = HUMAN_ASSIGNEE_MIGRATION.read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS assignee_user_id", sql)
        self.assertIn("DROP CONSTRAINT IF EXISTS project_tasks_assignee_user_id_fkey", sql)
        self.assertIn("DROP CONSTRAINT IF EXISTS project_tasks_single_assignee_check", sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_project_tasks_assignee_user", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("DROP COLUMN", upper)
        self.assertNotIn("DELETE FROM", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("UPDATE PROJECT_TASKS SET", upper)

    def test_self_heal_block_exists_and_is_guarded_against_crashing_bootstrap(self):
        """The mirror in ensure_control_plane_schema() must exist, and must
        be wrapped in the same try/except LOGGER.warning pattern every
        sibling migration block uses -- a raw, unguarded ALTER here would
        take the whole process down on boot for any database this
        particular block fails against."""
        source = (REPO_ROOT / "server_modules" / "control_plane_repository.py").read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS assignee_user_id TEXT NULL", source)
        marker = "ADD COLUMN IF NOT EXISTS assignee_user_id TEXT NULL"
        idx = source.index(marker)
        # The guarding try/except must appear within a short window before
        # and after the ALTER itself.
        window = source[max(0, idx - 400) : idx + 1600]
        self.assertIn("try:", window)
        self.assertIn("except Exception as exc:", window)
        self.assertIn("never let this crash bootstrap", window)


# ── Layer 3: real Postgres, skipped when unreachable ──────────────────────


def _database_url_available() -> bool:
    """Opt-in, and ONLY from an already-exported DATABASE_URL -- see
    test_task_subtasks_and_labels.py's own _database_url_available for the
    full reasoning (no .env reading, no os.environ mutation, so a plain
    `pytest` run leaves every later test's global state untouched)."""
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
    """Same persistent bridge loop test_task_subtasks_and_labels.py uses --
    see that file's _run docstring for why this is not a stylistic choice
    (the cached asyncpg pool is bound to the loop that created it)."""
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


class HumanAssigneeDatabaseTests(_BridgeAsyncTestCase):
    """Against real SQL -- the CHECK constraint is a property of actual
    Postgres, which a fake pool can only pretend to have. Each test mints
    its own tenant/workspace/project/user/agent ids so runs never collide,
    and tearDown removes exactly what it made."""

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
        # assign_task_to_user validates the assignee is an ACTIVE workspace
        # MEMBER (_workspace_user_exists queries workspace_memberships, not
        # bare `users`) -- mirrors _agent_install_exists checking the agent
        # belongs to this workspace, not just that it exists anywhere.
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
        # project_tasks and workspace_agent_installs cascade from projects;
        # users/agent_definitions do not, so those go explicitly.
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

    async def test_the_check_constraint_rejects_both_assignees_at_once(self):
        """THE mandatory proof: the database itself, not just the service
        layer's discipline about which column it writes, refuses a row with
        both a human and an agent assignee. Bypasses project_tasks_service
        entirely -- a raw UPDATE straight at the table -- so this is
        provably the CHECK constraint doing the rejecting, not a validation
        branch in application code that a future direct-SQL write path
        could route around."""
        import asyncpg

        with self.assertRaises(asyncpg.PostgresError):
            await self.pool.execute(
                """
                UPDATE project_tasks
                SET assignee_agent_id = $3, assignee_user_id = $4
                WHERE tenant_id = $1 AND id = $2
                """,
                self.tenant_id, self.task["id"], self.agent_install_id, self.user_id,
            )
        # And the task itself is untouched -- a rejected statement writes
        # nothing at all, not a half-applied row.
        after = await project_tasks_service.get_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
        )
        self.assertIsNone(after["assignee_agent_id"])
        self.assertIsNone(after["assignee_user_id"])

    async def test_assigning_a_human_then_an_agent_switches_cleanly(self):
        """End-to-end through the real service functions and real SQL:
        assigning a human, then reassigning to an agent, must never trip the
        CHECK constraint from the application's own side (assign_task's
        UPDATE clears assignee_user_id itself) and must leave exactly one
        assignee set at a time."""
        result = await project_tasks_service.assign_task_to_user(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id,
            task_id=self.task["id"], user_id=self.user_id,
        )
        self.assertEqual(result["task"]["assignee_user_id"], self.user_id)
        self.assertIsNone(result["task"]["assignee_agent_id"])
        self.assertIsNone(result["wake_request"])
        self.assertIsNone(result["wake_error"])

        with patch(
            "server_modules.bounded_scheduler_service.schedule_task_assigned_wakeup",
            new=AsyncMock(return_value={"id": "wake-1", "status": "pending"}),
        ):
            result = await project_tasks_service.assign_task(
                tenant_id=self.tenant_id, workspace_id=self.workspace_id,
                task_id=self.task["id"], agent_id=self.agent_install_id,
            )
        self.assertEqual(result["task"]["assignee_agent_id"], self.agent_install_id)
        self.assertIsNone(result["task"]["assignee_user_id"])

        final = await project_tasks_service.get_task(
            tenant_id=self.tenant_id, workspace_id=self.workspace_id, task_id=self.task["id"],
        )
        self.assertEqual(final["assignee_agent_id"], self.agent_install_id)
        self.assertIsNone(final["assignee_user_id"])


if __name__ == "__main__":
    unittest.main()
