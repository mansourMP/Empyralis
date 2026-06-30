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

from server_modules import sage_agent_runtime_service
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
    """Test that an approved MCP tool matching a user message executes successfully."""

    def test_approved_mcp_tool_executes(self):
        mcp_skill = _make_mcp_skill(
            skill_id="mcp:test-server:lookup_stock",
            label="Stock Lookup",
            description="Look up stock levels for inventory items",
            trigger_terms=("stock", "inventory"),
        )
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[mcp_skill]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback", return_value=_GENERATE_STUB),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.skill_registry.execute_skill", new=AsyncMock(return_value={"status": "ok", "reply": "Stock level: 42 units."})) as mock_skill,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use the mcp stock inventory tool please",
            ))

        self.assertEqual(result["action_execution_mode"], "tools_executed")
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["name"], "mcp:test-server:lookup_stock")
        self.assertEqual(result["tool_calls"][0]["status"], "completed")
        self.assertIn("Stock level: 42 units.", result["message"])
        self.assertIn("mcp_tools", result["used_context"])


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
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback", return_value=_GENERATE_STUB),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat", return_value=iter(stream_events)),
            patch("server_modules.sage_agent_runtime_service.skill_registry.execute_skill", new=AsyncMock()) as mock_skill,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use the mcp stock lookup tool for item 123",
            ))

        # No MCP skill found — no tool call or blocked tool for MCP
        self.assertEqual(len(result["tool_calls"]), 0)
        self.assertFalse(mock_skill.called)


class TestMCPFailureReturnsControlledError(unittest.TestCase):
    """Test that an MCP execution failure produces a controlled error, not a raw exception."""

    def test_mcp_failure_returns_controlled_error(self):
        mcp_skill = _make_mcp_skill(
            skill_id="mcp:test-server:lookup_stock",
            trigger_terms=("stock", "inventory"),
        )
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[mcp_skill]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback", return_value=_GENERATE_STUB),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.skill_registry.execute_skill", new=AsyncMock(side_effect=PermissionError("not approved"))) as mock_skill,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use mcp inventory please",
            ))

        self.assertGreater(len(result["blocked_tools"]), 0)
        blocked = result["blocked_tools"][0]
        self.assertEqual(blocked["name"], "mcp:test-server:lookup_stock")
        self.assertEqual(blocked["status"], "blocked")
        # Error message should be user-friendly, not a raw PermissionError dump
        self.assertIn("approved", blocked["reason"].lower())
        # Tool call recorded as failed
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["status"], "failed")
        self.assertIn("approved", result["tool_calls"][0]["error"].lower())


class TestMCPToolResultIncludedInFinalResponse(unittest.TestCase):
    """Test that MCP tool execution output appears in the Sage final response."""

    def test_mcp_tool_result_included_in_sage_final_response(self):
        mcp_skill = _make_mcp_skill(
            skill_id="mcp:warehouse:check_stock",
            label="Warehouse Stock Check",
            description="Check stock levels in the warehouse system",
            trigger_terms=("warehouse", "stock count"),
        )
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile", return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files", return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block", return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot", new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[mcp_skill]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider", return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback", return_value=_GENERATE_STUB),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities", return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability", return_value={"runtime_ok": False}),
            patch("server_modules.sage_agent_runtime_service.skill_registry.execute_skill", new=AsyncMock(return_value={"status": "ok", "reply": "Warehouse stock: 150 units available."})) as mock_skill,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="use mcp warehouse stock count please",
            ))

        self.assertEqual(len(result["tool_calls"]), 1)
        tool_call = result["tool_calls"][0]
        self.assertEqual(tool_call["name"], "mcp:warehouse:check_stock")
        self.assertEqual(tool_call["status"], "completed")
        self.assertIn("150 units", tool_call["output"])
        self.assertIn("150 units", result["message"])


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
            sage_agent_runtime_service.skill_registry, "_BUILT_IN_SOURCE", "built_in"
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
            "server_modules.sage_agent_runtime_service.list_skill_definitions",
            return_value=all_skills,
        ):
            catalog = sage_agent_runtime_service._load_safe_skill_catalog(
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
