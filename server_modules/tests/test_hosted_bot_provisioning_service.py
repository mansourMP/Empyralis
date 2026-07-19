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


class AssignByoBotConflictTests(unittest.IsolatedAsyncioTestCase):
    """FIX 2 regression cover: assign_byo_bot previously had NO conflict
    translation at all -- a real inbound-owner conflict (channel_key
    'telegram_bot' has been covered by uq_agent_channel_bindings_
    inbound_owner_v2 since Phase 3D) would have propagated a raw asyncpg
    unique-violation string straight through routes_fleet.py's
    fleet_assign_agent_telegram (`except Exception as exc: return
    {"ok": False, "error": str(exc)}`) into the Fleet UI's error banner.
    Mirrors discord_bot_provisioning_service.assign_agent_discord's existing
    soft-pre-check + hard-guarantee-translation pattern."""

    async def test_pre_check_rejects_a_bot_already_bound_to_another_agent(self) -> None:
        with (
            patch.object(prov, "get_me", new=AsyncMock(return_value={"username": "parts_pro_bot", "id": "999"})),
            patch.object(prov.bindings, "find_inbound_owner_conflict", new=AsyncMock(return_value={"agent_install_id": "agent-owner"})),
            patch.object(prov, "store_byo_bot_credential") as store_mock,
            patch.object(prov.bindings, "upsert_channel_binding", new=AsyncMock()) as upsert_mock,
        ):
            with self.assertRaises(prov.TelegramBotAlreadyBoundError) as ctx:
                await prov.assign_byo_bot(
                    agent_install_id="agent-2", workspace_id="ws-1", tenant_id="tenant-1", token="tok",
                )
        self.assertIn("parts_pro_bot", str(ctx.exception))
        self.assertIn("already bound", str(ctx.exception).lower())
        # The pre-check must reject BEFORE writing a (now-orphaned)
        # credential or touching the binding table.
        store_mock.assert_not_called()
        upsert_mock.assert_not_awaited()

    async def test_race_condition_db_violation_is_translated_and_credential_rolled_back(self) -> None:
        """The soft pre-check is advisory only -- if two binds land
        concurrently, upsert_channel_binding itself raises the raw unique-
        index violation. That must be translated to a specific message
        (never a raw Postgres string), AND the credential written just
        before the failed write must be rolled back so it isn't orphaned."""
        db_error = RuntimeError(
            'duplicate key value violates unique constraint '
            '"uq_agent_channel_bindings_inbound_owner_v2"'
        )
        with (
            patch.object(prov, "get_me", new=AsyncMock(return_value={"username": "parts_pro_bot", "id": "999"})),
            patch.object(prov.bindings, "find_inbound_owner_conflict", new=AsyncMock(return_value=None)),
            patch.object(prov, "store_byo_bot_credential", return_value="cred-123"),
            patch.object(prov, "agent_bot_webhook_url", return_value=""),
            patch.object(prov.bindings, "upsert_channel_binding", new=AsyncMock(side_effect=db_error)),
            patch.object(prov, "delete_vault_credential_by_id") as delete_mock,
        ):
            with self.assertRaises(prov.TelegramBotAlreadyBoundError) as ctx:
                await prov.assign_byo_bot(
                    agent_install_id="agent-2", workspace_id="ws-1", tenant_id="tenant-1", token="tok",
                )
        message = str(ctx.exception).lower()
        self.assertNotIn("constraint", message)
        self.assertNotIn("duplicate key", message)
        self.assertIn("already bound", message)
        delete_mock.assert_called_once_with("cred-123")

    async def test_successful_assign_when_no_conflict(self) -> None:
        with (
            patch.object(prov, "get_me", new=AsyncMock(return_value={"username": "parts_pro_bot", "id": "999"})),
            patch.object(prov.bindings, "find_inbound_owner_conflict", new=AsyncMock(return_value=None)),
            patch.object(prov, "store_byo_bot_credential", return_value="cred-123"),
            patch.object(prov, "agent_bot_webhook_url", return_value=""),
            patch.object(prov.bindings, "upsert_channel_binding", new=AsyncMock(return_value={"id": "x"})) as upsert_mock,
        ):
            result = await prov.assign_byo_bot(
                agent_install_id="agent-2", workspace_id="ws-1", tenant_id="tenant-1", token="tok",
            )
        self.assertEqual(result["bot_username"], "parts_pro_bot")
        upsert_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
