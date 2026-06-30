import unittest

from server_modules.connectors.runtime_status_service import RuntimeStatusService


class RuntimeStatusServiceTests(unittest.TestCase):
    def test_runtime_status_text_renders_metrics(self) -> None:
        service = RuntimeStatusService(
            local_companion_snapshot=lambda: {"online_workers": 2, "pending_runs": 3, "claimed_runs": 1},
            current_metrics=lambda: {"runs_started": 10, "runs_completed": 8, "runs_failed": 1, "runs_timeout": 1},
            latest_run_summary=lambda: "abcd1234 completed",
            runtime_valid=lambda: True,
        )
        text = service.runtime_status_text("ws-1")
        self.assertIn("workspace: ws-1", text)
        self.assertIn("runtime: healthy", text)
        self.assertIn("started=10 completed=8 failed=1 timeout=1", text)
        self.assertIn("online_workers=2 pending=3 claimed=1", text)
        self.assertIn("recent: abcd1234 completed", text)


if __name__ == "__main__":
    unittest.main()
