"""
Phase A wiring tests — docs/design/mcp-applications-plan.md.

Covers the wiring of the already-built MCP client engine
(server_modules/mcp_registry_service.py — discovery, invocation, approval,
credential resolution, metering) into the live, structured tool-calling
loop:

  (a) a workspace's enabled+approved MCP tools appear as deferred (Tier-2)
      tool-registry entries with parseable, reversible tool names
      (McpRegistrySourceTests)
  (b) calling an mcp-namespaced tool dispatches to the MCP invoker with the
      correct server_id/tool_name/structured arguments
      (McpDispatchBranchTests)
  (c) the MCP approval gate still blocks unapproved tools, even when the
      authority-mandate gate itself passes (owner tier)
      (McpApprovalGateTests)
  (d) the EMPYRALIS_MCP_TOOLS_ENABLED kill switch, when off, produces zero
      MCP registry entries and zero MCP dispatch — flag on/off is otherwise
      the only difference in behavior (McpKillSwitchTests)

Registry state is seeded via the real mcp_registry_service.
upsert_workspace_mcp_server() with the Rust-kernel write gate mocked to
"allow" — the same pattern test_mcp_registry_service_rust_gate.py already
uses. (Calling upsert_workspace_mcp_server() WITHOUT this mock — as
test_mcp_registry_service.py's other tests do — fails in this sandbox with
McpRegistryRustGateError: unexpected_next_action; that's a pre-existing,
unrelated test-infra gap, not something introduced by this work.)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import direct_chat_operator_binding_service
from server_modules import direct_tool_execution_service
from server_modules import mcp_registry_service
from server_modules import skills_service
from server_modules import tool_registry_service


def _run(coro):
    return asyncio.run(coro)


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "save_mcp_server_registry",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class _McpRegistryFixtureMixin:
    """Shared setUp/tearDown: a temp-dir-backed MCP server registry with the
    Rust write-gate mocked to always allow (see module docstring)."""

    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "mcp_servers.json"
        self.registry_file_patcher = patch.object(
            mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", self.registry_path
        )
        self.registry_file_patcher.start()
        self.rust_gate_patcher = patch.object(
            mcp_registry_service.rust_runtime_kernel_client,
            "runtime_state_store_decision",
            return_value=dict(_ALLOW_DECISION),
        )
        self.rust_gate_patcher.start()

    def tearDown(self) -> None:
        self.rust_gate_patcher.stop()
        self.registry_file_patcher.stop()
        self.temp_dir.cleanup()
        super().tearDown()

    def _seed_server(
        self,
        *,
        workspace_id: str,
        server_id: str,
        label: str,
        enabled: bool = True,
        tools: list,
    ) -> dict:
        return mcp_registry_service.upsert_workspace_mcp_server(
            workspace_id=workspace_id,
            server_id=server_id,
            label=label,
            transport="streamable_http",
            endpoint="https://example.com/mcp",
            enabled=enabled,
            tools=tools,
        )


# ── (a) Registry source: deferred entries, discoverable, reversible names ──


class McpRegistrySourceTests(_McpRegistryFixtureMixin, unittest.TestCase):
    def test_enabled_approved_tool_appears_as_deferred_registry_entry(self) -> None:
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            tools=[
                {
                    "name": "search_pages",
                    "label": "Search Pages",
                    "description": "Search Notion pages by keyword.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                    "enabled": True,
                    "approved": True,
                },
            ],
        )

        payloads = mcp_registry_service.list_workspace_mcp_direct_tool_payloads("ws-1")
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["name"], "mcp__notion-work__search_pages")
        self.assertEqual(payloads[0]["connector_id"], "mcp")
        self.assertEqual(payloads[0]["parameters"]["properties"]["query"]["type"], "string")

        # Reversible: mcp_tool_name() <-> parse_mcp_tool_name()
        parsed = mcp_registry_service.parse_mcp_tool_name(payloads[0]["name"])
        self.assertEqual(parsed, {"server_id": "notion-work", "tool_name": "search_pages"})

        # Parseable by the dispatch-layer name router for free (splits on
        # the FIRST "__" only) -> connector_id="mcp", action_id="<server>__<tool>"
        connector_id, action_id = direct_chat_operator_binding_service.parse_tool_name(
            payloads[0]["name"]
        )
        self.assertEqual(connector_id, "mcp")
        self.assertEqual(action_id, "notion-work__search_pages")

        # Feeds tool_registry_service.build_registry_entries() as source #4
        # via availability_payload["mcp_tools"] (see the call-site comment in
        # sage_agent_runtime_service.py's _direct_tool_bundle()).
        entries = tool_registry_service.build_registry_entries([], {"mcp_tools": payloads})
        entry_names = {e.tool_name for e in entries}
        self.assertIn("mcp__notion-work__search_pages", entry_names)

        # Deferred, not always-on — the agent must discover it via
        # query_tool_registry, it must never be injected into every turn.
        self.assertNotIn("mcp__notion-work__search_pages", tool_registry_service.ALWAYS_ON_TOOL_NAMES)

        # Discoverable via keyword search — the query_tool_registry mechanism.
        # Keywords are drawn from name + description + connector_id, and the
        # server label / tool label are folded into the description, so a
        # query naming either the app or the action should surface it.
        for query in ("search notion pages", "notion"):
            results = tool_registry_service.search_tool_registry(query, entries)
            result_names = {r["function"]["name"] for r in results}
            self.assertIn(
                "mcp__notion-work__search_pages",
                result_names,
                f"query {query!r} did not surface the MCP tool",
            )

    def test_unapproved_tool_is_excluded(self) -> None:
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            tools=[
                {
                    "name": "delete_page",
                    "description": "Permanently delete a Notion page.",
                    "input_schema": {},
                    "enabled": True,
                    "approved": False,
                },
            ],
        )
        self.assertEqual(mcp_registry_service.list_workspace_mcp_direct_tool_payloads("ws-1"), [])

    def test_disabled_tool_is_excluded_even_if_approved(self) -> None:
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            tools=[
                {
                    "name": "archive_page",
                    "description": "Archive a Notion page.",
                    "input_schema": {},
                    "enabled": False,
                    "approved": True,
                },
            ],
        )
        self.assertEqual(mcp_registry_service.list_workspace_mcp_direct_tool_payloads("ws-1"), [])

    def test_disabled_server_contributes_no_entries_even_if_tool_approved(self) -> None:
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            enabled=False,
            tools=[
                {
                    "name": "search_pages",
                    "description": "Search Notion pages by keyword.",
                    "input_schema": {},
                    "enabled": True,
                    "approved": True,
                },
            ],
        )
        self.assertEqual(mcp_registry_service.list_workspace_mcp_direct_tool_payloads("ws-1"), [])

    def test_other_workspace_does_not_see_this_workspace_servers(self) -> None:
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            tools=[
                {
                    "name": "search_pages",
                    "description": "Search Notion pages by keyword.",
                    "input_schema": {},
                    "enabled": True,
                    "approved": True,
                },
            ],
        )
        self.assertEqual(mcp_registry_service.list_workspace_mcp_direct_tool_payloads("ws-other"), [])


# ── (b) Dispatch branch: mcp__ tool call reaches the MCP invoker ──────────


def _direct_tool_execution_callbacks() -> direct_tool_execution_service.DirectToolExecutionCallbacks:
    """Minimal callbacks fixture for skills_service.execute_single_direct_
    tool_call_async — only parse_tool_name (must be the real router, so
    "mcp__<server>__<tool>" actually resolves to connector_id="mcp") and
    tool_arguments_payload (passthrough) matter for the MCP branch; the rest
    are unused stubs required only by the dataclass's required fields."""
    return direct_tool_execution_service.DirectToolExecutionCallbacks(
        compact_step_detail=lambda value: None,
        titleize_direct_step_token=lambda value: str(value or ""),
        run_async_tool_call=lambda awaitable: awaitable,
        parse_tool_name=direct_chat_operator_binding_service.parse_tool_name,
        tool_arguments_payload=lambda payload: payload if isinstance(payload, dict) else {},
        parse_json_object_loose=lambda value: {},
        safe_positive_int=lambda value, default=0: default,
        normalize_reasoning_effort=lambda value: None,
        build_direct_local_tool_config=lambda *a, **k: ("", {}),
        format_direct_local_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        build_direct_tool_config=lambda *a, **k: {},
        format_direct_tool_result=lambda result: json.dumps(result, ensure_ascii=False),
        llm_task=lambda *a, **k: {},
        web_search=lambda query: [],
        web_fetch=lambda url: "",
        search_memory_notebook=lambda *a, **k: [],
        get_memory_notebook_excerpt=lambda *a, **k: {},
    )


