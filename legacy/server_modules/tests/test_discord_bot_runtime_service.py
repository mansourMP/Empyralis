from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from server_modules.connectors.discord_bot_runtime_service import DiscordBotRuntimeService


class FakeDiscordListener:
    instances: list["FakeDiscordListener"] = []

    def __init__(self, credentials, *, allowed_channel_ids=None, on_event=None):
        self.credentials = credentials
        self.allowed_channel_ids = list(allowed_channel_ids or [])
        self.on_event = on_event
        self.ran = False
        FakeDiscordListener.instances.append(self)

    def run_forever(self):
        self.ran = True


class DiscordBotRuntimeServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeDiscordListener.instances.clear()

    def _service(self, *, rows, secrets=None, route_message=None):
        secrets = secrets or {}
        return DiscordBotRuntimeService(
            load_vault=lambda: {"credentials": rows},
            resolve_vault_credential=lambda credential_id, workspace_id=None: secrets[credential_id],
            listener_factory=FakeDiscordListener,
            append_event=lambda **kwargs: kwargs,
            route_message=route_message or AsyncMock(return_value={"ok": True, "run_id": "run-1"}),
            resolve_tenant=AsyncMock(return_value="tenant-1"),
        )

    def test_start_loads_only_discord_bot_connectors(self):
        service = self._service(
            rows=[
                {"id": "cred-discord", "provider": "discord_bot", "workspace_id": "workspace-1"},
                {"id": "cred-slack", "provider": "slack", "workspace_id": "workspace-1"},
            ],
            secrets={"cred-discord": {"bot_token": "token", "channel_id": "123"}},
        )

        result = service.start(block=True)

        self.assertEqual(result["started"], 1)
        self.assertEqual(len(FakeDiscordListener.instances), 1)
        self.assertTrue(FakeDiscordListener.instances[0].ran)
        self.assertEqual(FakeDiscordListener.instances[0].allowed_channel_ids, ["123"])

    def test_start_fails_closed_when_bot_token_missing(self):
        service = self._service(
            rows=[{"id": "cred-discord", "provider": "discord_bot", "workspace_id": "workspace-1"}],
            secrets={"cred-discord": {"channel_id": "123"}},
        )

        result = service.start(block=True)

        self.assertEqual(result["started"], 0)
        self.assertEqual(result["statuses"][0]["status"], "failed")
        self.assertEqual(result["statuses"][0]["reason"], "missing_bot_token")
        self.assertEqual(FakeDiscordListener.instances, [])

    def test_preflight_reports_ready_and_missing_token_without_starting(self):
        service = self._service(
            rows=[
                {"id": "cred-ready", "provider": "discord_bot", "workspace_id": "workspace-1"},
                {"id": "cred-missing", "provider": "discord_bot", "workspace_id": "workspace-1"},
            ],
            secrets={
                "cred-ready": {"bot_token": "token", "channel_id": "123"},
                "cred-missing": {"channel_id": "456"},
            },
        )

        result = service.preflight()

        self.assertFalse(result["ok"])
        self.assertEqual(result["connector_count"], 2)
        self.assertEqual(result["ready_count"], 1)
        self.assertEqual([item["status"] for item in result["statuses"]], ["pass", "fail"])
        self.assertEqual(result["statuses"][1]["reason"], "missing_bot_token")
        self.assertEqual(FakeDiscordListener.instances, [])

    async def test_guild_mention_routes_to_studio_with_master_fallback_disabled(self):
        route_message = AsyncMock(return_value={"ok": True, "triggered": True, "run_id": "run-1"})
        service = self._service(rows=[], route_message=route_message)

        result = await service.handle_parsed_event(
            {
                "kind": "event",
                "event_type": "message_create",
                "message_type": "mention",
                "channel_id": "123",
                "guild_id": "456",
                "message_id": "msg-1",
                "user_id": "user-1",
                "username": "Mansur",
                "text": "hello",
            },
            connector_entry={
                "id": "cred-discord",
                "provider": "discord_bot",
                "workspace_id": "workspace-1",
                "metadata": {"channel_registry_bindings": {"discord": {"endpoint_key": "discord:123"}}},
            },
            credentials={"bot_token": "token", "channel_id": "123"},
        )

        self.assertTrue(result["triggered"])
        route_message.assert_awaited_once()
        kwargs = route_message.await_args.kwargs
        self.assertEqual(kwargs["channel_key"], "discord")
        self.assertEqual(kwargs["endpoint_key"], "discord:123")
        self.assertEqual(kwargs["customer_message"], "hello")
        self.assertFalse(kwargs["allow_master_fallback"])
        self.assertEqual(kwargs["metadata"]["delivery_source"], "discord_gateway")

    async def test_bot_authored_message_create_is_ignored_if_listener_exposes_it(self):
        route_message = AsyncMock(return_value={"ok": True, "triggered": True, "run_id": "run-1"})
        service = self._service(rows=[], route_message=route_message)

        result = await service.handle_parsed_event(
            {
                "kind": "event",
                "event_type": "message_create",
                "message_type": "mention",
                "channel_id": "123",
                "message_id": "msg-bot-1",
                "user_id": "bot-1",
                "username": "RelayBot",
                "text": "<@999> ignore this bot-authored echo",
                "raw_event": {"author": {"id": "bot-1", "bot": True}},
            },
            connector_entry={"id": "cred-discord", "provider": "discord_bot", "workspace_id": "workspace-1"},
            credentials={"bot_token": "token", "channel_id": "123"},
        )

        self.assertFalse(result["triggered"])
        self.assertEqual(result["reason"], "bot_authored")
        route_message.assert_not_awaited()

    async def test_mismatched_channel_does_not_route(self):
        route_message = AsyncMock(return_value={"ok": True, "run_id": "run-1"})
        service = self._service(rows=[], route_message=route_message)

        result = await service.handle_parsed_event(
            {
                "kind": "event",
                "event_type": "message_create",
                "message_type": "direct_message",
                "channel_id": "999",
                "message_id": "msg-1",
                "text": "hello",
            },
            connector_entry={"id": "cred-discord", "provider": "discord_bot", "workspace_id": "workspace-1"},
            credentials={"bot_token": "token", "channel_id": "123"},
        )

        self.assertFalse(result["triggered"])
        self.assertEqual(result["reason"], "connector_mismatch")
        route_message.assert_not_awaited()

    async def test_sync_listener_callback_for_mention_schedules_inside_running_loop(self):
        route_message = AsyncMock(return_value={"ok": True, "triggered": True, "run_id": "run-1"})
        service = self._service(rows=[], route_message=route_message)

        result = service.handle_parsed_event_sync(
            {
                "kind": "event",
                "event_type": "message_create",
                "message_type": "mention",
                "channel_id": "123",
                "guild_id": "456",
                "message_id": "msg-1",
                "text": "hello",
            },
            connector_entry={"id": "cred-discord", "provider": "discord_bot", "workspace_id": "workspace-1"},
            credentials={"bot_token": "token", "channel_id": "123"},
        )
        await asyncio.sleep(0)

        self.assertEqual(result, {"ok": True, "scheduled": True})
        route_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
