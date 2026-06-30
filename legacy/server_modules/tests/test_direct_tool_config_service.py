import json
import unittest

from server_modules import direct_tool_config_service as service


class DirectToolConfigServiceTests(unittest.TestCase):
    def test_build_direct_tool_config_parses_google_workspace_email(self) -> None:
        payload = service.build_direct_tool_config(
            "google_workspace",
            "send_email",
            "to john@example.com subject Demo body Hello there",
            parse_json_object_loose=lambda value: {},
        )

        self.assertEqual(payload["to_email"], "john@example.com")
        self.assertEqual(payload["subject"], "Demo")
        self.assertEqual(payload["text"], "Hello there")

    def test_build_direct_local_tool_config_requires_computer_click_target(self) -> None:
        with self.assertRaises(RuntimeError):
            service.build_direct_local_tool_config("computer", "click", {})

    def test_tool_write_action_available_requires_connected_capability_action(self) -> None:
        self.assertTrue(
            service.tool_write_action_available(
                "slack",
                "post_message",
                [{"id": " Slack ", "connected": True, "write_actions": [" post_message "]}],
            )
        )
        self.assertFalse(
            service.tool_write_action_available(
                "slack",
                "post_message",
                [{"id": "slack", "connected": False, "write_actions": ["post_message"]}],
            )
        )

    def test_approved_action_to_tool_call_maps_http_request_name(self) -> None:
        payload = service.approved_action_to_tool_call(
            {"connector": "http", "action": "request", "input": "{\"url\":\"https://example.com\"}"},
            parse_json_object_loose=lambda value: json.loads(value),
        )

        self.assertEqual(payload["name"], "http_request")
        self.assertIn("https://example.com", payload["arguments"])

    def test_format_direct_local_tool_result_formats_shell_output(self) -> None:
        result = service.format_direct_local_tool_result(
            {
                "result_data": {
                    "child_result": {
                        "outputs": {
                            "actions": [
                                {
                                    "tool": "execute_shell_command",
                                    "command": "echo hello",
                                    "stdout_preview": "hello",
                                }
                            ]
                        }
                    }
                }
            }
        )

        self.assertIn("echo hello", result)
        self.assertIn("hello", result)


if __name__ == "__main__":
    unittest.main()