class McpDispatchBranchTests(unittest.TestCase):
    def test_mcp_namespaced_tool_call_dispatches_with_structured_args(self) -> None:
        tool_call = {
            "name": "mcp__notion-work__search_pages",
            "arguments": {"query": "roadmap", "limit": 5},
        }
        with patch.object(
            mcp_registry_service,
            "invoke_workspace_mcp_tool_async",
            new=AsyncMock(
                return_value={
                    "status": "ok",
                    "reply": "Found 3 pages.",
                    "mcp": {"server_id": "notion-work", "tool_name": "search_pages", "payload": {"count": 3}},
                }
            ),
        ) as mock_invoke:
            result = _run(
                skills_service.execute_single_direct_tool_call_async(
                    tool_call=tool_call,
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={"authority_tier": "owner", "metadata": {"sage_agent_id": "sage-main"}},
                    callbacks=_direct_tool_execution_callbacks(),
                )
            )

        self.assertTrue(mock_invoke.called)
        _, kwargs = mock_invoke.call_args
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["server_id"], "notion-work")
        self.assertEqual(kwargs["tool_name"], "search_pages")
        self.assertEqual(kwargs["arguments"], {"query": "roadmap", "limit": 5})
        # Return value is a plain string (same shape every other direct-chat
        # tool call returns), carrying the human-readable reply through.
        self.assertIsInstance(result, str)
        self.assertIn("Found 3 pages.", result)

    def test_malformed_mcp_tool_name_raises_instead_of_misdispatching(self) -> None:
        # "mcp__onlyoneseg" has no second "__" separator -> not a valid
        # <server>__<tool> pair. Must fail loudly, not silently no-op or
        # fall through to the custom-OAuth-connector branch.
        tool_call = {"name": "mcp__onlyoneseg", "arguments": {}}
        with patch.object(
            mcp_registry_service, "invoke_workspace_mcp_tool_async", new=AsyncMock()
        ) as mock_invoke:
            with self.assertRaises(RuntimeError):
                _run(
                    skills_service.execute_single_direct_tool_call_async(
                        tool_call=tool_call,
                        workspace_id="ws-1",
                        thread_id="thread-1",
                        session_ctx={"authority_tier": "owner"},
                        callbacks=_direct_tool_execution_callbacks(),
                    )
                )
        self.assertFalse(mock_invoke.called)

    def test_authority_mandate_gate_still_blocks_mcp_calls_for_unattributed_audience_tier(self) -> None:
        """The pre-existing authority-mandate gate (skills_service.
        _authority_mandate_gate) runs BEFORE the MCP branch and must still
        block a non-owner-tier caller from reaching it — the same as it
        already does for every other connector."""
        tool_call = {"name": "mcp__notion-work__search_pages", "arguments": {"query": "x"}}
        with patch.object(
            mcp_registry_service, "invoke_workspace_mcp_tool_async", new=AsyncMock()
        ) as mock_invoke:
            with patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()):
                with self.assertRaises(RuntimeError):
                    _run(
                        skills_service.execute_single_direct_tool_call_async(
                            tool_call=tool_call,
                            workspace_id="ws-1",
                            thread_id="thread-1",
                            # authority_tier omitted entirely -> fails closed to
                            # "audience", and an MCP tool has no audience_safe
                            # descriptor, so the mandate gate must block it.
                            session_ctx={},
                            callbacks=_direct_tool_execution_callbacks(),
                        )
                    )
        self.assertFalse(mock_invoke.called)


