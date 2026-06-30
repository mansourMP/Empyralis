import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service


class PersonalChannelsServiceRustGateTests(unittest.TestCase):
    def test_personal_gateway_config_accepts_dispatch_gateway_operation(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        with patch(
            "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_service_operation_allowed",
                "operation": "tool_execute",
                "next_action": "dispatch_gateway_operation",
            },
        ) as rust_mock:
            decision = personal_channels_service._enforce_personal_gateway_config_decision(
                gateway_id="gw-1",
                registration=registration,
                capability_id=personal_channels_service.TELEGRAM_PERSONAL_CONFIGURE_CAPABILITY,
                run_id="run-1",
                trace_id="trace-1",
            )

        payload = rust_mock.call_args.args[1]
        self.assertEqual(payload["operation"], "tool_execute")
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["capability_id"], "channel.telegram.personal.configure")
        self.assertEqual(decision["next_action"], "dispatch_gateway_operation")

    def test_telegram_personal_gateway_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "reason": "gateway_service_operation_allowed",
                        "operation": "tool_execute",
                        "next_action": "allow_gateway_service_operation",
                    },
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_execution_service.execute_tool_via_gateway",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch config tool")),
                ) as execute_mock,
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service.configure_telegram_personal_gateway(
                        gateway_id="gw-1",
                        registration=registration,
                        api_id=123456,
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            execute_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_whatsapp_personal_gateway_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "reason": "gateway_service_operation_allowed",
                        "operation": "tool_execute",
                        "next_action": "request_gateway_owner_approval",
                    },
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_execution_service.execute_tool_via_gateway",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch config tool")),
                ) as execute_mock,
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service.configure_whatsapp_personal_gateway(
                        gateway_id="gw-1",
                        registration=registration,
                        phone_number="8618657105303",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            execute_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_personal_channel_dispatch_accepts_dispatch_gateway_operation(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        with patch(
            "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "gateway_service_operation_allowed",
                "operation": "protocol_route",
                "next_action": "dispatch_gateway_operation",
            },
        ) as rust_mock:
            decision = personal_channels_service._enforce_personal_channel_dispatch_decision(
                gateway_id="gw-1",
                registration=registration,
                capability_id="channel.whatsapp.personal.send",
                request_id="send-1",
            )

        payload = rust_mock.call_args.args[1]
        self.assertEqual(payload["operation"], "protocol_route")
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["capability_id"], "channel.whatsapp.personal.send")
        self.assertEqual(decision["next_action"], "dispatch_gateway_operation")

    def test_whatsapp_personal_send_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "reason": "gateway_service_operation_allowed",
                        "operation": "protocol_route",
                        "next_action": "allow_gateway_service_operation",
                    },
                ),
                patch(
                    "server_modules.personal_channels_service.personal_channels_repository.create_or_get_outbound_message",
                    return_value=({"status": "queued", "remote_jid": "jid", "text": "hello"}, True),
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch outbound")),
                    create=True,
                ) as dispatch_mock,
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service.send_whatsapp_personal_message(
                        gateway_id="gw-1",
                        registration=registration,
                        remote_jid="8618657105303@s.whatsapp.net",
                        text="hello",
                        idempotency_key="send-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            dispatch_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_telegram_personal_send_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "reason": "gateway_service_operation_allowed",
                        "operation": "protocol_route",
                        "next_action": "request_gateway_owner_approval",
                    },
                ),
                patch(
                    "server_modules.personal_channels_service.personal_channels_repository.create_or_get_outbound_message",
                    return_value=({"status": "queued", "remote_jid": "jid", "text": "hello"}, True),
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch outbound")),
                    create=True,
                ) as dispatch_mock,
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service.send_telegram_personal_message(
                        gateway_id="gw-1",
                        registration=registration,
                        remote_jid="telegram-user-1",
                        text="hello",
                        idempotency_key="send-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            dispatch_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_whatsapp_automatic_reply_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        inbound = {"external_message_id": "msg-1", "remote_jid": "8618657105303@s.whatsapp.net"}
        outbound = {
            "status": "queued",
            "remote_jid": "8618657105303@s.whatsapp.net",
            "text": "auto-reply",
            "reply_to_external_message_id": "msg-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "reason": "gateway_service_operation_allowed",
                        "operation": "protocol_route",
                        "next_action": "allow_gateway_service_operation",
                    },
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch automatic reply")),
                    create=True,
                ) as dispatch_mock,
                patch(
                    "server_modules.personal_channels_service.personal_channels_repository.create_or_get_outbound_message",
                    return_value=(outbound, True),
                ),
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service._deliver_whatsapp_personal_reply(
                        gateway_id="gw-1",
                        registration=registration,
                        inbound=inbound,
                        remote_jid="8618657105303@s.whatsapp.net",
                        external_message_id="msg-1",
                        text="auto-reply",
                        push_name=None,
                        duplicate=False,
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            dispatch_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_telegram_automatic_reply_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        inbound = {"external_message_id": "msg-1", "remote_jid": "telegram-user-1"}
        outbound = {
            "status": "queued",
            "remote_jid": "telegram-user-1",
            "text": "auto-reply",
            "reply_to_external_message_id": "msg-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "reason": "gateway_service_operation_allowed",
                        "operation": "protocol_route",
                        "next_action": "request_gateway_owner_approval",
                    },
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch automatic reply")),
                    create=True,
                ) as dispatch_mock,
                patch(
                    "server_modules.personal_channels_service.personal_channels_repository.create_or_get_outbound_message",
                    return_value=(outbound, True),
                ),
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service._deliver_telegram_personal_reply(
                        gateway_id="gw-1",
                        registration=registration,
                        inbound=inbound,
                        remote_jid="telegram-user-1",
                        external_message_id="msg-1",
                        text="auto-reply",
                        duplicate=False,
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            dispatch_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_local_bridge_personal_send_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "reason": "gateway_service_operation_allowed",
                        "operation": "protocol_route",
                        "next_action": "allow_gateway_service_operation",
                    },
                ),
                patch(
                    "server_modules.personal_channels_service.personal_channels_repository.create_or_get_outbound_message",
                    return_value=({"status": "queued", "remote_jid": "jid", "text": "hello"}, True),
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch local bridge send")),
                    create=True,
                ) as dispatch_mock,
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service.send_local_bridge_personal_message(
                        gateway_id="gw-1",
                        registration=registration,
                        channel_key="signal_personal",
                        provider="signal_local_bridge",
                        remote_jid="signal-user-1",
                        text="hello",
                        idempotency_key="send-1",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            dispatch_mock.assert_not_awaited()

        asyncio.run(run_test())

    def test_local_bridge_automatic_reply_blocks_wrong_rust_action_before_dispatch(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        inbound = {"external_message_id": "msg-1", "remote_jid": "signal-user-1"}
        outbound = {
            "status": "queued",
            "remote_jid": "signal-user-1",
            "text": "auto-reply",
            "reply_to_external_message_id": "msg-1",
        }

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                    return_value={
                        "ok": True,
                        "decision": "allow",
                        "reason": "gateway_service_operation_allowed",
                        "operation": "protocol_route",
                        "next_action": "allow_gateway_service_operation",
                    },
                ),
                patch(
                    "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                    new=AsyncMock(side_effect=AssertionError("should not dispatch local bridge automatic reply")),
                    create=True,
                ) as dispatch_mock,
                patch(
                    "server_modules.personal_channels_service.personal_channels_repository.create_or_get_outbound_message",
                    return_value=(outbound, True),
                ),
            ):
                with self.assertRaises(ValueError) as raised:
                    await personal_channels_service._deliver_local_bridge_personal_reply(
                        gateway_id="gw-1",
                        registration=registration,
                        inbound=inbound,
                        channel_key="signal_personal",
                        provider="signal_local_bridge",
                        label="Signal",
                        remote_jid="signal-user-1",
                        external_message_id="msg-1",
                        push_name=None,
                        duplicate=False,
                        no_reply_prefix="signal_personal:noreply:",
                    )

            self.assertIn("unexpected next_action", str(raised.exception))
            dispatch_mock.assert_not_awaited()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
