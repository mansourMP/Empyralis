import unittest
from unittest.mock import AsyncMock, patch

from server_modules import gateway_browser_service


class GatewayBrowserServiceRustGateTests(unittest.TestCase):
    def test_browser_session_start_calls_gateway_action_rust_gate(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "status": "active",
            "device_trust_state": "trusted",
            "active_session_id": "session-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.gateway_browser_service._require_active_gateway_registration",
                    return_value=registration,
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "browser_session_start",
                        "next_action": "start_browser_session",
                    },
                ) as decision_mock,
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision: decision,
                ),
                patch(
                    "server_modules.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
                    new=AsyncMock(return_value={"result": {}, "request_id": "req-1"}),
                ),
                patch(
                    "server_modules.gateway_browser_service._append_browser_activity",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "server_modules.gateway_browser_service.gateway_execution_service._enforce_gateway_quota_check",
                    return_value={"next_action": "allow_gateway_service_operation"},
                ) as quota_mock,
            ):
                await gateway_browser_service.execute_browser_capability_via_gateway(
                    gateway_id="gw-1",
                    capability_id=gateway_browser_service.BROWSER_SESSION_START_CAPABILITY,
                    arguments={"url": "https://example.com"},
                    run_id="run-1",
                    trace_id="trace-1",
                    workspace_id="ws-1",
                    request_id="req-1",
                )

            request = decision_mock.call_args.kwargs
            self.assertEqual(request["operation"], "browser_session_start")
            self.assertEqual(request["gateway_id"], "gw-1")
            self.assertEqual(request["workspace_id"], "ws-1")
            self.assertEqual(quota_mock.call_args.kwargs["quota_profile"], "gateway_browser_session")

        import asyncio

        asyncio.run(run_test())

    def test_browser_session_start_blocks_wrong_quota_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "status": "active",
            "device_trust_state": "trusted",
            "active_session_id": "session-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.gateway_browser_service._require_active_gateway_registration",
                    return_value=registration,
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "browser_session_start",
                        "next_action": "start_browser_session",
                    },
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision: decision,
                ),
                patch(
                    "server_modules.gateway_browser_service.gateway_execution_service._enforce_gateway_quota_check",
                    side_effect=ValueError("Rust gateway-service returned unexpected next_action for quota_check: dispatch_gateway_operation"),
                ),
                patch(
                    "server_modules.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch")),
                ) as execute_mock,
            ):
                with self.assertRaises(ValueError) as raised:
                    await gateway_browser_service.execute_browser_capability_via_gateway(
                        gateway_id="gw-1",
                        capability_id=gateway_browser_service.BROWSER_SESSION_START_CAPABILITY,
                        arguments={"url": "https://example.com"},
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        request_id="req-1",
                    )

            self.assertIn("quota_check", str(raised.exception))
            execute_mock.assert_not_awaited()

        import asyncio

        asyncio.run(run_test())

    def test_browser_action_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "status": "active",
            "device_trust_state": "trusted",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.gateway_browser_service._require_active_gateway_registration",
                    return_value=registration,
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "browser_action",
                        "next_action": "request_gateway_browser_approval",
                    },
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision: decision,
                ),
                patch(
                    "server_modules.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch")),
                ) as execute_mock,
            ):
                with self.assertRaises(RuntimeError) as raised:
                    await gateway_browser_service.execute_browser_capability_via_gateway(
                        gateway_id="gw-1",
                        capability_id=gateway_browser_service.BROWSER_SESSION_ACTION_CAPABILITY,
                        arguments={"action": "click", "browser_session_id": "gbsess-1"},
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        request_id="req-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            execute_mock.assert_not_awaited()

        import asyncio

        asyncio.run(run_test())

    def test_browser_interrupt_requires_stop_browser_session_action(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "status": "active",
            "device_trust_state": "trusted",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.gateway_browser_service._require_active_gateway_registration",
                    return_value=registration,
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "browser_session_stop",
                        "next_action": "stop_browser_session",
                    },
                ) as decision_mock,
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision: decision,
                ),
                patch(
                    "server_modules.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
                    new=AsyncMock(return_value={"result": {}, "request_id": "req-1"}),
                ),
                patch(
                    "server_modules.gateway_browser_service._append_browser_activity",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await gateway_browser_service.execute_browser_capability_via_gateway(
                    gateway_id="gw-1",
                    capability_id=gateway_browser_service.BROWSER_SESSION_INTERRUPT_CAPABILITY,
                    arguments={"browser_session_id": "gbsess-1"},
                    run_id="run-1",
                    trace_id="trace-1",
                    workspace_id="ws-1",
                    request_id="req-1",
                )

            request = decision_mock.call_args.kwargs
            self.assertEqual(request["operation"], "browser_session_stop")
            self.assertEqual(request["gateway_id"], "gw-1")

        import asyncio

        asyncio.run(run_test())

    def test_browser_interrupt_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "status": "active",
            "device_trust_state": "trusted",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.gateway_browser_service._require_active_gateway_registration",
                    return_value=registration,
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "browser_session_stop",
                        "next_action": "dispatch_browser_action",
                    },
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision: decision,
                ),
                patch(
                    "server_modules.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch")),
                ) as execute_mock,
            ):
                with self.assertRaises(RuntimeError) as raised:
                    await gateway_browser_service.execute_browser_capability_via_gateway(
                        gateway_id="gw-1",
                        capability_id=gateway_browser_service.BROWSER_SESSION_INTERRUPT_CAPABILITY,
                        arguments={"browser_session_id": "gbsess-1"},
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        request_id="req-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            execute_mock.assert_not_awaited()

        import asyncio

        asyncio.run(run_test())

    def test_browser_resume_requires_resume_browser_session_action(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "status": "active",
            "device_trust_state": "trusted",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.gateway_browser_service._require_active_gateway_registration",
                    return_value=registration,
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "browser_session_resume",
                        "next_action": "resume_browser_session",
                    },
                ) as decision_mock,
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision: decision,
                ),
                patch(
                    "server_modules.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
                    new=AsyncMock(return_value={"result": {}, "request_id": "req-1"}),
                ),
                patch(
                    "server_modules.gateway_browser_service._append_browser_activity",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await gateway_browser_service.execute_browser_capability_via_gateway(
                    gateway_id="gw-1",
                    capability_id=gateway_browser_service.BROWSER_SESSION_RESUME_CAPABILITY,
                    arguments={"browser_session_id": "gbsess-1", "checkpoint": {}},
                    run_id="run-1",
                    trace_id="trace-1",
                    workspace_id="ws-1",
                    request_id="req-1",
                )

            request = decision_mock.call_args.kwargs
            self.assertEqual(request["operation"], "browser_session_resume")

        import asyncio

        asyncio.run(run_test())

    def test_browser_takeover_requires_takeover_browser_session_action(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "status": "active",
            "device_trust_state": "trusted",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.gateway_browser_service._require_active_gateway_registration",
                    return_value=registration,
                ),
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.gateway_action_decision",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "operation": "browser_session_takeover",
                        "next_action": "takeover_browser_session",
                    },
                ) as decision_mock,
                patch(
                    "server_modules.gateway_browser_service.rust_runtime_kernel_client.enforce_kernel_decision",
                    side_effect=lambda _command, decision: decision,
                ),
                patch(
                    "server_modules.gateway_browser_service.gateway_execution_service.execute_tool_via_gateway",
                    new=AsyncMock(return_value={"result": {}, "request_id": "req-1"}),
                ),
                patch(
                    "server_modules.gateway_browser_service._append_browser_activity",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await gateway_browser_service.execute_browser_capability_via_gateway(
                    gateway_id="gw-1",
                    capability_id=gateway_browser_service.BROWSER_SESSION_TAKEOVER_CAPABILITY,
                    arguments={"browser_session_id": "gbsess-1", "note": "manual"},
                    run_id="run-1",
                    trace_id="trace-1",
                    workspace_id="ws-1",
                    request_id="req-1",
                )

            request = decision_mock.call_args.kwargs
            self.assertEqual(request["operation"], "browser_session_takeover")

        import asyncio

        asyncio.run(run_test())

    def test_cloud_browser_fallback_requires_prepare_cloud_browser_fallback_action(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "device_id": "device-1",
        }
        with (
            patch(
                "server_modules.gateway_browser_service.rust_runtime_kernel_client.gateway_action_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "operation": "browser_session_start",
                    "next_action": "prepare_cloud_browser_fallback",
                },
            ) as decision_mock,
            patch(
                "server_modules.gateway_browser_service.rust_runtime_kernel_client.enforce_kernel_decision",
                side_effect=lambda _command, decision: decision,
            ),
            patch(
                "server_modules.gateway_browser_service.gateway_state_repository.upsert_gateway_browser_session",
                return_value={"browser_session_id": "gbsess-1", "status": "fallback_ready"},
            ) as upsert_mock,
            patch(
                "server_modules.gateway_browser_service.gateway_state_repository.record_gateway_event",
                return_value=None,
            ),
        ):
            payload = gateway_browser_service.build_cloud_browser_fallback(
                registration=registration,
                run_id="run-1",
                trace_id="trace-1",
                session_profile="default",
                reason="gateway_unavailable",
            )

        request = decision_mock.call_args.kwargs
        self.assertEqual(request["operation"], "browser_session_start")
        self.assertEqual(request["capability_id"], gateway_browser_service.BROWSER_SESSION_START_CAPABILITY)
        self.assertFalse(request["gateway_connected"])
        self.assertTrue(request["cloud_fallback_available"])
        upsert_mock.assert_called_once()
        self.assertEqual(payload["execution_target"], "cloud_browser")

    def test_cloud_browser_fallback_blocks_wrong_rust_action_before_state_write(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "device_id": "device-1",
        }
        with (
            patch(
                "server_modules.gateway_browser_service.rust_runtime_kernel_client.gateway_action_decision",
                return_value={
                    "ok": True,
                    "decision": "allow",
                    "operation": "browser_session_start",
                    "next_action": "start_browser_session",
                },
            ),
            patch(
                "server_modules.gateway_browser_service.rust_runtime_kernel_client.enforce_kernel_decision",
                side_effect=lambda _command, decision: decision,
            ),
            patch(
                "server_modules.gateway_browser_service.gateway_state_repository.upsert_gateway_browser_session",
                side_effect=AssertionError("should not persist fallback session"),
            ) as upsert_mock,
        ):
            with self.assertRaises(RuntimeError) as raised:
                gateway_browser_service.build_cloud_browser_fallback(
                    registration=registration,
                    run_id="run-1",
                    trace_id="trace-1",
                    session_profile="default",
                    reason="gateway_unavailable",
                )

        self.assertIn("unexpected next_action", str(raised.exception))
        upsert_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
