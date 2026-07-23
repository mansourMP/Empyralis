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
        "status": "open",
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
        self.assertEqual(task["status"], "open")
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
        # Status is normalized (lowercased) before hitting the query.
        self.assertEqual(args[-1], "open")

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
        self.assertEqual(args[-2:], ("proj-1", "open"))

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

    async def test_assign_task_sets_assignee_flips_open_to_in_progress_and_schedules_wakeup(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                _task_row(status="open"),  # get_task
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
        self.assertEqual(update_args, ("tenant-1", "ws-1", "task-1", "agent-1"))

    async def test_assign_task_reports_wake_error_without_undoing_assignment(self):
        """A scheduler failure must not roll back the (already-durable,
        already the thing the owner cares about) assignment itself."""
        pool = _QueuedFakePool(
            fetchrow_results=[
                _task_row(status="open"),
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