class McpLiveGenerationLoopDispatchTests(unittest.TestCase):
    """Covers direct_chat_operator_binding_service.execute_single_direct_
    tool_call — the closure empirically verified (by tracing
    direct_chat_generation_service.stream_provider_backed_direct_chat's
    tool-execution ThreadPoolExecutor call through DirectChatGenerationServices
    -> build_direct_chat_generation_services -> build_direct_chat_tool_
    runtime_bindings) to be THE actual dispatch point the live Sage/web-chat
    generation loop calls for every structured tool call — distinct from
    (and not reachable through) skills_service.execute_single_direct_tool_
    call_async, which currently has no live caller. Both were given the same
    MCP branch; this test exercises the real one directly, mirroring
    McpDispatchBranchTests above for the sync entrypoint."""

    def test_operator_binding_live_closure_dispatches_mcp_call_with_structured_args(self) -> None:
        bindings = direct_chat_operator_binding_service.build_direct_chat_tool_runtime_bindings(
            # Unused by the MCP branch under test (only direct_tool_execution_
            # callbacks() is read before the "mcp" check fires) — stubbed out.
            direct_chat_runtime_facade_callbacks=lambda: None,
            direct_tool_execution_callbacks=_direct_tool_execution_callbacks,
            execute_single_direct_tool_call_fn=lambda **kwargs: None,
        )
        tool_call = {"name": "mcp__notion-work__search_pages", "arguments": {"query": "roadmap"}}
        with patch.object(
            mcp_registry_service,
            "invoke_workspace_mcp_tool",
            return_value={"status": "ok", "reply": "Found 3 pages.", "mcp": {"payload": {"count": 3}}},
        ) as mock_invoke:
            result = bindings.execute_single_direct_tool_call(
                tool_call=tool_call,
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"metadata": {"sage_agent_id": "sage-main"}},
            )

        self.assertTrue(mock_invoke.called)
        _, kwargs = mock_invoke.call_args
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["server_id"], "notion-work")
        self.assertEqual(kwargs["tool_name"], "search_pages")
        self.assertEqual(kwargs["arguments"], {"query": "roadmap"})
        self.assertIsInstance(result, str)
        self.assertIn("Found 3 pages.", result)

    def test_operator_binding_live_closure_respects_the_kill_switch(self) -> None:
        bindings = direct_chat_operator_binding_service.build_direct_chat_tool_runtime_bindings(
            direct_chat_runtime_facade_callbacks=lambda: None,
            direct_tool_execution_callbacks=_direct_tool_execution_callbacks,
            execute_single_direct_tool_call_fn=lambda **kwargs: None,
        )
        tool_call = {"name": "mcp__notion-work__search_pages", "arguments": {}}
        with patch.dict(os.environ, {"EMPYRALIS_MCP_TOOLS_ENABLED": "0"}):
            with patch.object(mcp_registry_service, "invoke_workspace_mcp_tool") as mock_invoke:
                with self.assertRaises(RuntimeError):
                    bindings.execute_single_direct_tool_call(
                        tool_call=tool_call,
                        workspace_id="ws-1",
                        thread_id="thread-1",
                        session_ctx={},
                    )
            self.assertFalse(mock_invoke.called)


