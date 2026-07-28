"""Tests for the Tasks -> Agents backend foundation (docs/design/
tasks-to-agents-research.md Section 4.6, steps 1-3):

1. project_tasks_service.py -- the task table's CRUD + the one shared
   assign_task() entry point.
2. bounded_scheduler_service.schedule_task_assigned_wakeup -- the new
   "task_assigned" trigger reason, built on the existing propose/schedule
   path (mirrors test_bounded_scheduler_service.py's own mocking style for
   maybe_schedule_event_trigger/propose_self_wakeup).
3. runtime_heartbeat_service.build_heartbeat_turn_request -- threading a
   task_assigned wake request's task_id/title/description into the turn's
   metadata and seed-prompt message.
4. direct_chat_generation_service.stream_provider_backed_direct_chat --
   seeding current_plan from the task's persisted plan at turn start and
   persisting it back at turn end, run against the REAL generator (only the
   provider stream and DB calls are mocked), following
   test_continuous_work_plan_loop.py's harness pattern.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_trace_service
from server_modules import bounded_scheduler_service
from server_modules import direct_chat_generation_service
from server_modules import project_tasks_service
from server_modules import runtime_heartbeat_service


# ── Fake pool plumbing (mirrors test_agent_registry_repository.py's
# _FakePool/_FakeConnection pattern: plain pool.fetch/fetchrow/execute,
# queued per call so a single fake pool instance can stand in for the
# several sequential DB round-trips assign_task makes) ─────────────────────


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
        "assignee_agent_id": None,
        "created_by": "user-1",
        "due_at": None,
        "plan": [],
        "metadata": {},
        "created_at": "2026-07-23T00:00:00Z",
        "updated_at": "2026-07-23T00:00:00Z",
    }
    row.update(overrides)
    return row


class ProjectTasksCrudTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_task_requires_title(self):
        with self.assertRaises(ValueError):
            await project_tasks_service.create_task(
                tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1", title="  ",
            )

    async def test_create_task_requires_project_id(self):
        with self.assertRaises(ValueError):
            await project_tasks_service.create_task(
                tenant_id="tenant-1", workspace_id="ws-1", project_id="", title="Do the thing",
            )

    async def test_create_task_without_postgres_raises_durable_config_error(self):
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(project_tasks_service.control_plane_repository.runtime_db.DurableRuntimeConfigurationError):
                await project_tasks_service.create_task(
                    tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1", title="Do the thing",
                )

    async def test_create_task_inserts_and_returns_row(self):
        pool = _QueuedFakePool(fetchrow_results=[_task_row()])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.create_task(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                project_id="proj-1",
                title="Ship the widget",
                description="Build and ship it.",
                created_by="user-1",
            )

        self.assertEqual(task["id"], "task-1")
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["assignee_agent_id"], None)
        self.assertEqual(len(pool.fetchrow_calls), 1)
        query, args = pool.fetchrow_calls[0]
        self.assertIn("INSERT INTO project_tasks", query)
        self.assertEqual(args[3], "proj-1")
        self.assertEqual(args[4], "Ship the widget")

    async def test_get_task_scopes_by_tenant_and_workspace(self):
        pool = _QueuedFakePool(fetchrow_results=[_task_row()])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.get_task(tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1")

        self.assertEqual(task["id"], "task-1")
        query, args = pool.fetchrow_calls[0]
        self.assertIn("WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3", query)
        self.assertEqual(args, ("tenant-1", "ws-1", "task-1"))

    async def test_get_task_missing_returns_none(self):
        pool = _QueuedFakePool(fetchrow_results=[None])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.get_task(tenant_id="tenant-1", workspace_id="ws-1", task_id="nope")
        self.assertIsNone(task)

    async def test_list_tasks_filters_by_project_assignee_and_status(self):
        pool = _QueuedFakePool(fetch_results=[[_task_row()]])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            rows = await project_tasks_service.list_tasks(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                project_id="proj-1",
                assignee_agent_id="agent-1",
                status="OPEN",
            )

        self.assertEqual(len(rows), 1)
        query, args = pool.fetch_calls[0]
        self.assertIn("project_id = $3", query)
        self.assertIn("assignee_agent_id = $4", query)
        self.assertIn("status = $5", query)
        # Status is normalized before hitting the query: lowercased, AND the
        # legacy 'open' spelling mapped forward to its new name 'todo'. A
        # caller still filtering by 'open' therefore gets the rows it means.
        self.assertEqual(args[-1], "todo")

    async def test_update_task_rejects_invalid_status(self):
        pool = _QueuedFakePool()
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            with self.assertRaises(ValueError):
                await project_tasks_service.update_task(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="closed",
                )

    async def test_update_task_patches_fields(self):
        pool = _QueuedFakePool(fetchrow_results=[_task_row(status="in_progress")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="in_progress",
            )
        self.assertEqual(task["status"], "in_progress")

    async def test_get_task_plan_returns_empty_list_when_task_missing(self):
        pool = _QueuedFakePool(fetchrow_results=[None])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            plan = await project_tasks_service.get_task_plan(tenant_id="tenant-1", workspace_id="ws-1", task_id="ghost")
        self.assertEqual(plan, [])

    async def test_get_task_plan_returns_persisted_plan(self):
        pool = _QueuedFakePool(fetchrow_results=[_task_row(plan=[{"id": "p1", "title": "Step 1", "status": "active"}])])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            plan = await project_tasks_service.get_task_plan(tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1")
        self.assertEqual(plan, [{"id": "p1", "title": "Step 1", "status": "active"}])

    async def test_set_task_plan_writes_json_and_returns_updated_row(self):
        new_plan = [{"id": "p1", "title": "Step 1", "status": "done"}]
        pool = _QueuedFakePool(fetchrow_results=[_task_row(plan=new_plan)])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.set_task_plan(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", plan=new_plan,
            )
        self.assertEqual(task["plan"], new_plan)
        query, args = pool.fetchrow_calls[0]
        self.assertIn("SET plan = $4::jsonb", query)
        self.assertIn('"title": "Step 1"', args[-1])


class TaskPriorityTests(unittest.IsolatedAsyncioTestCase):
    """Task priority on Linear's five-level scale
    (migrations/add_task_priority.sql):

        0 = none (default) | 1 = urgent | 2 = high | 3 = medium | 4 = low

    The property that matters most here is the INVERSION: 1 is the most
    urgent and 4 the least, matching what Linear stores and what the Linear
    MCP API accepts. Getting that backwards would be silent and disastrous
    (every 'urgent' card rendering as 'low'), so it is pinned explicitly
    rather than only implied by the sort test.
    """

    async def test_encoding_matches_linears_scale_exactly(self):
        self.assertEqual(project_tasks_service.TASK_PRIORITY_NONE, 0)
        self.assertEqual(project_tasks_service.TASK_PRIORITY_URGENT, 1)
        self.assertEqual(project_tasks_service.TASK_PRIORITY_HIGH, 2)
        self.assertEqual(project_tasks_service.TASK_PRIORITY_MEDIUM, 3)
        self.assertEqual(project_tasks_service.TASK_PRIORITY_LOW, 4)
        self.assertEqual(
            project_tasks_service.TASK_PRIORITY_LABELS,
            {0: "none", 1: "urgent", 2: "high", 3: "medium", 4: "low"},
        )
        self.assertEqual(project_tasks_service.VALID_TASK_PRIORITIES, {0, 1, 2, 3, 4})

    async def test_default_priority_is_none_zero(self):
        self.assertEqual(project_tasks_service.DEFAULT_TASK_PRIORITY, 0)

    async def test_create_task_defaults_priority_to_zero(self):
        """A task created with no priority argument is untriaged (0), and
        that 0 is written explicitly rather than left to the column
        default -- so the value is the same whether or not the database has
        had the migration's DEFAULT applied."""
        pool = _QueuedFakePool(fetchrow_results=[_task_row()])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.create_task(
                tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1", title="Ship the widget",
            )
        self.assertEqual(task["priority"], 0)
        self.assertEqual(task["priority_label"], "none")
        _query, args = pool.fetchrow_calls[0]
        self.assertEqual(args[8], 0)

    async def test_create_task_accepts_every_valid_priority(self):
        for priority in project_tasks_service.TASK_PRIORITY_ORDER:
            with self.subTest(priority=priority):
                pool = _QueuedFakePool(fetchrow_results=[_task_row(priority=priority)])
                with patch(
                    "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                    new=AsyncMock(return_value=pool),
                ):
                    task = await project_tasks_service.create_task(
                        tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
                        title="Ship the widget", priority=priority,
                    )
                self.assertEqual(task["priority"], priority)
                _query, args = pool.fetchrow_calls[0]
                self.assertEqual(args[8], priority)

    async def test_update_task_accepts_every_valid_priority(self):
        for priority in project_tasks_service.TASK_PRIORITY_ORDER:
            with self.subTest(priority=priority):
                pool = _QueuedFakePool(fetchrow_results=[_task_row(priority=priority)])
                with patch(
                    "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                    new=AsyncMock(return_value=pool),
                ):
                    task = await project_tasks_service.update_task(
                        tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", priority=priority,
                    )
                self.assertEqual(task["priority"], priority)
                # Reached the UPDATE verbatim, not coerced to a default.
                _query, args = pool.fetchrow_calls[0]
                self.assertEqual(args[8], priority)

    async def test_out_of_range_priority_is_rejected_on_create(self):
        for bad in (5, -1, 99):
            with self.subTest(bad=bad):
                pool = _QueuedFakePool(fetchrow_results=[_task_row()])
                with patch(
                    "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                    new=AsyncMock(return_value=pool),
                ):
                    with self.assertRaises(ValueError):
                        await project_tasks_service.create_task(
                            tenant_id="tenant-1", workspace_id="ws-1", project_id="proj-1",
                            title="Ship the widget", priority=bad,
                        )
                # Rejected before any INSERT was attempted.
                self.assertEqual(pool.fetchrow_calls, [])

    async def test_out_of_range_priority_is_rejected_on_update(self):
        for bad in (5, -1, "sometime_soon"):
            with self.subTest(bad=bad):
                pool = _QueuedFakePool(fetchrow_results=[_task_row()])
                with patch(
                    "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                    new=AsyncMock(return_value=pool),
                ):
                    with self.assertRaises(ValueError):
                        await project_tasks_service.update_task(
                            tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", priority=bad,
                        )
                self.assertEqual(pool.fetchrow_calls, [])

    async def test_rejection_message_spells_out_the_inversion(self):
        """The error an agent reads has to say WHICH end of the scale is
        urgent -- that is the single thing a caller getting this error is
        most likely to have gotten wrong."""
        pool = _QueuedFakePool()
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            with self.assertRaises(ValueError) as ctx:
                await project_tasks_service.update_task(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", priority=7,
                )
        message = str(ctx.exception)
        self.assertIn("1 = urgent", message)
        self.assertIn("4 = low", message)
        self.assertIn("0 = none", message)

    async def test_priority_names_are_accepted_as_input(self):
        """The integer is canonical, but a caller (human or model) that
        says 'urgent' should not be punished for it."""
        for name, expected in (
            ("urgent", 1), ("HIGH", 2), ("medium", 3), ("low", 4), ("none", 0),
            ("p1", 1), ("critical", 1), ("no_priority", 0), ("2", 2),
        ):
            with self.subTest(name=name):
                pool = _QueuedFakePool(fetchrow_results=[_task_row(priority=expected)])
                with patch(
                    "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                    new=AsyncMock(return_value=pool),
                ):
                    await project_tasks_service.update_task(
                        tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", priority=name,
                    )
                _query, args = pool.fetchrow_calls[0]
                self.assertEqual(args[8], expected)

    async def test_boolean_priority_is_rejected_not_read_as_urgent(self):
        """`True` is an int in Python. Letting it through would silently
        make a mis-passed flag mean 'urgent'."""
        pool = _QueuedFakePool()
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            with self.assertRaises(ValueError):
                await project_tasks_service.update_task(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", priority=True,
                )

    async def test_omitting_priority_leaves_it_untouched(self):
        """None means "don't patch this field" -- distinct from 0, which
        means "clear it back to untriaged"."""
        pool = _QueuedFakePool(fetchrow_results=[_task_row(priority=1)])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="done",
            )
        query, args = pool.fetchrow_calls[0]
        self.assertIn("priority = COALESCE($9::smallint, priority)", query)
        self.assertIsNone(args[8])

    async def test_empty_string_priority_is_read_as_omitted_not_invalid(self):
        """Some model clients emit "" for an optional parameter they chose
        to skip. Failing the whole turn over that would be worse than
        reading it as "leave the priority alone"."""
        pool = _QueuedFakePool(fetchrow_results=[_task_row(priority=1)])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", priority="  ",
            )
        _query, args = pool.fetchrow_calls[0]
        self.assertIsNone(args[8])
        self.assertEqual(task["priority"], 1)  # untouched

    async def test_clearing_priority_to_zero_is_a_real_patch(self):
        pool = _QueuedFakePool(fetchrow_results=[_task_row(priority=0)])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", priority=0,
            )
        _query, args = pool.fetchrow_calls[0]
        self.assertEqual(args[8], 0)  # NOT None -- the write actually happens
        self.assertEqual(task["priority"], 0)

    async def test_priority_survives_a_full_round_trip(self):
        """Write 'urgent', read it back on every read path -- get_task,
        list_tasks and list_my_tasks all carry the value AND its label."""
        for priority, label in project_tasks_service.TASK_PRIORITY_LABELS.items():
            with self.subTest(priority=priority):
                row = _task_row(priority=priority)
                pool = _QueuedFakePool(
                    fetchrow_results=[row, row],
                    fetch_results=[[row], [row]],
                )
                with patch(
                    "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                    new=AsyncMock(return_value=pool),
                ):
                    written = await project_tasks_service.update_task(
                        tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", priority=priority,
                    )
                    fetched = await project_tasks_service.get_task(
                        tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
                    )
                    listed = await project_tasks_service.list_tasks(tenant_id="tenant-1", workspace_id="ws-1")
                    mine = await project_tasks_service.list_my_tasks(
                        tenant_id="tenant-1", workspace_id="ws-1", agent_id="agent-1",
                    )
                for task in (written, fetched, listed[0], mine[0]):
                    self.assertEqual(task["priority"], priority)
                    self.assertEqual(task["priority_label"], label)

    async def test_a_row_from_an_unmigrated_database_reads_as_none(self):
        """A SELECT against a database that predates the priority column
        returns no `priority` key at all. That must read as 0/'none', not
        blow up -- the deploy-before-migrate window has to stay survivable."""
        row = _task_row()
        row.pop("priority")
        pool = _QueuedFakePool(fetchrow_results=[row])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.get_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
            )
        self.assertEqual(task["priority"], 0)
        self.assertEqual(task["priority_label"], "none")

    async def test_every_read_query_selects_the_priority_column(self):
        """The column has to be in the SELECT list or the field silently
        reads as 'none' forever, no matter what was written."""
        row = _task_row(priority=1)
        pool = _QueuedFakePool(fetchrow_results=[row], fetch_results=[[row], [row]])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.get_task(tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1")
            await project_tasks_service.list_tasks(tenant_id="tenant-1", workspace_id="ws-1")
            await project_tasks_service.list_my_tasks(
                tenant_id="tenant-1", workspace_id="ws-1", agent_id="agent-1",
            )
        for query, _args in pool.fetchrow_calls + pool.fetch_calls:
            self.assertIn("status, priority", " ".join(query.split()))


class TaskPrioritySortTests(unittest.IsolatedAsyncioTestCase):
    """"Urgent first, none last" is NOT a plain ORDER BY priority, because 0
    means UNSET and has to sink to the bottom rather than float to the top.
    These pin the expression that gets that right."""

    async def test_default_sort_is_unchanged_recency(self):
        pool = _QueuedFakePool(fetch_results=[[_task_row()]])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.list_tasks(tenant_id="tenant-1", workspace_id="ws-1")
        query, _args = pool.fetch_calls[0]
        normalized = " ".join(query.split())
        self.assertIn("ORDER BY created_at DESC", normalized)
        self.assertNotIn("NULLIF(priority, 0)", normalized)

    async def test_priority_sort_puts_urgent_first_and_untriaged_last(self):
        pool = _QueuedFakePool(fetch_results=[[_task_row()]])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.list_tasks(
                tenant_id="tenant-1", workspace_id="ws-1", sort="priority",
            )
        query, _args = pool.fetch_calls[0]
        normalized = " ".join(query.split())
        # ASC over NULLIF(priority, 0) means 1 (urgent) sorts before 4 (low);
        # NULLS LAST is what pushes 0/none to the bottom instead of the top.
        self.assertIn("ORDER BY NULLIF(priority, 0) ASC NULLS LAST, created_at DESC", normalized)

    async def test_priority_sort_keeps_list_my_tasks_mine_first_split(self):
        """An agent asking "what next" wants ITS OWN urgent work ahead of
        somebody else's unclaimed urgent work, not interleaved with it."""
        pool = _QueuedFakePool(fetch_results=[[_task_row()]])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.list_my_tasks(
                tenant_id="tenant-1", workspace_id="ws-1", agent_id="agent-1", sort="priority",
            )
        query, _args = pool.fetch_calls[0]
        normalized = " ".join(query.split())
        self.assertIn(
            "ORDER BY (assignee_agent_id IS NOT NULL) DESC, NULLIF(priority, 0) ASC NULLS LAST, created_at DESC",
            normalized,
        )

    async def test_unknown_sort_falls_back_to_the_default_instead_of_erroring(self):
        """A bad sort key is a display preference, not a correctness
        question -- it must not fail a whole board load."""
        pool = _QueuedFakePool(fetch_results=[[_task_row()]])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            rows = await project_tasks_service.list_tasks(
                tenant_id="tenant-1", workspace_id="ws-1", sort="; DROP TABLE project_tasks",
            )
        self.assertEqual(len(rows), 1)
        query, _args = pool.fetch_calls[0]
        normalized = " ".join(query.split())
        self.assertIn("ORDER BY created_at DESC", normalized)
        self.assertNotIn("DROP TABLE", normalized)


class TaskPrioritySchemaMirrorTests(unittest.IsolatedAsyncioTestCase):
    """The standalone migration and control_plane_repository's own schema
    blueprint have to agree -- a fresh database provisioned from the
    blueprint and an existing one healed by the migration must end up with
    the same column. This is the convention add_task_status_vocabulary.sql
    established; forgetting the mirror is how the two silently diverge."""

    def test_migration_file_declares_the_column_and_range_check(self):
        from pathlib import Path

        sql = (Path(__file__).resolve().parents[2] / "migrations" / "add_task_priority.sql").read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS priority SMALLINT NOT NULL DEFAULT 0", sql)
        self.assertIn("CHECK (priority BETWEEN 0 AND 4)", sql)
        # Idempotent: re-running must not fail on the constraint already
        # existing (the reason it is dropped-then-re-added by name).
        self.assertIn("DROP CONSTRAINT IF EXISTS project_tasks_priority_check", sql)

    def test_new_database_blueprint_declares_the_same_column(self):
        from server_modules import control_plane_repository as repository

        sql = " ".join(repository.CONTROL_PLANE_SCHEMA_SQL.split())
        self.assertIn("priority SMALLINT NOT NULL DEFAULT 0", sql)
        self.assertIn("CHECK (priority BETWEEN 0 AND 4)", sql)


class TaskStatusVocabularyTests(unittest.IsolatedAsyncioTestCase):
    """The seven-status, Linear-style kanban vocabulary
    (migrations/add_task_status_vocabulary.sql):

        backlog | todo | in_progress | awaiting_input | blocked | in_review | done

    `backlog` and `in_review` are new; `open` was renamed to `todo` and must
    keep working as an alias rather than erroring, since older clients,
    cached agent tool schemas and un-migrated DB rows can all still say it.
    """

    async def test_vocabulary_is_exactly_the_seven_kanban_columns(self):
        self.assertEqual(
            project_tasks_service.TASK_STATUS_ORDER,
            ("backlog", "todo", "in_progress", "awaiting_input", "blocked", "in_review", "done"),
        )
        self.assertEqual(
            project_tasks_service.VALID_TASK_STATUSES,
            set(project_tasks_service.TASK_STATUS_ORDER),
        )

    async def test_default_status_is_todo_not_open(self):
        self.assertEqual(project_tasks_service.DEFAULT_TASK_STATUS, "todo")

    async def test_every_new_status_is_accepted_and_written_through(self):
        """Each of the seven — including the two new ones — must survive
        update_task and reach the UPDATE's status parameter verbatim."""
        for status in project_tasks_service.TASK_STATUS_ORDER:
            with self.subTest(status=status):
                pool = _QueuedFakePool(fetchrow_results=[_task_row(status=status)])
                with patch(
                    "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                    new=AsyncMock(return_value=pool),
                ):
                    task = await project_tasks_service.update_task(
                        tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status=status,
                    )
                self.assertEqual(task["status"], status)
                _query, args = pool.fetchrow_calls[0]
                self.assertEqual(args[5], status)

    async def test_in_review_is_a_real_settable_status(self):
        """The point of the whole vocabulary change: agent-completed work has
        somewhere to wait for a human instead of jumping straight to done."""
        pool = _QueuedFakePool(fetchrow_results=[_task_row(status="in_review")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="in_review",
            )
        self.assertEqual(task["status"], "in_review")

    async def test_open_is_accepted_as_an_alias_and_stored_as_todo(self):
        """Backward compatibility: a caller still sending the old name must
        not error, and must land on `todo` — not be written through as a
        literal 'open' the new CHECK constraint would reject."""
        pool = _QueuedFakePool(fetchrow_results=[_task_row(status="todo")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="open",
            )
        self.assertEqual(task["status"], "todo")
        _query, args = pool.fetchrow_calls[0]
        self.assertEqual(args[5], "todo")

    async def test_open_alias_is_case_and_whitespace_insensitive_too(self):
        pool = _QueuedFakePool(fetchrow_results=[_task_row(status="todo")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="  OPEN  ",
            )
        _query, args = pool.fetchrow_calls[0]
        self.assertEqual(args[5], "todo")

    async def test_legacy_open_row_read_back_normalizes_to_todo(self):
        """A row written before the forward migration still literally holds
        'open'. Reads must present it as 'todo' rather than leaking a status
        that is no longer in the vocabulary."""
        pool = _QueuedFakePool(fetchrow_results=[_task_row(status="open")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.get_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
            )
        self.assertEqual(task["status"], "todo")

    async def test_invalid_statuses_are_still_rejected_loudly(self):
        """Unchanged guarantee: an unknown status raises rather than being
        silently coerced. 'closed'/'cancelled'/'review' are the near-misses
        most likely to be guessed by an agent or an older client."""
        for status in ("closed", "cancelled", "review", "in-review", "backlogged", "in progress", "ready", ""):
            with self.subTest(status=status):
                pool = _QueuedFakePool()
                with patch(
                    "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                    new=AsyncMock(return_value=pool),
                ):
                    with self.assertRaises(ValueError):
                        await project_tasks_service.update_task(
                            tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status=status,
                        )
                self.assertEqual(pool.fetchrow_calls, [])

    async def test_punctuation_and_spacing_are_sanitized_not_rejected(self):
        """Pre-existing normalizer behavior, documented rather than changed:
        characters outside [a-z_] are stripped before the vocabulary check,
        so "To Do" resolves to `todo`. Note this is stripping, not fuzzy
        matching — "in progress" collapses to "inprogress" and is still
        rejected by the test above."""
        pool = _QueuedFakePool(fetchrow_results=[_task_row(status="todo")])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.update_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="To Do",
            )
        _query, args = pool.fetchrow_calls[0]
        self.assertEqual(args[5], "todo")

    async def test_rejection_message_names_the_new_vocabulary(self):
        pool = _QueuedFakePool()
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            with self.assertRaises(ValueError) as raised:
                await project_tasks_service.update_task(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", status="cancelled",
                )
        message = str(raised.exception)
        for status in project_tasks_service.TASK_STATUS_ORDER:
            self.assertIn(status, message)

    async def test_assigning_a_backlog_task_starts_it(self):
        """`backlog` is new, so it needs the same "assignment starts the
        work" treatment `todo` gets — otherwise dragging a task out of the
        backlog onto an agent would leave it sitting in backlog forever."""
        self.assertIn("backlog", project_tasks_service.UNSTARTED_TASK_STATUSES)
        self.assertIn("todo", project_tasks_service.UNSTARTED_TASK_STATUSES)
        # The legacy spelling stays covered for the window between deploying
        # this code and applying the forward migration.
        self.assertIn("open", project_tasks_service.UNSTARTED_TASK_STATUSES)
        # Work already underway (or finished) is never rewound by assignment.
        for status in ("in_progress", "awaiting_input", "blocked", "in_review", "done"):
            self.assertNotIn(status, project_tasks_service.UNSTARTED_TASK_STATUSES)


class ListMyTasksTests(unittest.IsolatedAsyncioTestCase):
    """list_my_tasks backs the MCP `empyralis_list_my_tasks` tool: tasks
    assigned to the caller (external_agent_id or agent_id) OR unassigned
    (backlog) tasks -- and, same as list_tasks, always scoped to
    (tenant_id, workspace_id) so a caller from one workspace can never see
    another's rows."""

    async def test_filters_by_external_agent_id_or_unassigned(self):
        pool = _QueuedFakePool(fetch_results=[[_task_row(assignee_agent_id="ext_agent_aaa")]])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            rows = await project_tasks_service.list_my_tasks(
                tenant_id="tenant-1", workspace_id="ws-1", external_agent_id="ext_agent_aaa",
            )
        self.assertEqual(len(rows), 1)
        query, args = pool.fetch_calls[0]
        self.assertIn("WHERE tenant_id = $1 AND workspace_id = $2", query)
        self.assertIn("(assignee_agent_id = $3 OR assignee_agent_id IS NULL)", query)
        self.assertEqual(args, ("tenant-1", "ws-1", "ext_agent_aaa"))

    async def test_no_caller_identity_still_scopes_to_unassigned_only(self):
        """No external_agent_id/agent_id -- e.g. an OAuth session that hasn't
        minted a roster identity -- must degrade to "unassigned only", never
        raise and never silently return every task in the workspace."""
        pool = _QueuedFakePool(fetch_results=[[]])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.list_my_tasks(tenant_id="tenant-1", workspace_id="ws-1")
        query, args = pool.fetch_calls[0]
        self.assertIn("(FALSE OR assignee_agent_id IS NULL)", query)
        self.assertEqual(args, ("tenant-1", "ws-1"))

    async def test_optional_project_and_status_filters(self):
        pool = _QueuedFakePool(fetch_results=[[]])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            await project_tasks_service.list_my_tasks(
                tenant_id="tenant-1", workspace_id="ws-1", external_agent_id="ext_agent_aaa",
                project_id="proj-1", status="OPEN",
            )
        query, args = pool.fetch_calls[0]
        self.assertIn("project_id = $4", query)
        self.assertIn("status = $5", query)
        # 'OPEN' -> 'todo': lowercased and alias-mapped, same as list_tasks.
        self.assertEqual(args[-2:], ("proj-1", "todo"))

    async def test_no_postgres_returns_empty_list_not_error(self):
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            rows = await project_tasks_service.list_my_tasks(tenant_id="tenant-1", workspace_id="ws-1")
        self.assertEqual(rows, [])


class AddTaskCommentTests(unittest.IsolatedAsyncioTestCase):
    """add_task_comment backs the MCP `empyralis_comment_on_task` tool.
    Comments live in task.metadata.comments (no new table -- see the
    function's own docstring) via a single atomic jsonb-append UPDATE."""

    async def test_requires_non_empty_body(self):
        with self.assertRaises(ValueError):
            await project_tasks_service.add_task_comment(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
                author_type="external_agent", author_id="ext_agent_aaa", body="   ",
            )

    async def test_without_postgres_raises_durable_config_error(self):
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(project_tasks_service.control_plane_repository.runtime_db.DurableRuntimeConfigurationError):
                await project_tasks_service.add_task_comment(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
                    author_type="external_agent", author_id="ext_agent_aaa", body="hello",
                )

    async def test_appends_comment_via_atomic_jsonb_update_and_scopes_by_workspace(self):
        pool = _QueuedFakePool(fetchrow_results=[_task_row(metadata={"comments": [{"body": "hello"}]})])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.add_task_comment(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1",
                author_type="external_agent", author_id="ext_agent_aaa", body="hello",
            )
        self.assertEqual(task["metadata"]["comments"], [{"body": "hello"}])
        query, args = pool.fetchrow_calls[0]
        self.assertIn("WHERE tenant_id = $1 AND workspace_id = $2 AND id = $3", query)
        self.assertIn("jsonb_set", query)
        self.assertEqual(args[:3], ("tenant-1", "ws-1", "task-1"))
        self.assertIn("ext_agent_aaa", args[3])  # the appended comment JSON carries the author id

    async def test_missing_task_returns_none(self):
        pool = _QueuedFakePool(fetchrow_results=[None])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            task = await project_tasks_service.add_task_comment(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="ghost",
                author_type="external_agent", author_id="ext_agent_aaa", body="hello",
            )
        self.assertIsNone(task)


class AssignTaskTests(unittest.IsolatedAsyncioTestCase):
    """assign_task is the ONE shared code path (docs/design/tasks-to-agents-
    research.md §2 pitfall #2) -- the API and a future @-mention resolver
    both call this, never two forks."""

    async def test_assign_task_raises_when_task_missing(self):
        pool = _QueuedFakePool(fetchrow_results=[None])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            with self.assertRaises(ValueError):
                await project_tasks_service.assign_task(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="ghost", agent_id="agent-1",
                )

    async def test_assign_task_raises_when_agent_missing(self):
        # get_task succeeds, _agent_install_exists's lookup returns None.
        pool = _QueuedFakePool(fetchrow_results=[_task_row(), None])
        with patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ):
            with self.assertRaises(ValueError):
                await project_tasks_service.assign_task(
                    tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", agent_id="ghost-agent",
                )

    async def test_assign_task_sets_assignee_flips_todo_to_in_progress_and_schedules_wakeup(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                _task_row(status="todo"),  # get_task
                {"id": "agent-1"},  # _agent_install_exists
                _task_row(status="in_progress", assignee_agent_id="agent-1"),  # UPDATE ... RETURNING
            ]
        )
        wake_mock = AsyncMock(return_value={"id": "wake-1", "status": "pending"})
        with (
            patch(
                "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
                new=AsyncMock(return_value=pool),
            ),
            patch("server_modules.bounded_scheduler_service.schedule_task_assigned_wakeup", new=wake_mock),
        ):
            result = await project_tasks_service.assign_task(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                task_id="task-1",
                agent_id="agent-1",
                triggered_by="owner-user",
            )

        self.assertEqual(result["task"]["assignee_agent_id"], "agent-1")
        self.assertEqual(result["task"]["status"], "in_progress")
        self.assertEqual(result["wake_request"], {"id": "wake-1", "status": "pending"})
        self.assertIsNone(result["wake_error"])
        wake_mock.assert_awaited_once_with(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            agent_id="agent-1",
            task_id="task-1",
            title="Ship the widget",
            description="Build and ship it.",
            triggered_by="owner-user",
        )
        # The UPDATE went through the assignee column, not a generic patch.
        update_query, update_args = pool.fetchrow_calls[-1]
        self.assertIn("SET assignee_agent_id = $4", update_query)
        self.assertEqual(update_args[:4], ("tenant-1", "ws-1", "task-1", "agent-1"))
        # $5 is the "not started yet" set the UPDATE flips to in_progress:
        # both new not-started columns, plus the legacy 'open' spelling so a
        # row written before the status migration still starts correctly.
        self.assertEqual(update_args[4], ["backlog", "todo", "open"])
        self.assertIn("status = ANY($5::text[])", update_query)

    async def test_assign_task_reports_wake_error_without_undoing_assignment(self):
        """A scheduler failure must not roll back the (already-durable,
        already the thing the owner cares about) assignment itself."""
        pool = _QueuedFakePool(
            fetchrow_results=[
                _task_row(status="todo"),
                {"id": "agent-1"},
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
                new=AsyncMock(side_effect=RuntimeError("scheduler unavailable")),
            ),
        ):
            result = await project_tasks_service.assign_task(
                tenant_id="tenant-1", workspace_id="ws-1", task_id="task-1", agent_id="agent-1",
            )

        self.assertEqual(result["task"]["assignee_agent_id"], "agent-1")
        self.assertIsNone(result["wake_request"])
        self.assertIn("scheduler unavailable", result["wake_error"])


class ScheduleTaskAssignedWakeupTests(unittest.IsolatedAsyncioTestCase):
    """Mirrors test_bounded_scheduler_service.py's own mocking style for
    maybe_schedule_event_trigger/propose_self_wakeup -- same _load_scheduler_
    scope/_persist_wakeup/_trigger_ambient_monitor seams, new trigger_kind."""

    async def test_requires_agent_task_id_and_title(self):
        with self.assertRaises(bounded_scheduler_service.SchedulerPolicyError):
            await bounded_scheduler_service.schedule_task_assigned_wakeup(
                tenant_id="tenant-1", workspace_id="ws-1", agent_id="", task_id="task-1", title="Do it",
            )

    async def test_persists_task_assigned_wake_request_and_triggers_monitor(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=0,
            quiet_hours_end=0,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(return_value={"id": "wake-3", "status": "pending"}),
            ) as persist_mock,
            patch(
                "server_modules.bounded_scheduler_service._trigger_ambient_monitor",
                return_value={"ok": True},
            ) as trigger_mock,
        ):
            record = await bounded_scheduler_service.schedule_task_assigned_wakeup(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="agent-1",
                task_id="task-1",
                title="Ship the widget",
                description="Build and ship it.",
                triggered_by="owner-user",
            )

        self.assertEqual(record, {"id": "wake-3", "status": "pending"})
        self.assertEqual(persist_mock.await_args.kwargs["trigger_kind"], "task_assigned")
        self.assertEqual(persist_mock.await_args.kwargs["approval_required"], False)
        self.assertEqual(persist_mock.await_args.kwargs["requested_by"], "owner-user")
        payload = persist_mock.await_args.kwargs["payload"]
        self.assertEqual(payload["agent_id"], "agent-1")
        self.assertEqual(payload["task_id"], "task-1")
        self.assertEqual(payload["task_title"], "Ship the widget")
        self.assertEqual(payload["task_description"], "Build and ship it.")
        trigger_mock.assert_called_once_with("ws-1")

    async def test_task_wake_ceiling_rejects_loudly_at_the_limit(self):
        """STEP 6 numeric backstop (agent-identity plan): once a task_id has
        already logged max_wakes_per_task_per_day() wake requests in the
        trailing 24h, the next request must be refused with a loud, explicit
        SchedulerPolicyError -- never a silent clamp/drop -- and must never
        reach _load_scheduler_scope/_persist_wakeup at all."""
        with (
            patch(
                "server_modules.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=bounded_scheduler_service.DEFAULT_MAX_WAKES_PER_TASK_PER_DAY),
            ) as count_mock,
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(side_effect=AssertionError("must not proceed past the wake ceiling")),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(side_effect=AssertionError("must not persist a wake past the ceiling")),
            ),
        ):
            with self.assertRaises(bounded_scheduler_service.SchedulerPolicyError) as raised:
                await bounded_scheduler_service.schedule_task_assigned_wakeup(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    agent_id="agent-1",
                    task_id="task-1",
                    title="Ship the widget",
                )
        self.assertIn("wake ceiling", str(raised.exception))
        self.assertEqual(count_mock.await_args.kwargs["task_id"], "task-1")

    async def test_task_wake_ceiling_allows_one_below_the_limit(self):
        """Sanity check for the ceiling above: strictly below the cap still
        proceeds and persists normally."""
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=0,
            quiet_hours_end=0,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=bounded_scheduler_service.DEFAULT_MAX_WAKES_PER_TASK_PER_DAY - 1),
            ),
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(return_value={"id": "wake-4", "status": "pending"}),
            ) as persist_mock,
            patch(
                "server_modules.bounded_scheduler_service._trigger_ambient_monitor",
                return_value={"ok": True},
            ),
        ):
            record = await bounded_scheduler_service.schedule_task_assigned_wakeup(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="agent-1",
                task_id="task-1",
                title="Ship the widget",
            )
        self.assertEqual(record, {"id": "wake-4", "status": "pending"})
        persist_mock.assert_awaited_once()


