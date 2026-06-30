import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from server_modules import routes_personal_channels


class RoutesPersonalChannelsRustGateTests(unittest.TestCase):
    def test_personal_channel_approval_request_accepts_request_gateway_owner_approval(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        current_user = {"user_id": "owner-1", "role": "member"}
        with patch(
            "server_modules.routes_personal_channels.rust_runtime_kernel_client.gateway_service_decision",
            return_value={
                "ok": True,
                "decision": "requires_approval",
                "reason": "risk_requires_approval,approval_memory_miss",
                "operation": "approval_request",
                "next_action": "request_gateway_owner_approval",
            },
        ) as rust_mock:
            decision = routes_personal_channels._enforce_personal_channel_approval_request(
                registration=registration,
                current_user=current_user,
                capability_id="channel.whatsapp.personal.send",
                run_id="run-1",
                trace_id="trace-1",
                request_id="req-1",
            )

        payload = rust_mock.call_args.kwargs
        self.assertEqual(payload["operation"], "approval_request")
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["risk_level"], "critical")
        self.assertEqual(decision["next_action"], "request_gateway_owner_approval")

    def test_personal_channel_approval_request_blocks_wrong_rust_action_before_request(self) -> None:
        registration = {
            "gateway_id": "gw-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        current_user = {"user_id": "owner-1", "role": "member"}

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.routes_personal_channels.rust_runtime_kernel_client.gateway_service_decision",
                    return_value={
                        "ok": True,
                        "decision": "requires_approval",
                        "reason": "risk_requires_approval",
                        "operation": "approval_request",
                        "next_action": "allow_gateway_service_operation",
                    },
                ),
                patch(
                    "server_modules.routes_personal_channels.gateway_approval_service.request_gateway_tool_approval",
                    new=AsyncMock(side_effect=AssertionError("should not request approval")),
                ) as approval_mock,
            ):
                with pytest.raises(HTTPException) as raised:
                    await routes_personal_channels._request_personal_channel_send_approval(
                        action="personal_channel.whatsapp.send",
                        registration=registration,
                        current_user=current_user,
                        gateway_id="gw-1",
                        channel_key="whatsapp_personal",
                        capability_id="channel.whatsapp.personal.send",
                        remote_jid="8618657105303@s.whatsapp.net",
                        text="hello",
                        idempotency_key="send-1",
                        reply_to_external_message_id=None,
                    )

            self.assertEqual(raised.exception.status_code, 423)
            self.assertIn("unexpected decision/next_action", str(raised.exception.detail))
            approval_mock.assert_not_awaited()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
