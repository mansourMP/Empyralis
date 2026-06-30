import unittest

from server_modules import personal_channel_thread_command_service as service


class PersonalChannelThreadCommandServiceTests(unittest.TestCase):
    def test_parse_thread_commands(self) -> None:
        cases = {
            "/new launch notes": ("new", "start_new_thread", "", "launch notes"),
            "/threads": ("threads", "list_threads", "", ""),
            "/use thread_123": ("use", "switch_thread", "thread_123", ""),
            "/status": ("status", "show_status", "", ""),
            "/help": ("help", "show_help", "", ""),
        }

        for text, expected in cases.items():
            command = service.parse_thread_command(text)

            self.assertIsNotNone(command)
            assert command is not None
            self.assertEqual((command.command, command.action, command.thread_id, command.title), expected)
            self.assertFalse(command.dispatch_allowed)
            self.assertTrue(command.requires_owner_context)

    def test_invalid_use_command_does_not_accept_free_text(self) -> None:
        command = service.parse_thread_command("/use ../bad")

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.action, "invalid_thread_reference")
        self.assertEqual(command.thread_id, "")

    def test_normal_messages_are_not_thread_commands(self) -> None:
        self.assertIsNone(service.parse_thread_command("new thread please"))
        self.assertIsNone(service.parse_thread_command("please /use this thread"))

    def test_channel_thread_key_is_stable_and_sanitized(self) -> None:
        key = service.canonical_channel_thread_key(
            channel_key="telegram personal",
            gateway_id="gateway/1",
            remote_jid="+1 555 123",
            thread_alias="daily brief",
        )

        self.assertEqual(key, "telegram_personal:gateway_1:1_555_123:daily_brief")

    def test_list_threads_reply_is_bounded(self) -> None:
        command = service.parse_thread_command("/threads")
        assert command is not None
        reply = service.build_thread_command_reply(
            command,
            available_threads=[{"id": f"thread-{index}", "title": f"Thread {index}"} for index in range(12)],
        )

        self.assertIn("thread-0", reply)
        self.assertIn("thread-9", reply)
        self.assertNotIn("thread-10", reply)


if __name__ == "__main__":
    unittest.main()
