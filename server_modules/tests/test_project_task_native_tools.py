"""2026-07-25: the platform-agent side of the project task board — closing
the loop the "task_assigned" wakeup opens (docs/design/tasks-to-agents-
research.md Section 4.6). Before this, project_tasks_service.py's create/
update/comment/assign functions existed and were reachable by an EXTERNAL
MCP client (mcp_server.py's empyralis_* tools), but a platform agent had no
native tool that reached them during its own turn — the only writer of
task.status in the whole codebase was the human-facing PATCH endpoint
(routes_fleet.py), so an assigned agent could track its own checklist
(update_plan/current_plan) but never signal completion back to the board.

This file exercises the REAL, unmocked native dispatch (tool name ->
direct_chat_operator_binding_service.parse_tool_name -> skills_service.
execute_single_direct_tool_call -> the real project_tasks_service
functions) for the six new project_task__* tools, against a fake Postgres
pool (mirrors test_project_tasks.py's _QueuedFakePool).

The isolation property under test throughout: every project_task__* tool is
scoped to the CALLING agent's own project (resolved server-side from
session identity, never a caller-supplied project_id) — an agent cannot
read, edit, comment on, or assign a task belonging to a different project,
matching the locked "project is the collaboration boundary" ruling.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_execution_service
from server_modules import skills_service


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    """acquire() target control_plane_repository.rls_fetchrow/rls_fetch open
    now that project_tasks is FORCE RLS (MAN-109). Delegates straight back
    to the owning _QueuedFakePool's own queued fetchrow/fetch so its
    fetchrow_calls/fetch_calls keep observing the exact real query and args
    they always did; the RLS scope-setting execute() call is swallowed here
    rather than recorded, since nothing in this file asserts on it.

    skills_service.execute_single_direct_tool_call also fires a best-effort
    (try/except-wrapped, pre-existing, unrelated to project_tasks/RLS)
    activity_ledger_events write+read on every dispatch, via the same
    control_plane_repository._scoped_connection -> pool.acquire() path.
    Before pool.acquire() existed on this fake, that write always raised
    AttributeError and was silently swallowed, so it never touched
    fetchrow_results. Now that acquire() is real, it would otherwise steal
    queued results this file's tests reserved for the actual
    project_tasks_service business calls (agent_project_id, create_task,
    etc.) -- so activity_ledger_events reads are answered directly here,
    without draining the shared queue."""

    def __init__(self, pool: "_QueuedFakePool") -> None:
        self._pool = pool

    async def fetchrow(self, query, *args):
        if "activity_ledger_events" in query:
            return {"id": args[0] if args else "aevt_fake", "status": "logged"}
        return await self._pool.fetchrow(query, *args)

    async def fetch(self, query, *args):
        return await self._pool.fetch(query, *args)

    async def execute(self, query, *args):
        return "SELECT 1"

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
    """Mirrors test_project_tasks.py's _QueuedFakePool exactly."""

    def __init__(self, *, fetchrow_results=None, fetch_results=None):
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetch_results = list(fetch_results or [])
        self.fetchrow_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []

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
        return "UPDATE 1"

    def acquire(self):
        return _FakeAcquire(_FakeConnection(self))


def _task_row(**overrides) -> dict:
    row = {
        "id": "task-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "project_id": "proj-1",
        "title": "Ship the widget",
        "description": "Build and ship it.",
        "status": "open",
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


def _callbacks() -> direct_tool_execution_service.DirectToolExecutionCallbacks:
    """Real tool-name parsing, real project_tasks_service functions (via the
    real run_async_tool_call bridge — asyncio.run is safe here because each
    test calls the SYNC execute_single_direct_tool_call directly from a
    plain unittest.TestCase, never from inside a running event loop, same
    constraint production respects by only reaching this function from a
    background thread)."""
    return direct_tool_execution_service.DirectToolExecutionCallbacks(
        compact_step_detail=lambda value: None,
        titleize_direct_step_token=lambda value: str(value or ""),
        run_async_tool_call=asyncio.run,
        parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
        tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
        parse_json_object_loose=lambda value: {},
        safe_positive_int=lambda value, default=0: int(value) if str(value or "").strip().isdigit() else default,
        normalize_reasoning_effort=lambda value: None,
        build_direct_local_tool_config=lambda connector_id, action_id, tool_input: ("", {}),
        format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        build_direct_tool_config=lambda connector_id, action_id, tool_input: {
            "connector": connector_id, "action": action_id, "input": tool_input,
        },
        format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        llm_task=lambda *args, **kwargs: {"ok": True},
        web_search=lambda query: [],
        web_fetch=lambda url: "",
        search_memory_notebook=lambda *args, **kwargs: {
            "results": [], "files_searched": 0, "errors": [], "status": "no_files", "message": "",
        },
        get_memory_notebook_excerpt=lambda *args, **kwargs: {},
    )


def _call(tool_name: str, arguments: dict, *, pool: _QueuedFakePool, agent_id: str = "agent-1"):
    from unittest.mock import AsyncMock, patch

    with patch(
        "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
        new=AsyncMock(return_value=pool),
    ):
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": tool_name, "arguments": arguments},
            workspace_id="ws-1",
            thread_id="thread-1",
            session_ctx={"agent_install_id": agent_id, "tenant_id": "tenant-1"},
            callbacks=_callbacks(),
        )
    return json.loads(raw)


