from __future__ import annotations

import importlib
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import cli_setup_service


class CliSetupServiceFriendlyErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        global cli_setup_service
        cli_setup_service = importlib.import_module("server_modules.cli_setup_service")

    def test_maps_capability_missing_to_not_enabled_locally(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error("gateway_capability_missing", login=False, runtime="claude_code")
        self.assertIn("isn't enabled", message.lower())

    def test_maps_capability_not_ready_to_not_enabled_locally(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error("gateway_capability_not_ready", login=True, runtime="codex")
        self.assertIn("isn't enabled", message.lower())

    def test_maps_offline_to_gateway_offline(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error("gateway_offline", login=False, runtime="claude_code")
        self.assertIn("offline", message.lower())

    def test_maps_not_currently_connected_to_gateway_offline(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error("Gateway is not currently connected.", login=True, runtime="codex")
        self.assertIn("offline", message.lower())

    def test_maps_registration_missing_to_not_paired(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error("gateway_registration_missing", login=False, runtime="claude_code")
        self.assertIn("re-pair", message.lower())

    def test_maps_workspace_mismatch_to_not_paired(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error("gateway_workspace_mismatch", login=False, runtime="claude_code")
        self.assertIn("re-pair", message.lower())

    def test_maps_npm_missing(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error(
            "Claude Code install failed on this Gateway (npm_missing): npm was not found on PATH.",
            login=False,
            runtime="claude_code",
        )
        self.assertIn("npm", message.lower())

    def test_maps_permission_denied(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error(
            "Codex install failed on this Gateway (permission_denied): EACCES",
            login=False,
            runtime="codex",
        )
        self.assertIn("permission", message.lower())

    def test_maps_network_error(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error(
            "Codex install failed on this Gateway (network_error): ETIMEDOUT",
            login=False,
            runtime="codex",
        )
        self.assertIn("network", message.lower())

    def test_maps_install_timeout_distinctly_from_login_timeout(self) -> None:
        install_message = cli_setup_service._friendly_cli_setup_error("timeout", login=False, runtime="claude_code")
        login_message = cli_setup_service._friendly_cli_setup_error("timeout", login=True, runtime="claude_code")
        self.assertIn("install", install_message.lower())
        self.assertIn("sign-in", login_message.lower())

    def test_maps_not_installed_per_runtime(self) -> None:
        claude_message = cli_setup_service._friendly_cli_setup_error("not_installed", login=True, runtime="claude_code")
        codex_message = cli_setup_service._friendly_cli_setup_error("not_installed", login=True, runtime="codex")
        self.assertIn("claude code", claude_message.lower())
        self.assertIn("codex", codex_message.lower())

    def test_maps_cancelled(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error("cancelled", login=True, runtime="claude_code")
        self.assertIn("cancelled", message.lower())

    def test_maps_crash_per_runtime_and_action(self) -> None:
        install_claude = cli_setup_service._friendly_cli_setup_error("crash", login=False, runtime="claude_code")
        install_codex = cli_setup_service._friendly_cli_setup_error("crash", login=False, runtime="codex")
        login_claude = cli_setup_service._friendly_cli_setup_error("crash", login=True, runtime="claude_code")
        login_codex = cli_setup_service._friendly_cli_setup_error("crash", login=True, runtime="codex")
        self.assertIn("installing claude code", install_claude.lower())
        self.assertIn("installing codex", install_codex.lower())
        self.assertIn("claude code sign-in", login_claude.lower())
        self.assertIn("codex sign-in", login_codex.lower())

    def test_unmatched_reason_falls_back_to_honest_raw_reason(self) -> None:
        message = cli_setup_service._friendly_cli_setup_error("some_new_unclassified_reason", login=False, runtime="codex")
        self.assertIn("some_new_unclassified_reason", message)


class CliSetupServiceRuntimeValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global cli_setup_service
        cli_setup_service = importlib.import_module("server_modules.cli_setup_service")

    async def test_install_rejects_unsupported_runtime_before_dispatch(self) -> None:
        with patch.object(cli_setup_service.gateway_execution_service, "execute_tool_via_gateway", AsyncMock()) as dispatch_mock:
            with self.assertRaises(cli_setup_service.CliSetupError) as ctx:
                await cli_setup_service.install_cli_runtime(
                    gateway_id="gw-1", workspace_id="ws-1", runtime="gpt-5", run_id="run-1",
                )
        self.assertEqual(ctx.exception.status_code, 400)
        dispatch_mock.assert_not_awaited()

    async def test_login_start_rejects_unsupported_runtime_before_dispatch(self) -> None:
        with patch.object(cli_setup_service.gateway_execution_service, "execute_tool_via_gateway", AsyncMock()) as dispatch_mock:
            with self.assertRaises(cli_setup_service.CliSetupError):
                await cli_setup_service.start_cli_login(
                    gateway_id="gw-1", workspace_id="ws-1", runtime="not-a-runtime", run_id="run-1",
                )
        dispatch_mock.assert_not_awaited()

    async def test_login_input_rejects_blank_code_before_dispatch(self) -> None:
        with patch.object(cli_setup_service.gateway_execution_service, "execute_tool_via_gateway", AsyncMock()) as dispatch_mock:
            with self.assertRaises(cli_setup_service.CliSetupError):
                await cli_setup_service.submit_cli_login_input(
                    gateway_id="gw-1", workspace_id="ws-1", run_id="run-1", code="   ",
                )
        dispatch_mock.assert_not_awaited()


class CliSetupServiceDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global cli_setup_service
        cli_setup_service = importlib.import_module("server_modules.cli_setup_service")

    async def test_install_cli_runtime_dispatches_with_normalized_runtime(self) -> None:
        dispatch_mock = AsyncMock(return_value={"result": {"installed": True}})
        with patch.object(cli_setup_service.gateway_execution_service, "execute_tool_via_gateway", dispatch_mock):
            result = await cli_setup_service.install_cli_runtime(
                gateway_id="gw-1", workspace_id="ws-1", runtime="  Claude_Code  ", run_id="run-1", trace_id="trace-1",
            )
        self.assertEqual(result["result"]["installed"], True)
        dispatch_mock.assert_awaited_once()
        kwargs = dispatch_mock.await_args.kwargs
        self.assertEqual(kwargs["capability_id"], "cli.install")
        self.assertEqual(kwargs["arguments"], {"runtime": "claude_code"})
        self.assertEqual(kwargs["gateway_id"], "gw-1")
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["run_id"], "run-1")
        self.assertEqual(kwargs["agent_scope"], "sage")

    async def test_install_cli_runtime_translates_valueerror_into_friendly_cli_setup_error(self) -> None:
        dispatch_mock = AsyncMock(side_effect=ValueError("gateway_offline"))
        with patch.object(cli_setup_service.gateway_execution_service, "execute_tool_via_gateway", dispatch_mock):
            with self.assertRaises(cli_setup_service.CliSetupError) as ctx:
                await cli_setup_service.install_cli_runtime(
                    gateway_id="gw-1", workspace_id="ws-1", runtime="codex", run_id="run-1",
                )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("offline", str(ctx.exception).lower())

    async def test_install_cli_runtime_translates_permissionerror_to_403(self) -> None:
        dispatch_mock = AsyncMock(side_effect=PermissionError("full_access Agent Computer execution is available only to Sage."))
        with patch.object(cli_setup_service.gateway_execution_service, "execute_tool_via_gateway", dispatch_mock):
            with self.assertRaises(cli_setup_service.CliSetupError) as ctx:
                await cli_setup_service.install_cli_runtime(
                    gateway_id="gw-1", workspace_id="ws-1", runtime="codex", run_id="run-1",
                )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_start_cli_login_dispatches_login_start_capability(self) -> None:
        dispatch_mock = AsyncMock(return_value={"result": {"run_id": "run-2", "status": "started"}})
        with patch.object(cli_setup_service.gateway_execution_service, "execute_tool_via_gateway", dispatch_mock):
            result = await cli_setup_service.start_cli_login(
                gateway_id="gw-1", workspace_id="ws-1", runtime="claude_code", run_id="run-2",
            )
        self.assertEqual(result["result"]["status"], "started")
        kwargs = dispatch_mock.await_args.kwargs
        self.assertEqual(kwargs["capability_id"], "cli.login.start")
        self.assertEqual(kwargs["arguments"], {"runtime": "claude_code"})

    async def test_start_cli_login_wraps_not_installed_honestly(self) -> None:
        dispatch_mock = AsyncMock(
            side_effect=ValueError('Claude Code sign-in failed on this Gateway (not_installed): "claude" was not found on PATH.')
        )
        with patch.object(cli_setup_service.gateway_execution_service, "execute_tool_via_gateway", dispatch_mock):
            with self.assertRaises(cli_setup_service.CliSetupError) as ctx:
                await cli_setup_service.start_cli_login(
                    gateway_id="gw-1", workspace_id="ws-1", runtime="claude_code", run_id="run-2",
                )
        self.assertIn("claude code", str(ctx.exception).lower())
        self.assertIn("install it first", str(ctx.exception).lower())

    async def test_submit_cli_login_input_dispatches_with_trimmed_code(self) -> None:
        dispatch_mock = AsyncMock(return_value={"result": {"ok": True}})
        with patch.object(cli_setup_service.gateway_execution_service, "execute_tool_via_gateway", dispatch_mock):
            result = await cli_setup_service.submit_cli_login_input(
                gateway_id="gw-1", workspace_id="ws-1", run_id="run-2", code="  ABCD-1234  ",
            )
        self.assertTrue(result["result"]["ok"])
        kwargs = dispatch_mock.await_args.kwargs
        self.assertEqual(kwargs["capability_id"], "cli.login.input")
        self.assertEqual(kwargs["arguments"], {"code": "ABCD-1234"})
        self.assertEqual(kwargs["run_id"], "run-2")

    async def test_cancel_cli_login_dispatches_interrupt_with_default_reason(self) -> None:
        interrupt_mock = AsyncMock(return_value={"interrupted": True, "run_id": "run-2"})
        with patch.object(cli_setup_service.gateway_execution_service, "interrupt_tool_via_gateway", interrupt_mock):
            result = await cli_setup_service.cancel_cli_login(
                gateway_id="gw-1", workspace_id="ws-1", run_id="run-2",
            )
        self.assertTrue(result["interrupted"])
        kwargs = interrupt_mock.await_args.kwargs
        self.assertEqual(kwargs["run_id"], "run-2")
        self.assertEqual(kwargs["reason"], "user_cancelled")

    async def test_cancel_cli_login_forwards_explicit_reason(self) -> None:
        interrupt_mock = AsyncMock(return_value={"interrupted": True, "run_id": "run-2"})
        with patch.object(cli_setup_service.gateway_execution_service, "interrupt_tool_via_gateway", interrupt_mock):
            await cli_setup_service.cancel_cli_login(
                gateway_id="gw-1", workspace_id="ws-1", run_id="run-2", reason="navigated away",
            )
        self.assertEqual(interrupt_mock.await_args.kwargs["reason"], "navigated away")

    async def test_cancel_cli_login_translates_not_connected_to_409(self) -> None:
        interrupt_mock = AsyncMock(side_effect=ValueError("Gateway is not currently connected."))
        with patch.object(cli_setup_service.gateway_execution_service, "interrupt_tool_via_gateway", interrupt_mock):
            with self.assertRaises(cli_setup_service.CliSetupError) as ctx:
                await cli_setup_service.cancel_cli_login(gateway_id="gw-1", workspace_id="ws-1", run_id="run-2")
        self.assertEqual(ctx.exception.status_code, 409)


class CliSetupServiceEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        global cli_setup_service
        cli_setup_service = importlib.import_module("server_modules.cli_setup_service")

    def test_list_cli_login_events_filters_by_run_id(self) -> None:
        all_events = [
            {"message_type": "cli.login.output", "payload": {"run_id": "run-1", "event": "output", "kind": "url", "text": "https://x"}},
            {"message_type": "cli.login.output", "payload": {"run_id": "run-2", "event": "output", "kind": "url", "text": "https://y"}},
            {"message_type": "cli.login.output", "payload": {"run_id": "run-1", "event": "done", "ok": True}},
        ]
        with patch.object(cli_setup_service.gateway_state_repository, "list_gateway_events", return_value=all_events) as list_mock:
            items = cli_setup_service.list_cli_login_events(gateway_id="gw-1", run_id="run-1", limit=50)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["payload"]["run_id"] == "run-1" for item in items))
        list_mock.assert_called_once()
        call_kwargs = list_mock.call_args.kwargs
        self.assertEqual(call_kwargs["message_type"], "cli.login.output")

    def test_list_cli_login_events_trims_to_requested_limit(self) -> None:
        all_events = [
            {"message_type": "cli.login.output", "payload": {"run_id": "run-1", "seq": i}}
            for i in range(10)
        ]
        with patch.object(cli_setup_service.gateway_state_repository, "list_gateway_events", return_value=all_events):
            items = cli_setup_service.list_cli_login_events(gateway_id="gw-1", run_id="run-1", limit=3)
        self.assertEqual(len(items), 3)
        self.assertEqual([item["payload"]["seq"] for item in items], [7, 8, 9])

    def test_list_cli_login_events_returns_empty_for_unknown_run_id(self) -> None:
        all_events = [
            {"message_type": "cli.login.output", "payload": {"run_id": "run-1", "event": "output"}},
        ]
        with patch.object(cli_setup_service.gateway_state_repository, "list_gateway_events", return_value=all_events):
            items = cli_setup_service.list_cli_login_events(gateway_id="gw-1", run_id="never-started", limit=50)
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
