import unittest

from server_modules.connectors.telegram_transport_service import TelegramTransportService


class TelegramTransportServiceTests(unittest.TestCase):
    def _make_service(self, **overrides):
        self.events = overrides.pop("events", [])
        self.dead = overrides.pop("dead", [])
        self.requests = overrides.pop("requests", [])
        return TelegramTransportService(
            poll_seconds=5.0,
            http_json_request=overrides.pop(
                "http_json_request",
                lambda *args, **kwargs: self.requests.append({"args": args, "kwargs": kwargs}) or {"status": 200, "json": {"ok": True, "result": {"message_id": "42"}}},
            ),
            session_key=overrides.pop("session_key", lambda chat_id: f"telegram:{chat_id}"),
            safe_path_token=overrides.pop("safe_path_token", lambda value: "safe"),
            reply_keyboard=overrides.pop("reply_keyboard", lambda profile: {"keyboard": [[{"text": "x"}]]}),
            append_dead_letter=overrides.pop("append_dead_letter", lambda **kwargs: self.dead.append(kwargs)),
            record_channel_event=overrides.pop("record_channel_event", lambda **kwargs: self.events.append(kwargs)),
            utc_now_iso=overrides.pop("utc_now_iso", lambda: "2026-04-05T00:00:00Z"),
        )

    def test_send_message_records_event(self):
        service = self._make_service()
        message_id = service.send_message(bot_token="bot", chat_id="chat", text="hello", profile={})
        self.assertEqual(message_id, "42")
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["event_type"], "message")

    def test_edit_message_returns_false_on_transport_error(self):
        service = self._make_service(http_json_request=lambda *args, **kwargs: {"status": 500, "json": {"ok": False, "description": "bad"}})
        result = service.edit_message(bot_token="bot", chat_id="chat", message_id="1", text="hello")
        self.assertFalse(result)
        self.assertEqual(len(self.dead), 1)

    def test_send_chat_action_ignores_failures(self):
        service = self._make_service(http_json_request=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
        service.send_chat_action("bot", "chat")
        self.assertEqual(len(self.dead), 0)


if __name__ == "__main__":
    unittest.main()