# ── (c) The MCP-specific approval gate still blocks unapproved tools ──────


class McpApprovalGateTests(_McpRegistryFixtureMixin, unittest.TestCase):
    def _metering_patches(self):
        from server_modules import agent_action_metering_service

        return (
            patch.object(agent_action_metering_service, "record_started", new=AsyncMock()),
            patch.object(agent_action_metering_service, "record_completed", new=AsyncMock()),
            patch.object(agent_action_metering_service, "record_failed", new=AsyncMock()),
            patch.object(agent_action_metering_service, "record_blocked", new=AsyncMock()),
        )

    def test_invoker_blocks_unapproved_tool_even_though_mandate_gate_would_pass(self) -> None:
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            tools=[
                {
                    "name": "delete_page",
                    "description": "Permanently delete a Notion page.",
                    "input_schema": {},
                    "enabled": True,
                    "approved": False,  # discovered but never approved
                },
            ],
        )
        started, completed, failed, blocked = self._metering_patches()
        with started, completed, failed, blocked as mock_blocked:
            with self.assertRaises(PermissionError):
                _run(
                    mcp_registry_service.invoke_workspace_mcp_tool_async(
                        workspace_id="ws-1",
                        server_id="notion-work",
                        tool_name="delete_page",
                        arguments={"page_id": "abc123"},
                    )
                )
            self.assertTrue(mock_blocked.called)

    def test_dispatch_branch_propagates_the_approval_block(self) -> None:
        """End-to-end: an MCP tool call for an unapproved tool, routed
        through the real (non-mocked) skills_service dispatch branch and the
        real (non-mocked) mcp_registry_service invoker, must raise —
        never silently succeed, never fall through to another connector."""
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            tools=[
                {
                    "name": "delete_page",
                    "description": "Permanently delete a Notion page.",
                    "input_schema": {},
                    "enabled": True,
                    "approved": False,
                },
            ],
        )
        started, completed, failed, blocked = self._metering_patches()
        tool_call = {"name": "mcp__notion-work__delete_page", "arguments": {"page_id": "abc123"}}
        with started, completed, failed, blocked:
            with self.assertRaises(PermissionError):
                _run(
                    skills_service.execute_single_direct_tool_call_async(
                        tool_call=tool_call,
                        workspace_id="ws-1",
                        thread_id="thread-1",
                        session_ctx={"authority_tier": "owner"},
                        callbacks=_direct_tool_execution_callbacks(),
                    )
                )


