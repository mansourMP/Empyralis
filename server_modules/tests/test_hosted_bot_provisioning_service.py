"""Tests for hosted_bot_provisioning_service.route_agent_inbound (BYO Telegram bots).

FIX B regression cover: route_agent_inbound previously had no way to carry a
real per-message sender id at all — it substituted chat_id when calling
dispatch_sage_reply_safe. A BYO bot can be added to a group by anyone (it's
a real, discoverable Telegram bot), so a group's shared chat_id being used
as "sender_id" collapsed every distinct member into the same identity.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import hosted_bot_provisioning_service as prov


def _binding(**overrides) -> dict:
    base = {
        "workspace_id": "ws-1",
        "tenant_id": "tenant-1",
        "binding": {
            "bot_username": "parts_pro_bot",
            "credential_id": "cred-1",
        },
    }
    base.update(overrides)
    return base


class RouteAgentInboundSenderIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_sender_id_is_threaded_through_to_the_reply_dispatcher(self) -> None:
        captured = {}

        async def _fake_dispatch(**kwargs):
            captured.update(kwargs)
            return True

        with patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=_binding())), \
             patch.object(prov, "resolve_bot_token", return_value="bot-token-123"), \
             patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})), \
             patch("server_modules.sage_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch):
            result = await prov.route_agent_inbound(
                agent_install_id="agent-1",
                chat_id="-100999",
                message="hello from a group member",
                sender_id="42424242",
                reply_to_message_id=7,
            )

        self.assertTrue(result.get("routed"))
        self.assertEqual(captured.get("sender_id"), "42424242")
        self.assertNotEqual(captured.get("sender_id"), "-100999")

    async def test_two_different_senders_in_the_same_chat_get_distinct_sender_ids(self) -> None:
        captured_ids = []

        async def _fake_dispatch(**kwargs):
            captured_ids.append(kwargs.get("sender_id"))
            return True

        with patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=_binding())), \
             patch.object(prov, "resolve_bot_token", return_value="bot-token-123"), \
             patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})), \
             patch("server_modules.sage_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch):
            await prov.route_agent_inbound(
                agent_install_id="agent-1", chat_id="-100999", message="hi", sender_id="111",
            )
            await prov.route_agent_inbound(
                agent_install_id="agent-1", chat_id="-100999", message="hi", sender_id="222",
            )

        self.assertEqual(captured_ids, ["111", "222"])

    async def test_missing_sender_id_falls_back_to_chat_id_rather_than_crashing(self) -> None:
        # Defensive fallback for the pathological case where the caller has
        # no sender id at all (e.g. Telegram omitted `from`) — never a real
        # 1:1 DM, but must not raise or send an empty sender_id.
        captured = {}

        async def _fake_dispatch(**kwargs):
            captured.update(kwargs)
            return True

        with patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=_binding())), \
             patch.object(prov, "resolve_bot_token", return_value="bot-token-123"), \
             patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})), \
             patch("server_modules.sage_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch):
            await prov.route_agent_inbound(
                agent_install_id="agent-1", chat_id="555444", message="hi",
            )

        self.assertEqual(captured.get("sender_id"), "555444")


if __name__ == "__main__":
    unittest.main()
