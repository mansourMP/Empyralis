"""feat/document-agent-tools: the agent-facing side of a project's owned
markdown documents (project_documents_repository.py) -- "the owned-context
layer for a team, with execution attached" (CLAUDE.md). Before this,
project_documents existed as a table + repository + human-facing CRUD API
(merged as "project documents table, RLS, and CRUD API"), but no agent tool
reached it during its own turn -- an agent could not read or write the
documents its own project owned.

This file exercises the REAL, unmocked native dispatch (tool name ->
direct_chat_operator_binding_service.parse_tool_name -> skills_service.
execute_single_direct_tool_call -> the real project_documents_repository
functions) for the four new document__* tools, against a fake Postgres pool.
Fixture code (_FakeTransaction/_FakeConnection/_FakeAcquire/_QueuedFakePool/
_callbacks/_call) is a near-byte-for-byte port of test_project_task_native_
tools.py's own fixtures -- duplicated rather than imported, same reasoning
that file's sibling tests give for their own duplication: importing a class
from a different test file couples this file's correctness to unrelated
edits landing there concurrently.

The isolation property under test throughout: every document__* tool is
scoped to the CALLING agent's own project (resolved server-side from session
identity, never a caller-supplied project_id) -- an agent cannot read or
write a document belonging to a different project, matching the locked
"project is the collaboration boundary" ruling project_task__* already
enforces.

The property document__edit exists FOR: old_string must match the document's
current body EXACTLY ONCE. Zero matches and multiple matches both fail
loudly with no mutation -- never guess, never silently overwrite, never fall
back to a whole-body rewrite. ``EditUniqueMatchTests`` below exercises all
three branches (no match, multiple matches, exactly one) plus the "no
changes were made" property on both failure branches.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_execution_service
from server_modules import agent_turn_runtime_service
from server_modules import skills_service


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    """See test_project_task_native_tools.py's own _FakeConnection for the
    full reasoning -- activity_ledger_events reads/writes are answered
    directly here so they never drain the queue this file reserves for the
    real project_documents_repository business calls."""

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
    """Mirrors test_project_task_native_tools.py's _QueuedFakePool exactly."""

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


def _document_row(**overrides) -> dict:
    row = {
        "id": "doc-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "project_id": "proj-1",
        "title": "Runbook",
        "path": "runbook.md",
        "body": "# Runbook\n\nStep one.\nStep two.\n",
        "created_by": "agent-1",
        "updated_by": "agent-1",
        "metadata": {},
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _callbacks() -> direct_tool_execution_service.DirectToolExecutionCallbacks:
    """Real tool-name parsing, real project_documents_repository functions
    (via the real run_async_tool_call bridge -- asyncio.run is safe here for
    the same reason test_project_task_native_tools.py's own _callbacks()
    gives: each test calls the SYNC execute_single_direct_tool_call directly
    from a plain unittest.TestCase, never from inside a running event
    loop)."""
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


def _call(
    tool_name: str,
    arguments: dict,
    *,
    pool: _QueuedFakePool,
    agent_id: str = "agent-1",
    authority_tier: str | None = "owner",
):
    """authority_tier defaults to "owner" only to keep these fixtures the
    shape production builds. Since 2026-08-21 it makes no difference to
    document__*: the audience tool tier is gone (authority_mandate_service),
    and document tools are ordinary work tools that any tier may call. The
    class below asserts exactly that, so the default here is convention, not
    a precondition."""
    # Every document__* dispatch resolves the caller's project via
    # project_tasks_service.agent_project_id (same resolver project_task__*
    # uses -- see skills_service.py's connector_id == "document" dispatch's
    # own comment) and then reads/writes via project_documents_repository,
    # so both modules' own ensure_control_plane_schema() must resolve to
    # this test's single fake pool.
    async def _resolve(*_args, **_kwargs):
        return pool

    with patch(
        "server_modules.project_documents_repository.control_plane_repository.ensure_control_plane_schema",
    ) as mock_documents_schema, patch(
        "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
    ) as mock_tasks_schema:
        mock_documents_schema.side_effect = _resolve
        mock_tasks_schema.side_effect = _resolve
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": tool_name, "arguments": arguments},
            workspace_id="ws-1",
            thread_id="thread-1",
            # authority_tier=None omits the KEY entirely (an unattributed
            # caller), which is a genuinely different input from any tier
            # string — _authority_mandate_gate branches on key presence.
            session_ctx={
                "agent_install_id": agent_id,
                "tenant_id": "tenant-1",
                **({} if authority_tier is None else {"authority_tier": authority_tier}),
            },
            callbacks=_callbacks(),
        )
    return json.loads(raw)