class ProjectTaskNativeToolTests(unittest.TestCase):
    def test_create_scopes_to_callers_own_project(self):
        pool = _QueuedFakePool(
            # agent_project_id's lookup, create_task's own task_seq
            # allocation (migrations/add_task_sequence_numbers.sql), then
            # the INSERT itself.
            fetchrow_results=[{"project_id": "proj-1"}, {"task_seq": 1}, _task_row()],
        )
        result = _call("project_task__create", {"title": "Draft the summary"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["project_id"], "proj-1")
        # agent_project_id's lookup ran before create_task's INSERT.
        self.assertIn("workspace_agent_installs", pool.fetchrow_calls[0][0])
        self.assertIn("UPDATE projects SET task_seq", pool.fetchrow_calls[1][0])
        self.assertIn("INSERT INTO project_tasks", pool.fetchrow_calls[2][0])

    def test_create_requires_title(self):
        pool = _QueuedFakePool(fetchrow_results=[{"project_id": "proj-1"}])
        with self.assertRaises(RuntimeError):
            _call("project_task__create", {"title": "  "}, pool=pool)

    def test_agent_with_no_project_gets_clear_error(self):
        pool = _QueuedFakePool(fetchrow_results=[None])
        with self.assertRaises(RuntimeError) as ctx:
            _call("project_task__list", {}, pool=pool)
        self.assertIn("no project", str(ctx.exception))

    def test_list_filters_to_callers_own_project(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}],
            fetch_results=[[_task_row()]],
        )
        result = _call("project_task__list", {}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["tasks"]), 1)
        _, args = pool.fetch_calls[0]
        self.assertIn("proj-1", args)

    def test_get_own_project_task_succeeds(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _task_row(project_id="proj-1")],
        )
        result = _call("project_task__get", {"task_id": "task-1"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["id"], "task-1")

    def test_get_cross_project_task_is_rejected(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _task_row(project_id="proj-OTHER")],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call("project_task__get", {"task_id": "task-1"}, pool=pool)
        self.assertIn("different project", str(ctx.exception))

    def test_update_marks_task_done(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                _task_row(project_id="proj-1"),
                _task_row(project_id="proj-1", status="done"),
            ],
        )
        result = _call("project_task__update", {"task_id": "task-1", "status": "done"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "done")

    def test_update_cross_project_task_is_rejected_before_any_write(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _task_row(project_id="proj-OTHER")],
        )
        with self.assertRaises(RuntimeError):
            _call("project_task__update", {"task_id": "task-1", "status": "done"}, pool=pool)
        # Only the ownership-check fetchrow ran — no UPDATE was attempted.
        self.assertEqual(len(pool.fetchrow_calls), 2)

    def test_update_rejects_invalid_status(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _task_row(project_id="proj-1")],
        )
        with self.assertRaises(RuntimeError):
            _call("project_task__update", {"task_id": "task-1", "status": "not_a_real_status"}, pool=pool)

    def test_comment_on_own_project_task(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                _task_row(project_id="proj-1"),
                _task_row(project_id="proj-1", metadata={"comments": [{"body": "done with step 1"}]}),
            ],
        )
        result = _call("project_task__comment", {"task_id": "task-1", "body": "done with step 1"}, pool=pool)
        self.assertTrue(result["ok"])

    def test_assign_rejects_target_agent_in_a_different_project(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},       # caller's own project
                _task_row(project_id="proj-1"),  # ownership check on the task
                {"project_id": "proj-OTHER"},   # target agent's project
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call("project_task__assign", {"task_id": "task-1", "agent_id": "agent-2"}, pool=pool)
        self.assertIn("not in your project", str(ctx.exception))

    def test_assign_within_same_project_reassigns_the_task(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},        # caller's own project
                _task_row(project_id="proj-1"),  # ownership check on the task
                {"project_id": "proj-1"},        # target agent's project (same)
                _task_row(project_id="proj-1"),  # assign_task's own get_task
                {"id": "agent-2"},               # assign_task's _agent_install_exists
                _task_row(project_id="proj-1", assignee_agent_id="agent-2", status="in_progress"),
            ],
        )
        result = _call("project_task__assign", {"task_id": "task-1", "agent_id": "agent-2"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["assignee_agent_id"], "agent-2")


class ProjectTaskStatusVocabularyReachabilityTests(unittest.TestCase):
    """`in_review` only earns its keep if an AGENT can actually put a task
    there. An agent can only emit what the tool schema advertises, so the
    reachability property has two halves: the descriptor enum must offer the
    status, and the dispatch path must write it through. Both are checked
    here — a correct service layer behind a stale tool schema would leave
    `in_review` permanently unreachable by the only actors that produce
    work needing review.
    """

    def _descriptor(self, tool_name: str):
        for descriptor in skills_service._builtin_tool_descriptors():
            if descriptor.tool_name == tool_name:
                return descriptor
        raise AssertionError(f"tool descriptor {tool_name} not found")

    def test_update_tool_schema_offers_all_seven_statuses(self):
        from server_modules import project_tasks_service

        enum = self._descriptor("project_task__update").parameters["properties"]["status"]["enum"]
        self.assertEqual(enum, list(project_tasks_service.TASK_STATUS_ORDER))
        self.assertIn("in_review", enum)
        self.assertIn("backlog", enum)

    def test_list_tool_schema_offers_all_seven_statuses(self):
        from server_modules import project_tasks_service

        enum = self._descriptor("project_task__list").parameters["properties"]["status"]["enum"]
        self.assertEqual(enum, list(project_tasks_service.TASK_STATUS_ORDER))

    def test_update_tool_description_tells_the_agent_to_use_in_review(self):
        """The enum alone doesn't get used — the description is what makes an
        agent hand finished work back for review instead of self-closing it."""
        description = self._descriptor("project_task__update").description
        self.assertIn("in_review", description)

    def test_agent_can_actually_move_its_own_task_to_in_review(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                _task_row(project_id="proj-1", status="in_progress"),
                _task_row(project_id="proj-1", status="in_review"),
            ],
        )
        result = _call("project_task__update", {"task_id": "task-1", "status": "in_review"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "in_review")
        # The status reached the UPDATE verbatim, not coerced to a default.
        _query, args = pool.fetchrow_calls[-1]
        self.assertEqual(args[5], "in_review")

    def test_agent_can_move_a_task_to_backlog(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                _task_row(project_id="proj-1"),
                _task_row(project_id="proj-1", status="backlog"),
            ],
        )
        result = _call("project_task__update", {"task_id": "task-1", "status": "backlog"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "backlog")

    def test_agent_sending_the_legacy_open_status_still_works(self):
        """An agent holding a cached copy of the old tool schema keeps
        working — 'open' lands on 'todo' instead of erroring the turn."""
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                _task_row(project_id="proj-1"),
                _task_row(project_id="proj-1", status="todo"),
            ],
        )
        result = _call("project_task__update", {"task_id": "task-1", "status": "open"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "todo")
        _query, args = pool.fetchrow_calls[-1]
        self.assertEqual(args[5], "todo")


class ProjectTaskPriorityReachabilityTests(unittest.TestCase):
    """Priority is only worth having if an AGENT can set it, not just a
    human — otherwise the board never gets triaged unless somebody opens the
    UI. An agent can only ever emit a field its tool schema advertises, so
    the reachability property has two halves, both checked here: the
    descriptor must offer `priority`, and the dispatch path must actually
    write it through to project_tasks_service. A correct service layer
    behind a stale tool schema is exactly the failure mode this class
    exists to catch (it is what was missed when the field was first
    scoped).
    """

    def _descriptor(self, tool_name: str):
        for descriptor in skills_service._builtin_tool_descriptors():
            if descriptor.tool_name == tool_name:
                return descriptor
        raise AssertionError(f"tool descriptor {tool_name} not found")

    def test_create_and_update_tool_schemas_both_offer_priority(self):
        for tool_name in ("project_task__create", "project_task__update"):
            with self.subTest(tool_name=tool_name):
                schema = self._descriptor(tool_name).parameters["properties"]
                self.assertIn("priority", schema)
                self.assertEqual(schema["priority"]["enum"], [0, 1, 2, 3, 4])

    def test_priority_schema_description_explains_the_inversion(self):
        """The enum alone is useless to a model: 1..4 with no explanation
        reads as "bigger is more urgent", which is backwards."""
        description = self._descriptor("project_task__update").parameters["properties"]["priority"]["description"]
        for expected in ("0 = none", "1 = urgent", "2 = high", "3 = medium", "4 = low"):
            self.assertIn(expected, description)
        self.assertIn("MOST urgent", description)

    def test_update_tool_description_tells_the_agent_to_triage(self):
        self.assertIn("priority", self._descriptor("project_task__update").description)

    def test_list_tool_offers_priority_sorting(self):
        schema = self._descriptor("project_task__list").parameters["properties"]
        self.assertIn("sort", schema)
        self.assertIn("priority", schema["sort"]["enum"])

    def test_agent_can_create_a_task_at_urgent(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, {"task_seq": 1}, _task_row(priority=1)],
        )
        result = _call(
            "project_task__create", {"title": "Prod is down", "priority": 1}, pool=pool,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["priority"], 1)
        self.assertEqual(result["task"]["priority_label"], "urgent")
        # Reached the INSERT verbatim rather than being dropped on the floor.
        _query, args = pool.fetchrow_calls[-1]
        self.assertEqual(args[8], 1)

    def test_agent_can_set_every_priority_on_its_own_task(self):
        from server_modules import project_tasks_service

        for priority in project_tasks_service.TASK_PRIORITY_ORDER:
            with self.subTest(priority=priority):
                pool = _QueuedFakePool(
                    fetchrow_results=[
                        {"project_id": "proj-1"},
                        _task_row(project_id="proj-1"),
                        _task_row(project_id="proj-1", priority=priority),
                    ],
                )
                result = _call(
                    "project_task__update", {"task_id": "task-1", "priority": priority}, pool=pool,
                )
                self.assertTrue(result["ok"])
                self.assertEqual(result["task"]["priority"], priority)
                _query, args = pool.fetchrow_calls[-1]
                self.assertEqual(args[8], priority)

    def test_agent_setting_an_out_of_range_priority_is_rejected(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _task_row(project_id="proj-1")],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call("project_task__update", {"task_id": "task-1", "priority": 9}, pool=pool)
        self.assertIn("urgent", str(ctx.exception))

    def test_agent_can_set_priority_and_status_in_one_call(self):
        """Triage and workflow are independent fields; patching both at once
        must not make either overwrite the other."""
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                _task_row(project_id="proj-1"),
                _task_row(project_id="proj-1", status="in_review", priority=2),
            ],
        )
        result = _call(
            "project_task__update",
            {"task_id": "task-1", "status": "in_review", "priority": 2},
            pool=pool,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "in_review")
        self.assertEqual(result["task"]["priority"], 2)
        _query, args = pool.fetchrow_calls[-1]
        self.assertEqual(args[5], "in_review")
        self.assertEqual(args[8], 2)

    def test_agent_reads_priority_back_on_list_and_get(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}],
            fetch_results=[[_task_row(priority=1)]],
        )
        listed = _call("project_task__list", {}, pool=pool)
        self.assertEqual(listed["tasks"][0]["priority"], 1)
        self.assertEqual(listed["tasks"][0]["priority_label"], "urgent")

        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _task_row(project_id="proj-1", priority=3)],
        )
        fetched = _call("project_task__get", {"task_id": "task-1"}, pool=pool)
        self.assertEqual(fetched["task"]["priority"], 3)
        self.assertEqual(fetched["task"]["priority_label"], "medium")

    def test_agent_can_ask_for_the_board_priority_ordered(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}],
            fetch_results=[[_task_row(priority=1)]],
        )
        result = _call("project_task__list", {"sort": "priority"}, pool=pool)
        self.assertTrue(result["ok"])
        query, _args = pool.fetch_calls[0]
        self.assertIn("NULLIF(priority, 0) ASC NULLS LAST", " ".join(query.split()))


if __name__ == "__main__":
    unittest.main()
