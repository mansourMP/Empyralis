import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "orion_ops_daemon.py"
    spec = importlib.util.spec_from_file_location("test_orion_ops_daemon_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


ops_daemon = _load_module()


class OpsDaemonWatchdogTests(TestCase):
    def test_startup_grace_suppresses_worker_and_telegram_issues(self):
        def fake_runtime_request(runtime_key, path, method="GET", payload=None, runtime_url=ops_daemon.DEFAULT_RUNTIME_URL):
            if path == "/health":
                return 200, {"ok": True, "telegram_autopilot_enabled": True}
            if path == "/runtime/runtimes/status":
                return 200, {"summary": {"online": 0}}
            if path == "/channels/telegram/autopilot/status":
                return 200, {"autopilot": {"active": False, "thread_alive": False, "last_error": None}}
            raise AssertionError(path)

        with (
            patch.object(ops_daemon, "_runtime_request", side_effect=fake_runtime_request),
            patch.object(ops_daemon, "_runtime_start_age_seconds", return_value=5),
        ):
            probe = ops_daemon._watchdog_probe("key", ops_daemon.DEFAULT_RUNTIME_URL)

        self.assertTrue(probe["runtime_ok"])
        self.assertTrue(probe["startup_grace_active"])
        self.assertEqual(probe["issues"], [])
        self.assertTrue(probe["healthy"])

    def test_post_grace_worker_and_telegram_issues_are_reported(self):
        def fake_runtime_request(runtime_key, path, method="GET", payload=None, runtime_url=ops_daemon.DEFAULT_RUNTIME_URL):
            if path == "/health":
                return 200, {"ok": True, "telegram_autopilot_enabled": True}
            if path == "/runtime/runtimes/status":
                return 200, {"summary": {"online": 0}}
            if path == "/channels/telegram/autopilot/status":
                return 200, {"autopilot": {"active": False, "thread_alive": False, "last_error": None}}
            raise AssertionError(path)

        with (
            patch.object(ops_daemon, "_runtime_request", side_effect=fake_runtime_request),
            patch.object(ops_daemon, "_runtime_start_age_seconds", return_value=120),
        ):
            probe = ops_daemon._watchdog_probe("key", ops_daemon.DEFAULT_RUNTIME_URL)

        self.assertFalse(probe["startup_grace_active"])
        self.assertIn("local_worker_offline", probe["issues"])
        self.assertIn("telegram_autopilot_inactive", probe["issues"])
        self.assertFalse(probe["healthy"])

    def test_active_local_work_suppresses_non_runtime_recovery_issues(self):
        def fake_runtime_request(runtime_key, path, method="GET", payload=None, runtime_url=ops_daemon.DEFAULT_RUNTIME_URL):
            if path == "/health":
                return 200, {"ok": True, "telegram_autopilot_enabled": True}
            if path == "/runtime/runtimes/status":
                return 200, {"summary": {"online": 0, "pending_runs": 1, "claimed_runs": 1}}
            if path == "/channels/telegram/autopilot/status":
                return 200, {"autopilot": {"active": False, "thread_alive": False, "last_error": None}}
            raise AssertionError(path)

        with (
            patch.object(ops_daemon, "_runtime_request", side_effect=fake_runtime_request),
            patch.object(ops_daemon, "_runtime_start_age_seconds", return_value=120),
        ):
            probe = ops_daemon._watchdog_probe("key", ops_daemon.DEFAULT_RUNTIME_URL)

        self.assertTrue(probe["active_local_work"])
        self.assertEqual(probe["pending_runs"], 1)
        self.assertEqual(probe["claimed_runs"], 1)
        self.assertEqual(probe["issues"], [])
        self.assertTrue(probe["healthy"])
