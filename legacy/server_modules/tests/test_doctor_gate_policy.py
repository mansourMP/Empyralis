import unittest

from server_modules import doctor_gate


class DoctorGatePolicyTests(unittest.TestCase):
    def test_local_companion_noncritical_fail_is_warning_only(self):
        report = {
            "available": True,
            "generated_at": "2026-03-27T00:00:00Z",
            "overview": {"status": "fail", "title": "Critical gaps found", "detail": "Resolve failed checks."},
            "summary": {"pass": 0, "warn": 0, "fail": 1},
            "checks": [
                {
                    "name": "telegram_autopilot_runtime",
                    "status": "fail",
                    "detail": "Telegram autopilot is enabled but worker thread is not active.",
                    "recommendation": "Restart runtime.",
                }
            ],
            "priority_fixes": [],
        }

        gate = doctor_gate.evaluate_doctor_run_gate(report, execution_target="local_companion")

        self.assertEqual(gate.get("status"), "warn")
        self.assertFalse(gate.get("blocking"))

    def test_local_companion_auth_policy_fail_blocks(self):
        report = {
            "available": True,
            "generated_at": "2026-03-27T00:00:00Z",
            "overview": {"status": "fail", "title": "Critical gaps found", "detail": "Resolve failed checks."},
            "summary": {"pass": 0, "warn": 0, "fail": 1},
            "checks": [
                {
                    "name": "auth_policy",
                    "status": "fail",
                    "detail": "ORION_AUTH_REQUIRED=1 but ORION_API_KEY is not configured.",
                    "recommendation": "Set ORION_API_KEY.",
                }
            ],
            "priority_fixes": [],
        }

        gate = doctor_gate.evaluate_doctor_run_gate(report, execution_target="local_companion")

        self.assertEqual(gate.get("status"), "fail")
        self.assertTrue(gate.get("blocking"))


if __name__ == "__main__":
    unittest.main()
