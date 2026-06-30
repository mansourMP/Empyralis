import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import doctor_gate


class DoctorGateTests(unittest.TestCase):
    def test_latest_doctor_report_reads_snapshot_without_name_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "doctor-report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "available": True,
                        "overview": {"status": "pass", "title": "Healthy", "detail": "All checks passed."},
                        "summary": {"pass": 1, "warn": 0, "fail": 0},
                        "checks": [],
                        "priority_fixes": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(doctor_gate, "_doctor_report_file", return_value=report_path):
                report = doctor_gate.latest_doctor_report()

        self.assertTrue(report.get("available"))
        self.assertEqual(report.get("overview", {}).get("title"), "Healthy")


if __name__ == "__main__":
    unittest.main()
