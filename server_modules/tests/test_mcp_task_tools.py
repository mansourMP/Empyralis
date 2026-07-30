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


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    """acquire() target control_plane_repository.rls_fetchrow/rls_fetch open
    now that project_tasks is FORCE RLS (MAN-109). Delegates straight back
    to the owning _WorkspaceScopedFakePool's own scoping-aware fetchrow/fetch
    so the (tenant_id, workspace_id) boundary logic these tests exist to
    prove keeps running unchanged. The RLS scope-setting execute() call is
    swallowed here rather than recorded -- unlike skills_service's own
    dispatcher, mcp_server.py's activity-ledger write
    (mcp_server._ledger_mcp_call) is already patched out by _patched() above,
    so there is no shared-queue collision to guard against here."""

    def __init__(self, pool: "_WorkspaceScopedFakePool") -> None:
        self._pool = pool

    async def fetchrow(self, query, *args):
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


class _WorkspaceScopedFakePool:
    """Fake pool that enforces (tenant_id, workspace_id) scoping the way the
    real SQL WHERE clauses do, so these tests prove the boundary itself --
    not just that some value was passed through unchanged."""

    def __init__(self, rows):
        self._rows = [dict(r) for r in rows]

    def acquire(self):
        return _FakeAcquire(_FakeConnection(self))

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
                    # update_task(...): args = (tenant, ws, id, title, description,
                    #                           status, clear_due_at, due_at, priority)
                    status = args[5]
                    if status:
                        updated["status"] = status
                        row["status"] = status
                    # Priority uses `is not None`, not truthiness: 0 is a real
                    # patch ("clear the priority"), only None means "leave it".
                    priority = args[8] if len(args) >= 9 else None
                    if priority is not None:
                        updated["priority"] = priority
                        row["priority"] = priority
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
        "priority": 0,
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
        # Names the valid status set, including the new review seam.
        self.assertIn("in_review", result["error"])
        self.assertIn("todo", result["error"])

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


class _InsertingFakePool:
    """A pool that answers an INSERT ... RETURNING with a canned row --
    _WorkspaceScopedFakePool above only simulates lookups against
    pre-seeded rows, which a brand-new task (create_task's whole point)
    can never be."""

    def __init__(self, inserted_row):
        self._inserted_row = dict(inserted_row)
        self.fetchrow_calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return dict(self._inserted_row)

    def acquire(self):
        return _FakeAcquire(_FakeConnection(self))


class CreateTaskTests(unittest.IsolatedAsyncioTestCase):
    """empyralis_create_task (2026-07-25): the external-MCP-client side of
    closing the "Claude/ChatGPT can open tasks like a Linear issue" gap --
    list/get/update_status/comment already existed, create did not."""

    async def test_create_task_happy_path(self):
        pool = _InsertingFakePool(_task_row(id="task-new", title="Draft the plan"))
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_create_task(
                project_id="proj-1", title="Draft the plan", ctx=_FakeCtx(),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["id"], "task-new")
        self.assertEqual(result["task"]["title"], "Draft the plan")

    async def test_create_task_requires_title(self):
        pool = _InsertingFakePool(_task_row())
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_create_task(project_id="proj-1", title="  ", ctx=_FakeCtx())
        self.assertFalse(result["ok"])

    async def test_create_task_not_gated_by_check_write(self):
        self.assertNotIn("_check_write", inspect.getsource(mcp_server.empyralis_create_task))

    def test_create_task_is_registered_in_always_live_tools(self):
        self.assertIn("empyralis_create_task", mcp_server.EMPYRALIST_MCP_TOOLS)


