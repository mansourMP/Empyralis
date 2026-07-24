"""Tests for the 4 new MCP task tools in mcp_server.py (Step 1 of "Mentions +
identity for platform AND external agents"): empyralis_list_my_tasks,
empyralis_get_task, empyralis_update_task_status, empyralis_comment_on_task.

Covers:
(a) each tool is reachable and returns ok=True on the happy path
(b) a key resolved to workspace A can never see/mutate workspace B's task --
    this repo has a live history of cross-tenant leaks, so this is tested
    explicitly against a fake pool that actually enforces (tenant_id,
    workspace_id) scoping, not just a call-args assertion
(c) "my tasks" resolves via the key's external_agent_id, and degrades to
    unassigned-only (with a traced note) when there is no identity
(d) an invalid status is rejected with a clear, agent-facing error
(e) the 4 tools are NOT gated by EMPYRALIS_MCP_WRITE_ENABLED (write-gate
    decision documented in mcp_server.py's module docstring) -- proved
    structurally (no _check_write call) AND behaviorally (writes_enabled=False
    still succeeds)
"""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import AsyncMock, patch

import mcp_server


def _resolved(workspace_id="ws-A", external_agent_id="ext_agent_aaa", writes_enabled=False):
    return {
        "workspace_id": workspace_id,
        "writes_enabled": writes_enabled,
        "external_agent_id": external_agent_id,
        "external_agent_display_name": "Atlas",
    }


class _WorkspaceScopedFakePool:
    """Fake pool that enforces (tenant_id, workspace_id) scoping the way the
    real SQL WHERE clauses do, so these tests prove the boundary itself --
    not just that some value was passed through unchanged."""

    def __init__(self, rows):
        self._rows = [dict(r) for r in rows]

    async def fetchrow(self, query, *args):
        # get_task / update_task / add_task_comment all take
        # (tenant_id, workspace_id, task_id, ...) positionally.
        tenant_id, workspace_id, task_id = args[0], args[1], args[2]
        for row in self._rows:
            if row["id"] == task_id and row["tenant_id"] == tenant_id and row["workspace_id"] == workspace_id:
                if "status" in query and "SET status" not in " ".join(query.split()) and len(args) >= 6:
                    pass  # update_task path -- status coalesce handled below
                updated = dict(row)
                if "UPDATE project_tasks" in query and "SET title" in query:
                    # update_task(...): args = (tenant, ws, id, title, description, status, clear_due_at, due_at)
                    status = args[5]
                    if status:
                        updated["status"] = status
                        row["status"] = status
                if "jsonb_set" in query:
                    # add_task_comment(...): args = (tenant, ws, id, comments_json)
                    import json as _json
                    new_comments = _json.loads(args[3])
                    existing = list((row.get("metadata") or {}).get("comments") or [])
                    existing.extend(new_comments)
                    updated_metadata = dict(row.get("metadata") or {})
                    updated_metadata["comments"] = existing
                    updated["metadata"] = updated_metadata
                    row["metadata"] = updated_metadata
                return updated
        return None

    async def fetch(self, query, *args):
        tenant_id, workspace_id = args[0], args[1]
        q = " ".join(query.split())
        matches = [r for r in self._rows if r["tenant_id"] == tenant_id and r["workspace_id"] == workspace_id]
        if "assignee_agent_id IS NULL" in q:
            # list_my_tasks's "(mine OR unassigned)" clause -- simulate it for
            # real instead of just passing every workspace row through, since
            # that's exactly the filter these tests are proving.
            if "(FALSE OR assignee_agent_id IS NULL)" in q:
                matches = [r for r in matches if r.get("assignee_agent_id") is None]
            else:
                caller_id = args[2]
                matches = [
                    r for r in matches
                    if r.get("assignee_agent_id") == caller_id or r.get("assignee_agent_id") is None
                ]
        return [dict(r) for r in matches]

    async def execute(self, query, *args):
        return "OK"


def _task_row(**overrides):
    row = {
        "id": "task-1",
        "tenant_id": "tenant-A",
        "workspace_id": "ws-A",
        "project_id": "proj-1",
        "title": "Ship it",
        "description": "",
        "status": "open",
        "assignee_agent_id": None,
        "created_by": "user-1",
        "due_at": None,
        "plan": [],
        "metadata": {},
        "created_at": "2026-07-24T00:00:00Z",
        "updated_at": "2026-07-24T00:00:00Z",
    }
    row.update(overrides)
    return row


