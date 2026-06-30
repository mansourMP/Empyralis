import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server_modules import gateway_protocol_service


class GatewayProtocolServiceOutboundRustGateTests(unittest.TestCase):
    def test_gateway_protocol_message_decision_accepts_channel_outbound(self) -> None:
        with patch(
            "server_modules.gateway_protocol_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "message_type": "channel_outbound",
                "next_action": "dispatch_channel_outbound",
            },
        ) as rust_mock:
            decision = gateway_protocol_service._enforce_gateway_protocol_message_decision(
                gateway_id="gw-1",
                session_id="sess-1",
                workspace_id="ws-1",
                message_type="channel.outbound",
                payload={"text": "hello"},
                tool_name="whatsapp_personal.send",
            )

        payload = rust_mock.call_args.args[1]
        self.assertEqual(payload["message_type"], "channel.outbound")
        self.assertEqual(payload["tool_name"], "whatsapp_personal.send")
        self.assertEqual(decision["next_action"], "dispatch_channel_outbound")

    def test_channel_protocol_route_accepts_dispatch_gateway_operation(self) -> None:
        with patch(
            "server_modules.gateway_protocol_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_service_operation_allowed",
                "operation": "protocol_route",
                "next_action": "dispatch_gateway_operation",
            },
        ) as rust_mock:
            decision = gateway_protocol_service._enforce_gateway_channel_protocol_route(
                gateway_id="gw-1",
                session_id="sess-1",
                workspace_id="ws-1",
                tenant_id="tenant-1",
                device_id="device-1",
                capability_id="whatsapp_personal.send",
                request_id="send-1",
            )

        payload = rust_mock.call_args.args[1]
        self.assertEqual(payload["operation"], "protocol_route")
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["capability_id"], "whatsapp_personal.send")
        self.assertEqual(decision["next_action"], "dispatch_gateway_operation")

    def test_dispatch_channel_outbound_blocks_wrong_rust_action_before_send_request(self) -> None:
        connection = SimpleNamespace(
            session_id="sess-1",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_request=AsyncMock(side_effect=AssertionError("should not send outbound frame")),
        )

        async def run_test() -> None:
            with (
                patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
                patch(
                    "server_modules.gateway_protocol_service.evaluate_gateway_quota",
                    return_value=SimpleNamespace(allowed=True, retry_after_seconds=None),
                ),
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
                    await gateway_protocol_service.dispatch_channel_outbound(
                        gateway_id="gw-1",
                        channel_key="whatsapp_personal",
                        provider="whatsapp_web",
                        remote_jid="8618657105303@s.whatsapp.net",
                        text="hello",
                        idempotency_key="send-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            connection.send_request.assert_not_awaited()

        asyncio.run(run_test())

    def test_dispatch_channel_outbound_blocks_wrong_gateway_protocol_action_before_send_request(self) -> None:
        connection = SimpleNamespace(
            session_id="sess-1",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_request=AsyncMock(side_effect=AssertionError("should not send outbound frame")),
        )

        def rust_side_effect(command, payload):
            if command == "gateway-protocol-decision":
                return {
                    "ok": True,
                    "decision": "allow",
                    "message_type": "channel_outbound",
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
                "operation": "protocol_route",
                "next_action": "dispatch_gateway_operation",
            }

        async def run_test() -> None:
            with (
                patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
                patch(
                    "server_modules.gateway_protocol_service.evaluate_gateway_quota",
                    return_value=SimpleNamespace(allowed=True, retry_after_seconds=None),
                ),
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
                    await gateway_protocol_service.dispatch_channel_outbound(
                        gateway_id="gw-1",
                        channel_key="whatsapp_personal",
                        provider="whatsapp_web",
                        remote_jid="8618657105303@s.whatsapp.net",
                        text="hello",
                        idempotency_key="send-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            connection.send_request.assert_not_awaited()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
