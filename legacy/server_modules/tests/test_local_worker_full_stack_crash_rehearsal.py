import json
import subprocess
import sys
from pathlib import Path
from unittest import TestCase


class LocalWorkerFullStackCrashRehearsalTests(TestCase):
    def test_full_stack_rehearsal_reaches_manual_degradation_gate(self):
        script_path = (
            Path(__file__).resolve().parents[2] / "scripts" / "run_local_worker_full_stack_crash_rehearsal.py"
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--cycles",
                "4",
                "--quiet-worker",
            ],
            capture_output=True,
            text=True,
            cwd=str(script_path.parents[1]),
            timeout=180,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "Full-stack crash rehearsal failed.\n"
                f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
            )

        payload = json.loads(proc.stdout)
        self.assertTrue(payload["browser_checkpoint_created_via_worker"])
        self.assertEqual(payload["status"], "waiting_for_input")
        self.assertEqual(payload["result_error"], "local_worker_recovery_exhausted")
        self.assertTrue(payload["manual_confirmation_required"])
        self.assertEqual(payload["attempt_count"], 3)
        self.assertEqual(payload["manual_gate_response"]["status"], "confirmation_required")
        self.assertGreaterEqual(len(payload["cycle_summaries"]), 4)


if __name__ == "__main__":
    raise SystemExit(0)
