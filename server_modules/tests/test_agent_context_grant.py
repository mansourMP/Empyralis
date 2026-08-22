"""feat/agent-context-grant: an agent reaches ONLY the projects it was granted.

FOUNDER'S CASE, verbatim (CLAUDE.md, 2026-08-20): *"even if I create this
agent on behalf of other businesses it wouldn't see my task or my context
about the platform, even though I created this agent for my father's
business."* Before this, an agent's reach was
``workspace_agent_installs.project_id`` -- a single home project it was
placed in -- and an install with NO project (a workspace-level agent, which
is what the founder's own decision makes every new agent) fell through the
document reader to THE ASKING PERSON'S ENTIRE REACH, i.e. every project in
the workspace for an owner. That is the hole this closes.

Two things this file is deliberately built to prove, because both are the
kind of claim that reads as true and is not:

  ZERO ROWS, NOT FILTERED AFTER THE FACT. The cross-project assertions below
  read the ARGUMENTS the repository actually sent to the database, not just
  the tool's return value. A tool that fetched project Q's tasks and dropped
  them in Python would pass a results-only assertion and still be a
  disclosure (CLAUDE.md: "a filtered item list beside an unfiltered summary
  is still a disclosure, just an arithmetic one").

  ABSENT AND EMPTY ARE DIFFERENT. An install with no grant recorded is the
  founder's 13 existing agents; silently revoking all of them would be a
  worse failure than the one being fixed. An install granted ``[]`` reaches
  nothing. Both are asserted, separately, in both directions.

Fixture code (_FakeTransaction/_FakeConnection/_FakeAcquire/_QueuedFakePool/
_callbacks/_call) is a port of test_document_native_tools.py's, duplicated
for the same reason that file gives for duplicating it from
test_project_task_native_tools.py.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import unittest
from unittest.mock import patch

from server_modules import agent_context_grant_service as grants
from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_execution_service
from server_modules import agent_turn_runtime_service
from server_modules import skills_service

_SERVER_MODULES = pathlib.Path(__file__).resolve().parents[1]

OWN_PROJECT = "proj-fathers-business"
OTHER_PROJECT = "proj-empyralis-platform"


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
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


def _install_row(*, granted, home_project_id=OWN_PROJECT) -> dict:
    """The one row the grant resolver reads. ``granted=None`` means the key
    is ABSENT -- an install that predates the grant."""
    metadata: dict = {"specialist_mode": "owner_edit"}
    if granted is not None:
        metadata[grants.GRANT_METADATA_KEY] = list(granted)
    return {"project_id": home_project_id, "metadata": metadata}


def _task_row(**overrides) -> dict:
    row = {
        "id": "task-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "project_id": OWN_PROJECT,
        "number": 1,
        "title": "A task",
        "description": "",
        "status": "todo",
        "priority": 0,
        "assignee_agent_id": None,
        "assignee_user_id": None,
        "created_by": "agent-1",
        "due_at": None,
        "plan": None,
        "parent_task_id": None,
        "completed_by_user_id": None,
        "completed_by_agent_id": None,
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _document_row(**overrides) -> dict:
    row = {
        "id": "doc-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "project_id": OWN_PROJECT,
        "title": "Runbook",
        "path": "runbook.md",
        "body": "# Runbook\n",
        "created_by": "agent-1",
        "updated_by": "agent-1",
        "metadata": {},
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _callbacks() -> direct_tool_execution_service.DirectToolExecutionCallbacks:
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


def _call(tool_name: str, arguments: dict, *, pool: _QueuedFakePool):
    async def _resolve(*_args, **_kwargs):
        return pool

    with patch(
        "server_modules.project_documents_repository.control_plane_repository.ensure_control_plane_schema",
    ) as mock_documents_schema, patch(
        "server_modules.project_tasks_service.control_plane_repository.ensure_control_plane_schema",
    ) as mock_tasks_schema, patch(
        "server_modules.control_plane_repository.ensure_control_plane_schema",
    ) as mock_shared_schema:
        mock_documents_schema.side_effect = _resolve
        mock_tasks_schema.side_effect = _resolve
        mock_shared_schema.side_effect = _resolve
        raw = skills_service.execute_single_direct_tool_call(
            tool_call={"name": tool_name, "arguments": arguments},
            workspace_id="ws-1",
            thread_id="thread-1",
            session_ctx={
                "agent_install_id": "agent-1",
                "tenant_id": "tenant-1",
                "authority_tier": "owner",
                "metadata": {"user_id": "owner-1"},
            },
            callbacks=_callbacks(),
        )
    return json.loads(raw)


def _all_query_args(calls) -> list:
    flat: list = []
    for _query, args in calls:
        for arg in args:
            if isinstance(arg, (list, tuple)):
                flat.extend(str(a) for a in arg)
            else:
                flat.append(str(arg))
    return flat


class GrantResolutionTests(unittest.TestCase):
    """The pure core. Three states, and they must never collapse."""

    def test_an_absent_key_is_LEGACY_and_keeps_the_home_project(self):
        """The founder's 13 existing agents. Silently revoking them would be
        a worse failure than the one this feature fixes."""
        grant = grants.grant_from_install_fields(
            metadata={"specialist_mode": "owner_edit"}, home_project_id=OWN_PROJECT,
        )
        self.assertEqual(grant.status, grants.STATUS_LEGACY)
        self.assertEqual(grant.project_ids, [OWN_PROJECT])
        self.assertEqual(grant.write_project_id, OWN_PROJECT)

    def test_a_json_null_is_also_LEGACY(self):
        grant = grants.grant_from_install_fields(
            metadata={grants.GRANT_METADATA_KEY: None}, home_project_id=OWN_PROJECT,
        )
        self.assertEqual(grant.status, grants.STATUS_LEGACY)
        self.assertEqual(grant.project_ids, [OWN_PROJECT])

    def test_an_EMPTY_list_is_a_real_grant_of_NOTHING_not_an_absence(self):
        """The default for every new agent. It must NOT read as legacy, or a
        new agent would be born with the old workspace-wide behaviour -- the
        exact thing the founder asked to be impossible."""
        grant = grants.grant_from_install_fields(
            metadata={grants.GRANT_METADATA_KEY: []}, home_project_id=OWN_PROJECT,
        )
        self.assertEqual(grant.status, grants.STATUS_GRANT)
        self.assertEqual(grant.project_ids, [])
        self.assertEqual(grant.write_project_id, "")
        self.assertFalse(grant.has_reach)

    def test_a_grant_decides_even_against_a_home_project_it_does_not_name(self):
        """The home-project column must never widen a grant. An agent moved
        out of a project keeps only what it was granted."""
        grant = grants.grant_from_install_fields(
            metadata={grants.GRANT_METADATA_KEY: [OTHER_PROJECT]}, home_project_id=OWN_PROJECT,
        )
        self.assertEqual(grant.project_ids, [OTHER_PROJECT])
        self.assertNotIn(OWN_PROJECT, grant.project_ids)

    def test_the_write_target_is_the_home_project_when_it_is_granted(self):
        grant = grants.grant_from_install_fields(
            metadata={grants.GRANT_METADATA_KEY: [OTHER_PROJECT, OWN_PROJECT]},
            home_project_id=OWN_PROJECT,
        )
        self.assertEqual(sorted(grant.project_ids), sorted([OWN_PROJECT, OTHER_PROJECT]))
        self.assertEqual(grant.write_project_id, OWN_PROJECT)

    def test_several_granted_projects_and_no_home_among_them_has_NO_write_target(self):
        """Never pick a winner silently -- CLAUDE.md records exactly that
        shape as the multi-agent provisioning-clobber bug."""
        grant = grants.grant_from_install_fields(
            metadata={grants.GRANT_METADATA_KEY: ["proj-a", "proj-b"]}, home_project_id="",
        )
        self.assertEqual(grant.write_project_id, "")
        self.assertTrue(grant.has_reach)

    def test_a_single_granted_project_is_an_unambiguous_write_target(self):
        grant = grants.grant_from_install_fields(
            metadata={grants.GRANT_METADATA_KEY: ["proj-only"]}, home_project_id="",
        )
        self.assertEqual(grant.write_project_id, "proj-only")

    def test_a_corrupt_value_resolves_to_NOTHING_never_back_to_legacy(self):
        """A wrong-shaped value must not buy back the wider pre-grant reach."""
        for bad in ("proj-1", 7, {"proj-1": True}, True):
            with self.subTest(bad=bad):
                grant = grants.grant_from_install_fields(
                    metadata={grants.GRANT_METADATA_KEY: bad}, home_project_id=OWN_PROJECT,
                )
                self.assertEqual(grant.status, grants.STATUS_GRANT)
                self.assertEqual(grant.project_ids, [])

    def test_ids_are_deduped_sorted_and_blank_entries_dropped(self):
        grant = grants.grant_from_install_fields(
            metadata={grants.GRANT_METADATA_KEY: ["b", "a", "a", "  ", "", " c "]},
            home_project_id="",
        )
        self.assertEqual(grant.project_ids, ["a", "b", "c"])

    def test_metadata_stored_as_a_json_STRING_still_resolves(self):
        """The SQLite fallback hands whole metadata blobs back as text."""
        grant = grants.grant_from_install_fields(
            metadata=json.dumps({grants.GRANT_METADATA_KEY: [OWN_PROJECT]}),
            home_project_id="",
        )
        self.assertEqual(grant.project_ids, [OWN_PROJECT])


class GrantReadFailsClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_missing_install_row_is_UNAVAILABLE_not_legacy(self):
        pool = _QueuedFakePool(fetchrow_results=[None])

        async def _resolve(*_a, **_k):
            return pool

        with patch(
            "server_modules.control_plane_repository.ensure_control_plane_schema",
        ) as mocked:
            mocked.side_effect = _resolve
            grant = await grants.resolve_agent_project_grant(
                tenant_id="tenant-1", workspace_id="ws-1", agent_install_id="agent-ghost",
            )
        self.assertEqual(grant.status, grants.STATUS_UNAVAILABLE)
        self.assertEqual(grant.project_ids, [])

    async def test_a_raising_read_is_UNAVAILABLE_not_legacy(self):
        class _Boom:
            async def fetchrow(self, *_a, **_k):
                raise RuntimeError("database is on fire")

            def acquire(self):
                raise RuntimeError("database is on fire")

        async def _resolve(*_a, **_k):
            return _Boom()

        with patch(
            "server_modules.control_plane_repository.ensure_control_plane_schema",
        ) as mocked:
            mocked.side_effect = _resolve
            grant = await grants.resolve_agent_project_grant(
                tenant_id="tenant-1", workspace_id="ws-1", agent_install_id="agent-1",
            )
        self.assertEqual(grant.status, grants.STATUS_UNAVAILABLE)

    async def test_a_blank_identity_is_UNAVAILABLE(self):
        for kwargs in (
            {"tenant_id": "", "workspace_id": "ws-1", "agent_install_id": "a"},
            {"tenant_id": "t", "workspace_id": "", "agent_install_id": "a"},
            {"tenant_id": "t", "workspace_id": "ws-1", "agent_install_id": ""},
        ):
            with self.subTest(**kwargs):
                grant = await grants.resolve_agent_project_grant(**kwargs)
                self.assertEqual(grant.status, grants.STATUS_UNAVAILABLE)


class CrossProjectIsolationTests(unittest.TestCase):
    """THE property, end to end through the REAL tool dispatch.

    Every assertion here reads what was sent to the DATABASE, not only what
    came back: an implementation that fetched the other project's rows and
    dropped them in Python would satisfy a results-only test and still be a
    disclosure."""

    def test_a_granted_agent_lists_tasks_from_its_OWN_project_only_zero_rows_from_the_other(self):
        pool = _QueuedFakePool(
            fetchrow_results=[_install_row(granted=[OWN_PROJECT])],
            fetch_results=[[_task_row()]],
        )
        result = _call("project_task__list", {}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual([t["project_id"] for t in result["tasks"]], [OWN_PROJECT])
        sent = _all_query_args(pool.fetch_calls)
        self.assertIn(OWN_PROJECT, sent)
        self.assertNotIn(
            OTHER_PROJECT, sent,
            "the other project's id must never reach the database, not even to be filtered out after",
        )

    def test_an_agent_granted_NOTHING_never_queries_the_task_board_at_all(self):
        """Zero rows AND zero queries -- 'granted nothing' is answered before
        the database is touched, so there is nothing to filter."""
        pool = _QueuedFakePool(
            fetchrow_results=[_install_row(granted=[])],
            fetch_results=[[_task_row()]],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call("project_task__list", {}, pool=pool)
        self.assertIn("no project", str(ctx.exception))
        self.assertEqual(pool.fetch_calls, [], "no task query may be issued for an ungranted agent")

    def test_a_granted_agent_cannot_read_a_task_that_lives_in_the_other_project(self):
        pool = _QueuedFakePool(
            fetchrow_results=[
                _install_row(granted=[OWN_PROJECT]),
                _task_row(id="task-other", project_id=OTHER_PROJECT),
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call("project_task__get", {"task_id": "task-other"}, pool=pool)
        self.assertIn("different project", str(ctx.exception))

    def test_a_granted_agent_lists_documents_from_its_OWN_project_only(self):
        pool = _QueuedFakePool(
            fetchrow_results=[_install_row(granted=[OWN_PROJECT])],
            fetch_results=[[_document_row()]],
        )
        result = _call("document__list", {}, pool=pool)
        self.assertTrue(result["ok"])
        sent = _all_query_args(pool.fetch_calls)
        self.assertIn(OWN_PROJECT, sent)
        self.assertNotIn(
            OTHER_PROJECT, sent,
            "a document query must be scoped to the grant, never filtered afterwards",
        )

    def test_an_agent_granted_NOTHING_never_queries_documents_at_all(self):
        pool = _QueuedFakePool(
            fetchrow_results=[_install_row(granted=[])],
            fetch_results=[[_document_row(project_id=OTHER_PROJECT)]],
        )
        with self.assertRaises(RuntimeError) as ctx:
            _call("document__list", {}, pool=pool)
        self.assertIn("reach", str(ctx.exception))
        self.assertEqual(pool.fetch_calls, [], "no document query may be issued for an ungranted agent")

    def test_an_agent_granted_NOTHING_cannot_write_a_document_either(self):
        pool = _QueuedFakePool(fetchrow_results=[_install_row(granted=[])])
        with self.assertRaises(RuntimeError):
            _call("document__write", {"title": "Leak"}, pool=pool)

    def test_LEGACY_still_reaches_its_home_project_exactly_as_before(self):
        """The migration property. An install with no grant recorded behaves
        identically to the pre-grant code -- this is what keeps the founder's
        existing agents working."""
        pool = _QueuedFakePool(
            fetchrow_results=[_install_row(granted=None)],
            fetch_results=[[_task_row()]],
        )
        result = _call("project_task__list", {}, pool=pool)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["tasks"]), 1)
        self.assertIn(OWN_PROJECT, _all_query_args(pool.fetch_calls))

    def test_an_agent_granted_the_OTHER_project_reads_that_one_and_not_its_home(self):
        """A grant is not additive with the home project column."""
        pool = _QueuedFakePool(
            fetchrow_results=[_install_row(granted=[OTHER_PROJECT], home_project_id=OWN_PROJECT)],
            fetch_results=[[_task_row(project_id=OTHER_PROJECT)]],
        )
        result = _call("project_task__list", {}, pool=pool)
        self.assertTrue(result["ok"])
        sent = _all_query_args(pool.fetch_calls)
        self.assertIn(OTHER_PROJECT, sent)
        self.assertNotIn(OWN_PROJECT, sent)


class ToolVisibilityFollowsTheGrantTests(unittest.TestCase):
    """The prompt-time half. An ungranted agent is not shown project tools
    at all, so refusing at execution time is the second line, not the only
    one."""

    def _toolset(self, *, project_ids, project_id=""):
        return {
            "core": set(),
            "connectors": set(),
            "tools": set(),
            "raw_tool_toggles": {},
            "mandate_audience_tools": [],
            "capability_providers": frozenset(),
            "agent_install_id": "agent-1",
            "subagents_enabled": False,
            "project_id": project_id,
            "project_ids": list(project_ids),
        }

    def test_a_granted_agent_is_offered_the_project_scoped_tools(self):
        toolset = self._toolset(project_ids=[OWN_PROJECT], project_id=OWN_PROJECT)
        for name in ("project_task__list", "document__read", "goal__list"):
            with self.subTest(name=name):
                self.assertTrue(agent_turn_runtime_service._specialist_tool_allowed(name, toolset))

    def test_an_ungranted_agent_is_offered_none_of_them(self):
        toolset = self._toolset(project_ids=[])
        for name in ("project_task__list", "document__read", "goal__list"):
            with self.subTest(name=name):
                self.assertFalse(agent_turn_runtime_service._specialist_tool_allowed(name, toolset))

    def test_read_tools_survive_a_grant_with_no_single_write_target(self):
        """Reach, not the write target, is what visibility asks -- an agent
        granted two projects still gets its read tools."""
        toolset = self._toolset(project_ids=["proj-a", "proj-b"], project_id="")
        self.assertTrue(agent_turn_runtime_service._specialist_tool_allowed("document__read", toolset))


class TheModelCannotWidenItsOwnGrantTests(unittest.TestCase):
    """Structural, because a behavioural test can only cover the write paths
    that exist today. The grant is resolved server-side from the install
    row; nothing a model says may reach it -- the same posture
    agent_goals.attempt_count and memory_write_private already take."""

    def test_the_grant_key_is_not_a_configure_agent_patch_key(self):
        """fleet__configure_agent / empyralis_configure_agent are callable BY
        AN AGENT. A grant a model can patch is not a boundary."""
        from server_modules import fleet_tools

        self.assertNotIn(grants.GRANT_METADATA_KEY, fleet_tools._ALLOWED_CONFIGURE_KEYS)
        for key in fleet_tools._ALLOWED_CONFIGURE_KEYS:
            self.assertNotIn(
                "context_project", key,
                f"'{key}' looks like a context-grant patch key -- the grant must have no model-reachable writer",
            )

    def test_no_registered_tool_takes_a_project_grant_argument(self):
        for descriptor in skills_service._builtin_tool_descriptors():
            params = (descriptor.parameters or {}).get("properties") or {}
            for name in params:
                self.assertNotIn(
                    "context_project", str(name).lower(),
                    f"{descriptor.tool_name} exposes '{name}' -- a model must not be able to name its own grant",
                )

    def test_no_project_scoped_tool_takes_a_project_id_from_the_model(self):
        """The tool arguments are the model's words. A project id taken from
        them would be the grant widened by whoever is talking.

        Allowlisted with a written verdict, the way every drift test in this
        codebase handles a real exception:

          fleet__get_project_activity -- a FLEET-MANAGEMENT tool, master/
          operator only (skills_service dispatches it under connector_id ==
          "fleet", never under project_task/document/goal), and workspace-
          wide by design. It is NOT one of the three project-scoped
          connectors this grant governs. Flagged rather than silently
          accepted: an operator agent can still name any project id and read
          that project's activity ledger, which is its own question and its
          own change.
        """
        allowed_argument_project_reads = {"fleet__get_project_activity"}
        source = (_SERVER_MODULES / "skills_service.py").read_text()
        tree = ast.parse(source)
        found: list[str] = []
        for node in ast.walk(tree):
            key = None
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                    and node.value.id == "argument_payload":
                key = getattr(node.slice, "value", None)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "get" and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "argument_payload" \
                    and node.args and isinstance(node.args[0], ast.Constant):
                key = node.args[0].value
            if isinstance(key, str) and key in ("project_id", "project_ids", grants.GRANT_METADATA_KEY):
                found.append(key)
        self.assertEqual(
            len(found), len(allowed_argument_project_reads),
            "skills_service grew a NEW project-shaped read off the model's own arguments "
            f"(found {len(found)}, allowlisted {sorted(allowed_argument_project_reads)}) -- "
            "if it is inside the project_task/document/goal dispatch it is a grant widened by the model",
        )

    def test_the_three_project_scoped_connectors_resolve_their_scope_server_side(self):
        """Each of the three dispatch blocks must resolve its scope from the
        install row, not from anything the model handed in."""
        source = (_SERVER_MODULES / "skills_service.py").read_text()
        self.assertEqual(
            source.count("_grants.resolve_agent_project_grant("), 4,
            "two for the CALLER (project_task__*, goal__*) and two for the TARGET of "
            "project_task__assign / goal__create -- handing work to an agent that cannot "
            "open the project produces a task nobody can ever work. document__* resolves "
            "the same grant one level down, through agent_document_scope_service.",
        )
        self.assertIn("resolve_agent_document_project_scope(", source)

    def test_the_only_metadata_writer_for_the_grant_is_the_owner_gated_route(self):
        """The storage key may be WRITTEN in exactly two places -- the
        owner-gated route, and agent creation's own default. A write is the
        key used as a DICT KEY (that is the only shape that reaches
        update_workspace_agent_install's metadata merge); a mention in a
        comment or docstring is not a writer."""
        writers: list[str] = []
        for path in sorted(_SERVER_MODULES.glob("*.py")):
            if path.name == "agent_context_grant_service.py":
                continue
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:  # pragma: no cover - defensive
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key in node.keys:
                    if isinstance(key, ast.Attribute) and key.attr == "GRANT_METADATA_KEY":
                        writers.append(path.name)
                    elif isinstance(key, ast.Constant) and key.value == grants.GRANT_METADATA_KEY:
                        writers.append(path.name)
        self.assertEqual(
            sorted(set(writers)), ["fleet_tools.py", "routes_fleet.py"],
            "a new module writing the grant key is a second door -- prove it is owner-gated first",
        )


class NewAgentsDefaultToNoneTests(unittest.TestCase):
    """A new agent is born with an EXPLICIT grant, never with no grant at
    all -- 'no grant recorded' means 'predates the grant' and would hand a
    brand-new agent the old workspace-membership behaviour."""

    def test_agent_creation_writes_an_explicit_grant(self):
        source = (_SERVER_MODULES / "fleet_tools.py").read_text()
        self.assertIn("GRANT_METADATA_KEY", source)
        tree = ast.parse(source)
        create_fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "fleet_create_agent"
        )
        writes = [
            node for node in ast.walk(create_fn)
            if isinstance(node, ast.Attribute) and node.attr == "GRANT_METADATA_KEY"
        ]
        self.assertTrue(
            writes,
            "fleet_create_agent must stamp the grant, or a new agent is born on the legacy path",
        )

    def test_the_creation_grant_is_exactly_the_project_it_was_placed_in(self):
        """Placement at creation IS the grant, and it is the ONLY thing
        inherited. Everything wider takes a deliberate act in Configure."""
        source = (_SERVER_MODULES / "fleet_tools.py").read_text()
        self.assertIn(
            "_context_grants.GRANT_METADATA_KEY: [_project_id] if _project_id else []",
            source,
        )


if __name__ == "__main__":
    unittest.main()


class ContextGrantRouteTests(unittest.IsolatedAsyncioTestCase):
    """The ONLY writer. Exercised as the real handler functions with their
    collaborators patched -- not as a code reading."""

    def setUp(self):
        from server_modules import routes_fleet

        self.routes = routes_fleet
        self.saved: dict = {}

        async def fake_tenant(_ws):
            return "tenant-1"

        async def fake_enforce(*_a, **_k):
            return None

        async def fake_list_projects(*, tenant_id, workspace_id, include_archived=False):
            return [
                {"id": OWN_PROJECT, "name": "Father's business"},
                {"id": OTHER_PROJECT, "name": "Empyralis platform"},
            ]

        async def fake_visible(*_a, **_k):
            return None  # a workspace owner sees every project

        self._patches = [
            patch.object(routes_fleet, "_resolve_tenant", fake_tenant),
            patch.object(routes_fleet, "_enforce_agent_project_access", fake_enforce),
            patch.object(routes_fleet, "_visible_project_ids", fake_visible),
            patch("server_modules.projects_repository.list_projects", fake_list_projects),
            patch.object(
                routes_fleet.auth_module, "enforce_workspace_access",
                lambda _user, ws, minimum_role="viewer": ws,
            ),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def _patch_install(self, *, granted, home_project_id=OWN_PROJECT):
        async def fake_update(install_id, *, tenant_id, workspace_id, metadata=None, **_k):
            self.saved["install_id"] = install_id
            self.saved["metadata"] = dict(metadata or {})
            return {
                "id": install_id,
                "project_id": home_project_id,
                "metadata": dict(metadata or {}),
            }

        async def fake_resolve(*, tenant_id, workspace_id, agent_install_id):
            return grants.grant_from_install_fields(
                metadata=_install_row(granted=granted, home_project_id=home_project_id)["metadata"],
                home_project_id=home_project_id,
            )

        self._patches.append(
            patch("server_modules.agent_registry_repository.update_workspace_agent_install", fake_update)
        )
        self._patches.append(
            patch.object(grants, "resolve_agent_project_grant", fake_resolve)
        )
        for p in self._patches[-2:]:
            p.start()

    async def test_get_reports_LEGACY_distinctly_from_granted_nothing(self):
        self._patch_install(granted=None)
        legacy = await self.routes.fleet_agent_context_projects(
            request=None, workspace_id="ws-1", agent_id="agent-1", current_user={"user_id": "owner-1"},
        )
        self.assertTrue(legacy["ok"])
        self.assertTrue(legacy["is_legacy"], "an install with no grant recorded must SAY so, not draw an empty list")
        self.assertEqual(sorted(p["id"] for p in legacy["projects"]), sorted([OTHER_PROJECT, OWN_PROJECT]))

    async def test_put_stores_the_whole_list_and_can_revoke_everything(self):
        self._patch_install(granted=[OWN_PROJECT])
        result = await self.routes.fleet_agent_set_context_projects(
            request=None, workspace_id="ws-1", agent_id="agent-1",
            body=self.routes.FleetAgentContextGrantRequest(project_ids=[]),
            current_user={"user_id": "owner-1"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.saved["metadata"][grants.GRANT_METADATA_KEY], [])
        self.assertFalse(
            result["is_legacy"],
            "revoking everything must store an EXPLICIT empty grant, never leave the install on the legacy path",
        )

    async def test_put_refuses_a_project_that_is_not_in_this_workspace(self):
        from fastapi import HTTPException

        self._patch_install(granted=[OWN_PROJECT])
        with self.assertRaises(HTTPException) as ctx:
            await self.routes.fleet_agent_set_context_projects(
                request=None, workspace_id="ws-1", agent_id="agent-1",
                body=self.routes.FleetAgentContextGrantRequest(project_ids=[OWN_PROJECT, "proj-somebody-elses"]),
                current_user={"user_id": "owner-1"},
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertNotIn(
            "metadata", self.saved,
            "a partially-applied grant is a screen that lies about what it saved -- refuse the whole write",
        )

    async def test_put_requires_owner_role(self):
        """A viewer must not be able to widen an agent's reach."""
        seen: list = []
        self.routes.auth_module.enforce_workspace_access = (
            lambda _user, ws, minimum_role="viewer": (seen.append(minimum_role), ws)[1]
        )
        self._patch_install(granted=[OWN_PROJECT])
        await self.routes.fleet_agent_set_context_projects(
            request=None, workspace_id="ws-1", agent_id="agent-1",
            body=self.routes.FleetAgentContextGrantRequest(project_ids=[OWN_PROJECT]),
            current_user={"user_id": "owner-1"},
        )
        self.assertEqual(seen, ["owner"])
