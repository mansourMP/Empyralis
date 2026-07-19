"""FIX 2 regression cover: discord_bot_provisioning_service.assign_agent_
discord's existing soft pre-check ("Discord bot @x is already bound to
another agent in this workspace") is enriched to name WHICH agent already
owns the bot when its label is cheaply resolvable
(agent_bindings_repository.get_agent_install_label), falling back to the
original "another agent" copy when it isn't -- e.g. the owning agent was
deleted, or the label lookup fails.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import discord_bot_provisioning_service as prov


class AssignAgentDiscordConflictMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_check_names_the_owning_agent_when_resolvable(self) -> None:
        existing = [{
            "key": prov.CHANNEL_KEY_DISCORD,
            "agent_install_id": "agent-owner",
            "binding": {"endpoint_key": "999", "is_inbound_owner": "true"},
        }]
        with (
            patch.object(prov, "discord_get_me", new=AsyncMock(return_value={"id": "999", "username": "sagebot"})),
            patch.object(prov.bindings, "list_workspace_channel_bindings", new=AsyncMock(return_value=existing)),
            patch.object(prov.bindings, "get_agent_install_label", new=AsyncMock(return_value="Sales Agent")),
            patch.object(prov, "store_byo_discord_credential") as store_mock,
        ):
            with self.assertRaises(prov.DiscordBotAlreadyBoundError) as ctx:
                await prov.assign_agent_discord(
                    agent_install_id="agent-2", workspace_id="ws-1", tenant_id="tenant-1", token="tok",
                )
        message = str(ctx.exception)
        self.assertIn("sagebot", message)
        self.assertIn("Sales Agent", message)
        store_mock.assert_not_called()

    async def test_pre_check_falls_back_to_generic_copy_when_label_unresolvable(self) -> None:
        existing = [{
            "key": prov.CHANNEL_KEY_DISCORD,
            "agent_install_id": "agent-owner",
            "binding": {"endpoint_key": "999", "is_inbound_owner": "true"},
        }]
        with (
            patch.object(prov, "discord_get_me", new=AsyncMock(return_value={"id": "999", "username": "sagebot"})),
            patch.object(prov.bindings, "list_workspace_channel_bindings", new=AsyncMock(return_value=existing)),
            patch.object(prov.bindings, "get_agent_install_label", new=AsyncMock(return_value=None)),
        ):
            with self.assertRaises(prov.DiscordBotAlreadyBoundError) as ctx:
                await prov.assign_agent_discord(
                    agent_install_id="agent-2", workspace_id="ws-1", tenant_id="tenant-1", token="tok",
                )
        message = str(ctx.exception)
        self.assertIn("sagebot", message)
        self.assertIn("another agent", message)

    async def test_no_conflict_proceeds_to_write_the_binding(self) -> None:
        with (
            patch.object(prov, "discord_get_me", new=AsyncMock(return_value={"id": "999", "username": "sagebot"})),
            patch.object(prov.bindings, "list_workspace_channel_bindings", new=AsyncMock(return_value=[])),
            patch.object(prov, "store_byo_discord_credential", return_value="cred-1"),
            patch.object(prov.bindings, "upsert_channel_binding", new=AsyncMock(return_value={"id": "x"})) as upsert_mock,
        ):
            result = await prov.assign_agent_discord(
                agent_install_id="agent-2", workspace_id="ws-1", tenant_id="tenant-1", token="tok",
            )
        self.assertEqual(result["bot_id"], "999")
        upsert_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