# ── (d) Kill switch: flag OFF => zero MCP entries, zero MCP dispatch ──────


class McpKillSwitchTests(_McpRegistryFixtureMixin, unittest.TestCase):
    def test_flag_off_yields_no_registry_entries_even_for_an_approved_tool(self) -> None:
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            tools=[
                {
                    "name": "search_pages",
                    "description": "Search Notion pages by keyword.",
                    "input_schema": {},
                    "enabled": True,
                    "approved": True,
                },
            ],
        )
        # Flag ON (default) -> the tool is there.
        with patch.dict(os.environ, {"EMPYRALIS_MCP_TOOLS_ENABLED": "1"}):
            self.assertTrue(mcp_registry_service.mcp_tools_enabled())
            self.assertEqual(len(mcp_registry_service.list_workspace_mcp_direct_tool_payloads("ws-1")), 1)

        # Flag OFF -> zero entries, and the flag helper itself reports off.
        with patch.dict(os.environ, {"EMPYRALIS_MCP_TOOLS_ENABLED": "0"}):
            self.assertFalse(mcp_registry_service.mcp_tools_enabled())
            self.assertEqual(mcp_registry_service.list_workspace_mcp_direct_tool_payloads("ws-1"), [])
            # build_registry_entries() itself is flag-agnostic by design (it
            # just consumes whatever availability_payload["mcp_tools"] the
            # caller hands it) — the kill switch lives at the source. Confirm
            # the source really does hand it nothing when off:
            entries = tool_registry_service.build_registry_entries(
                [], {"mcp_tools": mcp_registry_service.list_workspace_mcp_direct_tool_payloads("ws-1")}
            )
            self.assertEqual([e for e in entries if e.connector_id == "mcp"], [])

    def test_flag_off_blocks_the_dispatch_branch_even_for_an_approved_tool(self) -> None:
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            tools=[
                {
                    "name": "search_pages",
                    "description": "Search Notion pages by keyword.",
                    "input_schema": {},
                    "enabled": True,
                    "approved": True,
                },
            ],
        )
        tool_call = {"name": "mcp__notion-work__search_pages", "arguments": {"query": "x"}}
        with patch.dict(os.environ, {"EMPYRALIS_MCP_TOOLS_ENABLED": "0"}):
            with patch.object(
                mcp_registry_service, "invoke_workspace_mcp_tool_async", new=AsyncMock()
            ) as mock_invoke:
                with self.assertRaises(RuntimeError):
                    _run(
                        skills_service.execute_single_direct_tool_call_async(
                            tool_call=tool_call,
                            workspace_id="ws-1",
                            thread_id="thread-1",
                            session_ctx={"authority_tier": "owner"},
                            callbacks=_direct_tool_execution_callbacks(),
                        )
                    )
            self.assertFalse(mock_invoke.called)


