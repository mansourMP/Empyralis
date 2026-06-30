import unittest

from server_modules.connectors.telegram_inbound_context_service import TelegramInboundContextService


class TelegramInboundContextServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> TelegramInboundContextService:
        events = overrides.pop("events", [])
        messages = overrides.pop("messages", [])
        return TelegramInboundContextService(
            media_max_items=3,
            extract_message=overrides.pop("extract_message", lambda update: update.get("message")),
            chat_matches=overrides.pop("chat_matches", lambda configured_chat_id, chat: True),
            store_attachments=overrides.pop("store_attachments", lambda **kwargs: []),
            route_message=overrides.pop("route_message", lambda text, profile: {"action": "ignore"}),
            session_key_builder=overrides.pop("session_key_builder", lambda chat_id: f"session:{chat_id}"),
            trace_id_builder=overrides.pop("trace_id_builder", lambda chat_id, update_id, message_id: f"trace:{chat_id}:{update_id}:{message_id}"),
            record_channel_event=lambda **kwargs: events.append(kwargs) or {"id": "evt-1"},
            guided_setup_handler=overrides.pop("guided_setup_handler", lambda **kwargs: {"handled": False}),
            send_message=lambda **kwargs: messages.append(kwargs),
        )

    def test_extract_inbound_message_rejects_bot_and_chat_mismatch(self) -> None:
        service = self._make_service(chat_matches=lambda configured_chat_id, chat: False)
        result = service.extract_inbound_message(
            update={"update_id": 7, "message": {"chat": {"id": "chat"}, "from": {"id": "user"}}},
            configured_chat_id="chat",
        )
        self.assertFalse(result["handled"])
        self.assertEqual(result["reason"], "chat_mismatch")

        service = self._make_service()
        result = service.extract_inbound_message(
            update={"update_id": 8, "message": {"chat": {"id": "chat"}, "from": {"id": "user", "is_bot": True}}},
            configured_chat_id="chat",
        )
        self.assertFalse(result["handled"])
        self.assertEqual(result["reason"], "bot_sender")

    def test_build_inbound_context_promotes_image_only_run_and_records_event(self) -> None:
        events = []
        service = self._make_service(
            events=events,
            store_attachments=lambda **kwargs: [
                {
                    "kind": "photo",
                    "mime_type": "image/jpeg",
                    "relative_path": "telegram/ws/chat/file.jpg",
                    "bytes": 12,
                }
            ],
            route_message=lambda text, profile: {"action": "ignore"},
        )
        result = service.build_inbound_context(
            bot_token="token",
            workspace_id="ws",
            connector_id="conn",
            profile={"id": "profile-1"},
            configured_chat_id="chat-1",
            extracted_message={
                "message_id": "msg-1",
                "reply_to_message_id": "parent-1",
                "text": "",
                "attachments": [{"kind": "photo"}],
            },
            extracted_chat={"id": "chat-1"},
            extracted_sender={"id": "user-1"},
            update_id=5,
        )
        self.assertEqual(result["action"], "run")
        self.assertEqual(result["routed"]["source"], "image_only")
        self.assertEqual(result["source_event_id"], "evt-1")
        self.assertEqual(events[0]["metadata"]["attachments_count"], 1)

    def test_handle_guided_setup_records_and_sends_reply(self) -> None:
        events = []
        messages = []
        service = self._make_service(
            events=events,
            messages=messages,
            guided_setup_handler=lambda **kwargs: {"handled": True, "reply": "setup complete"},
        )
        result = service.handle_guided_setup(
            workspace_id="ws",
            connector_id="conn",
            profile={"id": "profile-1"},
            bot_token="token",
            chat_id="chat-1",
            message_text="yes",
            inbound_message_id="msg-1",
            session_key="session:chat-1",
            trace_id="trace-1",
            source_event_id="evt-1",
        )
        self.assertTrue(result["handled"])
        self.assertEqual(events[0]["event_type"], "automation_setup_reply")
        self.assertEqual(messages[0]["text"], "setup complete")


if __name__ == "__main__":
    unittest.main()
