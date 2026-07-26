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
            fetchrow_results=[{"project_id": "proj-1"}, _task_row()],
        )
        result = _call("project_task__create", {"title": "Draft the summary"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["project_id"], "proj-1")
        # agent_project_id's lookup ran before create_task's INSERT.
        self.assertIn("workspace_agent_installs", pool.fetchrow_calls[0][0])
        self.assertIn("INSERT INTO project_tasks", pool.fetchrow_calls[1][0])

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


if __name__ == "__main__":
    unittest.main()
