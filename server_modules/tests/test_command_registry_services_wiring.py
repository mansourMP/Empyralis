"""command_registry — the `services` / `availability_payload` /
`tool_capabilities` kwargs that /stop, /model, /tools, /status, /debug read.

Bug: neither live entry point that reaches command_registry.dispatch() /
process_message() — sage_turn_adapter.execute_sage_turn's own "/" command
block, and sage_command_dispatcher.dispatch_command (the other live path,
used by hosted Telegram and WeChat official before execute_sage_turn is ever
reached) — ever built or forwarded these kwargs. All five handlers therefore
always saw kwargs.get("services") is None (or an empty availability_payload
/tool_capabilities) and degraded to a stub reply on every real call.

Fixed by command_registry.build_service_kwargs_for_text(), called once by
each of those two entry points and forwarded through. These tests:

  (a) unit-test build_service_kwargs_for_text's own laziness — it must do
      zero work (no lookups at all) for a command that doesn't need it, and
      the two "expensive" kwargs (availability_payload/tool_capabilities)
      must not be built unless the command is /status or /tools.
  (b) prove each of the five handlers now returns REAL content instead of
      the degraded stub, dispatched through the real command_registry.
  (c) prove execute_sage_turn wires the same fix through end-to-end, and
      that building it costs nothing on an ordinary (non-"/") message.
  (d) prove sage_command_dispatcher.dispatch_command — the other live
      entry point — has the identical fix, not just execute_sage_turn.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import command_registry
from server_modules.sage_turn_adapter import execute_sage_turn


def _run(coro):
    return asyncio.run(coro)


class BuildServiceKwargsLazinessTests(unittest.TestCase):
    """build_service_kwargs_for_text must do real work ONLY for the five
    commands that need it, and must do NOTHING (no lookup, no dict/list
    built) for anything else — asserted on call count, not on the shape of
    the reply."""

    def test_plain_chat_text_needs_nothing(self):
        self.assertEqual(command_registry.build_service_kwargs_for_text("hello there", "ws-1"), {})

    def test_unrelated_command_needs_nothing(self):
        # /whoami never reads services/availability_payload/tool_capabilities
        # (grepped its body — it only reads sender_id/sender_name/channel_origin).
        self.assertEqual(command_registry.build_service_kwargs_for_text("/whoami", "ws-1"), {})

    def test_availability_lookup_is_not_called_for_a_command_that_does_not_need_it(self):
        with patch(
            "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability"
        ) as mock_avail:
            command_registry.build_service_kwargs_for_text("/stop", "ws-1")
            command_registry.build_service_kwargs_for_text("/model", "ws-1")
            command_registry.build_service_kwargs_for_text("/debug", "ws-1")
            command_registry.build_service_kwargs_for_text("/whoami", "ws-1")
            command_registry.build_service_kwargs_for_text("just chatting, no slash", "ws-1")
        mock_avail.assert_not_called()

    def test_stop_model_debug_get_services_but_not_availability(self):
        for text in ("/stop", "/model", "/debug"):
            extra = command_registry.build_service_kwargs_for_text(text, "ws-1")
            self.assertIn("services", extra, text)
            self.assertNotIn("availability_payload", extra, text)
            self.assertNotIn("tool_capabilities", extra, text)

    def test_status_and_tools_get_availability(self):
        # /status reads both services and availability_payload; /tools reads
        # only availability_payload/tool_capabilities (grepped its body —
        # it never touches kwargs["services"]).
        payload = {"ai_ready": True, "tool_capabilities": [{"name": "gmail"}]}
        with patch(
            "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability",
            return_value=payload,
        ) as mock_avail:
            extra_status = command_registry.build_service_kwargs_for_text("/status", "ws-1")
            extra_tools = command_registry.build_service_kwargs_for_text("/tools", "ws-1")
        self.assertEqual(mock_avail.call_count, 2)
        self.assertIn("services", extra_status)
        self.assertNotIn("services", extra_tools)
        for extra in (extra_status, extra_tools):
            self.assertEqual(extra["availability_payload"], payload)
            self.assertEqual(extra["tool_capabilities"], [{"name": "gmail"}])

    def test_availability_lookup_failure_degrades_to_empty_not_an_exception(self):
        with patch(
            "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability",
            side_effect=RuntimeError("db unavailable"),
        ):
            extra = command_registry.build_service_kwargs_for_text("/status", "ws-1")
        self.assertEqual(extra["availability_payload"], {})
        self.assertEqual(extra["tool_capabilities"], [])


class SlashCommandServicesRealInterfaceTests(unittest.TestCase):
    """The services object itself: exactly the two methods the handlers
    call, backed by the real production-wired functions, failing closed
    (never raising) on a lookup error."""

    def test_active_run_count_delegates_to_the_real_wired_function(self):
        services = command_registry._SlashCommandServices()
        with patch(
            "server_modules.direct_chat_runtime_exports._active_run_count",
            return_value=3,
        ) as mock_fn:
            self.assertEqual(services.active_run_count("ws-1"), 3)
        mock_fn.assert_called_once_with("ws-1")

    def test_active_run_count_fails_closed_to_zero(self):
        services = command_registry._SlashCommandServices()
        with patch(
            "server_modules.direct_chat_runtime_exports._active_run_count",
            side_effect=RuntimeError("boom"),
        ):
            self.assertEqual(services.active_run_count("ws-1"), 0)

    def test_connected_provider_tokens_delegates_to_the_real_wired_function(self):
        services = command_registry._SlashCommandServices()
        with patch(
            "server_modules.direct_chat_runtime_exports._connected_provider_tokens",
            return_value=["openai", "deepseek"],
        ) as mock_fn:
            self.assertEqual(services.connected_provider_tokens("ws-1"), ["openai", "deepseek"])
        mock_fn.assert_called_once_with("ws-1")

    def test_connected_provider_tokens_fails_closed_to_empty_list(self):
        services = command_registry._SlashCommandServices()
        with patch(
            "server_modules.direct_chat_runtime_exports._connected_provider_tokens",
            side_effect=RuntimeError("boom"),
        ):
            self.assertEqual(services.connected_provider_tokens("ws-1"), [])


class RealHandlerOutputThroughDispatchTests(unittest.TestCase):
    """Each of the five handlers, dispatched through the REAL
    command_registry.dispatch() with the kwargs build_service_kwargs_for_text
    produces — proving the fix end-to-end at the registry level, independent
    of which caller (execute_sage_turn vs dispatch_command) built the kwargs.
    """

    def _dispatch(self, text, workspace_id="ws-1", **extra_kwargs):
        service_kwargs = command_registry.build_service_kwargs_for_text(text, workspace_id)
        service_kwargs.update(extra_kwargs)
        return _run(
            command_registry.dispatch(
                text=text,
                workspace_id=workspace_id,
                surface="channel",
                **service_kwargs,
            )
        )

    def test_stop_reports_no_active_runs_instead_of_the_stub(self):
        with patch(
            "server_modules.direct_chat_runtime_exports._active_run_count",
            return_value=0,
        ):
            result = self._dispatch("/stop")
        self.assertNotIn("services not loaded", result["reply"])
        self.assertEqual(result["reply"], "No active runs to stop.")

    def test_bare_model_lists_real_connected_providers_instead_of_the_stub(self):
        with patch(
            "server_modules.direct_chat_runtime_exports._connected_provider_tokens",
            return_value=["openai"],
        ):
            result = self._dispatch("/model")
        self.assertNotIn("services not loaded", result["reply"])
        self.assertIn("Available models:", result["reply"])
        self.assertIn("openai", result["reply"])

    def test_tools_reports_real_gateway_and_connector_state_instead_of_offline_default(self):
        payload = {"ai_ready": True, "tool_capabilities": [{"name": "google_drive"}]}
        with patch(
            "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability",
            return_value=payload,
        ):
            result = self._dispatch("/tools")
        self.assertNotIn("(offline — no gateway)", result["reply"])
        self.assertIn("file__read", result["reply"])
        self.assertNotIn("(none configured)", result["reply"])
        self.assertIn("google_drive", result["reply"])

    def test_status_reports_real_ai_ready_and_providers_instead_of_the_stub(self):
        payload = {"ai_ready": True, "tool_capabilities": []}
        with (
            patch(
                "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value=payload,
            ),
            patch(
                "server_modules.direct_chat_runtime_exports._connected_provider_tokens",
                return_value=["deepseek"],
            ),
            patch(
                "server_modules.direct_chat_runtime_exports._active_run_count",
                return_value=2,
            ),
        ):
            result = self._dispatch("/status")
        self.assertNotIn("Status unavailable", result["reply"])
        self.assertIn("Runtime status", result["reply"])
        self.assertIn("AI ready: yes", result["reply"])
        self.assertIn("deepseek", result["reply"])
        self.assertIn("Active runs: 2", result["reply"])

    def test_debug_reports_real_connected_providers_instead_of_none(self):
        owner_workspace = {
            "created_by_user_id": "owner-1",
            "identity_links": {},
            "metadata": {},
            "tenant_id": "tenant-1",
        }
        with (
            patch(
                "server_modules.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value=owner_workspace),
            ),
            patch(
                "server_modules.direct_chat_runtime_exports._connected_provider_tokens",
                return_value=["deepseek"],
            ),
        ):
            result = self._dispatch("/debug", sender_id="owner-1")
        self.assertNotIn("temporarily unavailable", result["reply"])
        self.assertIn("Providers:          deepseek", result["reply"])


class ExecuteSageTurnServicesWiringTests(unittest.TestCase):
    """The end-to-end path: execute_sage_turn is the single unified entry
    every channel and web chat route through (its own module docstring).
    A slash command reaching it must now get a real handler reply, and an
    ordinary chat message must never pay for any of this."""

    def test_status_through_execute_sage_turn_is_real_not_degraded(self):
        payload = {"ai_ready": True, "tool_capabilities": []}
        sage_chat = AsyncMock(side_effect=AssertionError("must not reach the model for a command-only message"))
        with (
            patch("server_modules.sage_agent_runtime_service.handle_sage_chat", new=sage_chat),
            patch(
                "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value=payload,
            ),
            patch(
                "server_modules.direct_chat_runtime_exports._connected_provider_tokens",
                return_value=["openai"],
            ),
            patch(
                "server_modules.direct_chat_runtime_exports._active_run_count",
                return_value=0,
            ),
        ):
            result = _run(execute_sage_turn(workspace_id="ws-1", message="/status"))
        self.assertNotIn("Status unavailable", result.message)
        self.assertIn("Runtime status", result.message)
        self.assertIn("openai", result.message)
        sage_chat.assert_not_called()

    def test_ordinary_chat_message_never_builds_any_of_this(self):
        """The core "lazy" requirement: a plain, non-"/" message must never
        trigger the availability/provider/run-count lookups at all — those
        are for slash commands only."""
        sage_chat = AsyncMock(return_value={
            "message": "Hello!",
            "used_context": [],
            "tool_calls": [],
            "available_tools": [],
            "blocked_tools": [],
            "approvals_required": [],
            "memory_updates": [],
            "trace_id": "trace-1",
            "provider": "openai",
            "model": "gpt-4o",
        })
        with (
            patch("server_modules.sage_agent_runtime_service.handle_sage_chat", new=sage_chat),
            patch(
                "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability"
            ) as mock_avail,
            patch(
                "server_modules.direct_chat_runtime_exports._connected_provider_tokens"
            ) as mock_tokens,
            patch(
                "server_modules.direct_chat_runtime_exports._active_run_count"
            ) as mock_runs,
        ):
            result = _run(execute_sage_turn(workspace_id="ws-1", message="hello, how are you today?"))
        mock_avail.assert_not_called()
        mock_tokens.assert_not_called()
        mock_runs.assert_not_called()
        self.assertEqual(result.message, "Hello!")

    def test_unrelated_slash_command_also_builds_nothing_expensive(self):
        """/whoami is a real, dispatched command but reads none of these
        kwargs — build_service_kwargs_for_text must not do the /status or
        /tools lookup just because SOME command ran."""
        sage_chat = AsyncMock(side_effect=AssertionError("must not reach the model for a command-only message"))
        with (
            patch("server_modules.sage_agent_runtime_service.handle_sage_chat", new=sage_chat),
            patch(
                "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability"
            ) as mock_avail,
        ):
            result = _run(execute_sage_turn(
                workspace_id="ws-1",
                message="/whoami",
                channel_sender_id="sender-abc",
            ))
        mock_avail.assert_not_called()
        self.assertIn("sender-abc", result.message)


class DispatchCommandServicesWiringTests(unittest.TestCase):
    """sage_command_dispatcher.dispatch_command is the OTHER live entry
    point every channel routes through before execute_sage_turn (hosted
    Telegram, WeChat official — see that module's own docstring) and had
    the identical gap. Proven directly against it, not just against
    execute_sage_turn."""

    def test_status_through_dispatch_command_is_real_not_degraded(self):
        from server_modules.sage_command_dispatcher import dispatch_command

        payload = {"ai_ready": False, "tool_capabilities": []}
        with (
            patch(
                "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability",
                return_value=payload,
            ),
            patch(
                "server_modules.direct_chat_runtime_exports._connected_provider_tokens",
                return_value=["deepseek"],
            ),
            patch(
                "server_modules.direct_chat_runtime_exports._active_run_count",
                return_value=0,
            ),
        ):
            reply = _run(dispatch_command(command="/status", workspace_id="ws-1"))
        self.assertIsNotNone(reply)
        self.assertNotIn("Status unavailable", reply)
        self.assertIn("Runtime status", reply)
        self.assertIn("deepseek", reply)

    def test_stop_through_dispatch_command_is_real_not_degraded(self):
        from server_modules.sage_command_dispatcher import dispatch_command

        with patch(
            "server_modules.direct_chat_runtime_exports._active_run_count",
            return_value=0,
        ):
            reply = _run(dispatch_command(command="/stop", workspace_id="ws-1"))
        self.assertEqual(reply, "No active runs to stop.")

    def test_unrelated_command_through_dispatch_command_builds_nothing_expensive(self):
        from server_modules.sage_command_dispatcher import dispatch_command

        with patch(
            "server_modules.direct_chat_runtime_exports._resolve_direct_chat_availability"
        ) as mock_avail:
            reply = _run(dispatch_command(
                command="/whoami", workspace_id="ws-1", sender_id="sender-xyz",
            ))
        mock_avail.assert_not_called()
        self.assertIn("sender-xyz", reply)


if __name__ == "__main__":
    unittest.main()
