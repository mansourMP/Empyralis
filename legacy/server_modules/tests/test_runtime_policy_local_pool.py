import unittest
from unittest.mock import patch

from server_modules import runtime_policy


class RuntimePolicyLocalPoolTests(unittest.TestCase):
    @patch("server_modules.local_queue.handle_get_local_workers_status")
    def test_local_runtime_pool_uses_current_status_helper(self, mock_status):
        mock_status.return_value = {
            "items": [
                {
                    "runtime_id": "empyralis-tauri-local",
                    "worker_id": "empyralis-tauri-local",
                    "runtime_type": "local_companion",
                    "display_name": "Empyralis Local Worker",
                    "capabilities": ["read_write_files"],
                    "execution_targets": ["local_companion"],
                    "trust_state": "verified",
                    "online": True,
                    "current_run_id": None,
                    "last_seen_at": "2026-03-27T00:00:00Z",
                    "capability_count": 1,
                }
            ]
        }

        state = runtime_policy._local_runtime_pool_state()

        self.assertEqual(state.get("online_runtime_ids"), ["empyralis-tauri-local"])
        self.assertEqual(state.get("available_runtime_ids"), ["empyralis-tauri-local"])
        mock_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
