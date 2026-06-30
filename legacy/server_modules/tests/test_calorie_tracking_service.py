from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from server_modules import calorie_tracking_service, mini_apps_service, workspace_context


class CalorieTrackingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="calorie-service-")
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", Path(self._tmpdir.name) / "workspace")
        self._workspace_patch.start()

    def tearDown(self) -> None:
        self._workspace_patch.stop()
        self._tmpdir.cleanup()

    def test_log_event_and_update_goal_sync_contract(self) -> None:
        now_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        calorie_tracking_service.log_calorie_event(
            "ws-1",
            {
                "id": "meal-1",
                "meal_label": "Lunch",
                "calories": 710,
                "protein_g": 55,
                "carbs_g": 60,
                "fat_g": 24,
                "timestamp": now_ts,
            },
            write_authorization={"explicit_user_intent": True},
        )
        calorie_tracking_service.update_calorie_goals(
            "ws-1",
            {
                "calorie_goal": 2200,
                "protein_goal_g": 180,
            },
            write_authorization={"explicit_user_intent": True},
        )

        contract = mini_apps_service.get_mini_app_contract("ws-1", "calorie_tracking")
        self.assertEqual(contract["current_state"]["today_totals"]["calories"], 710.0)
        self.assertEqual(contract["current_state"]["goals"]["calories"], 2200.0)
        self.assertEqual(contract["daily_summary"]["status"], "on_target")
        self.assertEqual(contract["records_count"], 1)

    def test_overview_for_specific_date_uses_filtered_day_totals(self) -> None:
        calorie_tracking_service.log_calorie_event(
            "ws-1",
            {
                "id": "meal-a",
                "meal_label": "Breakfast",
                "calories": 550,
                "timestamp": "2026-04-16T08:00:00Z",
            },
            write_authorization={"explicit_user_intent": True},
        )
        calorie_tracking_service.log_calorie_event(
            "ws-1",
            {
                "id": "meal-b",
                "meal_label": "Dinner",
                "calories": 900,
                "timestamp": "2026-04-17T18:00:00Z",
            },
            write_authorization={"explicit_user_intent": True},
        )

        overview = calorie_tracking_service.calorie_overview("ws-1", date_filter="2026-04-16")
        self.assertEqual(overview["date"], "2026-04-16")
        self.assertEqual(overview["daily_summary"]["totals"]["calories"], 550.0)
        self.assertEqual(overview["records_preview_count"], 1)
        self.assertEqual(overview["records_preview"][0]["id"], "meal-a")

    def test_write_requires_explicit_user_intent(self) -> None:
        with self.assertRaises(PermissionError):
            calorie_tracking_service.log_calorie_event(
                "ws-1",
                {
                    "id": "meal-a",
                    "meal_label": "Breakfast",
                    "calories": 500,
                },
            )


if __name__ == "__main__":
    unittest.main()