class DocumentListReadNativeToolTests(unittest.TestCase):
    def test_list_scopes_to_callers_own_project(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}],
            fetch_results=[[_document_row()]],
        )
        result = _call("document__list", {}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(result["documents"][0]["path"], "runbook.md")
        # No body in the list summary.
        self.assertNotIn("body", result["documents"][0])
        _query, args = pool.fetch_calls[0]
        self.assertIn(
            ["proj-1"], args,
            "the read scope reaching SQL must be EXACTLY this agent's own project -- "
            "pinned as the whole list, not merely 'contains proj-1', so a widened "
            "scope fails here instead of passing on a substring",
        )

    def test_agent_with_no_project_gets_clear_error_on_list(self):
        pool = _QueuedFakePool(fetchrow_results=[None])
        with self.assertRaises(RuntimeError) as ctx:
            _call("document__list", {}, pool=pool)
        self.assertIn("no project", str(ctx.exception))

    def test_read_by_path_succeeds(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _document_row()],
        )
        result = _call("document__read", {"path": "runbook.md"}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["document"]["body"], _document_row()["body"])

    def test_read_missing_path_in_own_project_gets_clear_error(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, None],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call("document__read", {"path": "no-such-doc.md"}, pool=pool)
        self.assertIn("No document at path", str(ctx.exception))

    def test_read_by_id_cross_project_is_rejected(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _document_row(project_id="proj-OTHER")],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call("document__read", {"id": "doc-1"}, pool=pool)
        # Wording changed when reads became scope-based (a turn can have
        # several projects in reach, so "a different project" stopped being
        # accurate). The PROPERTY under test is unchanged and is asserted
        # more tightly than before: the denial must not leak the other
        # project's identity.
        self.assertIn("not in reach", str(ctx.exception))
        self.assertNotIn(
            "proj-OTHER", str(ctx.exception),
            "a denial must not disclose which project the document actually belongs to",
        )

    def test_read_requires_path_or_id(self):
        pool = _QueuedFakePool(fetchrow_results=[{"project_id": "proj-1"}])
        with self.assertRaises(RuntimeError):
            _call("document__read", {}, pool=pool)


class EditUniqueMatchTests(unittest.TestCase):
    """The core guarantee document__edit exists for: old_string must match
    the document's CURRENT body exactly once. See this module's own
    docstring."""

    def test_no_match_fails_loudly_and_makes_no_change(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _document_row(body="Step one.\nStep two.\n")],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call(
                "document__edit",
                {"path": "runbook.md", "old_string": "Step three.", "new_string": "Step four."},
                pool=pool,
            )
        self.assertIn("not found", str(ctx.exception))
        self.assertIn("No changes were made", str(ctx.exception))
        # Only the ownership-check fetchrow ran -- no UPDATE was attempted.
        self.assertEqual(len(pool.fetchrow_calls), 2)

    def test_multiple_matches_fails_loudly_and_makes_no_change(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                _document_row(body="Step one.\nStep one.\nStep two.\n"),
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call(
                "document__edit",
                {"path": "runbook.md", "old_string": "Step one.", "new_string": "Step ONE."},
                pool=pool,
            )
        self.assertIn("appears 2 times", str(ctx.exception))
        self.assertIn("exactly once", str(ctx.exception))
        self.assertEqual(len(pool.fetchrow_calls), 2)

    def test_exactly_one_match_succeeds(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                _document_row(body="Step one.\nStep two.\n"),
                _document_row(body="Step one.\nStep TWO, revised.\n"),
            ],
        )
        result = _call(
            "document__edit",
            {"path": "runbook.md", "old_string": "Step two.", "new_string": "Step TWO, revised."},
            pool=pool,
        )
        self.assertTrue(result["ok"])
        # The UPDATE actually ran with the replaced body and the acting
        # agent's identity stamped as updated_by.
        _query, args = pool.fetchrow_calls[-1]
        self.assertIn("UPDATE project_documents", _query)
        self.assertIn("Step TWO, revised.", args[4])
        self.assertNotIn("Step two.\n", args[4])
        self.assertEqual(args[5], "agent-1")

    def test_old_string_and_new_string_must_differ(self):
        pool = _QueuedFakePool(
            fetchrow_results=[{"project_id": "proj-1"}, _document_row(body="Step one.\n")],
        )
        with self.assertRaises(RuntimeError):
            _call(
                "document__edit",
                {"path": "runbook.md", "old_string": "Step one.", "new_string": "Step one."},
                pool=pool,
            )

    def test_empty_old_string_is_rejected(self):
        pool = _QueuedFakePool(fetchrow_results=[{"project_id": "proj-1"}])
        with self.assertRaises(RuntimeError):
            _call(
                "document__edit",
                {"path": "runbook.md", "old_string": "", "new_string": "anything"},
                pool=pool,
            )

    def test_edit_on_a_path_outside_callers_project_is_not_found(self):
        # get_document_by_path is itself WHERE project_id = <caller's own> --
        # a different project's path simply never resolves, so the caller
        # never learns whether it exists (see connector_id == "document"
        # dispatch's own comment: "so a denial reads as 'not visible to this
        # agent' instead of leaking whether some other project's path/id
        # happens to exist").
        pool = _QueuedFakePool(fetchrow_results=[{"project_id": "proj-1"}, None])
        with self.assertRaises(RuntimeError) as ctx:
            _call(
                "document__edit",
                {"path": "someone-elses-doc.md", "old_string": "x", "new_string": "y"},
                pool=pool,
            )
        self.assertIn("No document at path", str(ctx.exception))