class TaskPriorityTests(unittest.IsolatedAsyncioTestCase):
    """An EXTERNAL agent (Claude/ChatGPT through this MCP surface) must be
    able to both READ and SET priority, not just see one a human typed --
    otherwise the field is human-only and the board never gets triaged by
    the agents actually working it. Priority uses Linear's scale, inversion
    and all: 0 = none, 1 = urgent, 2 = high, 3 = medium, 4 = low.
    """

    async def test_set_task_priority_is_registered_in_always_live_tools(self):
        self.assertIn("empyralis_set_task_priority", mcp_server.EMPYRALIST_MCP_TOOLS)

    async def test_set_task_priority_is_not_gated_by_check_write(self):
        """Same write-gate decision as the other task tools (see the module
        docstring): triage is bounded to tasks already visible through this
        key, not a workspace-wide configuration mutation."""
        self.assertNotIn("_check_write", inspect.getsource(mcp_server.empyralis_set_task_priority))

    async def test_external_agent_can_set_every_priority(self):
        from server_modules import project_tasks_service

        for priority in project_tasks_service.TASK_PRIORITY_ORDER:
            with self.subTest(priority=priority):
                pool = _WorkspaceScopedFakePool([_task_row()])
                p1, p2, p3, p4 = _patched(pool)
                with p1, p2, p3, p4:
                    result = await mcp_server.empyralis_set_task_priority(
                        task_id="task-1", priority=priority, ctx=_FakeCtx(),
                    )
                self.assertTrue(result["ok"])
                self.assertEqual(result["task"]["priority"], priority)
                self.assertEqual(
                    result["task"]["priority_label"],
                    project_tasks_service.TASK_PRIORITY_LABELS[priority],
                )

    async def test_priority_survives_a_round_trip_through_set_then_get(self):
        pool = _WorkspaceScopedFakePool([_task_row()])
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            written = await mcp_server.empyralis_set_task_priority(
                task_id="task-1", priority=1, ctx=_FakeCtx(),
            )
            fetched = await mcp_server.empyralis_get_task(task_id="task-1", ctx=_FakeCtx())
        self.assertEqual(written["task"]["priority"], 1)
        self.assertEqual(fetched["task"]["priority"], 1)
        self.assertEqual(fetched["task"]["priority_label"], "urgent")

    async def test_out_of_range_priority_returns_a_clear_agent_facing_error(self):
        for bad in (5, -1, 99):
            with self.subTest(bad=bad):
                pool = _WorkspaceScopedFakePool([_task_row()])
                p1, p2, p3, p4 = _patched(pool)
                with p1, p2, p3, p4:
                    result = await mcp_server.empyralis_set_task_priority(
                        task_id="task-1", priority=bad, ctx=_FakeCtx(),
                    )
                self.assertFalse(result["ok"])
                # Names the scale AND which end is urgent -- the thing a
                # caller getting this error most likely got backwards.
                self.assertIn("1 = urgent", result["error"])
                self.assertIn("MOST urgent", result["error"])

    async def test_set_priority_cannot_touch_another_workspaces_task(self):
        pool = _WorkspaceScopedFakePool([_task_row(workspace_id="ws-A", tenant_id="tenant-A")])
        resolved = _resolved(workspace_id="ws-B", external_agent_id="ext_agent_bbb")
        p1, p2, p3, p4 = _patched(pool, tenant_id="tenant-B", resolved=resolved)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_set_task_priority(
                task_id="task-1", priority=1, ctx=_FakeCtx(),
            )
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    async def test_setting_priority_never_changes_status(self):
        """The two are independent facts, which is why they are separate
        tools -- setting one must not quietly move the other."""
        pool = _WorkspaceScopedFakePool([_task_row(status="in_progress")])
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_set_task_priority(
                task_id="task-1", priority=2, ctx=_FakeCtx(),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "in_progress")
        self.assertEqual(result["task"]["priority"], 2)

    async def test_setting_status_never_changes_priority(self):
        pool = _WorkspaceScopedFakePool([_task_row(priority=1)])
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_update_task_status(
                task_id="task-1", status="done", ctx=_FakeCtx(),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "done")
        self.assertEqual(result["task"]["priority"], 1)

    async def test_create_task_defaults_to_no_priority(self):
        pool = _InsertingFakePool(_task_row(id="task-new"))
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_create_task(
                project_id="proj-1", title="Draft the plan", ctx=_FakeCtx(),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["priority"], 0)
        _query, args = pool.fetchrow_calls[0]
        self.assertEqual(args[8], 0)

    async def test_external_agent_can_create_a_task_at_urgent(self):
        pool = _InsertingFakePool(_task_row(id="task-new", priority=1))
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_create_task(
                project_id="proj-1", title="Prod is down", priority=1, ctx=_FakeCtx(),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["priority"], 1)
        _query, args = pool.fetchrow_calls[0]
        self.assertEqual(args[8], 1)

    async def test_create_task_rejects_an_out_of_range_priority(self):
        pool = _InsertingFakePool(_task_row())
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_create_task(
                project_id="proj-1", title="Draft the plan", priority=7, ctx=_FakeCtx(),
            )
        self.assertFalse(result["ok"])

    async def test_list_my_tasks_returns_priority_and_can_sort_by_it(self):
        pool = _WorkspaceScopedFakePool([_task_row(id="task-urgent", priority=1)])
        p1, p2, p3, p4 = _patched(pool)
        with p1, p2, p3, p4:
            result = await mcp_server.empyralis_list_my_tasks(sort="priority", ctx=_FakeCtx())
        self.assertTrue(result["ok"])
        self.assertEqual(result["tasks"][0]["priority"], 1)
        self.assertEqual(result["tasks"][0]["priority_label"], "urgent")

    async def test_tool_docstrings_explain_which_end_of_the_scale_is_urgent(self):
        """An external agent has nothing but the docstring to go on -- the
        MCP tool description IS the schema description here."""
        for tool in (mcp_server.empyralis_set_task_priority, mcp_server.empyralis_create_task):
            with self.subTest(tool=tool.__name__):
                doc = tool.__doc__ or ""
                self.assertIn("1 = urgent", doc)
                self.assertIn("4 = low", doc)
                self.assertIn("MOST urgent", doc)


if __name__ == "__main__":
    unittest.main()
