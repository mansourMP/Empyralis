import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server_modules import gateway_protocol_service


class GatewayProtocolServiceToolInterruptRustGateTests(unittest.TestCase):
    def test_gateway_protocol_message_decision_accepts_tool_interrupt(self) -> None:
        with patch(
            "server_modules.gateway_protocol_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "message_type": "tool_interrupt",
                "next_action": "dispatch_tool_interrupt",
            },
        ) as rust_mock:
            decision = gateway_protocol_service._enforce_gateway_protocol_message_decision(
                gateway_id="gw-1",
                session_id="sess-1",
                workspace_id="ws-1",
                message_type="tool.interrupt",
                payload={"run_id": "run-1"},
                tool_name="tool.interrupt",
            )

        payload = rust_mock.call_args.args[1]
        self.assertEqual(payload["message_type"], "tool.interrupt")
        self.assertEqual(payload["tool_name"], "tool.interrupt")
        self.assertEqual(decision["next_action"], "dispatch_tool_interrupt")

    def test_quota_check_accepts_allow_gateway_service_operation(self) -> None:
        with patch(
            "server_modules.gateway_protocol_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_quota_allowed",
                "operation": "quota_check",
                "next_action": "allow_gateway_service_operation",
            },
        ) as rust_mock:
            decision = gateway_protocol_service._enforce_gateway_quota_check(
                gateway_id="gw-1",
                session_id="sess-1",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                device_id="device-1",
                request_id="req-1",
                quota_profile="gateway_tool_execution",
            )

        payload = rust_mock.call_args.args[1]
        self.assertEqual(payload["operation"], "quota_check")
        self.assertEqual(payload["quota_profile"], "gateway_tool_execution")
        self.assertEqual(decision["next_action"], "allow_gateway_service_operation")

    def test_tool_interrupt_protocol_route_accepts_dispatch_gateway_operation(self) -> None:
        with patch(
            "server_modules.gateway_protocol_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_service_operation_allowed",
                "operation": "tool_interrupt",
                "next_action": "dispatch_gateway_operation",
            },
        ) as rust_mock:
            decision = gateway_protocol_service._enforce_gateway_tool_interrupt_protocol_route(
                gateway_id="gw-1",
                session_id="sess-1",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                device_id="device-1",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-1",
            )

        payload = rust_mock.call_args.args[1]
        self.assertEqual(payload["operation"], "tool_interrupt")
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["capability_id"], "tool.interrupt")
        self.assertEqual(decision["next_action"], "dispatch_gateway_operation")

    def test_dispatch_tool_interrupt_blocks_wrong_rust_action_before_send_request(self) -> None:
        connection = SimpleNamespace(
            session_id="sess-1",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_request=AsyncMock(side_effect=AssertionError("should not send tool.interrupt")),
        )

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.gateway_protocol_service._get_live_connection",
                    return_value=connection,
                ),
                patch(
                    "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
                    return_value={
                        "gateway_id": "gw-1",
                        "workspace_id": "ws-1",
                        "tenant_id": "tenant-1",
                        "device_id": "device-1",
                    },
                ),
                patch(
                    "server_modules.gateway_protocol_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    side_effect=lambda _command, payload: {
                        "ok": True,
                        "decision": "allow",
                        "reason": "gateway_quota_allowed" if payload.get("operation") == "quota_check" else "gateway_service_operation_allowed",
                        "operation": payload.get("operation") or "",
                        "next_action": "allow_gateway_service_operation",
                    },
                ),
            ):
                with self.assertRaises(gateway_protocol_service.GatewayProtocolRustGateError) as raised:
                    await gateway_protocol_service.dispatch_tool_interrupt(
                        gateway_id="gw-1",
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        request_id="req-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            connection.send_request.assert_not_awaited()

        asyncio.run(run_test())

    def test_dispatch_tool_interrupt_blocks_wrong_gateway_protocol_action_before_send_request(self) -> None:
        connection = SimpleNamespace(
            session_id="sess-1",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_request=AsyncMock(side_effect=AssertionError("should not send tool.interrupt")),
        )

        def rust_side_effect(command, payload):
            if command == "gateway-protocol-decision":
                return {
                    "ok": True,
                    "decision": "allow",
                    "message_type": "tool_interrupt",
                    "next_action": "dispatch_gateway_operation",
                }
            if payload.get("operation") == "quota_check":
                return {
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_quota_allowed",
                    "operation": "quota_check",
                    "next_action": "allow_gateway_service_operation",
                }
            return {
                "ok": True,
                "decision": "allow",
                "reason": "gateway_service_operation_allowed",
                "operation": "tool_interrupt",
                "next_action": "dispatch_gateway_operation",
            }

        async def run_test() -> None:
            with (
                patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
                patch(
                    "server_modules.gateway_protocol_service._get_live_connection",
                    return_value=connection,
                ),
                patch(
                    "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
                    return_value={
                        "gateway_id": "gw-1",
                        "workspace_id": "ws-1",
                        "tenant_id": "tenant-1",
                        "device_id": "device-1",
                    },
                ),
                patch(
                    "server_modules.gateway_protocol_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    side_effect=rust_side_effect,
                ),
            ):
                with self.assertRaises(gateway_protocol_service.GatewayProtocolRustGateError) as raised:
                    await gateway_protocol_service.dispatch_tool_interrupt(
                        gateway_id="gw-1",
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        request_id="req-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            connection.send_request.assert_not_awaited()

        asyncio.run(run_test())

    def test_dispatch_tool_interrupt_blocks_wrong_quota_action_before_send_request(self) -> None:
        connection = SimpleNamespace(
            session_id="sess-1",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_request=AsyncMock(side_effect=AssertionError("should not send tool.interrupt")),
        )

        def rust_side_effect(_command, payload):
            if payload.get("operation") == "quota_check":
                return {
                    "ok": True,
                    "decision": "allow",
                    "reason": "gateway_quota_allowed",
                    "operation": "quota_check",
                    "next_action": "dispatch_gateway_operation",
                }
            return {
                "ok": True,
                "decision": "allow",
                "reason": "gateway_service_operation_allowed",
                "operation": "tool_interrupt",
                "next_action": "dispatch_gateway_operation",
            }

        async def run_test() -> None:
            with (
                patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
                patch(
                    "server_modules.gateway_protocol_service._get_live_connection",
                    return_value=connection,
                ),
                patch(
                    "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
                    return_value={
                        "gateway_id": "gw-1",
                        "workspace_id": "ws-1",
                        "tenant_id": "tenant-1",
                        "device_id": "device-1",
                    },
                ),
                patch(
                    "server_modules.gateway_protocol_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    side_effect=rust_side_effect,
                ),
            ):
                with self.assertRaises(gateway_protocol_service.GatewayProtocolRustGateError) as raised:
                    await gateway_protocol_service.dispatch_tool_interrupt(
                        gateway_id="gw-1",
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        request_id="req-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            connection.send_request.assert_not_awaited()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
