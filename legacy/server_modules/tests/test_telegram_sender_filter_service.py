import unittest

from server_modules.connectors.telegram_sender_filter_service import TelegramSenderFilterService


class TelegramSenderFilterServiceTests(unittest.TestCase):
    def test_handle_denied_sender_records_and_updates_state(self) -> None:
        events = []
        states = []

        service = TelegramSenderFilterService(
            record_channel_event_throttled=lambda **kwargs: events.append(kwargs),
            set_connector_state=lambda connector_id, patch: states.append((connector_id, patch)),
            utc_now_iso=lambda: "2026-04-04T00:00:00Z",
        )

        service.handle_denied_sender(
            connector_id="conn-1",
            label="label-1",
            workspace_id="ws-1",
            update_id=7,
            profile_id="profile-1",
            allow_from=["user-1"],
            sender_id="user-2",
            sender_username="denied",
            chat_id="chat-1",
            dropped_count=3,
            dedupe_seconds=8.0,
        )

        self.assertEqual(events[0]["event_type"], "drop_sender_not_allowed")
        self.assertEqual(states[0][0], "conn-1")
        self.assertEqual(states[0][1]["dropped_sender_count"], 3)


if __name__ == "__main__":
    unittest.main()
