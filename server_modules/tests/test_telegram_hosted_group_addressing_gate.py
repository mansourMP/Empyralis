"""FIX C: group mention/addressing gate for Telegram Hosted Path B + BYO.

Before this fix there was zero entities/mention parsing anywhere in the
hosted-bot path — a group/supergroup message that was already paired (or a
BYO bot added to any group by anyone, since it's a real, discoverable
Telegram bot) reached a live, dispatched reply for EVERY message, addressed
or not. text_addresses_bot (sage_telegram_hosted_service.py) is the shared
core matcher; is_message_addressed_to_bot binds it to the single shared
hosted bot's identity (env/cache), and hosted_bot_provisioning_service binds
it to each BYO bot's own resolved identity.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import hosted_bot_provisioning_service as prov
from server_modules import routes_sage_telegram_hosted as routes
from server_modules import sage_telegram_hosted_service as hosted


def _mention_entity(offset: int, length: int) -> dict:
    return {"type": "mention", "offset": offset, "length": length}


class TextAddressesBotPureFunctionTests(unittest.TestCase):
    """Core matcher, identity-agnostic — shared by both Path B and BYO."""

    def test_no_signal_is_not_addressed(self) -> None:
        self.assertFalse(
            hosted.text_addresses_bot(text="just chatting", entities=[], reply_to_from_id="", bot_id="9", bot_username="sage_bot")
        )

    def test_matching_mention_entity_is_addressed(self) -> None:
        text = "@sage_bot help me out"
        self.assertTrue(
            hosted.text_addresses_bot(
                text=text, entities=[_mention_entity(0, len("@sage_bot"))], reply_to_from_id="", bot_id="9", bot_username="sage_bot"
            )
        )

    def test_reply_to_bot_id_is_addressed(self) -> None:
        self.assertTrue(
            hosted.text_addresses_bot(text="yes", entities=[], reply_to_from_id="9", bot_id="9", bot_username="sage_bot")
        )

    def test_reply_to_a_different_participant_is_not_addressed(self) -> None:
        self.assertFalse(
            hosted.text_addresses_bot(text="yes", entities=[], reply_to_from_id="some-other-member", bot_id="9", bot_username="sage_bot")
        )


class IsMessageAddressedToBotTests(unittest.TestCase):
    """The Path B wrapper bound to the single shared hosted bot's identity."""

    def test_unaddressed_parsed_message_is_false(self) -> None:
        parsed = {"text": "anyone free for lunch?", "entities": [], "reply_to_from_id": ""}
        self.assertFalse(hosted.is_message_addressed_to_bot(parsed))

    def test_default_bot_id_reply_is_addressed(self) -> None:
        # SAGE_TELEGRAM_HOSTED_BOT_USER_ID defaults to "8870032163" with no
        # env override — this is the "stored bot identity" FIX C calls for.
        parsed = {"text": "sure", "entities": [], "reply_to_from_id": "8870032163"}
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(hosted.is_message_addressed_to_bot(parsed))

    @patch.dict("os.environ", {"EMPYRALIS_TELEGRAM_HOSTED_BOT_USERNAME": "sage_bot"})
    def test_mention_of_configured_username_is_addressed(self) -> None:
        text = "@sage_bot what's on my calendar?"
        parsed = {"text": text, "entities": [_mention_entity(0, len("@sage_bot"))], "reply_to_from_id": ""}
        self.assertTrue(hosted.is_message_addressed_to_bot(parsed))


class ProcessUpdateGroupGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._pairs_backup = dict(hosted._SAGE_HOSTED_PAIRS)
        hosted._SAGE_HOSTED_PAIRS.clear()
        hosted._SAGE_HOSTED_PAIRS["-100555"] = {"workspace_id": "ws-1", "paired_at": "2020-01-01T00:00:00+00:00"}
        self._persist_patch = patch.object(hosted, "_persist_after_mutation", lambda: None)
        self._persist_patch.start()

    def tearDown(self) -> None:
        self._persist_patch.stop()
        hosted._SAGE_HOSTED_PAIRS.clear()
        hosted._SAGE_HOSTED_PAIRS.update(self._pairs_backup)

    async def test_unaddressed_group_message_never_reaches_the_reply_dispatcher(self) -> None:
        update = {
            "update_id": 100,
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "chat": {"id": -100555, "type": "group"},
                "from": {"id": 555, "first_name": "Rando", "is_bot": False},
                "text": "does anyone know a good taco place",
            },
        }
        with patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)) as cmd_mock, \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as reply_mock:
            handled = await hosted._process_update(update)
        self.assertFalse(handled, "an unaddressed group message must never produce a Sage reply")
        cmd_mock.assert_not_called()
        reply_mock.assert_not_called()

    async def test_mentioning_the_bot_reaches_the_reply_dispatcher(self) -> None:
        with patch.dict("os.environ", {"EMPYRALIS_TELEGRAM_HOSTED_BOT_USERNAME": "sage_bot"}, clear=False):
            text = "@sage_bot what's the weather?"
            update = {
                "update_id": 101,
                "message": {
                    "message_id": 2,
                    "date": 1700000000,
                    "chat": {"id": -100555, "type": "group"},
                    "from": {"id": 555, "first_name": "Rando", "is_bot": False},
                    "text": text,
                    "entities": [_mention_entity(0, len("@sage_bot"))],
                },
            }
            with patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
                 patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as reply_mock:
                handled = await hosted._process_update(update)
        self.assertTrue(handled)
        reply_mock.assert_awaited_once()

    async def test_private_chat_is_never_gated_regardless_of_mention(self) -> None:
        hosted._SAGE_HOSTED_PAIRS["555444"] = {"workspace_id": "ws-1", "paired_at": "2020-01-01T00:00:00+00:00"}
        update = {
            "update_id": 102,
            "message": {
                "message_id": 3,
                "date": 1700000000,
                "chat": {"id": 555444, "type": "private"},
                "from": {"id": 555444, "first_name": "Owner", "is_bot": False},
                "text": "remind me to call mom",
            },
        }
        with patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as reply_mock:
            handled = await hosted._process_update(update)
        self.assertTrue(handled)
        reply_mock.assert_awaited_once()


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)
    return app


class TelegramWebhookRouteGroupGateTests(unittest.TestCase):
    """The webhook route has its own hosted.is_message_addressed_to_bot(parsed)
    check (separate code path from _process_update, used by the background
    polling loop) — proven independently here."""

    def setUp(self) -> None:
        routes._HOSTED_WEBHOOK_RATE_BUCKETS.clear()
        self._pairs_backup = dict(hosted._SAGE_HOSTED_PAIRS)
        hosted._SAGE_HOSTED_PAIRS.clear()
        hosted._SAGE_HOSTED_PAIRS["-100555"] = {"workspace_id": "ws-1", "paired_at": "2020-01-01T00:00:00+00:00"}
        self.client = TestClient(_build_app())

    def tearDown(self) -> None:
        hosted._SAGE_HOSTED_PAIRS.clear()
        hosted._SAGE_HOSTED_PAIRS.update(self._pairs_backup)

    def test_unaddressed_group_message_gets_no_reply_dispatch(self) -> None:
        body = {
            "update_id": 200,
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "chat": {"id": -100555, "type": "group"},
                "from": {"id": 999, "first_name": "Rando", "is_bot": False},
                "text": "just chatting about the weekend",
            },
        }
        with patch.object(hosted, "is_configured", return_value=True), \
             patch.object(hosted, "is_webhook_secret_configured", return_value=True), \
             patch.object(hosted, "verify_webhook_signature", return_value=True), \
             patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)) as cmd_mock, \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as reply_mock:
            resp = self.client.post(
                "/sage/telegram-hosted/webhook",
                json=body,
                headers={"X-Telegram-Bot-Api-Secret-Token": "whatever"},
            )
        self.assertEqual(resp.status_code, 200)
        cmd_mock.assert_not_called()
        reply_mock.assert_not_called()

    def test_addressed_group_message_reaches_the_reply_dispatcher(self) -> None:
        body = {
            "update_id": 201,
            "message": {
                "message_id": 2,
                "date": 1700000000,
                "chat": {"id": -100555, "type": "group"},
                "from": {"id": 999, "first_name": "Rando", "is_bot": False},
                "text": "quick question",
                "reply_to_message": {"message_id": 1, "from": {"id": 8870032163}},
            },
        }
        with patch.object(hosted, "is_configured", return_value=True), \
             patch.object(hosted, "is_webhook_secret_configured", return_value=True), \
             patch.object(hosted, "verify_webhook_signature", return_value=True), \
             patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as reply_mock:
            resp = self.client.post(
                "/sage/telegram-hosted/webhook",
                json=body,
                headers={"X-Telegram-Bot-Api-Secret-Token": "whatever"},
            )
        self.assertEqual(resp.status_code, 200)
        reply_mock.assert_awaited_once()


def _binding(**overrides) -> dict:
    base = {
        "workspace_id": "ws-1",
        "tenant_id": "tenant-1",
        "binding": {"bot_username": "parts_pro_bot", "credential_id": "cred-1"},
    }
    base.update(overrides)
    return base


class ByoRouteAgentInboundGroupGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        prov._BYO_BOT_ID_CACHE.clear()

    async def test_unaddressed_group_message_never_runs_a_turn(self) -> None:
        with patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=_binding())), \
             patch.object(prov, "resolve_bot_token", return_value="bot-token-123"), \
             patch.object(prov, "get_me", new=AsyncMock(return_value={"id": "bot-numeric-id", "username": "parts_pro_bot"})), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as dispatch_mock:
            result = await prov.route_agent_inbound(
                agent_install_id="agent-1",
                chat_id="-100999",
                message="anyone know a good mechanic?",
                sender_id="42",
                chat_type="group",
                entities=[],
                reply_to_from_id="",
            )
        self.assertTrue(result.get("routed"))
        self.assertFalse(result.get("processed", True))
        self.assertEqual(result.get("reason"), "group_not_addressed")
        dispatch_mock.assert_not_called()

    async def test_mention_of_bot_username_runs_a_real_turn(self) -> None:
        text = "@parts_pro_bot need brake pads"
        with patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=_binding())), \
             patch.object(prov, "resolve_bot_token", return_value="bot-token-123"), \
             patch.object(prov, "get_me", new=AsyncMock(return_value={"id": "bot-numeric-id", "username": "parts_pro_bot"})), \
             patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as dispatch_mock:
            result = await prov.route_agent_inbound(
                agent_install_id="agent-1",
                chat_id="-100999",
                message=text,
                sender_id="42",
                chat_type="group",
                entities=[{"type": "mention", "offset": 0, "length": len("@parts_pro_bot")}],
                reply_to_from_id="",
            )
        self.assertTrue(result.get("routed"))
        dispatch_mock.assert_awaited_once()

    async def test_reply_to_bots_own_numeric_id_runs_a_real_turn(self) -> None:
        with patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=_binding())), \
             patch.object(prov, "resolve_bot_token", return_value="bot-token-123"), \
             patch.object(prov, "get_me", new=AsyncMock(return_value={"id": "bot-numeric-id", "username": "parts_pro_bot"})), \
             patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as dispatch_mock:
            result = await prov.route_agent_inbound(
                agent_install_id="agent-1",
                chat_id="-100999",
                message="yes please",
                sender_id="42",
                chat_type="supergroup",
                entities=[],
                reply_to_from_id="bot-numeric-id",
            )
        self.assertTrue(result.get("routed"))
        dispatch_mock.assert_awaited_once()

    async def test_private_chat_is_never_gated(self) -> None:
        with patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=_binding())), \
             patch.object(prov, "resolve_bot_token", return_value="bot-token-123"), \
             patch.object(prov, "get_me", new=AsyncMock(return_value={"id": "bot-numeric-id", "username": "parts_pro_bot"})) as get_me_mock, \
             patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as dispatch_mock:
            result = await prov.route_agent_inbound(
                agent_install_id="agent-1",
                chat_id="555444",
                message="need brake pads",
                sender_id="555444",
                chat_type="private",
                entities=[],
                reply_to_from_id="",
            )
        self.assertTrue(result.get("routed"))
        dispatch_mock.assert_awaited_once()
        # No identity resolution needed at all for a private chat — never
        # gated, so resolve_byo_bot_id (and its getMe call) is skipped.
        get_me_mock.assert_not_called()

    async def test_bot_id_is_resolved_once_and_cached_across_calls(self) -> None:
        with patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=_binding())), \
             patch.object(prov, "resolve_bot_token", return_value="bot-token-123"), \
             patch.object(prov, "get_me", new=AsyncMock(return_value={"id": "bot-numeric-id", "username": "parts_pro_bot"})) as get_me_mock, \
             patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)):
            for _ in range(3):
                await prov.route_agent_inbound(
                    agent_install_id="agent-1",
                    chat_id="-100999",
                    message="yes",
                    sender_id="42",
                    chat_type="group",
                    entities=[],
                    reply_to_from_id="bot-numeric-id",
                )
        self.assertEqual(get_me_mock.call_count, 1, "getMe must be resolved once per credential, then cached")

    async def test_getme_failure_is_non_fatal_and_falls_back_to_mention_only(self) -> None:
        # A reply-to-bot check can't be verified without a resolvable id,
        # but the stored bot_username mention check must still work.
        text = "@parts_pro_bot are you there"
        with patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=_binding())), \
             patch.object(prov, "resolve_bot_token", return_value="bot-token-123"), \
             patch.object(prov, "get_me", new=AsyncMock(side_effect=RuntimeError("network down"))), \
             patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)) as dispatch_mock:
            result = await prov.route_agent_inbound(
                agent_install_id="agent-1",
                chat_id="-100999",
                message=text,
                sender_id="42",
                chat_type="group",
                entities=[{"type": "mention", "offset": 0, "length": len("@parts_pro_bot")}],
                reply_to_from_id="",
            )
        self.assertTrue(result.get("routed"))
        dispatch_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
