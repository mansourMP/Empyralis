"""
Tests for Sage Browser Certification (Branch 1).

Covers:
- Web search independence from Agent Computer availability
- Browser tool blocking when Agent Computer is offline
- Browser tool execution when Agent Computer is online
- Browser action transparency and trace events
- Intent detection (web search vs browser automation)

Key module under test: sage_agent_runtime_service.py
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from server_modules import (
    direct_chat_tool_catalog_service,
    sage_agent_runtime_service,
)


def _run(coro):
    return asyncio.run(coro)


def _trace(event_type, *, tool_call_id=None, data=None):
    return {
        "type": "trace",
        "payload": {
            "event_type": event_type,
            "tool_call_id": tool_call_id,
            "data": dict(data or {}),
        },
    }


class SageWebSearchIndependenceTests(unittest.TestCase):
    """Web search (web__search / web__fetch) must work even when Agent Computer is offline."""

    def test_web_search_triggers_action_loop_when_offline(self):
        """_message_might_need_sage_action_loop returns True for web search queries."""
        self.assertTrue(
            sage_agent_runtime_service._message_might_need_sage_action_loop(
                "search the web for OpenClaw browser docs"
            )
        )
        self.assertTrue(
            sage_agent_runtime_service._message_might_need_sage_action_loop(
                "look up the latest news about Empyralis"
            )
        )

    def test_web_search_tools_survive_offline_filtering(self):
        """_direct_tool_bundle keeps web__search and web__fetch when Agent Computer is offline."""
        availability = {
            "runtime_ok": False,
            "local_gateway_online": False,
            "local_worker_online": False,
        }
        with (
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities",
                return_value=[],
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value=availability,
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._build_direct_chat_tools",
                return_value=[{"name": "browser__navigate", "description": "Navigate browser"}],
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._build_local_direct_chat_tools",
                return_value=[],
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._build_builtin_direct_chat_tools",
                return_value=[
                    {"name": "web__search", "description": "Search the web"},
                    {"name": "web__fetch", "description": "Fetch a URL"},
                ],
            ),
        ):
            tools, _caps, _avail = sage_agent_runtime_service._direct_tool_bundle(
                workspace_id="ws-1", provider="openai",
            )

        tool_names = [t["name"] for t in tools]
        self.assertIn("web__search", tool_names, "web__search should survive offline filter")
        self.assertIn("web__fetch", tool_names, "web__fetch should survive offline filter")
        self.assertNotIn(
            "browser__navigate", tool_names,
            "browser__navigate should be filtered when Agent Computer is offline",
        )

    def test_sage_web_search_executes_via_action_loop_while_offline(self):
        """Full handle_sage_chat flow for web search with Agent Computer offline."""
        stream_events = [
            _trace(
                "tool.started",
                tool_call_id="call-search-1",
                data={"tool_name": "web__search", "args_preview": {"query": "Empyralis docs"}},
            ),
            _trace(
                "tool.result",
                tool_call_id="call-search-1",
                data={"status": "ok", "summary": "Found docs at https://docs.empyralis.com"},
            ),
            {"type": "final", "payload": {"reply": "Found Empyralis docs.", "actions": [], "error": ""}},
        ]
        availability = {
            "runtime_ok": False,
            "local_gateway_online": False,
        }
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                  return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                  return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block",
                  return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot",
                  new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                  return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback"),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities",
                return_value=[],
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value=availability,
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
                return_value=iter(stream_events),
            ) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="search the web for Empyralis docs",
            ))

        self.assertEqual(result["action_execution_mode"], "tools_executed")
        self.assertTrue(result["tool_calls"], "Expected at least one tool call")
        self.assertEqual(result["tool_calls"][0]["name"], "web__search")
        self.assertEqual(result["message"], "Found Empyralis docs.")
        self.assertTrue(mock_stream.called)
        self.assertIn("trace_events", result)
        self.assertIn("transparency_events", result)


class SageBrowserBlockingTests(unittest.TestCase):
    """Browser tool calls must be blocked when Agent Computer is offline."""

    def test_browser_open_blocked_when_offline(self):
        """_blocked_agent_computer_tool_for_message returns blocked entry when offline."""
        availability = {
            "runtime_ok": False,
            "local_gateway_online": False,
        }
        result = sage_agent_runtime_service._blocked_agent_computer_tool_for_message(
            "Open https://example.com", availability,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "browser__navigate")
        self.assertEqual(result["reason"], "agent_computer_unavailable")
        self.assertEqual(result["status"], "blocked")

    def test_browser_open_blocked_with_gateway_paired_but_offline(self):
        """Agent Computer with a selected_gateway_id but no online flags is offline."""
        availability = {
            "runtime_ok": False,
            "local_gateway_online": False,
            "selected_gateway_id": "gw-1",
        }
        result = sage_agent_runtime_service._blocked_agent_computer_tool_for_message(
            "Open https://example.com", availability,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "browser__navigate")
        self.assertEqual(result["status"], "blocked")

    def test_shell_and_screenshot_blocked_when_offline(self):
        """Non-browser agent-computer tools (shell, screenshot) are also blocked."""
        availability = {
            "runtime_ok": False,
            "local_gateway_online": False,
        }
        for msg in ("run command: ls -la", "take a screenshot", "read file /tmp/test"):
            result = sage_agent_runtime_service._blocked_agent_computer_tool_for_message(
                msg, availability,
            )
            self.assertIsNotNone(result, f"Expected blocked for: {msg}")
            self.assertEqual(result["reason"], "agent_computer_unavailable")

    def test_browser_navigates_when_online_via_gateway(self):
        """_blocked_agent_computer_tool_for_message returns None when gateway is online."""
        availability = {
            "runtime_ok": True,
            "local_gateway_online": True,
        }
        result = sage_agent_runtime_service._blocked_agent_computer_tool_for_message(
            "Open https://example.com", availability,
        )
        self.assertIsNone(result)

    def test_browser_navigates_when_online_via_verified_gateway(self):
        """_blocked_agent_computer_tool_for_message returns None with verified gateway."""
        availability = {
            "verified_user_device_gateway": {"gateway_id": "gw-1"},
        }
        result = sage_agent_runtime_service._blocked_agent_computer_tool_for_message(
            "Open https://example.com", availability,
        )
        self.assertIsNone(result)

    def test_browser_navigates_when_online_via_capability_truth(self):
        """_blocked_agent_computer_tool_for_message returns None with capability truth."""
        availability = {
            "capability_truth": {
                "my_computer": {
                    "local_tools_available": True,
                },
            },
        }
        result = sage_agent_runtime_service._blocked_agent_computer_tool_for_message(
            "Open https://example.com", availability,
        )
        self.assertIsNone(result)


class SageBrowserOnlineFlowTests(unittest.TestCase):
    """Full flow tests for browser tool execution when Agent Computer is online."""

    def test_browser_action_executes_when_online(self):
        """Full handle_sage_chat with browser action succeeds when online."""
        stream_events = [
            _trace(
                "tool.started",
                tool_call_id="call-br-1",
                data={"tool_name": "browser__navigate", "args_preview": {"url": "https://example.com"}},
            ),
            _trace(
                "tool.result",
                tool_call_id="call-br-1",
                data={"status": "ok", "summary": "Navigated to example.com"},
            ),
            {"type": "final", "payload": {"reply": "Done navigating.", "actions": [], "error": ""}},
        ]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                  return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                  return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block",
                  return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot",
                  new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                  return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback"),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities",
                return_value=[],
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
                return_value=iter(stream_events),
            ) as mock_stream,
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="open https://example.com in the browser",
            ))

        self.assertEqual(result["message"], "Done navigating.")
        self.assertEqual(result["tool_calls"][0]["name"], "browser__navigate")
        self.assertEqual(result["tool_calls"][0]["status"], "completed")
        self.assertEqual(result["action_execution_mode"], "tools_executed")

    def test_browser_action_emits_transparency_events(self):
        """Browser actions produce both trace_events and transparency_events with browser_action type."""
        stream_events = [
            _trace(
                "tool.started",
                tool_call_id="call-br-2",
                data={"tool_name": "browser__navigate", "args_preview": {"url": "https://example.com"}},
            ),
            _trace(
                "tool.result",
                tool_call_id="call-br-2",
                data={"status": "ok", "summary": "Navigated"},
            ),
            {"type": "final", "payload": {"reply": "Done.", "actions": [], "error": ""}},
        ]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                  return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                  return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block",
                  return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot",
                  new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                  return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback"),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities",
                return_value=[],
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
                return_value=iter(stream_events),
            ),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="open https://example.com in the browser",
            ))

        # trace_events should be present in the response
        self.assertIn("trace_events", result)
        self.assertGreater(len(result["trace_events"]), 0,
                           "Expected at least one trace event for browser action")

        # There should be a tool.started trace event for browser__navigate
        browser_started = [
            evt for evt in result["trace_events"]
            if evt.get("event_type") == "tool.started"
            and evt.get("data", {}).get("tool_name") == "browser__navigate"
        ]
        self.assertEqual(len(browser_started), 1,
                         "Expected one tool.started trace event for browser__navigate")

        # transparency_events should be present
        self.assertIn("transparency_events", result)
        self.assertGreater(len(result["transparency_events"]), 0,
                           "Expected at least one transparency event")

        # At least one transparency event should be browser_action type
        browser_action_events = [
            e for e in result["transparency_events"]
            if e.event_type == "browser_action"
        ]
        self.assertTrue(browser_action_events,
                        "Expected at least one browser_action transparency event")

    def test_browser_screenshot_transparency_event(self):
        """browser__screenshot also emits a browser_action transparency event."""
        stream_events = [
            _trace(
                "tool.started",
                tool_call_id="call-scr-1",
                data={"tool_name": "browser__screenshot", "args_preview": {}},
            ),
            _trace(
                "tool.result",
                tool_call_id="call-scr-1",
                data={"status": "ok", "summary": "Screenshot captured"},
            ),
            {"type": "final", "payload": {"reply": "Captured screenshot.", "actions": [], "error": ""}},
        ]
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                  return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                  return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block",
                  return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot",
                  new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                  return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback"),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities",
                return_value=[],
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value={"runtime_ok": True, "local_gateway_online": True},
            ),
            patch(
                "server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
                return_value=iter(stream_events),
            ),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="take a screenshot of the page",
            ))

        self.assertIn("transparency_events", result)
        browser_action = [
            e for e in result["transparency_events"]
            if e.event_type == "browser_action"
        ]
        self.assertTrue(browser_action,
                        "Expected browser_action transparency event for screenshot")
        self.assertIn("screenshot", browser_action[0].title)


class SageBrowserToolFilteringTests(unittest.TestCase):
    """_tool_requires_agent_computer correctly identifies agent-computer tools only."""

    def test_web_tools_not_agent_computer(self):
        """web__search and web__fetch are NOT agent-computer tools."""
        self.assertFalse(
            sage_agent_runtime_service._tool_requires_agent_computer("web__search")
        )
        self.assertFalse(
            sage_agent_runtime_service._tool_requires_agent_computer("web__fetch")
        )

    def test_browser_tools_are_agent_computer(self):
        """browser__ tools ARE agent-computer tools."""
        self.assertTrue(
            sage_agent_runtime_service._tool_requires_agent_computer("browser__navigate")
        )
        self.assertTrue(
            sage_agent_runtime_service._tool_requires_agent_computer("browser__screenshot")
        )

    def test_agent_computer_tool_prefixes(self):
        """All AGENT_COMPUTER_TOOL_PREFIXES are detected."""
        prefixes = ("browser__", "computer__", "file__", "shell__", "screenshot__")
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertTrue(
                    sage_agent_runtime_service._tool_requires_agent_computer(f"{prefix}test")
                )

    def test_hardware_action_is_agent_computer(self):
        """hardware__action is an agent-computer tool."""
        self.assertTrue(
            sage_agent_runtime_service._tool_requires_agent_computer("hardware__action")
        )


class SageIntentDetectionTests(unittest.TestCase):
    """Browser automation and web lookup intent detection must remain distinct."""

    def test_web_search_not_confused_with_browser(self):
        """'search the web' is web lookup, not browser automation."""
        msg = "search the web for Empyralis platform docs"
        self.assertTrue(
            direct_chat_tool_catalog_service.message_has_web_lookup_intent(msg),
            "search message should be web lookup",
        )
        self.assertFalse(
            direct_chat_tool_catalog_service.message_has_browser_automation_intent(msg),
            "search message should NOT be browser automation",
        )

    def test_browser_navigation_not_confused_with_web_search(self):
        """'go to example.com' is browser automation, not web lookup."""
        msg = "go to example.com"
        self.assertFalse(
            direct_chat_tool_catalog_service.message_has_web_lookup_intent(msg),
            "browser navigation should NOT be web lookup",
        )
        self.assertTrue(
            direct_chat_tool_catalog_service.message_has_browser_automation_intent(msg),
            "browser navigation should be browser automation",
        )

    def test_url_with_open_is_browser_not_web_lookup(self):
        """'Open https://example.com' is browser automation, not web lookup."""
        msg = "Open https://example.com"
        self.assertFalse(
            direct_chat_tool_catalog_service.message_has_web_lookup_intent(msg),
            "open URL should NOT be web lookup",
        )
        self.assertTrue(
            direct_chat_tool_catalog_service.message_has_browser_automation_intent(msg),
            "open URL should be browser automation",
        )

    def test_url_with_fetch_is_web_lookup_not_browser(self):
        """'fetch https://example.com' is web lookup, not browser automation."""
        msg = "fetch https://example.com"
        self.assertTrue(
            direct_chat_tool_catalog_service.message_has_web_lookup_intent(msg),
            "fetch URL should be web lookup",
        )
        self.assertFalse(
            direct_chat_tool_catalog_service.message_has_browser_automation_intent(msg),
            "fetch URL should NOT be browser automation",
        )

    def test_mixed_intent_lookup_plus_browser(self):
        """A message with both patterns correctly indicates both intents."""
        msg = "open https://example.com and search the web for recent news"
        self.assertTrue(
            direct_chat_tool_catalog_service.message_has_web_lookup_intent(msg),
        )
        self.assertTrue(
            direct_chat_tool_catalog_service.message_has_browser_automation_intent(msg),
        )


class SageBrowserStatusTests(unittest.TestCase):
    """_sage_agent_computer_browser_status correctly classifies browser availability."""

    def test_offline_when_all_flags_false(self):
        """No gateway flags, no verified gateway, no capability truth → not_selected."""
        status = sage_agent_runtime_service._sage_agent_computer_browser_status(
            {"runtime_ok": False, "local_gateway_online": False}
        )
        self.assertIn(status, ("not_selected", "offline"))

    def test_offline_when_gateway_selected_but_offline(self):
        """selected_gateway_id with no online flags → offline."""
        status = sage_agent_runtime_service._sage_agent_computer_browser_status(
            {"runtime_ok": False, "local_gateway_online": False, "selected_gateway_id": "gw-1"}
        )
        self.assertEqual(status, "offline")

    def test_online_when_runtime_ok_and_gateway_online(self):
        """runtime_ok=True + local_gateway_online=True → online."""
        status = sage_agent_runtime_service._sage_agent_computer_browser_status(
            {"runtime_ok": True, "local_gateway_online": True}
        )
        self.assertEqual(status, "online")

    def test_online_when_verified_gateway(self):
        """verified_user_device_gateway present → online."""
        status = sage_agent_runtime_service._sage_agent_computer_browser_status(
            {"verified_user_device_gateway": {"gateway_id": "gw-1"}}
        )
        self.assertEqual(status, "online")

    def test_online_when_capability_truth_local_tools(self):
        """capability_truth.my_computer.local_tools_available True → online."""
        status = sage_agent_runtime_service._sage_agent_computer_browser_status(
            {"capability_truth": {"my_computer": {"local_tools_available": True}}}
        )
        self.assertEqual(status, "online")

    def test_online_when_runtime_ok_and_worker_online(self):
        """runtime_ok=True + local_worker_online=True → online."""
        status = sage_agent_runtime_service._sage_agent_computer_browser_status(
            {"runtime_ok": True, "local_worker_online": True}
        )
        self.assertEqual(status, "online")

    def test_disconnected_state_is_offline(self):
        """runtime_state='disconnected' → offline."""
        status = sage_agent_runtime_service._sage_agent_computer_browser_status(
            {"runtime_state": "disconnected"}
        )
        self.assertEqual(status, "offline")


if __name__ == "__main__":
    unittest.main()