class WriteCreateOnlyTests(unittest.TestCase):
    def test_write_creates_a_new_document(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},  # agent_project_id
                None,                       # get_document_by_path: no collision
                _document_row(title="New Doc", path="new-doc.md", body="Hello."),  # INSERT ... RETURNING
            ],
            fetch_results=[[]],  # _unique_path's own existing-paths SELECT
        )
        result = _call("document__write", {"title": "New Doc", "body": "Hello."}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(result["document"]["path"], "new-doc.md")
        # created_by/updated_by stamped with the acting agent's identity --
        # create_document's INSERT reuses the same $8 placeholder for both.
        _query, args = pool.fetchrow_calls[-1]
        self.assertIn("INSERT INTO project_documents", _query)
        self.assertEqual(args[-1], "agent-1")

    def test_write_rejects_a_colliding_path_without_mutating_anything(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                {"project_id": "proj-1"},
                _document_row(title="Runbook", path="runbook.md"),  # collision found
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call("document__write", {"title": "Runbook"}, pool=pool)
        self.assertIn("already exists", str(ctx.exception))
        self.assertIn("document__edit", str(ctx.exception))
        # Only the collision-check fetchrow ran -- no INSERT attempted, and
        # _unique_path's own fetch never fired either.
        self.assertEqual(len(pool.fetchrow_calls), 2)
        self.assertEqual(len(pool.fetch_calls), 0)

    def test_write_requires_a_title(self):
        pool = _QueuedFakePool(fetchrow_results=[{"project_id": "proj-1"}])
        with self.assertRaises(RuntimeError):
            _call("document__write", {"title": "   "}, pool=pool)


class DocumentToolAudienceTierRemovedTests(unittest.TestCase):
    """PROOF THAT NO ORPHANED TOOL-TIER ENFORCEMENT REMAINS on this path.

    Until 2026-08-21 document__* was deliberately NOT audience_safe, and an
    audience-tier session was refused at _authority_mandate_gate before
    reaching the repository at all. The founder deleted that tier outright
    (see server_modules/authority_mandate_service.py). These two tests are
    the previous two, INVERTED rather than deleted — they are what fails if
    a tool-capability tier is ever quietly reintroduced on this surface.

    What is asserted instead of "blocked" is the strongest available
    positive: the call reaches the repository (pool.fetchrow_calls is
    non-empty), which a gate ahead of the dispatch could not allow."""

    def test_audience_tier_session_reaches_the_repository(self):
        pool = _QueuedFakePool(fetchrow_results=[{"project_id": "proj-1"}])
        _call("document__list", {}, pool=pool, authority_tier="audience")
        self.assertGreater(len(pool.fetchrow_calls), 0)

    def test_missing_authority_tier_also_reaches_the_repository(self):
        """An unattributed caller (no "authority_tier" key at all) still
        normalizes to the audience tier — that fail-closed default is kept —
        but the audience tier no longer costs an ordinary work tool. It costs
        only the machine-administration family, which document__* is not."""
        pool = _QueuedFakePool(fetchrow_results=[{"project_id": "proj-1"}])
        _call("document__list", {}, pool=pool, authority_tier=None)
        self.assertGreater(len(pool.fetchrow_calls), 0)


class DocumentToolTier1VisibilityTests(unittest.TestCase):
    """document__* must be a STRUCTURAL Tier-1 carve-out for a project-member
    specialist -- present in the actual assembled tool payload handed to the
    model, not just discoverable via Tier-2 query_tool_registry. Mirrors
    test_agent_turn_runtime_service.py's ProjectTaskToolTier1VisibilityTests
    exactly, for the sibling connector."""

    _DOCUMENT_NAMES = {"document__list", "document__read", "document__edit", "document__write"}

    def _toolset(self, *, project_id: str) -> dict:
        return {
            "core": agent_turn_runtime_service._core_direct_tool_names(),
            "connectors": set(),  # "document" never bound -- no path to bind it
            "tools": set(),
            "raw_tool_toggles": {},
            "capability_providers": frozenset(),
            "agent_install_id": "agent-pixel",
            "subagents_enabled": False,
            # feat/agent-context-grant: production builds this from the
            # agent's CONTEXT GRANT, so "project_ids" (the reach) is what
            # every visibility gate asks and "project_id" is only the write
            # target. A fixture carrying just the old scalar would agree
            # with itself and be wrong about the caller (CLAUDE.md).
            "project_id": project_id,
            "project_ids": [project_id] if project_id else [],
        }

    def _tool_names(self, *, specialist_toolset) -> list[str]:
        with patch.object(
            agent_turn_runtime_service.direct_chat_runtime_exports,
            "resolve_workspace_tool_capabilities",
            return_value=[],
        ), patch.object(
            agent_turn_runtime_service.direct_chat_runtime_exports,
            "_resolve_direct_chat_availability",
            return_value={},
        ):
            tools, _caps, _availability = agent_turn_runtime_service._direct_tool_bundle(
                workspace_id="ws-test",
                provider="openai",
                sender_class="owner",
                specialist_toolset=specialist_toolset,
            )
        return [str(t.get("name") or "") for t in tools]

    def test_project_member_specialist_gets_all_document_tools_in_tier1(self):
        names = set(self._tool_names(specialist_toolset=self._toolset(project_id="proj-1")))
        self.assertTrue(
            self._DOCUMENT_NAMES.issubset(names),
            f"missing: {self._DOCUMENT_NAMES - names}",
        )

    def test_non_member_specialist_never_gets_document_tools(self):
        names = set(self._tool_names(specialist_toolset=self._toolset(project_id="")))
        self.assertFalse(names & self._DOCUMENT_NAMES, f"unexpected leak: {names & self._DOCUMENT_NAMES}")

    def test_master_sage_turn_never_gets_document_tools(self):
        # specialist_toolset=None is the master/Sage path -- workspace-scoped
        # with no single owning project, same reasoning
        # ProjectTaskToolTier1VisibilityTests.test_master_sage_turn_never_gets_project_task_tools
        # gives for its own sibling assertion.
        names = set(self._tool_names(specialist_toolset=None))
        self.assertFalse(names & self._DOCUMENT_NAMES, f"unexpected leak: {names & self._DOCUMENT_NAMES}")

    def test_document_edit_tool_schema_is_well_formed(self):
        names_and_tools = {}
        with patch.object(
            agent_turn_runtime_service.direct_chat_runtime_exports,
            "resolve_workspace_tool_capabilities", return_value=[],
        ), patch.object(
            agent_turn_runtime_service.direct_chat_runtime_exports,
            "_resolve_direct_chat_availability", return_value={},
        ):
            tools, _caps, _availability = agent_turn_runtime_service._direct_tool_bundle(
                workspace_id="ws-test", provider="openai", sender_class="owner",
                specialist_toolset=self._toolset(project_id="proj-1"),
            )
        for t in tools:
            names_and_tools[t.get("name")] = t
        edit_tool = names_and_tools["document__edit"]
        self.assertEqual(edit_tool.get("connector_id"), "document")
        required = (edit_tool.get("parameters") or {}).get("required") or []
        self.assertEqual(set(required), {"path", "old_string", "new_string"})
        # The failure modes must be stated in the description an agent
        # actually reads, not just enforced silently at dispatch time.
        description = edit_tool.get("description") or ""
        self.assertIn("EXACTLY ONCE", description)
        self.assertIn("FAILS LOUDLY", description)


if __name__ == "__main__":
    unittest.main()
