from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_channel_router


class AgentChannelRouterRustGateTests(unittest.IsolatedAsyncioTestCase):
    def test_public_deployed_agent_gate_accepts_canonical_action(self):
        context = agent_channel_router.build_routing_context(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            channel_key="telegram",
            endpoint_key="@partspro_bot",
            customer_message="Need brake pads",
            install={"id": "install-specialist", "label": "Parts Pro"},
            owner_type="specialist",
            actor_id="telegram-user-1",
        )
        context.deployed_agent_id = "dagent_1"
        context.deployed_agent_state = "live"

        captured = {}

        def _fake_rust(command: str, payload: dict[str, object]):
            captured["command"] = command
            captured["payload"] = dict(payload)
            return {"next_action": "route_public_deployed_agent"}

        with patch.object(
            agent_channel_router.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=_fake_rust,
        ):
            result = agent_channel_router._enforce_public_deployed_agent_route_decision(context=context)

        self.assertEqual(result["next_action"], "route_public_deployed_agent")
        self.assertEqual(captured["command"], "deployed-agent-service-decision")
        self.assertEqual(captured["payload"]["operation"], "public_route")
        self.assertEqual(captured["payload"]["deployed_agent_id"], "dagent_1")
        self.assertEqual(captured["payload"]["channel_key"], "telegram")

    async def test_route_inbound_public_deployed_agent_wrong_rust_action_blocks_before_inbound_event(self):
        context = agent_channel_router.build_routing_context(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            channel_key="telegram",
            endpoint_key="@partspro_bot",
            customer_message="Need brake pads",
            install={"id": "install-specialist", "label": "Parts Pro"},
            owner_type="specialist",
            actor_id="telegram-user-1",
        )
        context.deployed_agent_id = "dagent_1"
        context.deployed_agent_state = "live"

        with (
            patch.object(agent_channel_router, "resolve_public_channel_owner", new=AsyncMock(return_value=context)),
            patch.object(
                agent_channel_router.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value={"next_action": "read_agent"},
            ),
            patch.object(agent_channel_router, "record_inbound_message", new=AsyncMock()) as inbound_mock,
        ):
            with self.assertRaises(agent_channel_router.ChannelSecurityDeniedError) as raised:
                await agent_channel_router.route_inbound_channel_message(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    channel_key="telegram",
                    endpoint_key="@partspro_bot",
                    customer_message="Need brake pads",
                    actor_id="telegram-user-1",
                    message_id="msg-1",
                )

        self.assertIn("unexpected_next_action:read_agent", str(raised.exception))
        inbound_mock.assert_not_awaited()
