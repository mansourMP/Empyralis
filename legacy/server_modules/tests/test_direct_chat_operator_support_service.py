import threading
import types
import unittest
from unittest import mock

from server_modules import direct_chat_operator_support_service as service


class DirectChatOperatorSupportServiceTests(unittest.TestCase):
    def test_normalize_tool_capabilities_filters_and_normalizes(self) -> None:
        payload = service.normalize_tool_capabilities(
            {
                "tool_capabilities": [
                    {
                        "id": " Slack ",
                        "label": " Slack Live ",
                        "connected": True,
                        "runtime_usable": True,
                        "read_actions": [" history.read ", ""],
                        "write_actions": [" post_message "],
                        "approval_required_actions": ["post_message"],
                    },
                    {"id": "", "label": "skip"},
                    "skip",
                ]
            }
        )

        self.assertEqual(payload[0]["id"], "slack")
        self.assertEqual(payload[0]["label"], "Slack Live")
        self.assertEqual(payload[0]["read_actions"], ["history.read"])
        self.assertEqual(payload[0]["write_actions"], ["post_message"])

    def test_tool_helpers_delegate_to_normalized_payload(self) -> None:
        availability = {
            "tool_capabilities": [
                {"id": "browser", "connected": True, "runtime_usable": False},
            ]
        }

        self.assertTrue(service.tool_connected(availability, "browser"))
        self.assertFalse(service.tool_runtime_usable(availability, "browser"))
        self.assertIsNone(service.tool_capability(availability, "missing"))

    def test_local_worker_available_defaults_true_when_not_declared(self) -> None:
        self.assertTrue(service.local_worker_available({}))
        self.assertFalse(service.local_worker_available({"runtime_ok": False}))

    def test_local_worker_available_prefers_live_worker_over_unpaired_gateway(self) -> None:
        self.assertTrue(
            service.local_worker_available(
                {"runtime_ok": True, "local_gateway_online": False, "local_worker_online": True}
            )
        )

    def test_local_worker_available_keeps_gateway_false_when_runtime_not_healthy(self) -> None:
        self.assertFalse(
            service.local_worker_available(
                {"runtime_ok": False, "local_gateway_online": False}
            )
        )

    def test_local_worker_available_does_not_treat_runtime_health_as_worker_presence(self) -> None:
        self.assertFalse(
            service.local_worker_available(
                {"runtime_ok": True, "local_gateway_online": False}
            )
        )

    def test_active_run_count_reads_live_runs_from_repository(self) -> None:
        with mock.patch.object(
            service.run_state_repository,
            "sync_list_live_runs",
            return_value=[
                {"run_id": "1", "status": "running", "context": {"workspace_id": "default"}},
                {"run_id": "2", "status": "completed", "context": {"workspace_id": "default"}},
                {"run_id": "3", "status": "queued", "context": {"workspace_id": "other"}},
            ],
        ):
            self.assertEqual(service.active_run_count("default"), 1)

    def test_recent_run_prompts_reads_history_from_shared_module(self) -> None:
        fake_shared = types.SimpleNamespace(
            RUN_HISTORY=[
                {"workspace_id": "default", "user_goal": "ship the next refactor cut"},
                {"workspace_id": "default", "user_goal": "ship the next refactor cut"},
                {"workspace_id": "other", "user_goal": "skip me"},
            ],
            RUN_HISTORY_LOCK=threading.Lock(),
        )

        with mock.patch.dict("sys.modules", {"server_modules.shared": fake_shared}):
            payload = service.recent_run_prompts_for_suggestions("default")

        self.assertEqual(payload, ["ship the next refactor cut"])


if __name__ == "__main__":
    unittest.main()
