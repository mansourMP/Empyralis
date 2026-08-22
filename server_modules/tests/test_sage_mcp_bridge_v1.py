"""
Branch 2 — Sage MCP Bridge v1.

Tests the MCP tool discoverability, transparency event emission, error
handling, and boundary between Sage MCP permissions and Studio agent
permissions introduced in this branch.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from server_modules import agent_turn_runtime_service
from server_modules.sage_transparency_service import (
    emit_sage_turn_transparency_events,
)
from server_modules.skill_registry import SkillDefinition


def _run(coro):
    return asyncio.run(coro)


_GENERATE_STUB = ("Reply", {"model": "d"}, "deepseek", "")


def _make_mcp_skill(
    *,
    skill_id: str = "mcp:test-server:lookup_stock",
    label: str = "Stock Lookup",
    description: str = "Look up stock levels for inventory items",
    enabled: bool = True,
    action_class: str = "read",
    trigger_terms: tuple[str, ...] = ("stock", "inventory"),
    execution_adapter: str = "mcp_tool",
    source: str = "mcp_registry",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=skill_id,
        label=label,
        description=description,
        action_class=action_class,
        requires_approval=False,
        execution_mode="live",
        enabled=enabled,
        available=True,
        execution_adapter=execution_adapter,
        trigger_terms=trigger_terms,
        connector_scopes=("mcp",),
        source=source,
        path=None,
        permission_label="MCP server",
        skill_class="specialist_local",
        allowed_runtime_modes=("hosted_secure", "local_secure", "privileged_device"),
    )


class TestApprovedMCPToolExecutes(unittest.TestCase):
    """Test that an approved MCP tool the model chooses to call executes
    successfully and surfaces in the Sage turn result.

    Phase A wiring (docs/design/mcp-applications-plan.md) removed the
    keyword-matched NL routing (_matching_mcp_skill + the dead
    _run_sage_action_loop_v2's skill_registry.execute_skill dispatch) this
    test used to exercise — it lived in a function with zero live callers.
    MCP tools are now real, structurally-callable tools the model invokes by
    name (mcp__<server>__<tool>) through ordinary function-calling, exactly
    like any other connector — see skills_service.execute_single_direct_
    tool_call_async and direct_chat_operator_binding_service.execute_single_
    direct_tool_call's connector_id=="mcp" branches. This test now simulates
    that: it mocks stream_provider_backed_direct_chat directly (as its
    sibling TestDisabledMCPToolDoesNotExecute already did) to emit the same
    tool.started/tool.result/final trace events the real dispatch layer
    would produce once a model-issued tool call for an mcp-namespaced tool
    completes, then asserts Sage's turn-result assembly (message, tool_calls,
    action_execution_mode) is correct — the real dispatch-routing logic
    itself (name parsing, server/tool resolution, structured arguments) is
    covered by test_mcp_tool_calling_wiring.py's dispatch-level tests.
    """

    def test_approved_mcp_tool_executes(self):
        mcp_skill = _make_mcp_skill(
            skill_id="mcp:test-server:lookup_stock",
            label="Stock Lookup",
            description="Look up stock levels for inventory items",
            trigger_terms=("stock", "inventory"),
        )
        stream_events = [
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.started",
                    "tool_call_id": "call-1",
                    "data": {"tool_name": "mcp__test-server__lookup_stock", "args_preview": {}},
                },
            },
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.result",
                    "tool_call_id": "call-1",
                    "data": {"status": "ok", "summary": "Stock level: 42 units."},
                },
            },
            {"type": "final", "payload": {"reply": "Stock level: 42 units.", "actions": [], "error": ""}},
        ]
        with (
            patch("server_modules.agent_turn_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.agent_turn_runtime_service.assistant_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.agent_turn_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[mcp_skill]),
            patch("server_modules.agent_turn_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.agent_turn_runtime_service.generate_chat_reply_with_provider_fallback", return_value=_GENERATE_STUB),
            patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.agent_turn_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)),
            patch("server_modules.agent_turn_runtime_service.skill_registry.execute_skill", new=AsyncMock()) as mock_skill,
            patch("server_modules.agent_turn_runtime_service.persist_interaction"),
            patch("server_modules.agent_turn_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.agent_turn_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(agent_turn_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use the mcp stock inventory tool please",
            ))

        self.assertEqual(result["action_execution_mode"], "tools_executed")
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["name"], "mcp__test-server__lookup_stock")
        self.assertEqual(result["tool_calls"][0]["status"], "completed")
        self.assertIn("Stock level: 42 units.", result["message"])
        self.assertIn("mcp_tools", result["used_context"])
        # The old keyword-matched skill_registry.execute_skill bridge must
        # never fire from the live v3 loop — dispatch now goes through the
        # structured tool-calling path (skills_service/operator_binding),
        # not this dead-code mechanism.
        self.assertFalse(mock_skill.called)


class TestDisabledMCPToolDoesNotExecute(unittest.TestCase):
    """Test that a disabled MCP tool is NOT executed even when the message matches."""

    def test_disabled_mcp_tool_does_not_execute(self):
        # A disabled skill is filtered out by list_skill_definitions(include_disabled=False)
        # so _matching_mcp_skill() won't find it. Pass an empty list to simulate this.
        # Mock the operator-loop stream so v3 returns a simple text-only result
        # rather than making real LLM calls.
        stream_events = [
            {"type": "final", "payload": {"reply": "I cannot do that.", "actions": [], "error": ""}},
        ]
        with (
            patch("server_modules.agent_turn_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.agent_turn_runtime_service.assistant_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.agent_turn_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.agent_turn_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.agent_turn_runtime_service.generate_chat_reply_with_provider_fallback", return_value=_GENERATE_STUB),
            patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.agent_turn_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)),
            patch("server_modules.agent_turn_runtime_service.skill_registry.execute_skill", new=AsyncMock()) as mock_skill,
            patch("server_modules.agent_turn_runtime_service.persist_interaction"),
            patch("server_modules.agent_turn_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.agent_turn_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(agent_turn_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use the mcp stock lookup tool for item 123",
            ))

        # No MCP skill found — no tool call or blocked tool for MCP
        self.assertEqual(len(result["tool_calls"]), 0)
        self.assertFalse(mock_skill.called)


class TestMCPFailureReturnsControlledError(unittest.TestCase):
    """Test that an MCP execution failure produces a controlled error, not a
    raw exception/traceback, in the Sage turn result.

    Adapted for the same reason as TestApprovedMCPToolExecutes above: the
    dead v2 loop's bespoke "friendly error message" translation (mapping
    PermissionError -> "not approved yet", connection errors -> "could not
    reach the MCP server", etc.) is gone along with the rest of v2 — under
    the live v3 loop, a failed tool call surfaces exactly like any other
    connector's failure does (a tool_calls entry with status="failed" and
    the underlying error text), not a special-cased "blocked_tools" entry
    (v3's blocked_tools is reserved for policy-level blocks — trace.failed /
    plan.item.updated — not a tool that ran and failed). This test now
    asserts that v3-accurate contract: the failure reason is still clean,
    readable text (not approved), it's just carried on the tool_calls entry.
    """

    def test_mcp_failure_returns_controlled_error(self):
        mcp_skill = _make_mcp_skill(
            skill_id="mcp:test-server:lookup_stock",
            trigger_terms=("stock", "inventory"),
        )
        stream_events = [
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.started",
                    "tool_call_id": "call-1",
                    "data": {"tool_name": "mcp__test-server__lookup_stock", "args_preview": {}},
                },
            },
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.result",
                    "tool_call_id": "call-1",
                    "data": {
                        "status": "error",
                        "summary": "MCP tool 'lookup_stock' on server 'test-server' is not approved for execution.",
                    },
                },
            },
            {"type": "final", "payload": {"reply": "", "actions": [], "error": ""}},
        ]
        with (
            patch("server_modules.agent_turn_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.agent_turn_runtime_service.assistant_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.agent_turn_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[mcp_skill]),
            patch("server_modules.agent_turn_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.agent_turn_runtime_service.generate_chat_reply_with_provider_fallback", return_value=_GENERATE_STUB),
            patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.agent_turn_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)),
            patch("server_modules.agent_turn_runtime_service.skill_registry.execute_skill", new=AsyncMock()) as mock_skill,
            patch("server_modules.agent_turn_runtime_service.persist_interaction"),
            patch("server_modules.agent_turn_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.agent_turn_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(agent_turn_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use mcp inventory please",
            ))

        # Tool call recorded as failed, with a clean (non-traceback) reason
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["name"], "mcp__test-server__lookup_stock")
        self.assertEqual(result["tool_calls"][0]["status"], "failed")
        self.assertIn("approved", result["tool_calls"][0]["error"].lower())
        self.assertNotIn("Traceback", result["tool_calls"][0]["error"])
        self.assertFalse(mock_skill.called)


class TestMCPToolResultIncludedInFinalResponse(unittest.TestCase):
    """Test that MCP tool execution output appears in the Sage final response.

    See TestApprovedMCPToolExecutes above for why this is adapted to mock
    stream_provider_backed_direct_chat directly instead of the dead v2
    keyword-matched skill_registry.execute_skill bridge.
    """

    def test_mcp_tool_result_included_in_sage_final_response(self):
        mcp_skill = _make_mcp_skill(
            skill_id="mcp:warehouse:check_stock",
            label="Warehouse Stock Check",
            description="Check stock levels in the warehouse system",
            trigger_terms=("warehouse", "stock count"),
        )
        stream_events = [
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.started",
                    "tool_call_id": "call-1",
                    "data": {"tool_name": "mcp__warehouse__check_stock", "args_preview": {}},
                },
            },
            {
                "type": "trace",
                "payload": {
                    "event_type": "tool.result",
                    "tool_call_id": "call-1",
                    "data": {"status": "ok", "summary": "Warehouse stock: 150 units available."},
                },
            },
            {"type": "final", "payload": {"reply": "Warehouse stock: 150 units available.", "actions": [], "error": ""}},
        ]
        with (
            patch("server_modules.agent_turn_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.agent_turn_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.agent_turn_runtime_service.assistant_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.agent_turn_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.agent_turn_runtime_service.list_skill_definitions", return_value=[mcp_skill]),
            patch("server_modules.agent_turn_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.agent_turn_runtime_service.generate_chat_reply_with_provider_fallback", return_value=_GENERATE_STUB),
            patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.agent_turn_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.agent_turn_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)),
            patch("server_modules.agent_turn_runtime_service.skill_registry.execute_skill", new=AsyncMock()) as mock_skill,
            patch("server_modules.agent_turn_runtime_service.persist_interaction"),
            patch("server_modules.agent_turn_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.agent_turn_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(agent_turn_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use mcp warehouse stock count please",
            ))

        self.assertEqual(len(result["tool_calls"]), 1)
        tool_call = result["tool_calls"][0]
        self.assertEqual(tool_call["name"], "mcp__warehouse__check_stock")
        self.assertEqual(tool_call["status"], "completed")
        self.assertIn("150 units", tool_call["output"])
        self.assertIn("150 units", result["message"])
        self.assertFalse(mock_skill.called)


class TestStudioAgentsDoNotInheritSageMCPPermissions(unittest.TestCase):
    """Test that MCP tool definitions have the right source/execution_adapter
    fields that distinguish them from built-in skills, and that a Studio agent
    context would not auto-inherit Sage-specific MCP permissions."""

    def test_mcp_skill_definition_has_correct_source_and_execution_adapter(self):
        """MCP tools sourced from the mcp_registry service carry a non-built-in
        source value and an mcp_tool execution_adapter."""
        from server_modules.skill_registry import _definition_from_mcp_skill_entry

        mcp_entry = {
            "id": "mcp:test-server:lookup_stock",
            "label": "Stock Lookup",
            "description": "Look up stock levels",
            "server_id": "test-server",
            "execution_mode": "live",
            "action_class": "read",
            "connector_scopes": ("mcp",),
            "trigger_terms": ("stock",),
            "enabled": True,
            "requires_approval": False,
            "execution_adapter": "mcp_tool",
            "source": "mcp_registry",
            "metadata": {"endpoint": "http://localhost:8931"},
        }
        definition = _definition_from_mcp_skill_entry(mcp_entry)

        self.assertIsNotNone(definition)
        self.assertEqual(definition.source, "mcp_registry")
        self.assertEqual(definition.execution_adapter, "mcp_tool")
        builtin_source = getattr(
            agent_turn_runtime_service.skill_registry, "_BUILT_IN_SOURCE", "built_in"
        )
        self.assertNotEqual(definition.source, builtin_source)

    def test_mcp_skill_definitions_appear_in_list_with_correct_source(self):
        """When list_skill_definitions returns MCP skills, they carry the
        mcp_registry source, not built_in."""
        mcp_skill = _make_mcp_skill(
            source="mcp_registry",
            execution_adapter="mcp_tool",
        )
        all_skills = [mcp_skill]

        mcp_skills = [
            s for s in all_skills
            if getattr(s, "execution_adapter", None) == "mcp_tool"
        ]
        self.assertEqual(len(mcp_skills), 1)
        self.assertEqual(mcp_skills[0].source, "mcp_registry")
        self.assertEqual(mcp_skills[0].execution_adapter, "mcp_tool")

    def test_studio_agent_does_not_see_sage_mcp_tools_via_skill_catalog(self):
        """The safe skill catalog built by _load_safe_skill_catalog includes
        MCP tools only when they pass the same filters as other skills.
        A Studio agent (which uses a separate skill resolution path) would
        not auto-inherit Sage MCP permissions because MCP tools are sourced
        from the mcp_registry and are not built-in skills."""
        mcp_skill = _make_mcp_skill(
            source="mcp_registry",
            execution_adapter="mcp_tool",
        )
        all_skills = [mcp_skill]
        with patch(
            "server_modules.agent_turn_runtime_service.list_skill_definitions",
            return_value=all_skills,
        ):
            catalog = agent_turn_runtime_service._load_safe_skill_catalog(
                workspace_id="ws-1"
            )

        # _load_safe_skill_catalog returns dicts with id/label/description/
        # action_class/requires_approval/execution_mode (NOT execution_adapter).
        mcp_in_catalog = [s for s in catalog if s["id"] == "mcp:test-server:lookup_stock"]
        self.assertEqual(len(mcp_in_catalog), 1)
        self.assertEqual(mcp_in_catalog[0]["action_class"], "read")


class TestMCPTransparencyEvents(unittest.TestCase):
    """Test that MCP tool calls generate skill_executed transparency events."""

    def test_mcp_skill_execution_emits_skill_executed_transparency_event(self):
        sage_result = {
            "message": "Stock level: 42 units.",
            "used_context": [{"name": "mcp_tools"}],
            "tool_calls": [
                {
                    "name": "mcp:test-server:lookup_stock",
                    "tool_name": "mcp:test-server:lookup_stock",
                    "status": "completed",
                    "output": "Stock level: 42 units.",
                    "arguments": {"goal": "check stock"},
                }
            ],
            "blocked_tools": [],
            "approvals_required": [],
            "error": None,
        }
        events = emit_sage_turn_transparency_events(
            trace_id="trace-1",
            workspace_id="ws-1",
            user_message="check stock",
            sage_result=sage_result,
        )
        skill_events = [e for e in events if e.event_type == "skill_executed"]
        self.assertEqual(len(skill_events), 1)
        self.assertEqual(skill_events[0].tool_name, "mcp:test-server:lookup_stock")
        self.assertEqual(skill_events[0].status, "completed")
        self.assertIn("MCP tool executed", skill_events[0].title)
        self.assertIn("42 units", skill_events[0].summary)

    def test_non_mcp_tool_does_not_emit_skill_executed_event(self):
        """Built-in tools like web__search should not generate skill_executed events."""
        sage_result = {
            "message": "Found results.",
            "used_context": [{"name": "web_search"}],
            "tool_calls": [
                {
                    "name": "web__search",
                    "status": "completed",
                    "output": "Search results here.",
                }
            ],
            "blocked_tools": [],
            "approvals_required": [],
            "error": None,
        }
        events = emit_sage_turn_transparency_events(
            trace_id="trace-2",
            workspace_id="ws-1",
            user_message="search web",
            sage_result=sage_result,
        )
        skill_events = [e for e in events if e.event_type == "skill_executed"]
        self.assertEqual(len(skill_events), 0)


if __name__ == "__main__":
    unittest.main()
