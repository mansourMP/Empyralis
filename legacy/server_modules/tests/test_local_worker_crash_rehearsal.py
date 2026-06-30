import importlib.util
from pathlib import Path
import sys
from unittest import TestCase


def _load_rehearsal_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "run_local_worker_crash_rehearsal.py"
    spec = importlib.util.spec_from_file_location("test_local_worker_crash_rehearsal_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["test_local_worker_crash_rehearsal_module"] = module
    spec.loader.exec_module(module)
    return module


rehearsal = _load_rehearsal_module()


class LocalWorkerCrashRehearsalTests(TestCase):
    def test_run_crash_rehearsal_exhausts_auto_retry_and_requires_manual_gate(self):
        summary = rehearsal.run_crash_rehearsal(
            cycles=4,
            kill_after_task_heartbeat_count=1,
            step_delay_seconds=30.0,
            quiet_worker=True,
        )

        self.assertEqual(summary["final_status"], "waiting_for_input")
        self.assertEqual(summary["final_error"], "local_worker_recovery_exhausted")
        self.assertTrue(summary["manual_confirmation_required"])
        self.assertTrue(summary["auto_retry_exhausted"])
        self.assertEqual(summary["attempt_count"], 3)
        self.assertEqual(summary["watchdog_resume_count"], 2)
        event_kinds = [str(item.get("kind") or "") for item in summary["events"]]
        self.assertIn("claim", event_kinds)
        self.assertIn("cleanup", event_kinds)
        self.assertIn("watchdog_resume", event_kinds)
        self.assertIn("worker_killed", event_kinds)


if __name__ == "__main__":
    raise SystemExit(0)