class _FakeCtx:
    pass


def _patched(pool, *, tenant_id="tenant-A", resolved=None):
    resolved = resolved if resolved is not None else _resolved()
    return (
        patch(
            "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
            new=AsyncMock(return_value=pool),
        ),
        patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=AsyncMock(return_value=tenant_id),
        ),
        patch.object(mcp_server, "_resolve_workspace", new=AsyncMock(return_value=resolved)),
        patch.object(mcp_server, "_ledger_mcp_call", new=AsyncMock()),
    )


class HappyPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_task_happy_path(self):
        pool = _WorkspaceScopedFakePool([_task_row()])
        with _patched(pool)[0], _patched(pool)[1], _patched(pool)[2], _patched(pool)[3]:
            result = await mcp_server.empyralis_get_task(task_id="task-1", ctx=_FakeCtx())
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["id"], "task-1")

    async def test_update_task_status_happy_path(self):
        pool = _WorkspaceScopedFakePool([_task_row()])
        with _patched(pool)[0], _patched(pool)[1], _patched(pool)[2], _patched(pool)[3]:
            result = await mcp_server.empyralis_update_task_status(task_id="task-1", status="in_progress", ctx=_FakeCtx())
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "in_progress")

    async def test_comment_on_task_happy_path(self):
        pool = _WorkspaceScopedFakePool([_task_row()])
        with _patched(pool)[0], _patched(pool)[1], _patched(pool)[2], _patched(pool)[3]:
            result = await mcp_server.empyralis_comment_on_task(task_id="task-1", body="working on it", ctx=_FakeCtx())
        self.assertTrue(result["ok"])
        comments = result["task"]["metadata"]["comments"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["body"], "working on it")
        self.assertEqual(comments[0]["author_id"], "ext_agent_aaa")
        self.assertEqual(comments[0]["author_type"], "external_agent")


class CrossTenantIsolationTests(unittest.IsolatedAsyncioTestCase):
    """The exact shape this repo has a live history of leaking: a task that
    lives in ws-A must be invisible to (and unmutatable by) a key resolved
    to ws-B, even when it asks for the SAME task_id."""

    async def test_get_task_cannot_see_other_workspace_task(self):
        pool = _WorkspaceScopedFakePool([_task_row(workspace_id="ws-A", tenant_id="tenant-A")])
        resolved = _resolved(workspace_id="ws-B", external_agent_id="ext_agent_bbb")
        p1, p2, p3, p4 = _patched(pool, tenant_id="tenant-B", resolved=resolved)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_get_task(task_id="task-1", ctx=_FakeCtx())
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    async def test_update_task_status_cannot_mutate_other_workspace_task(self):
        pool = _WorkspaceScopedFakePool([_task_row(workspace_id="ws-A", tenant_id="tenant-A")])
        resolved = _resolved(workspace_id="ws-B", external_agent_id="ext_agent_bbb")
        p1, p2, p3, p4 = _patched(pool, tenant_id="tenant-B", resolved=resolved)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_update_task_status(task_id="task-1", status="done", ctx=_FakeCtx())
        self.assertFalse(result["ok"])

    async def test_comment_on_task_cannot_write_to_other_workspace_task(self):
        pool = _WorkspaceScopedFakePool([_task_row(workspace_id="ws-A", tenant_id="tenant-A")])
        resolved = _resolved(workspace_id="ws-B", external_agent_id="ext_agent_bbb")
        p1, p2, p3, p4 = _patched(pool, tenant_id="tenant-B", resolved=resolved)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_comment_on_task(task_id="task-1", body="hi", ctx=_FakeCtx())
        self.assertFalse(result["ok"])

    async def test_list_my_tasks_never_returns_another_workspaces_rows(self):
        pool = _WorkspaceScopedFakePool([
            _task_row(id="task-a", workspace_id="ws-A", tenant_id="tenant-A", assignee_agent_id=None),
        ])
        resolved = _resolved(workspace_id="ws-B", external_agent_id="ext_agent_bbb")
        p1, p2, p3, p4 = _patched(pool, tenant_id="tenant-B", resolved=resolved)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_list_my_tasks(ctx=_FakeCtx())
        self.assertTrue(result["ok"])
        self.assertEqual(result["tasks"], [])


class ListMyTasksIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_via_external_agent_id_and_includes_backlog(self):
        mine = _task_row(id="task-mine", assignee_agent_id="ext_agent_aaa")
        others = _task_row(id="task-other", assignee_agent_id="ext_agent_bbb")
        backlog = _task_row(id="task-backlog", assignee_agent_id=None)
        pool = _WorkspaceScopedFakePool([mine, others, backlog])
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_list_my_tasks(ctx=_FakeCtx())
        self.assertTrue(result["ok"])
        ids = {t["id"] for t in result["tasks"]}
        self.assertIn("task-mine", ids)
        self.assertIn("task-backlog", ids)
        self.assertNotIn("task-other", ids)
        self.assertEqual(result["external_agent_id"], "ext_agent_aaa")
        self.assertNotIn("note", result)

    async def test_no_identity_degrades_to_unassigned_only_with_traced_note(self):
        mine = _task_row(id="task-mine", assignee_agent_id="ext_agent_aaa")
        backlog = _task_row(id="task-backlog", assignee_agent_id=None)
        pool = _WorkspaceScopedFakePool([mine, backlog])
        resolved = _resolved(external_agent_id=None)
        p1, p2, p3, p4 = _patched(pool, resolved=resolved)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_list_my_tasks(ctx=_FakeCtx())
        self.assertTrue(result["ok"])
        ids = {t["id"] for t in result["tasks"]}
        self.assertEqual(ids, {"task-backlog"})
        self.assertIsNone(result["external_agent_id"])
        self.assertIn("note", result)  # never a silent degradation


class InvalidStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_status_returns_clear_agent_facing_error(self):
        pool = _WorkspaceScopedFakePool([_task_row()])
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_update_task_status(task_id="task-1", status="cancelled", ctx=_FakeCtx())
        self.assertFalse(result["ok"])
        self.assertIn("open", result["error"])  # names the valid status set

    async def test_missing_task_returns_clear_error(self):
        pool = _WorkspaceScopedFakePool([])
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_update_task_status(task_id="ghost", status="done", ctx=_FakeCtx())
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())


class WriteGateDecisionTests(unittest.IsolatedAsyncioTestCase):
    """update_task_status / comment_on_task are deliberately NOT behind
    EMPYRALIS_MCP_WRITE_ENABLED (see mcp_server.py's module docstring for the
    full rationale). Proved two ways: structurally (no _check_write call in
    the tool body) and behaviorally (a writes_enabled=False key still
    succeeds, with the global write-enabled env flag unset)."""

    def test_neither_tool_calls_check_write(self):
        self.assertNotIn("_check_write", inspect.getsource(mcp_server.empyralis_update_task_status))
        self.assertNotIn("_check_write", inspect.getsource(mcp_server.empyralis_comment_on_task))
        self.assertNotIn("_check_write", inspect.getsource(mcp_server.empyralis_get_task))
        self.assertNotIn("_check_write", inspect.getsource(mcp_server.empyralis_list_my_tasks))

    async def test_update_status_succeeds_with_writes_disabled_key(self):
        pool = _WorkspaceScopedFakePool([_task_row()])
        resolved = _resolved(writes_enabled=False)
        p1, p2, p3, p4 = _patched(pool, resolved=resolved)
        with p1, p2, p3, p4, patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("EMPYRALIS_MCP_WRITE_ENABLED", None)
            result = await mcp_server.empyralis_update_task_status(task_id="task-1", status="done", ctx=_FakeCtx())
        self.assertTrue(result["ok"])

    async def test_comment_succeeds_with_writes_disabled_key(self):
        pool = _WorkspaceScopedFakePool([_task_row()])
        resolved = _resolved(writes_enabled=False)
        p1, p2, p3, p4 = _patched(pool, resolved=resolved)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_comment_on_task(task_id="task-1", body="status update", ctx=_FakeCtx())
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
