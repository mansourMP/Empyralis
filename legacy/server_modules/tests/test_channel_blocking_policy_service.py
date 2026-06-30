import unittest

from server_modules import channel_blocking_policy_service


class ChannelBlockingPolicyServiceTests(unittest.TestCase):
    def test_control_commands_are_owner_only_by_default(self) -> None:
        for command in ("/model gpt-5", "/config show", "/system reset", "/approve all", "/send hello"):
            result = channel_blocking_policy_service.check_personal_channel_control_command(text=command)

            self.assertTrue(result["blocked"])
            self.assertEqual(result["reason"], "owner_authorization_required")

    def test_owner_role_can_use_control_command(self) -> None:
        result = channel_blocking_policy_service.check_personal_channel_control_command(
            text="/model gpt-5",
            sender_role="owner",
        )

        self.assertFalse(result["blocked"])
        self.assertEqual(result["command"], "model")

    def test_normal_messages_are_not_commands(self) -> None:
        self.assertIsNone(
            channel_blocking_policy_service.check_personal_channel_control_command(
                text="Do you have this item in stock?",
            )
        )

    def test_thread_commands_include_thread_contract(self) -> None:
        result = channel_blocking_policy_service.check_personal_channel_control_command(
            text="/new morning brief",
            sender_role="owner",
        )

        self.assertFalse(result["blocked"])
        self.assertEqual(result["thread_command"]["action"], "start_new_thread")
        self.assertEqual(result["thread_command"]["title"], "morning brief")


if __name__ == "__main__":
    unittest.main()
