"""Route-level proof that routes_sage_telegram_hosted.py threads the real
per-message Telegram sender id through to the command/reply dispatchers,
never the shared chat_id.

FIX B: telegram_webhook, dev_poll_once, and telegram_agent_byo_webhook each
independently duplicated the "sender_id=str(chat_id)" collapse bug (4 of the
task's flagged call sites live in this one file). These tests drive the
actual FastAPI route handlers end to end (not just the underlying
sage_telegram_hosted_service helpers, which test_telegram_hosted_group_safety.py
already covers) to prove the wiring itself is fixed, not just the service
functions it calls.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import routes_sage_telegram_hosted as routes
from server_modules import sage_telegram_hosted_service as hosted


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)
    return app


def _telegram_message_update(*, update_id: int, chat_id: int, chat_type: str, from_id: int, text: str, message_id: int = 1, first_name: str = "Zoe") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": from_id, "first_name": first_name, "is_bot": False},
            "text": text,
        },
    }


class TelegramWebhookSenderIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        routes._HOSTED_WEBHOOK_RATE_BUCKETS.clear()
        self._pairs_backup = dict(hosted._SAGE_HOSTED_PAIRS)
        hosted._SAGE_HOSTED_PAIRS.clear()
        hosted._SAGE_HOSTED_PAIRS["-100555"] = {"workspace_id": "ws-1", "paired_at": "2020-01-01T00:00:00+00:00"}
        self.client = TestClient(_build_app())

    def tearDown(self) -> None:
        hosted._SAGE_HOSTED_PAIRS.clear()
        hosted._SAGE_HOSTED_PAIRS.update(self._pairs_backup)

    def test_webhook_passes_the_real_sender_id_to_the_reply_dispatcher(self) -> None:
        captured = {}

        async def _fake_dispatch_reply(**kwargs):
            captured.update(kwargs)
            return True

        body = _telegram_message_update(update_id=1, chat_id=-100555, chat_type="group", from_id=999888, text="hello team")
        with patch.object(hosted, "is_configured", return_value=True), \
             patch.object(hosted, "is_webhook_secret_configured", return_value=True), \
             patch.object(hosted, "verify_webhook_signature", return_value=True), \
             patch("server_modules.sage_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.sage_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch_reply):
            resp = self.client.post(
                "/sage/telegram-hosted/webhook",
                json=body,
                headers={"X-Telegram-Bot-Api-Secret-Token": "whatever"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured.get("sender_id"), "999888")
        self.assertNotEqual(captured.get("sender_id"), "-100555")

    def test_webhook_passes_the_real_sender_id_to_the_command_dispatcher(self) -> None:
        captured = {}

        async def _fake_dispatch_command(**kwargs):
            captured.update(kwargs)
            return "compacted"

        body = _telegram_message_update(update_id=2, chat_id=-100555, chat_type="group", from_id=777666, text="/compact")
        with patch.object(hosted, "is_configured", return_value=True), \
             patch.object(hosted, "is_webhook_secret_configured", return_value=True), \
             patch.object(hosted, "verify_webhook_signature", return_value=True), \
             patch("server_modules.sage_command_dispatcher.dispatch_command", new=_fake_dispatch_command), \
             patch.object(hosted, "send_message_safe", new=AsyncMock(return_value=True)):
            resp = self.client.post(
                "/sage/telegram-hosted/webhook",
                json=body,
                headers={"X-Telegram-Bot-Api-Secret-Token": "whatever"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured.get("sender_id"), "777666")

    def test_two_different_senders_in_the_same_paired_group_get_distinct_sender_ids(self) -> None:
        captured_ids = []

        async def _fake_dispatch_reply(**kwargs):
            captured_ids.append(kwargs.get("sender_id"))
            return True

        with patch.object(hosted, "is_configured", return_value=True), \
             patch.object(hosted, "is_webhook_secret_configured", return_value=True), \
             patch.object(hosted, "verify_webhook_signature", return_value=True), \
             patch("server_modules.sage_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.sage_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch_reply):
            self.client.post(
                "/sage/telegram-hosted/webhook",
                json=_telegram_message_update(update_id=3, chat_id=-100555, chat_type="group", from_id=111, text="hi", first_name="Alice"),
                headers={"X-Telegram-Bot-Api-Secret-Token": "whatever"},
            )
            self.client.post(
                "/sage/telegram-hosted/webhook",
                json=_telegram_message_update(update_id=4, chat_id=-100555, chat_type="group", from_id=222, text="hi", first_name="Bob"),
                headers={"X-Telegram-Bot-Api-Secret-Token": "whatever"},
            )

        self.assertEqual(captured_ids, ["111", "222"])


class TelegramByoWebhookSenderIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        routes._HOSTED_WEBHOOK_RATE_BUCKETS.clear()
        self.client = TestClient(_build_app())

    def test_byo_webhook_threads_the_real_sender_id_into_route_agent_inbound(self) -> None:
        captured = {}

        async def _fake_route_agent_inbound(**kwargs):
            captured.update(kwargs)
            return {"routed": True, "reply_sent": True}

        binding = {"binding": {"webhook_secret": "s3cr3t", "credential_id": "cred-1", "bot_username": "parts_pro_bot"}}
        body = _telegram_message_update(update_id=5, chat_id=-100777, chat_type="supergroup", from_id=424242, text="hi bot")

        with patch("server_modules.agent_bindings_repository.get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=binding)), \
             patch("server_modules.hosted_bot_provisioning_service.route_agent_inbound", new=_fake_route_agent_inbound):
            resp = self.client.post(
                "/sage/telegram-hosted/webhook/byo/agent-1",
                json=body,
                headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured.get("sender_id"), "424242")
        self.assertNotEqual(captured.get("sender_id"), "-100777")


if __name__ == "__main__":
    unittest.main()