# ── (e) MCP failures are visible: the protocol's own isError flag, and the
# server/tool/error detail on the trace event the user reads (MAN-125 item 3)


class _FakeCallToolResult:
    """Stand-in for mcp.types.CallToolResult."""

    def __init__(self, *, text: str, is_error: bool = False):
        self.isError = is_error
        self.structuredContent = None
        self.content = [type("_Item", (), {"text": text})()]


class McpFailureVisibilityTests(_McpRegistryFixtureMixin, unittest.TestCase):
    def _metering_patches(self):
        from server_modules import agent_action_metering_service

        return (
            patch.object(agent_action_metering_service, "record_started", new=AsyncMock()),
            patch.object(agent_action_metering_service, "record_completed", new=AsyncMock()),
            patch.object(agent_action_metering_service, "record_failed", new=AsyncMock()),
            patch.object(agent_action_metering_service, "record_blocked", new=AsyncMock()),
        )

    def _seed_approved(self) -> None:
        self._seed_server(
            workspace_id="ws-1",
            server_id="notion-work",
            label="Notion (Work)",
            tools=[
                {
                    "name": "search_pages",
                    "description": "Search Notion pages by keyword.",
                    "input_schema": {},
                    "enabled": True,
                    "approved": True,
                },
            ],
        )

    def test_mcp_result_is_error_reads_the_protocol_flag(self) -> None:
        self.assertTrue(mcp_registry_service.mcp_result_is_error(_FakeCallToolResult(text="x", is_error=True)))
        self.assertFalse(mcp_registry_service.mcp_result_is_error(_FakeCallToolResult(text="x")))
        self.assertTrue(mcp_registry_service.mcp_result_is_error({"isError": True}))
        self.assertFalse(mcp_registry_service.mcp_result_is_error({"isError": False}))
        self.assertFalse(mcp_registry_service.mcp_result_is_error(None))

    def test_is_error_result_returns_an_explicit_failure_not_a_green_ok(self) -> None:
        """The regression: a server replying isError=true with "Error: repo not
        found" used to come back stamped status "ok", paint a green activity row
        and hand the honesty guard a "real success" to anchor a reply on."""
        self._seed_approved()
        started, completed, failed, blocked = self._metering_patches()
        with started, completed as mock_completed, failed as mock_failed, blocked:
            with patch.object(
                mcp_registry_service,
                "_invoke_mcp_tool_with_auth_recovery_async",
                new=AsyncMock(return_value=_FakeCallToolResult(text="Error: repo not found", is_error=True)),
            ):
                result = _run(
                    mcp_registry_service.invoke_workspace_mcp_tool_async(
                        workspace_id="ws-1",
                        server_id="notion-work",
                        tool_name="search_pages",
                        arguments={"query": "x"},
                    )
                )

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["is_error"])
        # Billed as the failure it is, not as a completed call.
        self.assertTrue(mock_failed.called)
        self.assertFalse(mock_completed.called)
        detail = result["mcp_detail"]
        self.assertEqual(detail["server_id"], "notion-work")
        self.assertEqual(detail["tool_name"], "search_pages")
        self.assertEqual(detail["error_code"], "mcp_tool_error")
        self.assertIn("repo not found", detail["error"])

    def test_is_error_result_formats_to_a_payload_the_classifier_calls_failed(self) -> None:
        from server_modules import tool_result_status

        self._seed_approved()
        started, completed, failed, blocked = self._metering_patches()
        with started, completed, failed, blocked:
            with patch.object(
                mcp_registry_service,
                "_invoke_mcp_tool_with_auth_recovery_async",
                new=AsyncMock(return_value=_FakeCallToolResult(text="Error: repo not found", is_error=True)),
            ):
                result = _run(
                    mcp_registry_service.invoke_workspace_mcp_tool_async(
                        workspace_id="ws-1",
                        server_id="notion-work",
                        tool_name="search_pages",
                        arguments={"query": "x"},
                    )
                )

        formatted = mcp_registry_service.format_mcp_tool_result(result)
        self.assertTrue(tool_result_status.tool_result_failed(formatted))
        self.assertFalse(json.loads(formatted)["ok"])

    def test_successful_result_still_formats_and_classifies_as_success(self) -> None:
        from server_modules import tool_result_status

        self._seed_approved()
        started, completed, failed, blocked = self._metering_patches()
        with started, completed as mock_completed, failed as mock_failed, blocked:
            with patch.object(
                mcp_registry_service,
                "_invoke_mcp_tool_with_auth_recovery_async",
                new=AsyncMock(return_value=_FakeCallToolResult(text='{"pages": ["a", "b"]}')),
            ):
                result = _run(
                    mcp_registry_service.invoke_workspace_mcp_tool_async(
                        workspace_id="ws-1",
                        server_id="notion-work",
                        tool_name="search_pages",
                        arguments={"query": "x"},
                    )
                )

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["is_error"])
        self.assertTrue(mock_completed.called)
        self.assertFalse(mock_failed.called)
        self.assertFalse(tool_result_status.tool_result_failed(mcp_registry_service.format_mcp_tool_result(result)))

    def test_raised_mcp_failure_is_annotated_with_server_tool_and_error(self) -> None:
        """MCP calls fail by RAISING; the exception now carries the same facts
        the metering ledger records, so the trace event can show them."""
        self._seed_approved()
        started, completed, failed, blocked = self._metering_patches()
        with started, completed, failed as mock_failed, blocked:
            with patch.object(
                mcp_registry_service,
                "_invoke_mcp_tool_with_auth_recovery_async",
                new=AsyncMock(side_effect=RuntimeError("MCP tool call timed out after 3 attempts (60s each)")),
            ):
                with self.assertRaises(RuntimeError) as caught:
                    _run(
                        mcp_registry_service.invoke_workspace_mcp_tool_async(
                            workspace_id="ws-1",
                            server_id="notion-work",
                            tool_name="search_pages",
                            arguments={"query": "x"},
                        )
                    )

        self.assertTrue(mock_failed.called)
        detail = getattr(caught.exception, "mcp_detail", None)
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["server_id"], "notion-work")
        self.assertEqual(detail["tool_name"], "search_pages")
        self.assertEqual(detail["error_code"], "RuntimeError")
        self.assertIn("timed out", detail["error"])
        self.assertEqual(detail["server_label"], "Notion (Work)")

    def test_trace_detail_is_redacted_like_args_preview(self) -> None:
        """Third-party MCP servers put bearer tokens and keys in their error
        strings, and this block is rendered to the user and persisted."""
        detail = mcp_registry_service.build_mcp_trace_detail(
            server_id="notion-work",
            tool_name="search_pages",
            error="401 Unauthorized: Bearer sk-abcdefghijklmnop1234567890 rejected",
        )
        self.assertNotIn("sk-abcdefghijklmnop1234567890", detail["error"])
        self.assertIn("redacted", detail["error"])

    def test_trace_detail_for_a_non_mcp_tool_is_none(self) -> None:
        from server_modules import direct_chat_generation_service

        self.assertIsNone(direct_chat_generation_service._mcp_trace_detail_for_tool("web__search"))
        built = direct_chat_generation_service._mcp_trace_detail_for_tool(
            "mcp__notion-work__search_pages", error="boom", error_code="ok_false"
        )
        self.assertEqual(built["server_id"], "notion-work")
        self.assertEqual(built["tool_name"], "search_pages")
        self.assertEqual(built["error"], "boom")
        self.assertEqual(built["error_code"], "ok_false")


if __name__ == "__main__":
    unittest.main()