class BuildHeartbeatTurnRequestTaskThreadingTests(unittest.TestCase):
    """runtime_heartbeat_service.build_heartbeat_turn_request threading a
    task_assigned wake request's task_id/title/description into merged
    metadata and the seed-prompt message -- the ONLY seam that makes "task
    id in trace metadata" true for a scheduled wakeup."""

    def test_task_assigned_wake_request_threads_task_id_into_metadata_and_message(self):
        turn_request = runtime_heartbeat_service.build_heartbeat_turn_request(
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            tasks=[],
            metadata={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            pending_started=[],
            authority_tier="owner",
            wake_requests=[
                {
                    "id": "wake-1",
                    "trigger_kind": "task_assigned",
                    "summary": "Task assigned: Ship the widget",
                    "payload": {
                        "agent_id": "agent-1",
                        "task_id": "task-1",
                        "task_title": "Ship the widget",
                        "task_description": "Build and ship it.",
                    },
                }
            ],
        )

        self.assertEqual(turn_request["context_hints"]["metadata"]["task_id"], "task-1")
        self.assertEqual(turn_request["context_hints"]["metadata"]["assigned_task_title"], "Ship the widget")
        self.assertIn("Ship the widget", turn_request["message"])
        self.assertIn("Build and ship it.", turn_request["message"])

    def test_task_assigned_payload_arriving_as_json_string_still_threads_through(self):
        """A claimed wake request read straight back from the DB can carry
        `payload` as a raw JSON string when no jsonb codec is registered on
        that connection -- must not silently drop the task id."""
        turn_request = runtime_heartbeat_service.build_heartbeat_turn_request(
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            tasks=[],
            metadata={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            pending_started=[],
            authority_tier="owner",
            wake_requests=[
                {
                    "id": "wake-1",
                    "trigger_kind": "task_assigned",
                    "payload": '{"agent_id": "agent-1", "task_id": "task-2", "task_title": "Reply to the client"}',
                }
            ],
        )
        self.assertEqual(turn_request["context_hints"]["metadata"]["task_id"], "task-2")

    def test_ordinary_wake_request_never_stamps_task_id(self):
        """No behavior change for the (much more common) non-task-assigned
        wake requests -- event triggers and self-proposed wakeups must never
        pick up a task_id key at all."""
        turn_request = runtime_heartbeat_service.build_heartbeat_turn_request(
            build_inbound_agent_turn_request=lambda **kwargs: kwargs,
            tasks=[],
            metadata={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            pending_started=[],
            authority_tier="owner",
            wake_requests=[{"id": "wake-1", "trigger_kind": "event_trigger", "summary": "Something happened", "payload": {}}],
        )
        self.assertNotIn("task_id", turn_request["context_hints"]["metadata"])


# ── Plan seed/persist against the REAL generator (mirrors
# test_continuous_work_plan_loop.py's harness) ──────────────────────────────


def _round(*, provider: str, model: str, tool_calls: list | None = None, reply: str = "") -> list[dict]:
    return [
        {
            "type": "result",
            "reply": reply,
            "usage_masked": {"provider": provider},
            "provider": provider,
            "model": model,
            "attempted_providers": provider,
            "error": "",
            "tool_calls": tool_calls or [],
        }
    ]


def _update_plan_call(call_id: str, tasks: list[dict]) -> dict:
    return {"id": call_id, "name": "update_plan", "arguments": {"tasks": tasks}}


class _StreamRoundRecorder:
    def __init__(self, rounds: list[list[dict]]) -> None:
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append({})
        round_index = len(self.calls) - 1
        return iter(self._rounds[round_index])

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _TaskPlanTurnTestBase(unittest.TestCase):
    def _services(self, *, recorder: _StreamRoundRecorder):
        return direct_chat_generation_service.DirectChatGenerationServices(
            thinking_step_payload=lambda iteration, status, detail=None: {
                "type": "step", "iteration": iteration, "status": status, "detail": detail,
            },
            build_context_used=lambda **kwargs: kwargs,
            build_direct_tool_approval_response=lambda **kwargs: None,
            parse_tool_name=lambda name: tuple(str(name).split("__", 1)) if "__" in str(name) else ("", ""),
            tool_arguments_payload=lambda value: value if isinstance(value, dict) else {},
            parse_page_state=lambda value: {},
            direct_tool_step_payload=lambda connector_id, action_id, arguments, **kwargs: {
                "type": "step", "connector": connector_id, "action": action_id, "arguments": arguments, **kwargs,
            },
            execute_single_direct_tool_call=lambda **kwargs: "tool result payload",
            direct_tool_followup_message=lambda tool_name, result_text: f"{tool_name}: {result_text}",
            suggest_actions=lambda _message, _availability: [],
            clear_direct_tool_loop_state=lambda _session_key: None,
            persist_direct_chat_memory_best_effort=lambda **kwargs: None,
            persist_direct_chat_transcript_best_effort=lambda **kwargs: None,
            persist_direct_chat_hosted_usage_best_effort=lambda **kwargs: None,
            record_direct_tool_signature=lambda _session_key, _tool_call: False,
            direct_chat_error_reply=lambda error: f"Chat failed: {error}",
            capture_exception=lambda exc: None,
            generate_chat_reply_stream_with_provider_fallback=recorder,
        )

    def _run(self, *, recorder: _StreamRoundRecorder, session_ctx=None, trace_context=None):
        provider, model = "anthropic", "claude-sonnet-4-6"
        return list(
            direct_chat_generation_service.stream_provider_backed_direct_chat(
                services=self._services(recorder=recorder),
                context={"provider": provider, "tools": []},
                metadata={"provider": provider, "model": model, "tools": []},
                system_prompt="System prompt",
                normalized_workspace_id="ws-1",
                normalized_requested_provider=provider,
                normalized_requested_model=model,
                normalized_reasoning_effort=None,
                normalized_thread_id="thread-1",
                normalized_message="Work the assigned task.",
                compacted_prior_messages=[],
                prior_messages_used=False,
                history_mode="none",
                connected_systems=[],
                tool_capabilities=[],
                availability_payload={"ai_ready": True},
                tools=[],
                direct_chat_credentials={},
                proactive_suggestions=[],
                tool_loop_session_key="session-task-plan",
                fallback_reason=None,
                session_ctx=session_ctx,
                trace_context=trace_context,
                resolved_chat_max_iterations=5,
                direct_tool_result_summary_system_message="Summarize tool results.",
                assistant_plan_tools=[],
            )
        )


class SeedsAndPersistsAssignedTaskPlanTests(_TaskPlanTurnTestBase):
    def test_current_plan_seeds_from_the_assigned_tasks_persisted_plan(self):
        """current_plan starts from the task's OWN persisted plan (not [])
        when task_id/tenant_id are present in turn metadata -- proven by the
        seeded task's id surviving update_plan's title-match id-preservation
        logic, exactly like a same-turn repeat update_plan call would."""
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-1", workspace_id="ws-1", tenant_id="tenant-1",
            thread_id="thread-1", run_id=None, root_agent_id="sage",
        )
        recorder = _StreamRoundRecorder([
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                _update_plan_call("call_0", [{"title": "Step 1", "status": "active"}])
            ]),
            _round(provider="anthropic", model="claude-sonnet-4-6", reply="Working on it."),
        ])

        with (
            patch(
                "server_modules.project_tasks_service.get_task_plan",
                new=AsyncMock(return_value=[{"id": "seed-id-1", "title": "Step 1", "status": "pending"}]),
            ),
            patch("server_modules.project_tasks_service.set_task_plan", new=AsyncMock(return_value=None)),
            patch.object(agent_trace_service.control_plane_repository, "append_agent_trace_event", new=AsyncMock(return_value=None)),
        ):
            events = self._run(
                recorder=recorder,
                session_ctx={"metadata": {"task_id": "task-1", "tenant_id": "tenant-1"}},
                trace_context=trace_context,
            )

        plan_updated_events = [
            event["payload"]["data"]["tasks"]
            for event in events
            if event.get("type") == "trace" and event["payload"].get("event_type") == "plan.updated"
        ]
        self.assertEqual(len(plan_updated_events), 1)
        # "Step 1" keeps the SEEDED id -- proof current_plan did not start
        # empty for this turn.
        self.assertEqual(plan_updated_events[0][0]["id"], "seed-id-1")
        self.assertEqual(events[-1]["payload"]["reply"], "Working on it.")

    def test_final_plan_is_persisted_back_onto_the_task_at_turn_end(self):
        trace_context = agent_trace_service.TraceContext(
            trace_id="trace-1", workspace_id="ws-1", tenant_id="tenant-1",
            thread_id="thread-1", run_id=None, root_agent_id="sage",
        )
        recorder = _StreamRoundRecorder([
            _round(provider="anthropic", model="claude-sonnet-4-6", tool_calls=[
                _update_plan_call("call_0", [{"title": "Step 1", "status": "done"}])
            ]),
            _round(provider="anthropic", model="claude-sonnet-4-6", reply="Done."),
        ])

        with (
            patch("server_modules.project_tasks_service.get_task_plan", new=AsyncMock(return_value=[])),
            patch("server_modules.project_tasks_service.set_task_plan", new=AsyncMock(return_value=None)) as set_plan_mock,
            patch.object(agent_trace_service.control_plane_repository, "append_agent_trace_event", new=AsyncMock(return_value=None)),
        ):
            self._run(
                recorder=recorder,
                session_ctx={"metadata": {"task_id": "task-9", "tenant_id": "tenant-1"}},
                trace_context=trace_context,
            )

        set_plan_mock.assert_awaited_once()
        kwargs = set_plan_mock.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-1")
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["task_id"], "task-9")
        self.assertEqual([t["title"] for t in kwargs["plan"]], ["Step 1"])
        self.assertEqual([t["status"] for t in kwargs["plan"]], ["done"])

    def test_ordinary_turn_without_a_task_id_never_touches_project_tasks_service(self):
        """No behavior change for every turn that isn't working an assigned
        task -- the overwhelming majority of turns."""
        recorder = _StreamRoundRecorder([
            _round(provider="anthropic", model="claude-sonnet-4-6", reply="Just a normal reply."),
        ])

        with (
            patch("server_modules.project_tasks_service.get_task_plan", new=AsyncMock()) as get_plan_mock,
            patch("server_modules.project_tasks_service.set_task_plan", new=AsyncMock()) as set_plan_mock,
        ):
            events = self._run(recorder=recorder, session_ctx=None, trace_context=None)

        get_plan_mock.assert_not_awaited()
        set_plan_mock.assert_not_awaited()
        self.assertEqual(events[-1]["payload"]["reply"], "Just a normal reply.")


if __name__ == "__main__":
    unittest.main()
